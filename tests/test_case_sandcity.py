"""Sand City POA — the BLIND-PDF case (PRD §8A).

Nothing here was hand-authored: Claude drafted these rules from the raw 26-page MOU,
they were schema-validated against the case's real data vocabulary, and they were
gated on the golden case before ratification.

This test IS the golden gate: the ratified rule set must reproduce the scenario an
analyst worked out by hand from reading the contract.

  Officer C (bil FTO)  48 x1.05 x1.055 x2.5 x8 = 1063.44
  Sergeant C  58 x1.055 x2.5 x8 = 1223.80
  Officer B 45 x1.055 x2.5 x8 = 949.50
  Corporal C (bil FTO) 52 x1.05 x1.055 x2.5 x8 = 1152.06
                                    total = 4388.80
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.caseio import load_case  # noqa: E402
from core.engine import calculate  # noqa: E402

CASE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    "cases", "sandcity")

# This case ships BLIND on purpose — no ratified rules until someone runs the
# authoring loop (ingest -> Claude drafts -> validate -> golden gate -> approve).
# Until then there is nothing to assert, so the golden tests skip rather than fail.
pytestmark = pytest.mark.skipif(
    not load_case(CASE).rules(),
    reason="sandcity is blind: run Admin -> Ingest -> Approve to author rules first")


def _golden_run():
    case = load_case(CASE)
    golden = case.golden_case()
    names = set(golden["subjects"])
    subs = [s for s in case.subjects() if s["name"] in names]
    return calculate(golden["params"], subs, case.rules(), case.rounding_places()), golden


def test_golden_case_reproduces():
    res, golden = _golden_run()
    assert res.total == golden["expected_total"] == 4388.80


def test_per_officer():
    res, _ = _golden_run()
    by = {li.subject: li.total for li in res.line_items}
    assert by["Officer Step C (Graveyard, Bilingual FTO)"] == 1063.44   # bilingual + graveyard, then holiday
    assert by["Sergeant Step C (Graveyard)"] == 1223.80    # graveyard only
    assert by["Officer Step B (Graveyard)"] == 949.50
    assert by["Corporal Step C (Graveyard, Bilingual FTO)"] == 1152.06


def test_rules_are_ratified_only():
    """Only human-ratified rules may execute — unratified rules never load."""
    case = load_case(CASE)
    rules = case.rules()
    assert rules, "expected a ratified rule library"
    assert all(r.status == "ratified" and r.approver for r in rules)


def test_citations_reference_real_clauses():
    res, _ = _golden_run()
    li = next(li for li in res.line_items if li.subject == "Officer Step C (Graveyard, Bilingual FTO)")
    clauses = {c["clause"] for c in li.citations}
    assert clauses & {"3.3", "6.1", "5.2"}  # cites the sections it applied
