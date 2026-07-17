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
                                 "cases", "citywide"), case)
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


def _citywide(tmp_path, ratified: list | None = None):
    """A scratch copy of the 13-doc corpus with a chosen ratified library."""
    import json
    import shutil
    src = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "cases", "citywide")
    case = tmp_path / "citywide"
    shutil.copytree(src, case)
    if ratified is not None:
        (case / "rules" / "rules_ratified.json").write_text(
            json.dumps({"rules": ratified}, indent=2))
    return case


def _backup_rules():
    """The reference library — 11 rules across police, public works and fire."""
    import glob
    import json
    hits = sorted(glob.glob(os.path.expanduser(
        "~/holly_backup_*/rules/rules_ratified.json")))
    if not hits:
        pytest.skip("no reference library backup available")
    return json.load(open(hits[0]))["rules"]


def test_golden_pending_when_no_rule_covers_the_scenario(tmp_path, monkeypatch):
    """A scenario nothing covers is unproven — it is not WRONG, and must not block.

    A golden covers one bargaining unit; a corpus has several. Treating "no rule covers
    this yet" as a failure meant approving the police rules was blocked by the
    public-works golden, and a corpus could only be ratified in one atomic all-or-nothing
    action — the exact workflow the review queue exists to avoid.
    """
    case = _citywide(tmp_path, ratified=[])
    monkeypatch.setattr(__import__("core.app", fromlist=["app"]), "CASE_DIR", str(case))
    from core.app import _case, _check_golden

    c = _case()
    golden = next(g for g in c.golden_cases() if "police holiday" in g["name"])
    ok, detail = _check_golden(c, [], golden)          # empty library
    assert detail["status"] == "pending"
    assert ok is True, "an uncovered scenario must not block ratification"


def test_golden_fails_on_a_wrong_number_not_an_absent_one(tmp_path, monkeypatch):
    """The gate exists to stop WRONG numbers. A rule set that computes the wrong total
    must still block — loosening 'no rules' to pending must not loosen this."""
    case = _citywide(tmp_path, ratified=[])
    monkeypatch.setattr(__import__("core.app", fromlist=["app"]), "CASE_DIR", str(case))
    from core.app import _case, _check_golden

    c = _case()
    golden = next(g for g in c.golden_cases() if "police holiday" in g["name"])
    # A selector that fires but pays the wrong multiplier: a number, and the wrong one.
    bad = [{"id": "x:wrong", "kind": "selector", "result_type": "currency",
            "topic": "holiday", "priority": 10, "when": "True",
            "compute": "subject_base_hourly * hours",
            "citation": {"doc_id": "sandcity_poa_mou", "clause": "5.2", "page": 7}}]
    ok, detail = _check_golden(c, bad, golden)
    assert detail["status"] == "fail"
    assert ok is False
    assert detail["actual"] is not None, "a wrong number must be reported, not blank"


def test_ratify_judges_the_resulting_library_not_the_selection(tmp_path, monkeypatch):
    """Rules are only meaningful whole.

    A graveyard modifier computes nothing without the holiday selector it modifies.
    Judging the selection in isolation meant every rule for a unit had to be approved in
    one action, and any later addition was rejected for "no selector matched" against
    rules already live. That is a deadlock, not a gate.
    """
    rules = _backup_rules()
    police = [r for r in rules if r["citation"]["doc_id"] == "sandcity_poa_mou"
              and r["result_type"] == "currency"]
    selector = [r for r in police if r["kind"] == "selector"]
    modifiers = [r for r in police if r["kind"] == "modifier"]
    assert selector and modifiers

    # The selector is already live; the analyst now approves the modifiers.
    case = _citywide(tmp_path, ratified=selector)
    monkeypatch.setattr(__import__("core.app", fromlist=["app"]), "CASE_DIR", str(case))
    from core.app import _case, _check_golden, _ratified_dicts

    c = _case()
    golden = next(g for g in c.golden_cases()
                  if g.get("expected_total") == 4388.80)
    # Selection alone cannot answer anything -> would have blocked under the old gate.
    _ok, alone = _check_golden(c, modifiers, golden)
    assert alone["status"] == "pending"
    # Judged as live + selection, it reproduces the known answer.
    ok, together = _check_golden(c, _ratified_dicts(c) + modifiers, golden)
    assert together["status"] == "pass", together
    assert ok and together["actual"] == 4388.80


def test_ratify_accumulates_and_does_not_wipe_the_library(tmp_path, monkeypatch):
    """Ratification ADDS to the library. Writing only the new selection silently dropped
    every previously-ratified rule — approve A -> {A}; approve B -> {B}, and A was gone.
    This literally overwrote the reference library down to one rule during a test session.
    """
    import asyncio, json
    from types import SimpleNamespace
    case = _citywide(tmp_path, ratified=[])
    core_app = __import__("core.app", fromlist=["app"])
    monkeypatch.setattr(core_app, "CASE_DIR", str(case))

    # Two independent, self-contained selector rules (no golden gate interaction: neither
    # unit here has a currency golden they complete alone, so use days-type entitlement
    # rules which the police bereavement golden covers only when present).
    ruleA = {"id": "d1:a", "kind": "selector", "result_type": "text", "when": "True",
             "compute": "'x'", "citation": {"doc_id": "personnel_rules", "clause": "1"}}
    ruleB = {"id": "d1:b", "kind": "selector", "result_type": "text", "when": "True",
             "compute": "'y'", "citation": {"doc_id": "personnel_rules", "clause": "2"}}
    (case / "rules" / "rules_proposed.json").write_text(
        json.dumps({"rules": [ruleA, ruleB], "needs_data": []}))

    def ratify(ids):
        async def _json():
            return {"approver": "t", "rule_ids": ids}
        req = SimpleNamespace(json=_json)
        return asyncio.run(core_app.admin_ratify(req))

    r1 = ratify(["d1:a"])
    assert r1["ratified"] == ["d1:a"]
    r2 = ratify(["d1:b"])
    assert r2["ratified"] == ["d1:b"]
    live = {r["id"] for r in json.load(open(case / "rules" / "rules_ratified.json"))["rules"]}
    assert live == {"d1:a", "d1:b"}, f"library must stack, got {live}"


def test_policy_shortlist_only_includes_ingested_documents(tmp_path, monkeypatch):
    """docs_for_department reads case.yaml (the whole intended corpus). Shortlisting the
    declared set made a corpus with one uploaded document report "searched 13 of 13, 12
    not used" — naming 12 contracts that do not exist yet."""
    import json
    from core.catalog import Catalog
    case = _citywide(tmp_path, ratified=[])
    # Catalog holds ONE ingested doc, though case.yaml declares 13.
    Catalog(str(case / "catalog.json"))  # ensure path
    (case / "catalog.json").write_text(json.dumps({"documents": [
        {"doc_id": "tuition_reimbursement_policy", "department": "citywide",
         "clauses": [{"clause": "1.1", "text": "tuition", "page": 1, "bbox": []}]}]}))
    core_app = __import__("core.app", fromlist=["app"])
    monkeypatch.setattr(core_app, "CASE_DIR", str(case))

    c = core_app._case()
    cat = core_app._catalog(c)
    ingested = {d["doc_id"] for d in cat.documents()}
    scope = [d for d in c.docs_for_department(None) if d in ingested]
    assert scope == ["tuition_reimbursement_policy"]
    assert len(c.docs_for_department(None)) == 13    # declared
    assert len(scope) == 1                           # ingested


def test_ratify_gate_is_a_regression_guard_not_a_completeness_demand(tmp_path, monkeypatch):
    """Approving the rules a scenario was drafted FOR must succeed even while OTHER
    scenarios are still pending (their own rules not drafted yet). The old gate checked
    every scenario on every approval and blocked because a not-yet-authored paystub
    computed a wrong number — deadlocking scenario-by-scenario ratification. The gate is
    now: the target scenario must pass, and nothing that WAS passing may regress."""
    import asyncio, json
    from types import SimpleNamespace
    ref = _backup_rules()
    police = [r for r in ref if r["citation"]["doc_id"] in ("sandcity_poa_mou",)
              and r["result_type"] == "currency"]
    for r in police:
        r["_scenario"] = "police holiday shift 2025-06-30 (base MOU, graveyard 5.5%)"

    case = _citywide(tmp_path, ratified=[])
    (case / "rules" / "rules_proposed.json").write_text(
        json.dumps({"rules": police, "needs_data": []}))
    core_app = __import__("core.app", fromlist=["app"])
    monkeypatch.setattr(core_app, "CASE_DIR", str(case))

    async def _json():
        return {"approver": "t", "rule_ids": [r["id"] for r in police]}
    res = asyncio.run(core_app.admin_ratify(SimpleNamespace(json=_json)))
    # The public-works and 2026 scenarios are pending (no rules) — they must NOT block.
    assert res["ratified"], f"target-scenario rules should ratify; got {res.get('warning')}"
    live = {r["id"] for r in json.load(open(case / "rules" / "rules_ratified.json"))["rules"]}
    assert set(r["id"] for r in police) <= live


def test_check_golden_pending_when_governing_doc_has_no_rules(tmp_path, monkeypatch):
    """A number computed from an INCOMPLETE library is 'pending', not 'fail'. The side
    letter scenario needs the 6.5% amendment rule; with only the base MOU rules ratified
    it computes 4388.80 for a 4430.40 paystub — that is not-yet-authored, not wrong. Fail
    must mean only 'authored rules disagree with the paystub' or people ignore red."""
    import core.app as core_app
    ref = _backup_rules()
    # base MOU rules only (no side-letter rule)
    base_only = [r for r in ref if r["citation"]["doc_id"] == "sandcity_poa_mou"]
    case = _citywide(tmp_path, ratified=base_only)
    monkeypatch.setattr(core_app, "CASE_DIR", str(case))
    c = core_app._case()
    g = next(x for x in c.golden_cases() if "2026-07-04" in x["name"])
    ok, detail = core_app._check_golden(c, core_app._ratified_dicts(c), g)
    assert detail["status"] == "pending", detail
    assert "poa_side_letter_2025" in (detail.get("note") or "")
    assert ok is True, "an unauthored-doc scenario must not block ratification"


def test_with_live_merges_by_id_not_concatenates(tmp_path, monkeypatch):
    """A rule can be both ratified AND still in the review queue (approving does not remove
    it from the queue). Concatenating live+selection applied a differential twice — graveyard
    x1.055 compounded on itself — and 'broke' a passing scenario. Merge by id."""
    import core.app as core_app
    ref = _backup_rules()
    gv = next(r for r in ref if "graveyard" in r["id"] and r["citation"]["doc_id"] == "sandcity_poa_mou")
    case = _citywide(tmp_path, ratified=[gv])
    monkeypatch.setattr(core_app, "CASE_DIR", str(case))
    c = core_app._case()
    # re-selecting the SAME live rule must not double it
    merged = core_app._with_live(c, [gv])
    ids = [r["id"] for r in merged]
    assert ids.count(gv["id"]) == 1, f"id duplicated: {ids}"
