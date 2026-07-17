"""Rule DSL: schema, loading, and sandboxed expression evaluation.

Rules are pure data (JSON/YAML). Expressions inside rules are strings evaluated
in a restricted sandbox (simpleeval) over a flat namespace of "facts" — never
executable Python. This is what lets a new policy domain ship as data with no
new engine code (PRD 5.3).

Two rule kinds:
  - modifier: sets derived facts (e.g. bilingual +5% into `effective_base`)
  - selector: picks the formula, chosen by `priority` (highest matching wins)

A rule computes a TYPED VALUE, not necessarily money. An MOU is a rulebook and pay is
only one chapter of it: "5 days of bereavement (§11.3)", "15 calendar days to file a
grievance (§12.3)", "3.08 hours of vacation per pay period (Appendix C)" are equally
deterministic, citable rules. `result_type` says what the number MEANS, and the engine
is unit-agnostic — it evaluates the expression either way.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any

try:
    # EvalWithCompoundTypes (not SimpleEval) so rules may use tuple/list literals,
    # e.g. `holiday_weekday in ('Sat', 'Sun')` — natural to write and safe to allow.
    from simpleeval import EvalWithCompoundTypes as SimpleEval
    import simpleeval as _se
except Exception:  # pragma: no cover - dependency guard
    SimpleEval = None
    _se = None


class RuleError(Exception):
    pass


@dataclass
class Citation:
    doc_id: str = ""
    clause: str = ""
    page: int = 0
    bbox: list[float] = field(default_factory=list)  # [x0, y0, x1, y1] in PDF points
    char_span: list[int] = field(default_factory=list)

    @classmethod
    def from_dict(cls, d: dict | None) -> "Citation":
        d = d or {}
        return cls(
            doc_id=d.get("doc_id", ""),
            clause=d.get("clause", ""),
            page=int(d.get("page", 0)),
            bbox=list(d.get("bbox", []) or []),
            char_span=list(d.get("char_span", []) or []),
        )

    def to_dict(self) -> dict:
        return {
            "doc_id": self.doc_id,
            "clause": self.clause,
            "page": self.page,
            "bbox": self.bbox,
            "char_span": self.char_span,
        }


@dataclass
class Flag:
    when: str
    message: str
    alternate: str | None = None  # expression giving the alternate interpretation


RESULT_TYPES = ("currency", "days", "hours", "date", "boolean", "text")
ROLES = ("base", "differential", "premium", "exception")
# How often an amount is paid. A question about ONE 8-hour shift wants hourly and
# per-shift terms; it must NOT sweep in a $1,200 annual uniform allowance or a $1,800
# monthly medical contribution or 1× annual salary of life insurance — those are real
# money, but they are the wrong UNIT for the question. Filtering by pay_basis is what
# stops "what does this shift cost?" from adding a year of benefits (PRD §7.4).
PAY_BASES = ("hourly", "per_shift", "per_pay_period", "monthly", "annual", "one_time")
SHIFT_BASES = frozenset({"hourly", "per_shift"})   # what a single-shift cost includes


@dataclass
class Rule:
    id: str
    kind: str  # "modifier" | "selector"
    # What the computed value MEANS. Drives which rules answer which question and how
    # the answer is rendered ($1,660.00 vs "5 days"). Pay is not the only rule type.
    result_type: str = "currency"
    # ROLE in the pay stack (PRD §7a). Compensation is COMPOSITIONAL, not winner-take-all:
    #   base         — the pay formula. Mutually exclusive: exactly ONE wins per subject.
    #   differential — a multiplicative adjustment to the base (graveyard +5.5%). Stacks.
    #   premium      — an ADDITIVE, independent term (bilingual +$X). Never competes.
    #   exception    — suppresses/caps another term (holiday premium void on a regular day off).
    # This is why a $250 stipend must not be a `base` selector fighting the holiday formula:
    # it is a premium, and premiums add. Defaults preserve the old two-kind behaviour
    # (selector->base, modifier->differential) so hand-authored rules are unchanged.
    role: str = ""
    topic: str = ""          # bereavement | overtime | vacation | grievance | ...
    human_readable: str = ""
    when: str = "True"
    # PRECEDENCE is DERIVED, not authored. `scope_rank` comes from the document's authority
    # (statute < citywide policy < unit MOU < amendment) and is set at load time from the
    # manifest — see caseio. It is the PRIMARY sort key for base selection; `priority` is
    # only a within-scope tiebreak. An LLM must never invent precedence: "police shift beats
    # citywide holiday" is lex specialis (the more specific document wins), not a magic number.
    scope_rank: int = 0
    # How often this amount is paid. Defaults to `hourly`, which keeps every pre-existing
    # rule inside a shift-cost query (they were all hourly). A premium marked `annual` /
    # `monthly` / `per_pay_period` is excluded from a single-shift cost — it is the wrong
    # unit for the question, not missing data.
    pay_basis: str = "hourly"
    priority: int = 0
    set: dict[str, str] = field(default_factory=dict)   # modifier: fact -> expression
    compute: str | None = None                          # selector: expression -> line total
    flags: list[Flag] = field(default_factory=list)
    citation: Citation = field(default_factory=Citation)
    # Rules are UNTRUSTED by default. Only an explicit `status: ratified` (written by
    # the human approval gate) lets a rule execute — see load_rules().
    status: str = "proposed"
    approver: str = ""

    @classmethod
    def from_dict(cls, d: dict) -> "Rule":
        kind = d.get("kind")
        if kind not in ("modifier", "selector"):
            raise RuleError(f"rule {d.get('id')!r}: kind must be 'modifier' or 'selector'")
        # `or []` not `, []`: the LLM emits "flags": null, and a present-but-null key slips
        # past a default and then fails when iterated ('NoneType' is not iterable).
        flags = [Flag(**f) if not isinstance(f, Flag) else f for f in (d.get("flags") or [])]
        rt = d.get("result_type", "currency")
        if rt not in RESULT_TYPES:
            raise RuleError(f"rule {d.get('id')!r}: result_type must be one of {RESULT_TYPES}")
        # Role defaults preserve the historical two-kind behaviour exactly: a selector is
        # a base formula, a modifier is a differential. Explicit role="premium" opts a
        # rule into additive composition.
        role = d.get("role") or ("base" if kind == "selector" else "differential")
        if role not in ROLES:
            raise RuleError(f"rule {d.get('id')!r}: role must be one of {ROLES}")
        return cls(
            id=d["id"],
            kind=kind,
            result_type=rt,
            role=role,
            topic=d.get("topic", ""),
            human_readable=d.get("human_readable", ""),
            when=d.get("when", "True"),
            scope_rank=int(d.get("scope_rank", 0)),
            pay_basis=(d.get("pay_basis") or "hourly"),
            priority=int(d.get("priority", 0)),
            set=dict(d.get("set") or {}),
            compute=d.get("compute"),
            flags=flags,
            citation=Citation.from_dict(d.get("citation")),
            status=d.get("status", "proposed"),
            approver=d.get("approver", ""),
        )


def _make_evaluator(facts: dict[str, Any]):
    if SimpleEval is None:
        raise RuleError("simpleeval is not installed")
    ev = SimpleEval(names=dict(facts))
    # A minimal, safe helper set. No attribute access, no imports.
    ev.functions = {
        "min": min,
        "max": max,
        "round": round,
        "abs": abs,
        "len": len,
    }
    return ev


def eval_expr(expr: str, facts: dict[str, Any]) -> Any:
    """Evaluate a single DSL expression against `facts` in the sandbox."""
    ev = _make_evaluator(facts)
    try:
        return ev.eval(expr)
    except Exception as e:  # surface the offending expression, not a raw traceback
        raise RuleError(f"failed to evaluate {expr!r}: {e}") from e


SAFE_FUNCS = {"min", "max", "round", "abs", "len"}


def expr_names(expr: str) -> set[str]:
    """Identifiers referenced by a DSL expression (True/False/numbers are constants)."""
    import ast
    tree = ast.parse(expr, mode="eval")
    return {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}


def unsupported_syntax(expr: str) -> list[str]:
    """Node types the sandbox evaluator cannot execute.

    Static name-checking is not enough: an expression can be valid Python, reference
    only real facts, and still blow up at run time because the sandbox refuses the
    syntax (e.g. a tuple literal). Validation must mirror the evaluator, so we ask the
    evaluator itself which node types it handles.
    """
    import ast
    if SimpleEval is None:
        return []
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError:
        return []  # reported separately as a parse error
    supported = set(SimpleEval().nodes.keys())
    # structural/context nodes never dispatched by the evaluator
    ignore = (ast.Expression, ast.expr_context, ast.operator, ast.boolop,
              ast.unaryop, ast.cmpop, ast.arguments, ast.arg, ast.comprehension)
    bad = set()
    for node in ast.walk(tree):
        if isinstance(node, ignore):
            continue
        if type(node) not in supported:
            bad.add(type(node).__name__)
    return sorted(bad)


def validate_rules(rules: list[dict], known_facts: set[str]) -> dict[str, list[str]]:
    """Static check a drafted rule set BEFORE it can be ratified (PRD §8A).

    Catches the LLM's classic failure: inventing fact names that don't exist in the
    case's data schema (e.g. `subject_hazmat_certified` when the roster field is
    `hazmat`), or emitting a rule shape the engine can't execute. Returns
    {rule_id: [errors]} — empty means the set is executable.
    """
    targets: set[str] = set()
    for r in rules:
        targets.update((r.get("set") or {}).keys())
    allowed = set(known_facts) | targets | {"effective_base"} | SAFE_FUNCS

    errors: dict[str, list[str]] = {}
    for r in rules:
        rid = str(r.get("id") or "<no id>")
        errs: list[str] = []
        kind = r.get("kind")
        if kind not in ("modifier", "selector"):
            errs.append(f"kind must be 'modifier' or 'selector' (got {kind!r})")
        if kind == "selector" and not r.get("compute"):
            errs.append("selector rule must define `compute`")
        if kind == "modifier" and not r.get("set"):
            errs.append("modifier rule must define `set`")
        if kind == "modifier" and r.get("compute"):
            errs.append("modifier must use `set`, not `compute`")
        rt = r.get("result_type", "currency")
        if rt not in RESULT_TYPES:
            errs.append(f"result_type {rt!r} must be one of {list(RESULT_TYPES)}")

        exprs: list[str] = [str(r.get("when", "True"))]
        if r.get("compute"):
            exprs.append(str(r["compute"]))
        exprs.extend(str(v) for v in (r.get("set") or {}).values())
        for fl in (r.get("flags") or []):
            exprs.append(str(fl.get("when", "True")))
            if fl.get("alternate"):
                exprs.append(str(fl["alternate"]))

        for e in exprs:
            try:
                names = expr_names(e)
            except SyntaxError:
                errs.append(f"expression does not parse: {e!r}")
                continue
            unknown = names - allowed
            if unknown:
                errs.append(f"unknown fact(s) {sorted(unknown)} in {e!r} — "
                            f"not in the case's data schema")
            bad_syntax = unsupported_syntax(e)
            if bad_syntax:
                errs.append(f"syntax the evaluator cannot run ({', '.join(bad_syntax)}) "
                            f"in {e!r}")
        if not r.get("citation", {}).get("clause"):
            errs.append("missing citation clause (every rule must cite its source)")
        if errs:
            errors[rid] = errs
    return errors


def load_rules(path: str, ratified_only: bool = True) -> list[Rule]:
    """Load a rule library. By default ONLY rules a human ratified are returned — an
    unratified rule can never reach the engine, no matter how it got into the file."""
    if not os.path.exists(path):
        return []
    with open(path) as f:
        raw = json.load(f)
    rules = raw.get("rules", raw) if isinstance(raw, dict) else raw
    loaded = [Rule.from_dict(r) for r in rules]
    if not ratified_only:
        return loaded
    return [r for r in loaded if r.status == "ratified" and r.approver]
