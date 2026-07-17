"""Acceptance test for the reference case (PRD §10.1): the canonical July-4 prompt
must cost out to exactly $1,660.00 — Road Tech I (bil) 630 / Road Tech II 430 / Road Tech I 600 — with no
LLM and no API key involved. This is the determinism guarantee.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.caseio import load_case  # noqa: E402
from core.engine import calculate  # noqa: E402

CASE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "cases", "overtime")

PARAMS = {"hours": 8.0, "date": "July 4", "holiday_weekday": "Sat"}


def _run():
    case = load_case(CASE_DIR)
    return calculate(PARAMS, case.subjects(), case.rules(), case.rounding_places())


def test_total_is_1660():
    result = _run()
    assert result.total == 1660.00


def test_per_employee_amounts():
    result = _run()
    by_name = {li.subject: li.total for li in result.line_items}
    assert by_name["Road Tech I (Day, Bilingual)"] == 630.00   # bilingual bump (9.3) then 2.5x (9.1)
    assert by_name["Road Tech II (Weekend Schedule)"] == 430.00    # 9.2 flat bonus branch
    assert by_name["Road Tech I (Day)"] == 600.00    # plain 9.1


def test_correct_rules_selected():
    result = _run()
    by_name = {li.subject: li.rule_id for li in result.line_items}
    assert by_name["Road Tech I (Day, Bilingual)"] == "art9_1"
    assert by_name["Road Tech II (Weekend Schedule)"] == "art9_2"   # the discriminator
    assert by_name["Road Tech I (Day)"] == "art9_1"


def test_david_ambiguity_flagged():
    result = _run()
    weekend_tech = next(li for li in result.line_items if li.subject == "Road Tech II (Weekend Schedule)")
    assert weekend_tech.needs_human_confirmation
    assert weekend_tech.flags[0]["alternate"] == 150.00  # the alternate reading


def test_determinism():
    assert _run().total == _run().total == 1660.00
