"""Acceptance test for the Santa Cruz case — the only corpus this build ships.

Runs every hand-verified golden scenario in cases/santacruz/case.yaml through the SAME
gate the ratify step enforces (`core.app._check_golden`), with no LLM and no API key
involved. This is the determinism guarantee: the ratified rules must reproduce the
analyst-derived known answers, and must never produce a DIFFERENT number (a "fail").

Authored scenarios today:
  - 8-hour overtime shift, Firefighter/Paramedic top step (1.5x, Local 3535 MOU) = $640.80
  - bereavement leave, Firefighters Local 3535 (Article XIV)                      = 3 shifts

Other goldens (other bargaining units) are "pending" until their rules are drafted —
pending is safe (the engine refuses at run time), so it must not fail this test.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.app import _check_golden  # noqa: E402
from core.caseio import load_case  # noqa: E402

CASE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "cases", "santacruz")


def _case_and_rules():
    case = load_case(CASE_DIR)
    with open(os.path.join(CASE_DIR, "rules", "rules_ratified.json")) as f:
        rule_dicts = json.load(f).get("rules", [])
    return case, rule_dicts


def _golden(substr):
    case, rules = _case_and_rules()
    g = next(g for g in case.golden_cases() if substr.lower() in g["name"].lower())
    return _check_golden(case, rules, g)


def test_no_golden_ever_fails():
    """A ratified rule that disagrees with a known answer is the one thing the gate must
    block. Every golden is pass or pending — never fail."""
    case, rules = _case_and_rules()
    goldens = case.golden_cases()
    assert goldens, "santacruz must declare golden scenarios"
    for g in goldens:
        _, detail = _check_golden(case, rules, g)
        assert detail["status"] in ("pass", "pending"), f"{g['name']}: {detail}"


def test_overtime_firefighter_paramedic_is_640_80():
    ok, detail = _golden("overtime")
    assert ok and detail["status"] == "pass", detail
    assert detail["actual"] == 640.80


def test_bereavement_local3535_is_3_shifts():
    ok, detail = _golden("bereavement leave, Firefighters Local 3535")
    assert ok and detail["status"] == "pass", detail
    assert detail["actual"] == 3
