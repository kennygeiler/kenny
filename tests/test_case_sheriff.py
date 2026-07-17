"""Independent second case (Sheriff holdover) exercising the same engine with
different constants and condition types. Hand-computed target: $1,940.56.

  Detention Officer (Day, Hazmat)  (hazmat, Day)       40*1.08 * 2.0 * 6            = 518.40   (12.1)
  Detention Officer Step C (Graveyard) (Graveyard)         45 * 6 + 200                 = 470.00   (12.2)
  Detention Officer (Day)(Day)               40 * 2.0 * 6                 = 480.00   (12.1)
  Patrol Deputy (Graveyard, Hazmat) (hazmat, Graveyard) 42*1.08 * 6 + 200            = 472.16   (12.2)
                                                       total = 1940.56
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.caseio import load_case  # noqa: E402
from core.engine import calculate  # noqa: E402

CASE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "cases", "sheriff")
PARAMS = {"hours": 6.0, "day_type": "High-Security Day"}


def _run():
    case = load_case(CASE_DIR)
    return calculate(PARAMS, case.subjects(), case.rules(), case.rounding_places())


def test_total():
    assert _run().total == 1940.56


def test_per_officer():
    by = {li.subject: li.total for li in _run().line_items}
    assert by["Detention Officer (Day, Hazmat)"] == 518.40    # hazmat bump then 2.0x
    assert by["Detention Officer Step C (Graveyard)"] == 470.00   # graveyard flat-bonus branch
    assert by["Detention Officer (Day)"] == 480.00  # plain 12.1
    assert by["Patrol Deputy (Graveyard, Hazmat)"] == 472.16   # hazmat bump inside graveyard branch (cents)


def test_rule_selection_and_flags():
    items = {li.subject: li for li in _run().line_items}
    assert items["Detention Officer (Day)"].rule_id == "art12_1"
    assert items["Detention Officer Step C (Graveyard)"].rule_id == "art12_2"
    assert items["Detention Officer Step C (Graveyard)"].needs_human_confirmation
    assert items["Patrol Deputy (Graveyard, Hazmat)"].flags[0]["alternate"] == 200.00
