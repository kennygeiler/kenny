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
              "HOLLY_CHAT_RATE_LIMIT", "HOLLY_LEDGER_KEY", "FLY_APP_NAME"):
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

    @app.post("/admin/upload")
    def upload():
        return {"ok": True}

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


def test_viewer_admin_split_is_enforced(monkeypatch):
    """The split DEPLOY.md promises is enforced per-path (TICKETS.md C1): the viewer
    credential opens chat; only the admin credential opens /admin/* — otherwise an
    operator who shares the viewer password (as the docs tell them to) has silently
    shared the ratify gate, the uploads, and the full ledger."""
    c = _app(monkeypatch, HOLLY_VIEWER_PASSWORD="look", HOLLY_ADMIN_PASSWORD="decide")
    assert c.get("/").status_code == 401                       # anonymous
    assert c.get("/", headers=_basic("look")).status_code == 200
    assert c.get("/admin/ledger", headers=_basic("look")).status_code == 403
    assert c.post("/admin/upload", headers=_basic("look")).status_code == 403
    assert c.get("/admin/ledger", headers=_basic("decide")).status_code == 200
    assert c.get("/admin/ledger", headers=_basic("wrong")).status_code == 401


def test_deploy_refuses_to_start_without_ledger_key(monkeypatch):
    """Auth-required means tamper-evidence-required: without HOLLY_LEDGER_KEY the audit
    chain is rewritable by anyone with volume access (TICKETS.md A2)."""
    with pytest.raises(RuntimeError, match="HOLLY_LEDGER_KEY"):
        _app(monkeypatch, HOLLY_REQUIRE_AUTH="1",
             HOLLY_VIEWER_PASSWORD="look", HOLLY_ADMIN_PASSWORD="decide")


def test_cross_origin_state_change_is_refused(monkeypatch):
    """CSRF (TICKETS.md C2): a hostile page's form POST arrives with the browser's
    cached credentials AND an Origin naming the hostile site. Same-origin requests and
    non-browser clients (no Origin at all) pass."""
    c = _app(monkeypatch, HOLLY_VIEWER_PASSWORD="look", HOLLY_ADMIN_PASSWORD="decide")
    h = _basic("decide")
    assert c.post("/admin/upload", headers={**h, "Origin": "https://evil.example"}
                  ).status_code == 403
    assert c.post("/admin/upload", headers={**h, "Origin": "null"}).status_code == 403
    assert c.post("/admin/upload", headers={**h, "Origin": "http://testserver"}
                  ).status_code == 200          # same origin
    assert c.post("/admin/upload", headers=h).status_code == 200  # curl / tests
    # GETs are never blocked on Origin (they don't change state).
    assert c.get("/", headers={**_basic("look"), "Origin": "https://evil.example"}
                 ).status_code == 200


def test_failed_logins_are_throttled(monkeypatch):
    """An unthrottled Basic-auth prompt is an online password oracle (TICKETS.md C3).
    After the free misses, further attempts back off — and a good credential from the
    same client works again once the lockout expires (we reset on success before the
    lockout window here by staying under the doubling)."""
    c = _app(monkeypatch, HOLLY_VIEWER_PASSWORD="look", HOLLY_ADMIN_PASSWORD="decide")
    for _ in range(6):
        assert c.get("/", headers=_basic("wrong")).status_code == 401
    r = c.get("/", headers=_basic("wrong"))
    assert r.status_code == 429
    assert "Retry-After" in r.headers


def test_fly_client_ip_used_only_on_fly(monkeypatch):
    """Per-IP limits must key on the CLIENT, not the Fly proxy (TICKETS.md C4) — but a
    spoofable header must not let a local client pick its own bucket."""
    from types import SimpleNamespace
    req_fly = SimpleNamespace(headers={"fly-client-ip": "203.0.113.9"},
                              client=SimpleNamespace(host="172.16.0.1"))
    monkeypatch.delenv("FLY_APP_NAME", raising=False)
    assert auth._client_ip(req_fly) == "172.16.0.1"       # header ignored off-Fly
    monkeypatch.setenv("FLY_APP_NAME", "holly-demo")
    assert auth._client_ip(req_fly) == "203.0.113.9"      # trusted on Fly


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


