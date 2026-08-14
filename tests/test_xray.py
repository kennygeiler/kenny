"""X-ray clause endpoint (OCR_TICKETS.md OCR-1): page-scoped clause payload with the
page's point dimensions — without width/height the overlay cannot scale bboxes onto
the rendered image."""
import json
import os
import shutil
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import app as core_app  # noqa: E402


@pytest.fixture
def client(tmp_path, monkeypatch):
    src = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "cases", "santacruz")
    case = tmp_path / "santacruz"
    shutil.copytree(src, case)
    monkeypatch.setattr(core_app, "CASE_DIR", str(case))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    from fastapi.testclient import TestClient
    return TestClient(core_app.app), str(case)


def _doc_ids(case_dir):
    with open(os.path.join(case_dir, "catalog.json")) as f:
        return [d["doc_id"] for d in json.load(f)["documents"]]


def test_clauses_are_page_scoped_with_page_metrics(client):
    c, case_dir = client
    doc_id = _doc_ids(case_dir)[0]
    res = c.get(f"/doc/{doc_id}/clauses", params={"page": 1})
    assert res.status_code == 200
    body = res.json()
    assert body["doc_id"] == doc_id
    assert body["page"] == 1
    assert body["page_count"] >= 1
    assert body["width"] > 0 and body["height"] > 0
    assert body["clauses"], "page 1 of an ingested contract must have clauses"
    for cl in body["clauses"]:
        assert cl["page"] == 1
        assert cl["kind"], "kind must be normalised, never absent or empty"
        assert len(cl["bbox"]) == 4
    # The catalog omits `kind` for normal text; the endpoint must default it.
    assert any(cl["kind"] == "text" for cl in body["clauses"])


def test_page_is_clamped_into_range(client):
    c, case_dir = client
    doc_id = _doc_ids(case_dir)[0]
    body = c.get(f"/doc/{doc_id}/clauses", params={"page": 9999}).json()
    assert body["page"] == body["page_count"]
    body = c.get(f"/doc/{doc_id}/clauses", params={"page": 0}).json()
    assert body["page"] == 1


def test_unknown_doc_is_404(client):
    c, _ = client
    assert c.get("/doc/no-such-doc/clauses").status_code == 404
