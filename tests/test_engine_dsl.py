"""Generic DSL engine tests — independent of the overtime case."""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.engine import calculate  # noqa: E402
from core.ledger import Ledger  # noqa: E402
from core.ruledsl import Rule, eval_expr  # noqa: E402


def test_eval_sandbox_arithmetic_and_membership():
    facts = {"x": 10, "tags": ["a", "b"]}
    assert eval_expr("x * 2 + 1", facts) == 21
    assert eval_expr("'a' in tags", facts) is True
    assert eval_expr("x if x > 5 else 0", facts) == 10


def test_modifier_then_selector_priority():
    rules = [
        Rule.from_dict({"id": "m", "kind": "modifier", "priority": 100,
                        "when": "subject_flag == True",
                        "set": {"effective_base": "subject_base_hourly * 2"}}),
        Rule.from_dict({"id": "hi", "kind": "selector", "priority": 20,
                        "when": "special == True", "compute": "effective_base + 5"}),
        Rule.from_dict({"id": "lo", "kind": "selector", "priority": 10,
                        "when": "True", "compute": "effective_base"}),
    ]
    subjects = [{"name": "A", "base_hourly": 10, "flag": True}]
    # special True -> hi selector (higher priority) wins: (10*2)+5 = 25
    r = calculate({"special": True}, subjects, rules)
    assert r.total == 25.0
    assert r.line_items[0].rule_id == "hi"
    # special False -> falls through to lo: 20
    r2 = calculate({"special": False}, subjects, rules)
    assert r2.total == 20.0
    assert r2.line_items[0].rule_id == "lo"


def test_rounding_half_up():
    rules = [Rule.from_dict({"id": "s", "kind": "selector", "when": "True",
                             "compute": "subject_base_hourly * 3"})]
    # 10.005 * 3 = 30.015 -> 30.02 (half up)
    r = calculate({}, [{"name": "A", "base_hourly": 10.005}], rules)
    assert r.line_items[0].total == 30.02


def test_no_selector_matches_raises():
    rules = [Rule.from_dict({"id": "s", "kind": "selector", "when": "False",
                             "compute": "1"})]
    with pytest.raises(ValueError):
        calculate({}, [{"name": "A"}], rules)


def test_ledger_hash_chain_verifies(tmp_path):
    led = Ledger(str(tmp_path / "l.jsonl"))
    led.append("chat.prompt", {"text": "hi"}, actor="chat", query_id="q1")
    led.append("math.step", {"expr": "1+1", "value": 2}, query_id="q1")
    ok, msg = led.verify()
    assert ok, msg


def test_ledger_detects_tampering(tmp_path):
    path = tmp_path / "l.jsonl"
    led = Ledger(str(path))
    led.append("a", {"v": 1})
    led.append("b", {"v": 2})
    lines = path.read_text().splitlines()
    lines[0] = lines[0].replace('"v":1', '"v":999')  # tamper payload
    path.write_text("\n".join(lines) + "\n")
    ok, msg = Ledger(str(path)).verify()
    assert not ok
    assert "tampered" in msg or "mismatch" in msg


def test_ledger_survives_concurrent_writes(tmp_path):
    """Two people asking a question at the same moment must not break the chain.

    A hash chain is read-then-append; unserialised writers reuse a seq/prev_hash and
    the record fails verification — the audit trail destroying itself under exactly the
    load a shared demo produces.
    """
    import concurrent.futures as cf
    led = Ledger(str(tmp_path / "l.jsonl"))
    with cf.ThreadPoolExecutor(8) as ex:
        list(ex.map(lambda i: led.append("chat.prompt", {"n": i}, query_id=f"q{i}"),
                    range(40)))
    ok, msg = led.verify()
    assert ok, msg
    assert len(list(led.read())) == 40


def _r(**kw):
    """Build a ratified Rule from keyword fields (test shorthand)."""
    kw.setdefault("status", "ratified")
    kw.setdefault("approver", "t")
    kw.setdefault("citation", {"doc_id": "d", "clause": "1"})
    return Rule.from_dict(kw)


def test_premiums_add_they_do_not_compete_to_be_the_base():
    """A $250 stipend must ADD to the shift pay, not win a contest to REPLACE it. Modelling
    every dollar clause as a competing selector is what let a court-time rule and a stipend
    hijack an 8-hour holiday shift."""
    subject = {"name": "A", "base_hourly": 50.0}
    base = _r(id="holiday", kind="selector", role="base", when="hours > 0",
              compute="subject_base_hourly * 2 * hours")           # 50*2*8 = 800
    premium = _r(id="bilingual", kind="selector", role="premium", when="True",
                 compute="100")                                    # +100
    stipend = _r(id="uniform", kind="selector", role="premium", when="True",
                 compute="250")                                    # +250
    res = calculate({"hours": 8}, [subject], [base, premium, stipend])
    assert res.total == 1150.0                                     # 800 + 100 + 250
    # The base rule is the line's rule_id; premiums show in the trace, not as the winner.
    assert res.line_items[0].rule_id == "holiday"


def test_base_selection_follows_document_scope_not_authored_priority():
    """Precedence is lex specialis: the more specific document wins. A citywide 'holiday off
    = 0 pay' default must lose to a police-MOU 'assigned shift = paid' rule, regardless of
    any priority number — the scope rank decides."""
    subject = {"name": "cop", "base_hourly": 50.0}
    citywide = _r(id="holiday_off", kind="selector", role="base", when="True",
                  compute="0", scope_rank=1, priority=99)          # general, high priority
    police = _r(id="assigned_shift", kind="selector", role="base", when="hours > 0",
                compute="subject_base_hourly * hours", scope_rank=2, priority=1)  # specific
    res = calculate({"hours": 8}, [subject], [citywide, police])
    assert res.total == 400.0                                      # police won despite pri 1<99
    assert res.line_items[0].rule_id == "assigned_shift"


def test_role_defaults_preserve_the_old_two_kind_behaviour():
    """A rule set authored before roles existed must compute identically: a bare selector
    is a base, a bare modifier is a differential."""
    subject = {"name": "A", "base_hourly": 50.0}
    modifier = _r(id="grave", kind="modifier", when="True",
                  set={"effective_base": "effective_base * 1.1"})  # no role -> differential
    selector = _r(id="pay", kind="selector", when="True",
                  compute="effective_base * hours")                # no role -> base
    assert modifier.role == "differential" and selector.role == "base"
    res = calculate({"hours": 8}, [subject], [modifier, selector])
    assert res.total == 440.0                                      # 50*1.1*8


def test_annual_benefits_are_excluded_from_a_shift_cost():
    """"What does this 8-hour shift cost?" must not add a year of uniform allowance and a
    month of medical and 1x annual salary of life insurance. They are real money in the
    wrong UNIT for the question; a shift cost includes only hourly and per-shift pay."""
    subject = {"name": "cop", "base_hourly": 50.0}
    holiday = _r(id="holiday", kind="selector", role="base", pay_basis="hourly",
                 when="hours > 0", compute="subject_base_hourly * 2 * hours")   # 800
    uniform = _r(id="uniform", kind="selector", role="premium", pay_basis="annual",
                 when="True", compute="1200")
    life = _r(id="life", kind="selector", role="premium", pay_basis="annual",
              when="True", compute="subject_base_hourly * 2080")                # 104000
    bilingual = _r(id="bilingual", kind="selector", role="premium", pay_basis="hourly",
                   when="True", compute="subject_base_hourly * 0.05 * hours")   # +20

    from core.ruledsl import SHIFT_BASES
    res = calculate({"hours": 8}, [subject], [holiday, uniform, life, bilingual],
                    basis_scope=SHIFT_BASES)
    assert res.total == 820.0                # 800 base + 20 hourly premium; annuals excluded
    # Without a scope, everything is summed — the old, wrong behaviour for a shift query.
    res_all = calculate({"hours": 8}, [subject], [holiday, uniform, life, bilingual])
    assert res_all.total == 106020.0         # 800 + 1200 + 104000 + 20
