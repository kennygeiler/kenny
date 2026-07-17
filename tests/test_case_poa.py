"""POA costing case (Article 12: shift differential + FTO premium), locked fixture.

Bi-weekly pay period = 80 hours. Proves additive premiums and §12.3 stacking:
the differential % applies only to base; the FTO flat is a separate additive term
never multiplied by a percentage.

  Officer Step B (Graveyard, FTO) (Grave, FTO)  45*.08*80 + 250 = 538.00
  Officer Step B (Day)   (Day)                          =   0.00
  Sergeant (Grave)        55*.08*80        = 352.00
  Officer Step A (Graveyard, FTO)  (Grave, FTO)  40*.08*80 + 250  = 506.00
                                    total  = 1396.00
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.caseio import load_case  # noqa: E402
from core.engine import calculate  # noqa: E402

CASE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    "cases", "poa")


def _run():
    case = load_case(CASE)
    return calculate({}, case.subjects(), case.rules(), case.rounding_places())


def test_total():
    assert _run().total == 1396.00


def test_per_officer():
    by = {li.subject: li.total for li in _run().line_items}
    assert by["Officer Step B (Graveyard, FTO)"] == 538.00
    assert by["Officer Step B (Day)"] == 0.00
    assert by["Sergeant (Graveyard)"] == 352.00
    assert by["Officer Step A (Graveyard, FTO)"] == 506.00


def test_stacking_limitation_12_3():
    """§12.3: the FTO flat premium must NOT be multiplied by the differential %."""
    officer_b = next(li for li in _run().line_items if li.subject == "Officer Step B (Graveyard, FTO)")
    diff = next(t.value for t in officer_b.trace if t.rule_id == "art12_1_graveyard")
    fto = next(t.value for t in officer_b.trace if t.rule_id == "art12_2_fto")
    assert diff == 45 * 0.08 * 80   # 288.0 — % applied to base only
    assert fto == 250                # flat, untouched by the %
    assert officer_b.total == diff + fto
