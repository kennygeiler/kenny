"""Query router + Policy Q&A + hybrid retrieval (PRD §5.9, §8B)."""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import llm  # noqa: E402
from core.index import LocalHybridBackend, embeddings_available  # noqa: E402
from core.ingest import chunk_clauses  # noqa: E402


def test_classify_intent_stub():
    # no API key in tests -> deterministic keyword router
    assert llm.classify_intent("Calculate the total cost of an 8-hour shift") == "costing"
    assert llm.classify_intent("What does the MOU say about bilingual certification?") == "policy"
    assert llm.classify_intent("Is a weekend worker eligible for the holiday premium?") == "policy"


def test_answer_policy_grounded_fallback():
    passages = [{"doc_id": "mou", "clause": "9.3", "page": 1,
                 "text": "Bilingual employees receive a 5% bump to base rate."}]
    out = llm.answer_policy("bilingual pay?", passages)
    assert "5%" in out["answer"]           # grounded in the passage
    assert "9.3" in out["answer"]


def test_answer_policy_no_passages():
    out = llm.answer_policy("anything", [])
    assert "couldn't find" in out["answer"].lower()


@pytest.mark.skipif(not embeddings_available(), reason="sentence-transformers not installed")
def test_hybrid_semantic_recall(tmp_path):
    """Hybrid finds the right clause from a paraphrase with no keyword overlap."""
    be = LocalHybridBackend(str(tmp_path / "idx.jsonl"))
    clauses = [
        {"clause": "9.1", "page": 1, "bbox": [], "text": "Holiday premium of 1.5x base rate."},
        {"clause": "9.2", "page": 1, "bbox": [],
         "text": "If the regular shift already falls on a weekend, a flat $150 bonus instead."},
        {"clause": "9.3", "page": 1, "bbox": [], "text": "Bilingual certification 5% bump."},
    ]
    be.index("mou", chunk_clauses(clauses))
    hits = be.search("extra pay for someone who speaks two languages", doc_ids=["mou"], k=1)
    assert hits and hits[0]["clause"] == "9.3"  # semantic: 'two languages' -> bilingual


def test_classify_intent_separates_published_rates_from_costing():
    """A dollar sign is not one question.

    A salary schedule PUBLISHES rates: nothing is computed, no rule is needed, and no rule
    will ever be drafted from a rate table. Routing those to costing made Holly refuse a
    number it was holding — "no human-ratified rules" — for a question needing no rule.
    The test is whether arithmetic exists, not whether money is mentioned.
    """
    # Read from a cell -> lookup.
    for q in ["What is a Sergeant's Step C rate?",
              "What does the police salary schedule say a Corporal earns?",
              "What is the base hourly rate for a Fire Captain?"]:
        assert llm.classify_intent(q) == "lookup", q

    # Derived from a person, hours and a date -> costing, even though it says "rate".
    for q in ["What does an 8-hour holiday shift cost for the graveyard sergeants?",
              "Calculate the overtime pay for the graveyard crew on July 4th",
              "What is the total cost at the Sergeant rate for 8 hours?"]:
        assert llm.classify_intent(q) == "costing", q

    # Unaffected neighbours.
    assert llm.classify_intent("How many bereavement days does a sergeant get?") == "entitlement"
    assert llm.classify_intent("Does a weekend worker still get the holiday premium?") == "policy"


def test_answer_policy_lookup_never_computes(monkeypatch):
    """Prose invites paraphrase; a table invites arithmetic. Without a key the stub must
    quote the row, never try to read a cell by position."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    row = {"doc_id": "fire_salary_schedule", "clause": "",
           "text": "Fire Captain | $50.00 | $52.50 | $55.10 | $57.90 | $60.80",
           "page": 1}
    out = llm.answer_policy("Fire Captain Step C rate?", [row], lookup=True)
    assert out["source"] == "stub"
    assert row["text"] in out["answer"]          # the row, verbatim
    assert "fire_salary_schedule" in out["answer"]


def test_one_bad_chunk_does_not_discard_the_whole_document(monkeypatch):
    """A 26-page MOU had 13 of 14 chunks succeed with 33 rules between them — and drafted
    zero, because the map-reduce sat inside one try/except that fell back to the stub on
    any failure. The queue simply had no police rules in it, so the police golden could
    never pass and no error was ever shown.
    """
    monkeypatch.setattr(llm, "have_key", lambda: True)
    calls = {"n": 0}

    def fake(system, user, max_tokens=1500, label="llm"):
        calls["n"] += 1
        if calls["n"] == 2:                      # one poisoned chunk in the middle
            raise ValueError("Expecting ',' delimiter: line 96 column 6")
        return {"rules": [{"id": f"r{calls['n']}", "kind": "selector",
                           "result_type": "currency", "when": "True", "compute": "1",
                           "citation": {"clause": "1.1"}}],
                "needs_data": []}

    monkeypatch.setattr(llm, "_claude_json", fake)
    clauses = [{"clause": f"{i}.1", "page": 1, "text": "x", "bbox": []} for i in range(30)]
    out = llm.draft_rules(clauses, "big_mou", known_facts={"hours"}, field_values={},
                          bool_facts=[])

    assert len(out) == 2, "the surviving chunks' rules must be kept"
    assert llm.draft_rules.last_errors, "the failed chunk must be REPORTED, not swallowed"
    assert llm.draft_rules.last_errors[0]["pages"] == [1]


def test_truncated_response_is_retried_smaller_not_abandoned(monkeypatch):
    """Ten dense clauses can produce more rule JSON than the output cap allows. Truncation
    surfaced as a baffling JSONDecodeError and the remedy is to ask for less, not to give
    up — the 1500-token cap is what made the largest document draft nothing."""
    monkeypatch.setattr(llm, "have_key", lambda: True)
    seen = []

    def fake(system, user, max_tokens=1500, label="llm"):
        n = user.count('"clause"')
        seen.append(n)
        if n > 5:                                 # too much asked for at once
            raise llm.ResponseTruncated("hit the cap")
        return {"rules": [{"id": f"r{n}", "kind": "selector", "result_type": "currency",
                           "when": "True", "compute": "1",
                           "citation": {"clause": "1.1"}}], "needs_data": []}

    monkeypatch.setattr(llm, "_claude_json", fake)
    clauses = [{"clause": f"{i}.1", "page": 1, "text": "x", "bbox": []} for i in range(10)]
    out = llm.draft_rules(clauses, "d", known_facts={"hours"}, field_values={}, bool_facts=[])

    assert max(seen) == 10 and min(seen) <= 5, "the chunk must be split and retried"
    assert out, "halving must recover rules the cap would otherwise have lost"
    assert not llm.draft_rules.last_errors


def test_trail_records_whether_ai_actually_ran(monkeypatch):
    """The record must show WHETHER a model was involved, not only what it concluded.

    The ledger logged {"intent": "costing"} either way. With an expired key the keyword
    router answers and the trail looks identical — the product could quietly stop using
    AI and nothing would say so. For a system whose claim is "see how the AI reached its
    answer", whether it ran is the first thing the record has to show.
    """
    # No key -> the deterministic router answers, and says so.
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with llm.record() as trail:
        assert llm.classify_intent("What does an 8-hour shift cost for a graveyard sergeant?") == "costing"
    assert [c["source"] for c in trail] == ["fallback"]
    assert trail[0]["fn"] == "classify_intent"
    assert trail[0]["rule"] == "keyword router"      # names the substitute reasoning
    assert "model" not in trail[0]

    # Key present and the model answers -> recorded as claude, with the model id.
    monkeypatch.setattr(llm, "have_key", lambda: True)
    monkeypatch.setattr(llm, "_client", lambda: _stub_client({"intent": "policy"}))
    with llm.record() as trail:
        assert llm.classify_intent("what does the MOU say about uniforms?") == "policy"
    assert trail[0]["source"] == "claude"
    assert trail[0]["model"] == llm.MODEL
    assert "ms" in trail[0] and "prompt_chars" in trail[0]


def test_trail_records_a_model_failure_before_the_fallback_hides_it(monkeypatch):
    """A model call that failed and was absorbed by a fallback is the most common way
    this system has lied about itself. Both events must appear, in order."""
    monkeypatch.setattr(llm, "have_key", lambda: True)

    def boom():
        raise RuntimeError("API down")
    monkeypatch.setattr(llm, "_client", boom)

    with llm.record() as trail:
        llm.classify_intent("What does an 8-hour shift cost for a graveyard sergeant?")
    assert [c["source"] for c in trail] == ["error", "fallback"]
    assert "API down" in trail[0]["error"]


def _stub_client(payload):
    import json as _json

    class _Block:
        type = "text"
        text = _json.dumps(payload)

    class _Msg:
        content = [_Block()]
        stop_reason = "end_turn"

    class _Messages:
        def create(self, **kw):
            return _Msg()

    class _Client:
        messages = _Messages()

    return _Client()


def test_classifications_resolve_from_descriptions_not_names():
    """The roster is classifications, not people (PRD §6a). A question describes a class
    — "the graveyard police classifications" — and resolution is a deterministic
    attribute filter (AND across fields, OR within one), never a name lookup."""
    subjects = [
        {"name": "Officer Step C (Graveyard, Bilingual FTO)", "department": "police",
         "rank": "Officer", "shift": "Graveyard"},
        {"name": "Sergeant Step C (Graveyard)", "department": "police",
         "rank": "Sergeant", "shift": "Graveyard"},
        {"name": "Officer Step C (Day)", "department": "police",
         "rank": "Officer", "shift": "Day"},
        {"name": "Firefighter (Day, Bilingual)", "department": "fire",
         "rank": "Firefighter", "shift": "Day"},
    ]
    # attribute filter: shift AND department
    got = llm._resolve_classifications(
        "cost of an 8-hour holiday shift for the graveyard police classifications", subjects)
    assert got == ["Officer Step C (Graveyard, Bilingual FTO)", "Sergeant Step C (Graveyard)"]
    # a single class by rank
    assert llm._resolve_classifications("how many days does a police sergeant get", subjects) \
        == ["Sergeant Step C (Graveyard)"]
    # an exact label mention wins outright
    assert llm._resolve_classifications("for Firefighter (Day, Bilingual) please", subjects) \
        == ["Firefighter (Day, Bilingual)"]
    # nothing described -> empty, never a guess
    assert llm._resolve_classifications("what does the contract say about grievances", subjects) == []
