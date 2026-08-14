"""Async staged upload (OCR_TICKETS.md OCR-6): POST /admin/upload returns a job id,
the ingest runs off the request path walking stage parsing → tagging → indexing →
done, and the poller ends with the same result payload the endpoint used to return
inline — so a docling parse that takes minutes can no longer time out the request.
"""
import os
import shutil
import sys
import threading
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import app as core_app  # noqa: E402
from core import ingest  # noqa: E402


@pytest.fixture
def client(tmp_path, monkeypatch):
    src = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "cases", "santacruz")
    case = tmp_path / "santacruz"
    shutil.copytree(src, case)
    monkeypatch.setattr(core_app, "CASE_DIR", str(case))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    # The job registry is module-global and outlives a test's app state; a stale
    # running job from another test would 409 every upload here.
    core_app._JOBS.clear()
    from fastapi.testclient import TestClient
    return TestClient(core_app.app), str(case)


@pytest.fixture
def mini_pdf(tmp_path):
    """Two-page text PDF with section-numbered clauses (same builder as
    tests/test_ingest.py) — small enough that the raw-text tier ingests in ms."""
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas
    path = str(tmp_path / "mini_mou.pdf")
    c = canvas.Canvas(path, pagesize=letter)
    c.drawString(72, 700, "MEMORANDUM OF UNDERSTANDING")
    c.drawString(72, 660, "9.1 Holiday Premium Pay: 2.5x base for hours worked.")
    c.drawString(72, 620, "9.3 Bilingual premium: 5% of base rate.")
    c.showPage()
    c.drawString(72, 700, "11.3 Bereavement leave: 5 days per occurrence.")
    c.showPage()
    c.save()
    return path


def _poll_done(c, job_id, timeout=30.0):
    """Poll the status endpoint until the job leaves 'running'; return the last body.

    Every test that starts a job MUST drain it this way — the worker thread and the
    single-flight slot outlive the test otherwise, and the next upload 409s."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        body = c.get(f"/admin/ingest/status/{job_id}").json()
        if body["status"] != "running":
            return body
        time.sleep(0.05)
    pytest.fail(f"job {job_id} still running after {timeout}s")


def _upload(c, path, name=None):
    with open(path, "rb") as f:
        return c.post("/admin/upload",
                      files={"file": (name or os.path.basename(path), f,
                                      "application/pdf")})


def test_upload_is_async_and_staged(client, mini_pdf, monkeypatch):
    # Force past docling so the raw-text tier runs (fast, deterministic).
    monkeypatch.setattr(ingest, "_parse_with_docling", lambda p, d: None)
    c, _ = client
    res = _upload(c, mini_pdf)
    assert res.status_code == 200
    body = res.json()
    assert body["job_id"] and body["status"] == "running"
    assert body["doc_id"] == "mini_mou"

    job = _poll_done(c, body["job_id"])
    assert job["status"] == "done"
    assert job["stage"] == "done"

    r = job["result"]
    assert r["doc_id"] == "mini_mou"
    assert r["parse_source"] == "raw-text-fallback"
    assert r["clauses"] == 2                       # one page-level clause per page
    assert r["proposed_rules"] == []               # upload never drafts rules
    # Degraded extraction must be SAID, with the same wording bulk ingest uses.
    assert "degraded extraction" in (r["warning"] or "")

    # The document is really in the library, not just in the job payload.
    docs = c.get("/admin/coverage").json()["documents"]
    mine = [d for d in docs if d["doc_id"] == "mini_mou"]
    assert mine and mine[0]["parse_source"] == "raw-text-fallback"


def test_second_upload_while_one_runs_is_409(client, mini_pdf, monkeypatch):
    """Single-flight spans BOTH endpoints: an upload occupies the same slot a bulk
    ingest would, so a second upload (or a bulk ingest) must 409 while it runs."""
    monkeypatch.setattr(ingest, "_parse_with_docling", lambda p, d: None)
    gate = threading.Event()
    real = ingest.ingest_document

    def slow(*a, **kw):
        assert gate.wait(timeout=30), "test gate never opened"
        return real(*a, **kw)

    monkeypatch.setattr(ingest, "ingest_document", slow)
    c, _ = client
    first = _upload(c, mini_pdf)
    assert first.status_code == 200
    try:
        second = _upload(c, mini_pdf, name="another.pdf")
        assert second.status_code == 409
        bulk = c.post("/admin/ingest")
        assert bulk.status_code == 409
    finally:
        gate.set()                                 # let the worker finish either way
    assert _poll_done(c, first.json()["job_id"])["status"] == "done"


def test_bad_magic_bytes_reject_and_free_the_slot(client, tmp_path):
    c, _ = client
    fake = tmp_path / "not_really.pdf"
    fake.write_bytes(b"MZ this is no pdf at all")
    res = _upload(c, str(fake))
    assert res.status_code == 400
    assert "magic" in res.json()["error"]
    # The rejected upload must not leave a ghost running job holding the
    # single-flight slot — a real upload right after has to go through.
    assert not any(j.get("status") == "running" for j in core_app._JOBS.values())


def test_non_pdf_extension_is_rejected(client, tmp_path):
    c, _ = client
    f = tmp_path / "notes.txt"
    f.write_text("plain text")
    assert _upload(c, str(f)).status_code == 400
