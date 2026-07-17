"""Access control for a shared deploy — the checks that stop the demo link leaking."""
import os
import sys

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import auth  # noqa: E402


def _app(monkeypatch, **env):
    for k in ("HOLLY_VIEWER_PASSWORD", "HOLLY_ADMIN_PASSWORD", "HOLLY_REQUIRE_AUTH",
              "HOLLY_CHAT_RATE_LIMIT"):
        monkeypatch.delenv(k, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    app = FastAPI()

    @app.get("/healthz")
    def health():
        return {"ok": True}

    @app.get("/")
    def chat_page():
        return {"page": "chat"}

    @app.post("/chat")
    def chat():
        return {"answer": 1}

    @app.get("/admin/ledger")
    def ledger():
        return {"events": []}

    auth.install(app)
    return TestClient(app)


def _basic(pw: str) -> dict:
    return {"Authorization": "Basic " + __import__("base64").b64encode(
        f"u:{pw}".encode()).decode()}


def test_no_passwords_leaves_app_open_for_local_dev(monkeypatch):
    c = _app(monkeypatch)
    assert c.get("/").status_code == 200


def test_deploy_refuses_to_start_without_passwords(monkeypatch):
    # The whole point: a misconfigured deploy must fail loudly, not serve /admin openly.
    with pytest.raises(RuntimeError, match="Refusing to start"):
        _app(monkeypatch, HOLLY_REQUIRE_AUTH="1")


def test_identical_passwords_rejected(monkeypatch):
    with pytest.raises(RuntimeError, match="identical"):
        _app(monkeypatch, HOLLY_VIEWER_PASSWORD="x", HOLLY_ADMIN_PASSWORD="x")


def test_one_password_opens_everything_demo_posture(monkeypatch):
    """Demo posture: EITHER credential opens the whole app, /admin included, so a shared
    link lets a reviewer walk the full loop. Anonymous is still locked out. The per-path
    viewer/admin split returns when the link stops being a demo."""
    c = _app(monkeypatch, HOLLY_VIEWER_PASSWORD="look", HOLLY_ADMIN_PASSWORD="decide")
    assert c.get("/").status_code == 401                       # anonymous
    assert c.get("/", headers=_basic("look")).status_code == 200
    assert c.get("/admin/ledger", headers=_basic("look")).status_code == 200
    assert c.get("/admin/ledger", headers=_basic("decide")).status_code == 200
    assert c.get("/admin/ledger", headers=_basic("wrong")).status_code == 401


def test_healthz_is_open(monkeypatch):
    c = _app(monkeypatch, HOLLY_VIEWER_PASSWORD="look", HOLLY_ADMIN_PASSWORD="decide")
    assert c.get("/healthz").status_code == 200  # the platform probe has no credentials


def test_chat_rate_limited(monkeypatch):
    """Every prompt spends the operator's Anthropic budget; a loop must hit a wall."""
    c = _app(monkeypatch, HOLLY_VIEWER_PASSWORD="look", HOLLY_ADMIN_PASSWORD="decide",
             HOLLY_CHAT_RATE_LIMIT="3")
    h = _basic("look")
    assert [c.post("/chat", headers=h).status_code for _ in range(3)] == [200, 200, 200]
    assert c.post("/chat", headers=h).status_code == 429
    assert c.get("/", headers=h).status_code == 200  # reading is not rate limited


def test_deploy_refuses_image_whose_goldens_fail(tmp_path, monkeypatch):
    """The image ships whatever rules_ratified.json holds — including an empty one.

    A local blind-test state would otherwise become the DEPLOYED state: chat refuses every
    question, Verification is red for every visitor, and the build reports success. Same
    argument as the ratify gate (PRD §9), one layer up: a green build is not sufficient to
    make an image shippable.
    """
    import shutil
    case = tmp_path / "cases" / "demo"
    shutil.copytree(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                 "cases", "santacruz"), case)
    (case / "rules" / "rules_ratified.json").write_text('{"rules": []}')
    # Point the app at the scratch case by patching the module attribute, NOT by setting
    # CASE and reloading core.app: importing that module runs _load_dotenv(), which reads
    # the real .env and puts ANTHROPIC_API_KEY back into the process environment. That
    # leaks out of this test and silently sends later tests to the live API — it is what
    # broke test_classify_intent_stub, which passed alone and failed in the suite.
    from core import app as core_app
    monkeypatch.setattr(core_app, "CASE_DIR", str(case))
    from scripts.prepare_deploy import _goldens_fail

    assert _goldens_fail(str(case)) is True, "a demo with zero ratified rules must not ship"


def test_ingest_reports_missing_pdf_instead_of_cataloguing_it(tmp_path):
    """A declared document with no file must be a reported gap, not an empty document.

    Every tier of parse_pdf answers "we could not extract text from this PDF", and each
    swallowed a nonexistent path into the same "empty" result. Ingesting a case.yaml that
    declared 13 documents while 12 were not yet uploaded therefore catalogued 12 contracts
    with zero clauses — indistinguishable from a scan, unanswerable, and never explained.
    """
    from core import ingest

    with pytest.raises(FileNotFoundError, match="no PDF at"):
        ingest.parse_pdf(str(tmp_path / "does_not_exist.pdf"), "ghost_doc")


def test_extract_title_prefers_the_documents_own_header():
    """A title hand-written in case.yaml is a filing label; the header is what the
    document calls itself, and it is what a user holding the contract searches for."""
    from core.ingest import extract_title

    clauses = [
        {"label": "title", "text": "2026 Police Salary Schedule", "page": 1, "clause": ""},
        {"label": "text", "text": "Section 1.1 Rates.", "page": 1, "clause": "1.1"},
    ]
    assert extract_title(clauses, "/nope.pdf", fallback="Administrative Policy 09") == \
        "2026 Police Salary Schedule"

    # No docling title and no readable metadata -> first heading-ish line on page 1.
    assert extract_title(
        [{"label": "text", "text": "Fire Salary Schedule FY26", "page": 1, "clause": ""}],
        "/nope.pdf", fallback="fb") == "Fire Salary Schedule FY26"

    # Nothing usable must still yield the caller's name, never an empty library entry.
    assert extract_title([], "/nope.pdf", fallback="fallback name") == "fallback name"
    # A numbered clause is not a title.
    assert extract_title(
        [{"label": "text", "text": "Section 9.1 Holiday pay.", "page": 1, "clause": "9.1"}],
        "/nope.pdf", fallback="fb") == "fb"


def test_doc_file_route_rejects_path_traversal():
    """doc_id comes from the URL. If it were joined into a path, this route would be an
    arbitrary file read on a host that also holds the .env and the ledger."""
    from core import app as core_app
    from fastapi.testclient import TestClient

    c = TestClient(core_app.app)
    for bad in ["..%2F..%2F..%2Fetc%2Fpasswd", "....//....//etc/passwd", "nonexistent_doc"]:
        r = c.get(f"/doc/{bad}/file")
        assert r.status_code == 404, f"{bad} -> {r.status_code}"
        assert b"root:" not in r.content


def test_table_only_page_recovers_rows_not_a_blob():
    """docling calls a table-only page a Picture and iterate_items() yields nothing.

    The text is not missing — doc.texts still holds every span with its bbox. Dropping to
    the raw-text tier threw that away and produced one page-level blob, so a salary
    schedule could not be cited by row. Cells are regrouped into rows because a rate is
    only an answer with its row: "$58.00" means nothing on its own.
    """
    from core.ingest import _from_doc_texts

    class _BBox:
        def __init__(self, l, t, r, b): self.l, self.t, self.r, self.b = l, t, r, b

    class _Prov:
        def __init__(self, page, bbox): self.page_no, self.bbox = page, bbox

    class _Text:
        def __init__(self, text, page, l, t, r, b):
            self.text, self.prov = text, [_Prov(page, _BBox(l, t, r, b))]

    class _Doc:
        texts = [  # one header row and one data row, cells out of order
            _Text("Step C", 1, 200, 620, 240, 612),
            _Text("Classification", 1, 84, 620, 140, 612),
            _Text("$58.00", 1, 200, 600, 240, 592),
            _Text("Sergeant", 1, 84, 600, 140, 592),
        ]

    rows = _from_doc_texts(_Doc())
    texts = [r["text"] for r in rows]
    assert "Classification | Step C" in texts     # cells sorted back into reading order
    assert "Sergeant | $58.00" in texts           # the rate travels with its row
    for r in rows:
        assert r["bbox"] and len(r["bbox"]) == 4  # a real box, not the whole page
    # The row's box spans its cells, so the citation highlights the row.
    sergeant = next(r for r in rows if r["text"].startswith("Sergeant"))
    assert sergeant["bbox"][0] == 84 and sergeant["bbox"][2] == 240

    assert _from_doc_texts(type("D", (), {"texts": []})()) is None


