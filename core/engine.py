"""Deterministic DSL interpreter — the heart of the system (PRD 5.5).

Pure function. No LLM, no network, no randomness. Given query params, subject
records, and a ratified rule set, it produces per-subject line items and a total,
plus a full decision trace (which rules fired and why) and math steps for the
ledger. Same inputs -> same number, forever.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from .ruledsl import Rule, eval_expr


class NoRuleApplies(ValueError):
    """The rule set cannot produce a value for this subject at all.

    Distinct from a wrong answer, and the difference is load-bearing: a library with no
    applicable rule REFUSES at run time, which is safe and correct. A library that
    computes the wrong number is the thing the golden gate exists to stop. Callers must
    be able to tell "not modelled yet" from "modelled wrongly" — conflating them made
    incremental ratification impossible (see app._check_golden).

    Subclasses ValueError so existing handlers keep working.
    """


def _round(value: float, places: int = 2) -> float:
    q = Decimal(10) ** -places
    return float(Decimal(str(value)).quantize(q, rounding=ROUND_HALF_UP))


@dataclass
class TraceStep:
    kind: str          # "modifier" | "selector-considered" | "selector-chosen" | "math" | "flag"
    rule_id: str
    detail: str
    citation: dict = field(default_factory=dict)
    value: Any = None


@dataclass
class LineItem:
    subject: str          # a classification ("Sergeant Step C (Graveyard)") — never a person
    rule_id: str          # the chosen selector
    total: float          # per MEMBER of the classification; humans multiply by their headcount
    result_type: str = "currency"   # what the number means: currency|days|hours|...
    topic: str = ""
    citations: list[dict] = field(default_factory=list)
    trace: list[TraceStep] = field(default_factory=list)
    flags: list[dict] = field(default_factory=list)

    @property
    def needs_human_confirmation(self) -> bool:
        return any(f.get("needs_human_confirmation") for f in self.flags)


@dataclass
class Result:
    total: float
    line_items: list[LineItem]

    def to_dict(self) -> dict:
        return {
            "total": self.total,
            "line_items": [
                {
                    "subject": li.subject,
                    "rule_id": li.rule_id,
                    "total": li.total,
                    "result_type": li.result_type,
                    "topic": li.topic,
                    "citations": li.citations,
                    "flags": li.flags,
                    "trace": [t.__dict__ for t in li.trace],
                    "needs_human_confirmation": li.needs_human_confirmation,
                }
                for li in self.line_items
            ],
        }


def _base_facts(subject: dict[str, Any], params: dict[str, Any]) -> dict[str, Any]:
    """Flatten subject + params into a single namespace for expressions.

    Subject fields are prefixed `subject_` (e.g. `subject_base_hourly`); query
    params (hours, date, holiday_weekday) are top-level.
    """
    facts: dict[str, Any] = {}
    for k, v in subject.items():
        facts[f"subject_{k}"] = v
    facts.update(params)
    # Convention: the base pay rate every rule can build on. Modifiers may override.
    if "subject_base_hourly" in facts:
        facts["effective_base"] = facts["subject_base_hourly"]
    return facts


def _precedence(r: Rule):
    """Sort key for rule precedence: DERIVED from document authority, not authored.

    scope_rank first (statute < citywide < unit MOU < amendment — the more specific
    document wins, i.e. lex specialis), then priority as a within-scope tiebreak. This is
    what stops an LLM-invented `priority` from putting a court-time rule ahead of a holiday
    premium: precedence is a property of where the rule came from, not a number it guessed.
    """
    return (r.scope_rank, r.priority)


def calculate(params: dict[str, Any], subjects: list[dict[str, Any]],
              rules: list[Rule], rounding_places: int = 2,
              basis_scope: frozenset[str] | None = None) -> Result:
    # Compensation is a STACK, not a contest (PRD §7.4):
    #   differentials adjust the base rate, ONE base formula is chosen, premiums ADD.
    # Role defaults (selector->base, modifier->differential) make this identical to the
    # old two-kind engine for any rule set that predates roles.
    #
    # `basis_scope` restricts WHICH terms are in the right unit for the question. A
    # single-shift cost passes {hourly, per_shift}, so an annual uniform allowance and a
    # monthly medical contribution — real money, wrong unit — do not get added to one
    # shift. None means no filter (compute every term). Differentials are exempt: they
    # adjust the rate, they are not a standalone pay item with a period of their own.
    def _in_scope(r: Rule) -> bool:
        return basis_scope is None or r.pay_basis in basis_scope

    # Scope precedence (lex specialis) governs ONLY base selection — that is where "police
    # shift beats citywide holiday" is decided. Differentials and premiums are ordered by
    # `priority` alone, exactly as the old engine ordered modifiers: they ACCUMULATE into
    # facts and their order is authored, so reordering an initializer behind the setter it
    # zeroes (a real regression this caught) must not happen.
    differentials = sorted([r for r in rules if r.role == "differential"],
                           key=lambda r: r.priority, reverse=True)
    bases = sorted([r for r in rules if r.role == "base" and _in_scope(r)],
                   key=_precedence, reverse=True)
    premiums = sorted([r for r in rules if r.role == "premium" and _in_scope(r)],
                      key=lambda r: r.priority, reverse=True)

    line_items: list[LineItem] = []
    grand_total = 0.0

    for subject in subjects:
        facts = _base_facts(subject, params)
        trace: list[TraceStep] = []
        citations: list[dict] = []
        # A subject is a bag of attributes keyed by a CLASSIFICATION label — the unit
        # labor decisions are made in. The engine computes the per-member amount for the
        # class; multiplying by a headcount is the human's arithmetic, not ours.
        subj_name = str(subject.get("name") or subject.get("id") or "subject")

        # 1. Apply differentials (adjust the base rate; most specific document first).
        for m in differentials:
            if not bool(eval_expr(m.when, facts)):
                continue
            for fact_name, expr in m.set.items():
                new_val = eval_expr(expr, facts)
                facts[fact_name] = new_val
                trace.append(TraceStep(
                    kind="modifier", rule_id=m.id,
                    detail=f"{fact_name} = {expr} -> {new_val}",
                    citation=m.citation.to_dict(), value=new_val))
            if m.citation.clause:
                citations.append(m.citation.to_dict())

        # 2. Choose exactly ONE base formula — the most specific matching one. Bases are
        #    mutually exclusive (regular XOR overtime XOR holiday-worked); premiums are not
        #    bases and never reach this contest, which is what stopped a $250 stipend and a
        #    court-time rule from winning the holiday shift.
        chosen = None
        for s in bases:
            matched = bool(eval_expr(s.when, facts))
            trace.append(TraceStep(
                kind="selector-considered", rule_id=s.id,
                detail=f"when: {s.when} -> {matched} (scope {s.scope_rank}, priority {s.priority})",
                citation=s.citation.to_dict(), value=matched))
            if matched and chosen is None:
                chosen = s
        if chosen is None:
            raise NoRuleApplies(f"no base pay rule matched for subject {subj_name!r}")

        base_val = _round(float(eval_expr(chosen.compute, facts)), rounding_places)
        trace.append(TraceStep(
            kind="selector-chosen", rule_id=chosen.id,
            detail=f"chose {chosen.id} (scope {chosen.scope_rank}, priority {chosen.priority}); "
                   f"{chosen.human_readable}",
            citation=chosen.citation.to_dict(), value=base_val))
        trace.append(TraceStep(
            kind="math", rule_id=chosen.id,
            detail=f"{chosen.compute} = {base_val}", citation=chosen.citation.to_dict(),
            value=base_val))
        if chosen.citation.clause:
            citations.append(chosen.citation.to_dict())

        # 3. ADD every matching premium. Independent, additive terms — a bilingual stipend
        #    and a uniform allowance both apply; neither replaces the base.
        line_total = base_val
        for p in premiums:
            if not bool(eval_expr(p.when, facts)):
                continue
            add = _round(float(eval_expr(p.compute, facts)), rounding_places)
            line_total = _round(line_total + add, rounding_places)
            trace.append(TraceStep(
                kind="premium", rule_id=p.id,
                detail=f"+ {p.compute} = {add} ({p.human_readable or p.topic})",
                citation=p.citation.to_dict(), value=add))
            if p.citation.clause:
                citations.append(p.citation.to_dict())

        # 4. Ambiguity flags on the chosen rule.
        flags: list[dict] = []
        for fl in chosen.flags:
            if not bool(eval_expr(fl.when, facts)):
                continue
            alt = _round(float(eval_expr(fl.alternate, facts)), rounding_places) \
                if fl.alternate else None
            flag = {
                "rule_id": chosen.id,
                "message": fl.message,
                "primary": line_total,
                "alternate": alt,
                "needs_human_confirmation": True,
            }
            flags.append(flag)
            trace.append(TraceStep(
                kind="flag", rule_id=chosen.id,
                detail=f"{fl.message} (primary {line_total}, alternate {alt})",
                citation=chosen.citation.to_dict(), value=alt))

        line_items.append(LineItem(
            subject=subj_name, rule_id=chosen.id, total=line_total,
            result_type=chosen.result_type, topic=chosen.topic,
            citations=citations, trace=trace, flags=flags))
        grand_total += line_total

    return Result(total=_round(grand_total, rounding_places), line_items=line_items)
