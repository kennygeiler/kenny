"""Chat-correctness guards (TICKETS.md B1–B5): scoping, ground-checks, echo-back,
subjectless costing, and department-cue collisions."""
import os
import shutil
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import app as core_app  # noqa: E402
from core import llm  # noqa: E402


# --------------------------------------------------------------------------- #
# B1 — department scoping can never leak another unit's contracts
# --------------------------------------------------------------------------- #
class _Cat:
    def __init__(self, ids):
        self._ids = ids
    def documents(self):
        return [{"doc_id": d} for d in self._ids]
    def get(self, doc_id):
        return {"doc_id": doc_id} if doc_id in self._ids else None
    def clauses(self, doc_id):
        return []


class _Led:
    def __init__(self):
        self.events = []
    def append(self, type_, payload, actor="system", query_id=None):
        self.events.append({"type": type_, "payload": payload})


class _Backend:
    def __init__(self):
        self.calls = []
    def search(self, query, doc_ids=None, k=5):
        self.calls.append(doc_ids)
        return []


class _Case:
    dir = "/tmp/nowhere"
    manifest = {"sources": [{"id": "fire_mou", "department": "fire"}]}
    def departments(self):
        return ["fire", "police"]
    def docs_for_department(self, department):
        return [s["id"] for s in self.manifest["sources"]
                if department is None or s["department"] in (department, "citywide")]
    def subjects(self):
        return []
    def rules(self):
        return []
    def source_by_id(self, doc_id):
        for s in self.manifest["sources"]:
            if s["id"] == doc_id:
                return s
        return None
    def path(self, key, default=None):
        return None


def test_dept_scope_restricts_to_ingested():
    case = _Case()
    assert core_app._dept_scope(case, _Cat(["fire_mou"]), "fire") == ["fire_mou"]
    assert core_app._dept_scope(case, _Cat([]), "fire") == []        # declared ≠ ingested
    assert core_app._dept_scope(case, _Cat(["fire_mou"]), "police") == []


def test_entitlement_docless_department_never_searches_unscoped(monkeypatch):
    """The bug (app.py old:239): scope for a doc-less department was [], and search([])
    means 'no filter' — a police question was answered from the fire MOU. The search
    backend must not be called AT ALL when the scope is empty."""
    backend = _Backend()
    monkeypatch.setattr(core_app, "_backend", lambda case: backend)
    monkeypatch.setattr(core_app, "_catalog", lambda case: _Cat(["fire_mou"]))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    out = core_app._entitlement_answer(_Case(), _Led(), "q1",
                                       "how many bereavement days for police?",
                                       department="police")
    assert backend.calls == [], "empty scope must short-circuit, not search unfiltered"
    assert out.get("answer_source") == "none"
    assert "couldn't find" in out.get("answer", "").lower()


# --------------------------------------------------------------------------- #
# B2 — no LLM-produced figure reaches the user unverified
# --------------------------------------------------------------------------- #
_ROW = {"doc_id": "fire_salary_schedule", "clause": "A.1", "page": 3,
        "text": "Fire Captain | $50.00 | $52.50 | $55.10 | $57.90 | $60.80"}


def _model_says(monkeypatch, payload):
    monkeypatch.setattr(llm, "have_key", lambda: True)
    import json

    class _Block:
        type = "text"
        text = json.dumps(payload)

    class _Msg:
        content = [_Block()]
        stop_reason = "end_turn"

    class _Messages:
        @staticmethod
        def create(**kw):
            return _Msg()

    class _Client:
        messages = _Messages()

    monkeypatch.setattr(llm, "_client", lambda: _Client())


def test_invented_figure_is_downgraded_to_verbatim_quote(monkeypatch):
    """$59.40 is the average of Steps D and E — a number the model computed and the
    documents never printed. It must not be shown."""
    _model_says(monkeypatch, {"answer": "The Fire Captain rate is $59.40 (§A.1)."})
    with llm.record() as trail:
        out = llm.answer_policy("Fire Captain Step C rate?", [_ROW], lookup=True)
    assert out["source"] == "guarded"
    assert "59.40" not in out["answer"]
    assert _ROW["text"] in out["answer"]              # the evidence, verbatim
    assert out["unverified_figures"] == ["59.40"]
    assert any(c.get("rule", "").startswith("ground-check") for c in trail)


def test_grounded_figure_passes_through(monkeypatch):
    _model_says(monkeypatch, {"answer": "Step C is $55.10 per hour (§A.1)."})
    out = llm.answer_policy("Fire Captain Step C rate?", [_ROW], lookup=True)
    assert out["source"] == "claude"
    assert "$55.10" in out["answer"]


def test_ungrounded_figures_ignores_citations_and_thousands():
    assert llm._ungrounded_figures("Per §A.1 on page 3: $1,660.80",
                                   [{"clause": "A.1", "page": 3,
                                     "text": "total of 1660.80 for the shift"}]) == []
    assert llm._ungrounded_figures("$999.99", [_ROW]) == ["999.99"]


# --------------------------------------------------------------------------- #
# B3 — model-extracted numbers must echo the question
# --------------------------------------------------------------------------- #
def test_model_hours_absent_from_prompt_are_stripped_and_flagged():
    out = llm._normalize_intent({"source": "claude", "hours": 80.0, "subjects": []},
                                [], prompt="cost of an 8-hour shift for a sergeant")
    assert out["hours"] == 0.0
    assert out["unverified_numbers"] == {"hours": 80.0}


def test_model_hours_present_in_prompt_pass():
    out = llm._normalize_intent({"source": "claude", "hours": 8.0, "subjects": []},
                                [], prompt="cost of an 8-hour shift for a sergeant")
    assert out["hours"] == 8.0
    assert "unverified_numbers" not in out


def test_stub_hours_are_not_second_guessed():
    # The regex stub can only ever echo the prompt; the check is for the model path.
    out = llm._normalize_intent({"source": "stub", "hours": 8.0, "subjects": []},
                                [], prompt="whatever")
    assert out["hours"] == 8.0


# --------------------------------------------------------------------------- #
# B4 — a costing question naming nobody asks, never costs the roster
# --------------------------------------------------------------------------- #
@pytest.fixture
def client(tmp_path, monkeypatch):
    src = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "cases", "santacruz")
    case = tmp_path / "santacruz"
    shutil.copytree(src, case)
    monkeypatch.setattr(core_app, "CASE_DIR", str(case))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    from fastapi.testclient import TestClient
    return TestClient(core_app.app)


def test_subjectless_costing_asks_who(client):
    res = client.post("/chat", json={"prompt": "What does an 8-hour holiday shift "
                                               "cost?"}).json()
    assert res["mode"] == "clarify"
    assert "who is this for" in res["question"].lower()


def test_explicit_everyone_is_honoured(client):
    res = client.post("/chat", json={"prompt": "What does an 8-hour holiday shift cost "
                                               "for all classifications?"}).json()
    assert not (res.get("mode") == "clarify"
                and "who is this for" in res.get("question", "").lower())


# --------------------------------------------------------------------------- #
# B5 — an explicit department name beats a shared rank word
# --------------------------------------------------------------------------- #
def test_explicit_department_name_governs(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    both = ["fire", "police"]
    assert llm.extract_department("holiday pay for a police captain", both) == "police"
    assert llm.extract_department("holiday pay for a fire captain", both) == "fire"
    # rank word alone, single-department corpus: still resolves
    assert llm.extract_department("what does a captain earn", ["fire"]) == "fire"


def test_department_outside_corpus_returns_none_not_a_guess(monkeypatch):
    """'police captain' in a fire-only corpus must NOT scope to fire via 'captain' —
    the question is about a unit these documents don't govern."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert llm.extract_department("bereavement days for a police captain", ["fire"]) is None
