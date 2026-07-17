"""LLM touchpoints — the translation layer only (PRD Principle 1).

Every AI call in the product lives in this file. Seven functions, each with a
deterministic fallback so the whole product runs with NO api key:

  QUERY TIME (per question)
    - classify_intent     : prompt -> costing | lookup | entitlement | policy
    - extract_department  : prompt -> which unit's contract
    - parse_intent        : prompt -> {subjects, hours, date}, normalized to the roster
    - answer_policy       : retrieved clauses -> a grounded answer (the only prose a user reads)
    - rank_documents      : query + catalog -> ranked candidates (fallback routing only)
  INGEST TIME (once per document)
    - tag_document        : document -> department / tags / summary
  PER SCENARIO (a known paystub, via /admin/draft_scenario)
    - draft_rules         : the paystub's few clauses -> PROPOSED rules + needs_data

None of these ever computes money — that is the deterministic engine's job, and no
module on the money path (engine, ruledsl, governance, ledger) imports this one.

OBSERVABILITY. Every call records itself to a per-request trail (see `record()`), which
the app drains into the ledger as `llm.call` events. This exists because the trail used
to log only what the model CONCLUDED — `{"intent": "costing"}` — and not whether a model
was involved at all. With an expired key the deterministic fallback answers and the
ledger looks identical, so the product could quietly stop using AI and nothing would say
so. For a system whose claim is "you can see how the AI reached its answer", *whether it
ran* is the first thing the record has to show.
"""
from __future__ import annotations

import contextlib
import contextvars
import json
import os
import re
import time
from typing import Any

MODEL = "claude-opus-4-8"

# Per-request trail of model calls. A ContextVar, not a module global: FastAPI runs sync
# endpoints in a threadpool, so two concurrent questions would otherwise write into each
# other's trail and each user would read the other's reasoning in their audit drawer.
_TRAIL: contextvars.ContextVar[list | None] = contextvars.ContextVar("llm_trail",
                                                                    default=None)


@contextlib.contextmanager
def record():
    """Collect every AI call made inside this block. Yields the list."""
    token = _TRAIL.set([])
    try:
        yield _TRAIL.get()
    finally:
        _TRAIL.reset(token)


def _note(fn: str, source: str, **detail) -> None:
    """Record one AI touchpoint. `source` is the honest answer to 'did AI do this?':
      claude   — the model answered
      fallback — deterministic code answered (no key, or the model failed)
    """
    trail = _TRAIL.get()
    if trail is None:
        return
    entry = {"fn": fn, "source": source}
    if source == "claude":
        entry["model"] = MODEL
    trail.append({**entry, **detail})

_WEEKDAYS = {
    "monday": "Mon", "tuesday": "Tue", "wednesday": "Wed", "thursday": "Thu",
    "friday": "Fri", "saturday": "Sat", "sunday": "Sun",
}


def have_key() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


# --------------------------------------------------------------------------- #
# classify_intent — is this a costing question or a policy question?
# --------------------------------------------------------------------------- #
_COST_CUES = ("cost", "calculate", "how much", "total ", "pay for", "what will it",
              "price", "budget", "dollar")
# Strong cues force a 'policy' classification regardless of cost words in the prompt.
_POLICY_STRONG = ("eligible", "what does", "say about", "allowed", "explain", "define",
                  "entitled", "does the", "is a ", "are ", "can a ", "who qualifies",
                  "what happens", "rules for", "policy on", "how many days")
_POLICY_CUES = _POLICY_STRONG + ("what is", "when ", "who ", "require", "mean")


# "how many bereavement DAYS", "how much vacation do I ACCRUE", "DEADLINE to file"
_ENTITLEMENT_RE = re.compile(
    r"how (many|much)\b[^?]*\b(day|days|hour|hours|shift|shifts|week|weeks|leave|"
    r"vacation|sick|time)\b|deadline|accrue|accrual|entitled to|how long")

# A figure the documents PUBLISH: "Sergeant's Step C rate", "the salary schedule",
# "base hourly rate for a Fire Captain", "the uniform allowance amount".
_LOOKUP_RE = re.compile(
    r"\bstep\s*[a-e]\b|salary schedule|pay schedule|pay scale|\brate\b|\brates\b|"
    r"hourly rate|base pay|salary (for|of)|allowance amount|stipend")
# ...unless the question asks for arithmetic over it. "Cost", hours and dates mean a
# figure must be DERIVED, so the costing engine owns it even though it mentions a rate.
_COMPUTE_RE = re.compile(
    r"\bcost\b|\bcosts\b|\btotal\b|\bcalculate\b|\bcompute\b|how much (would|will|does) "
    r"it cost|\b\d+\s*-?\s*hour|\bovertime pay for\b|\bimpact\b")


def classify_intent(prompt: str) -> str:
    """Return 'costing' | 'lookup' | 'entitlement' | 'policy'.

    Holly answers questions about MOUs; costing is one KIND of question:
      costing     -> a dollar amount to COMPUTE     ("what does this shift cost?")
      lookup      -> a dollar amount already PRINTED ("what is a Sergeant's Step C rate?")
      entitlement -> a determinate non-money value  ("how many bereavement days?")
      policy      -> what the contract says         ("does a weekend worker still get it?")

    `lookup` exists because "$" is not the same question twice. A salary schedule
    PUBLISHES rates; nothing is computed, no rules are needed, and no rule will ever be
    drafted from a rate table. Routing those to costing made Holly refuse a number it was
    holding — "I can't cost this: no human-ratified rules" — for a question that needed no
    rule at all. The distinguishing test is not the dollar sign, it is whether an
    arithmetic step exists: a cost is DERIVED from a person, hours and a date; a rate is
    READ from a cell.
    """
    if have_key():
        try:
            out = _claude_json(  # noqa: labelled below
                "Classify the question about a labor contract:\n"
                "'costing'     = asks what something WOULD COST — a dollar amount that "
                "must be CALCULATED from a person, hours, a shift or a date.\n"
                "'lookup'      = asks for a figure ALREADY PUBLISHED in the documents and "
                "read as-is: a pay rate for a classification/step, a salary schedule "
                "entry, a stipend or allowance amount. Nothing is calculated.\n"
                "'entitlement' = asks for a determinate NON-MONEY value a person gets "
                "or must meet: number of days/hours/shifts of leave, an accrual rate, a "
                "deadline, or a yes/no eligibility.\n"
                "'policy'      = asks what the contract says, or for an explanation.\n"
                "The test for costing vs lookup: is there arithmetic to do? "
                "'What does an 8-hour holiday shift cost for a graveyard sergeant?' = costing. "
                "'What is a Sergeant's Step C rate?' = lookup.\n"
                "Return ONLY {\"intent\": \"costing\"|\"lookup\"|\"entitlement\"|\"policy\"}.",
                prompt, label="classify_intent")
            it = out.get("intent")
            if it in ("costing", "lookup", "entitlement", "policy"):
                return it
        except Exception:
            pass
    # Reached only with no key, or after the model failed. Recorded either way: a
    # keyword router silently standing in for AI is exactly what the trail must show.
    _note("classify_intent", "fallback", rule="keyword router")
    p = prompt.lower()
    # Order matters, and it is not the order the cue lists suggest:
    #   1. entitlement — "how many days" is never a rate and never a cost.
    #   2. lookup      — mentions rates and dollars, so it must be tested before the cost
    #                    cues claim it.
    #   3. an explicit ASK TO COMPUTE outranks the policy phrasing wrapped around it.
    #      "What does an 8-hour holiday shift cost?" opens with "what does", a _POLICY_
    #      STRONG cue, and was routed to policy — so with no API key the clearest costing
    #      question in the corpus never reached the engine. The verb governs, not the
    #      preamble.
    if _ENTITLEMENT_RE.search(p):
        return "entitlement"
    if _LOOKUP_RE.search(p) and not _COMPUTE_RE.search(p):
        return "lookup"
    if _COMPUTE_RE.search(p):
        return "costing"
    if any(c in p for c in _POLICY_STRONG):
        return "policy"
    if any(c in p for c in _COST_CUES):
        return "costing"
    # Default to policy: quoting the contract is the safe failure. Guessing "costing"
    # on an unrecognized question risks computing an answer that was never asked for.
    return "policy"


# --------------------------------------------------------------------------- #
# extract_department — which unit's contract is the question about?
# --------------------------------------------------------------------------- #
_DEPT_CUES = {
    "police": ("police", "officer", "poa", "patrol", "sworn", "sergeant", "corporal"),
    "sheriff": ("sheriff", "correctional", "corrections", "detention", "jail", "deputy"),
    "fire": ("fire", "firefighter", "iaff", "suppression", "paramedic", "engineer",
             "captain", "medic"),
    "public-works": ("public works", "seiu", "road tech", "maintenance", "road crew"),
}


def extract_department(prompt: str, departments: list[str]) -> str | None:
    """Return the department the question is about, or None if not stated. Used to
    shortlist candidate documents in a multi-department corpus."""
    if have_key():
        try:
            out = _claude_json(
                "Which department is this question about? Choose exactly one of "
                + json.dumps(departments) + " or null if the question does not say. "
                "Do not guess from topic alone — only answer if the question names or "
                "clearly implies the department (e.g. 'officer' -> police, "
                "'firefighter' -> fire). Return ONLY {\"department\": \"...\"|null}.",
                prompt, label="extract_department")
            d = out.get("department")
            if d in departments:
                return d
            if d in (None, "null", ""):
                return None
        except Exception:
            pass
    p = prompt.lower()
    for dept, cues in _DEPT_CUES.items():
        if dept in departments and any(c in p for c in cues):
            return dept
    return None


# --------------------------------------------------------------------------- #
# answer_policy — grounded answer composed ONLY from retrieved clauses
# --------------------------------------------------------------------------- #
def answer_policy(query: str, passages: list[dict], lookup: bool = False) -> dict:
    """Compose a short answer strictly from the retrieved clause text. Never adds
    facts. Falls back to quoting the top passage verbatim when no key.

    `lookup=True` answers from a rate table. The instruction is tightened because the
    failure mode differs: prose invites paraphrase, a table invites ARITHMETIC — asked
    for a Step C rate the model will happily average two steps or annualise an hourly
    figure. Read the cell; do not compute. Anything derived is a costing question and
    belongs to the engine (PRD §8).
    """
    if not passages:
        return {"answer": "I couldn't find a relevant clause in the governing document(s).",
                "source": "none"}
    if have_key():
        try:
            ctx = "\n\n".join(f"[{p.get('doc_id')} §{p.get('clause')}] {p.get('text')}"
                              for p in passages)
            system = (
                "Answer the question using ONLY the provided contract clauses. Do not "
                "add facts not present. Cite the clause(s) inline like (§12.1). If the "
                "clauses don't answer it, say so. Return ONLY {\"answer\": \"...\"}.")
            if lookup:
                system = (
                    "Read a PUBLISHED figure out of the provided documents. Table rows "
                    "are given as 'Classification | Step A | Step B | ...' with the "
                    "header row alongside — match the column by position.\n"
                    "RULES:\n"
                    "- Quote the figure EXACTLY as printed. Never calculate, convert, "
                    "annualise, average or adjust it. If the question needs arithmetic, "
                    "say it is a costing question and do not attempt the number.\n"
                    "- If the exact row or column is not present, say which is missing. "
                    "Never interpolate a step or infer a classification's rate from "
                    "another's.\n"
                    "- Name the document and section the figure came from.\n"
                    "Return ONLY {\"answer\": \"...\"}.")
            out = _claude_json(system, f"Question: {query}\n\nClauses:\n{ctx}",
                               label="answer_policy" + ("/lookup" if lookup else ""))
            ans = out.get("answer")
            if ans:
                return {"answer": ans, "source": "claude"}
        except Exception:
            pass
    _note("answer_policy", "fallback", rule="verbatim quote of the top passage")
    top = passages[0]
    if lookup:
        # No key: quote the row verbatim. A stub must never try to read a cell out of a
        # pipe-delimited row by position — that is arithmetic-by-guesswork.
        return {"answer": f"From {top.get('doc_id')}: {top.get('text')}", "source": "stub"}
    return {"answer": f"Per §{top.get('clause')}: {top.get('text')}", "source": "stub"}


def _client():
    from anthropic import Anthropic
    return Anthropic()


class ResponseTruncated(ValueError):
    """The model hit its output cap, so the JSON is cut off mid-object.

    Worth its own type because the caller's remedy is specific — ask for less, not fall
    back — and because the symptom is otherwise a baffling `JSONDecodeError: Expecting ','
    delimiter`, which reads like the model emitted bad JSON rather than good JSON that
    was truncated. That misdiagnosis is what let a 26-page MOU silently draft 0 rules.
    """


def _claude_json(system: str, user: str, max_tokens: int = 1500,
                 label: str = "llm") -> dict:
    """Ask Claude for a single JSON object. Raises on any failure so callers can
    fall back deterministically.

    The single chokepoint for every model call in the product, which is why the timing
    and outcome are recorded here rather than in seven call sites.
    """
    started = time.time()
    try:
        client = _client()
        msg = client.messages.create(
            model=MODEL,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        text = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")
        stop = getattr(msg, "stop_reason", "")
        if stop == "max_tokens":
            raise ResponseTruncated(
                f"model hit the {max_tokens}-token output cap; the JSON is incomplete")
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end == -1:
            raise ValueError("no JSON object in model response")
        out = json.loads(text[start:end + 1])
    except Exception as e:
        # Recorded, then re-raised. A model call that failed and was quietly absorbed by
        # a fallback is the single most common way this system has lied about itself.
        _note(label, "error", ms=int((time.time() - started) * 1000),
              error=f"{type(e).__name__}: {e}")
        raise
    _note(label, "claude", ms=int((time.time() - started) * 1000),
          prompt_chars=len(system) + len(user), stop_reason=stop)
    return out


# --------------------------------------------------------------------------- #
# parse_intent (run time)
# --------------------------------------------------------------------------- #
def parse_intent(prompt: str, extraction_cfg: dict, subjects: list[dict]) -> dict:
    names = [str(s.get("name", "")) for s in subjects]
    if have_key():
        try:
            system = ("You extract query parameters for a costing engine. "
                      "Return ONLY a JSON object matching this shape: "
                      + json.dumps(extraction_cfg.get("output_shape", {})) +
                      ". Never compute costs.\n"
                      "The roster is CLASSIFICATIONS, not people: " + json.dumps(names) +
                      "\n'subjects' must be labels copied EXACTLY from that list — resolve a "
                      "description like 'the graveyard police classifications' to every label "
                      "it covers. [] if the question names none.")
            out = _claude_json(system, prompt, label="parse_intent")
            out["source"] = "claude"
            return _normalize_intent(out, subjects, prompt)
        except Exception:
            pass
    return _normalize_intent(_parse_intent_stub(prompt, names), subjects, prompt)


# The attribute dimensions a question can describe a classification BY. A classification
# is an attribute bag, so "the graveyard police classifications" is a filter — shift ×
# department — not a name lookup.
_CLASS_FIELDS = ("department", "rank", "shift", "bargaining_unit")


def _resolve_classifications(text: str, subjects: list[dict]) -> list[str]:
    """Resolve free text to roster classification labels, deterministically.

    Two passes: exact label mentions win; otherwise treat the text as an attribute
    filter — every field with at least one value mentioned constrains (AND across
    fields, OR within a field). "graveyard police" -> shift Graveyard AND department
    police -> the four police graveyard classifications.
    """
    tl = text.lower()
    hits = [s["name"] for s in subjects
            if s.get("name") and str(s["name"]).lower() in tl]
    if hits:
        return hits

    def _mentioned(value: str, plural_ok: bool) -> bool:
        # Whole-word — plain substring made "holiDAY shift" mention the Day shift and pull
        # day classifications into a graveyard question. Plural tolerance only for ranks
        # ("the sergeantS"): on a shift it turns the unit word "days" into the Day shift.
        suffix = "s?" if plural_ok else ""
        return re.search(rf"\b{re.escape(value.lower())}{suffix}\b", tl) is not None

    field_vals: dict[str, set] = {}
    for f in _CLASS_FIELDS:
        mentioned = {str(s.get(f)) for s in subjects
                     if s.get(f) and _mentioned(str(s.get(f)), plural_ok=(f == "rank"))}
        if mentioned:
            field_vals[f] = mentioned
    if not field_vals:
        return []
    return [s["name"] for s in subjects
            if all(str(s.get(f)) in vs for f, vs in field_vals.items())]


def _normalize_intent(out: dict, subjects: list[dict], prompt: str = "") -> dict:
    """Align raw LLM/stub output to the data's own vocabulary so downstream matching is
    exact: 3-letter weekdays, and subjects resolved to exact classification labels."""
    wd = str(out.get("holiday_weekday") or "").strip()
    if wd:
        out["holiday_weekday"] = wd[:3].title()
    labels = {str(s.get("name", "")) for s in subjects}
    resolved: list[str] = []
    for r in out.get("subjects") or []:
        r = str(r).strip()
        if r in labels:                       # the model copied a label exactly
            if r not in resolved:
                resolved.append(r)
            continue
        for m in _resolve_classifications(r, subjects):   # or described a class
            if m not in resolved:
                resolved.append(m)
    if not resolved and prompt:               # last resort: read the prompt itself
        resolved = _resolve_classifications(prompt, subjects)
    if resolved:
        out["subjects"] = resolved
    return out


def _parse_intent_stub(prompt: str, names: list[str]) -> dict:
    p = prompt.lower()
    # Exact label mentions only; class DESCRIPTIONS ("graveyard police") are resolved by
    # the shared attribute filter in _normalize_intent.
    matched = [full for full in names if full.lower() in p]
    hours = 0.0
    m = re.search(r"(\d+(?:\.\d+)?)\s*-?\s*hour", p)
    if m:
        hours = float(m.group(1))
    weekday = ""
    for word, abbr in _WEEKDAYS.items():
        if word in p:
            weekday = abbr
            break
    date = ""
    dm = re.search(r"(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s*\d{1,2}",
                   p)
    if dm:
        date = dm.group(0)
    _note("parse_intent", "fallback", rule="regex + roster name matching")
    return {"subjects": matched, "hours": hours, "date": date,
            "holiday_weekday": weekday, "source": "stub"}


# --------------------------------------------------------------------------- #
# draft_rules (authoring)
# --------------------------------------------------------------------------- #
def _dsl_contract(known_facts: set[str], field_values: dict | None = None,
                  bool_facts: list | None = None) -> str:
    vals = ""
    if field_values:
        lines = "\n".join(f"  {k} is one of: {v}" for k, v in sorted(field_values.items()))
        vals = (f"\nCATEGORICAL VALUES — compare against these EXACTLY (case-sensitive). "
                f"Writing 'graveyard' when the data says 'Graveyard' silently breaks the "
                f"rule:\n{lines}\n")
    if bool_facts:
        vals += (f"\nBOOLEAN facts — compare with True/False, NEVER with the strings "
                 f"'True'/'False': {sorted(bool_facts)}\n"
                 f"  correct:   subject_bilingual == True\n"
                 f"  WRONG:     subject_bilingual == 'True'\n")
    vals += ("\nSTRING facts hold text, not booleans. `holiday_weekday` is a 3-letter day "
             "like 'Sat' or an empty string — never compare it to True. If a clause "
             "applies to a scenario you cannot detect from the facts (e.g. 'is today a "
             "holiday?'), use when: True and say so in human_readable.\n")
    return f"""
You convert contract clauses into rules for a DETERMINISTIC rule engine. You never
compute the answer — you only express the rule.

An MOU is a RULEBOOK, and pay is only one chapter of it. Draft a rule for ANY clause
that yields a determinate value for a person, not just money:
  currency  "2.5x base for holiday hours"        -> result_type: "currency"
  days      "5 days of bereavement leave"        -> result_type: "days"
  hours     "3.08 hours of vacation per period"  -> result_type: "hours"
  date      "15 calendar days to file"           -> result_type: "days"
  boolean   "eligible after 12 months"           -> result_type: "boolean"
Set `result_type` on every selector, and a short `topic` (bereavement, overtime,
vacation, grievance, uniform, education, ...) so the right rule answers the right
question. Modifiers inherit the type of the selector they feed.

Do NOT draft rules for clauses with no determinate value — narrative or procedural
text (management rights, just cause, conduct standards, recognition). Those are
answered by quoting the contract, not by computing. Report them under needs_data with
"category": "narrative".

HARD CONSTRAINT — the ONLY facts that exist are:
{sorted(known_facts)}
Any expression referencing a name outside this list is INVALID and will be rejected.
Never invent a fact (e.g. do not write subject_is_certified if it is not listed).
{vals}

EXPRESSION SYNTAX — expressions are PYTHON, evaluated in a sandbox:
  boolean operators: and · or · not          NEVER &&  ||  !
  comparison:        ==  !=  <  <=  >  >=    membership: in
  arithmetic:        +  -  *  /  ( )         conditional: X if COND else Y
  correct:   subject_shift == 'Graveyard' and subject_bilingual == True
  WRONG:     subject_shift == 'Graveyard' && subject_bilingual == True

SCOPE — do NOT re-check the bargaining unit or department inside "when". The engine only
ever gives a rule the employees that document already governs. Writing
`subject_bargaining_unit == '...'` is redundant; scope belongs in the citation, not the
condition.

PAY IS A STACK, NOT A CONTEST. This is the most important thing to get right. A person's
pay is ONE base formula, ADJUSTED by differentials, PLUS independent premiums. Mark each
rule's ROLE so the engine composes them — do not make everything a selector that competes:

  role "base"         — THE pay formula for a scenario. Exactly one base wins per person,
                        so bases must be MUTUALLY EXCLUSIVE by their "when" (regular vs
                        overtime vs holiday-worked). "compute" is the whole base amount,
                        e.g. "effective_base * 2.5 * hours" for holiday work.
  role "differential" — a %/rate ADJUSTMENT to the base rate (graveyard +5.5%, a bilingual
                        5% OF BASE RATE). Use "set" to adjust effective_base, e.g.
                        {{"effective_base": "effective_base * 1.055"}}. Use this for ANY
                        percentage of the base rate that later pay multiplies — even if the
                        clause calls it a "premium". If a holiday clause says it applies
                        "after the shift differential and bilingual premium", those two are
                        differentials: they change the rate the 2.5x multiplies, so they
                        must compound, not add flat.
  role "premium"      — a SEPARATE amount added ON TOP after the base is computed, that a
                        later multiplier does NOT touch: a flat $250 FTO stipend, a $1,200
                        uniform allowance. "compute" is JUST the added amount, e.g. "250".
                        A premium NEVER competes to be the base.
  role "exception"    — suppresses or caps another term. (Rare; omit if unsure.)

Differential vs premium, the test: does a later multiplier apply to it? A "5% of base
rate" that the holiday 2.5x multiplies -> DIFFERENTIAL (compounds). A flat $250 nobody
multiplies -> premium (adds). When a clause says a percentage is "added to the base hourly
rate", it is adjusting the RATE -> differential.

Choosing the role is usually obvious from the verb: "shall be paid X for" -> base;
"shall be increased by N%" / "% of base rate" -> differential; "shall ALSO receive / in
addition / a flat $X allowance" -> premium.

DO NOT set a "priority" or any precedence number. Precedence is DERIVED by the engine
from which document a rule comes from (a side letter overrides the MOU it amends; an MOU
overrides a citywide policy). You cannot see the other documents, so you cannot rank
against them — and guessing a number is exactly how a generic overtime rule ends up
beating a specific holiday premium. Just state the rule and its role.

RULE MECHANICS:
- differential -> requires "set": {{"<target>": "<expr>"}}, no "compute".
- base / premium -> requires "compute": "<expr>", no "set".
- `effective_base` starts equal to subject_base_hourly; differentials adjust it and compound.
- A flat premium is its own additive term — never fold a flat dollar amount into a
  percentage multiplication.

PAY_BASIS — how often the amount is paid. REQUIRED on every currency rule, because a
question about ONE shift must include only per-hour and per-shift pay, never a year of
benefits. Read it from the clause's own words:
  "per hour" / "for all hours worked" / "1.5x the rate"    -> "hourly"
  "per shift" / "each shift" / "per call"                  -> "per_shift"
  "per pay period" / "bi-weekly" / "each paycheck"         -> "per_pay_period"
  "per month" / "monthly contribution"                     -> "monthly"
  "annual" / "per year" / "1x annual salary"               -> "annual"
  a single lump sum                                         -> "one_time"
  Uniform allowance $1,200/yr -> annual. Medical $1,800/mo -> monthly. Life insurance =
  1x annual salary -> annual. FTO $250 per pay period -> per_pay_period. These are real
  money but the WRONG UNIT for a shift; mark them and the engine excludes them from a
  shift cost. Do NOT drop them — they answer a different question.

`hours` IS THE LENGTH OF THE SHIFT BEING COSTED — nothing else. Never key a rule on it as
if it meant hours of some OTHER event:
- "court time over 3 hours", "callback minimum 2 hours", "standby per 24-hour period" are
  EVENT pay. They fire only when that event OCCURRED, which is a fact the roster does not
  have. `hours > 3` does NOT mean "a court appearance ran long" — it means "the shift is
  longer than 3 hours", which is almost always true and would pay every shift court time.
  Do NOT draft these. List under needs_data (category "missing_attribute", field "event").
- CRITICAL — never use "when": "True" for a rule that applies to only SOME employees or
  only when an event occurred. If NO available fact identifies the subset/event, DO NOT
  draft it; list it under needs_data. "when": "True" is acceptable ONLY for a genuine
  catch-all that truly applies to everyone every time (a flat allowance everyone gets).

Return ONLY JSON:
{{"rules": [{{
  "id": "snake_case_id",
  "kind": "modifier"|"selector",   // differential -> modifier; base/premium -> selector
  "role": "base"|"differential"|"premium"|"exception",
  "pay_basis": "hourly"|"per_shift"|"per_pay_period"|"monthly"|"annual"|"one_time",  // currency rules
  "result_type": "currency"|"days"|"hours"|"date"|"boolean"|"text",
  "topic": "<short topic>",
  "when": "<expression>", "set": {{...}} OR "compute": "<expression>",
  "human_readable": "<one sentence, cite the section>",
  "citation": {{"clause": "<section number>", "page": <int>}}
 }}],
 "needs_data": [{{"clause": "<section>", "reason": "<what the clause provides>",
                 "missing": "<plain-English description of what is required, or n/a>",
                 "missing_field": "<snake_case field a data owner would add, e.g.
                                   subject_assignment / event / subject_step; omit if narrative>",
                 "category": "missing_attribute"|"narrative"}}]}}
"""


def draft_rules(clauses: list[dict], doc_id: str, known_facts: set[str] | None = None,
                field_values: dict | None = None, bool_facts: list | None = None) -> list[dict]:
    """Propose DSL rules for parsed clauses. Claude is given the case's REAL fact
    vocabulary (the data schema) so drafts reference fields that actually exist;
    anything it still gets wrong is caught by validate_rules() before ratification."""
    draft_rules.last_errors = []
    if have_key() and known_facts:
        system = _dsl_contract(known_facts, field_values, bool_facts)
        # Map-reduce over sections so a large doc (50+ pages, hundreds of clauses)
        # never overflows a single request or the output cap (PRD §8B).
        rules: list[dict] = []
        needs: list[dict] = []
        failed: list[dict] = []
        for group in _chunked(clauses, 10):
            # A chunk is isolated. This whole loop used to sit inside one try/except that
            # fell back to the stub on ANY failure, so ONE malformed response discarded
            # the work of every other chunk: the 26-page POA MOU had 13 of 14 chunks
            # succeed with 33 rules between them, and drafted 0. Silently — the queue
            # simply had no police rules in it, and the golden could never pass.
            out = _draft_group(system, group, doc_id)
            if out is None:
                failed.append({"clauses": [c.get("clause") for c in group],
                               "pages": sorted({c.get("page") for c in group})})
                continue
            got_rules, got_needs = out
            rules.extend(got_rules)
            needs.extend(got_needs)

        if rules or needs:
            draft_rules.last_needs_data = needs  # surfaced by the review gate
            # Partial extraction is reported, never swallowed: a clause nobody drafted
            # and nobody flagged is a hole in the library that looks like completeness.
            draft_rules.last_errors = failed
            return rules
        # Nothing at all came back — the key may be bad or the API down. Fall back, but
        # say so rather than presenting stub output as if the model had produced it.
        draft_rules.last_errors = failed or [{"clauses": ["*"], "pages": []}]

    _note("draft_rules", "fallback", rule="offline keyword stub", doc_id=doc_id)
    draft_rules.last_needs_data = []
    return _draft_rules_stub(clauses, doc_id)


def _draft_group(system: str, group: list[dict], doc_id: str):
    """Draft one chunk. Returns (rules, needs_data), or None if the chunk is unusable.

    On truncation the remedy is to ask for less, not to give up: the chunk is halved and
    retried. Ten dense clauses can produce more rule JSON than the output cap allows, and
    the cap is what broke the largest document in the corpus.
    """
    payload = [{"clause": c.get("clause"), "page": c.get("page"),
                "text": c.get("text", "")} for c in group]
    try:
        out = _claude_json(system, "Clauses:\n" + json.dumps(payload, indent=2),
                           max_tokens=_DRAFT_MAX_TOKENS, label="draft_rules")
    except ResponseTruncated:
        if len(group) == 1:
            return None  # a single clause that cannot fit its own answer: report it
        mid = len(group) // 2
        halves = [_draft_group(system, group[:mid], doc_id),
                  _draft_group(system, group[mid:], doc_id)]
        if all(h is None for h in halves):
            return None
        rules, needs = [], []
        for h in halves:
            if h:
                rules.extend(h[0])
                needs.extend(h[1])
        return rules, needs
    except Exception:
        return None

    rules, needs = [], []
    for nd in out.get("needs_data", []):
        nd["doc_id"] = doc_id
        needs.append(nd)
    for r in out.get("rules", []):
        cit = r.get("citation") or {}
        cit["doc_id"] = doc_id
        # carry the bbox from the parsed clause so citations highlight
        for c in group:
            if str(c.get("clause")) == str(cit.get("clause")):
                cit.setdefault("page", c.get("page"))
                cit["bbox"] = c.get("bbox", [])
                break
        r["citation"] = cit
        r["status"] = "proposed"
        # Role is the authoritative field now; `kind` is a mechanical consequence of it
        # (differential -> modifier, everything else -> selector). Derive whichever the
        # model left out so a draft is valid regardless of which it emphasised.
        role, kind = r.get("role"), r.get("kind")
        if role and not kind:
            r["kind"] = "modifier" if role == "differential" else "selector"
        elif kind and not role:
            r["role"] = "differential" if kind == "modifier" else "base"
        # Precedence is derived from document scope, never authored — drop any number the
        # model emitted despite the instruction not to.
        r.pop("priority", None)
        rules.append(r)
    return rules, needs


draft_rules.last_needs_data = []
draft_rules.last_errors = []

# Rule drafting is the largest output in the system — ten clauses can yield a dozen rules,
# each with an expression, a citation and a human_readable line. The 1500-token default
# shared with every other call is what made the largest document in the corpus draft zero.
_DRAFT_MAX_TOKENS = 8000


def _chunked(items: list, n: int):
    for i in range(0, len(items), n):
        yield items[i:i + n]


def _draft_rules_stub(clauses: list[dict], doc_id: str) -> list[dict]:
    """Offline stand-in for LLM rule drafting.

    This is a DEMO SHIM, not a rule generator: it only recognizes the reference
    overtime MOU. Each template must match the clause NUMBER *and* fingerprint words in
    the clause TEXT — otherwise a different document that happens to have a §9.1 (e.g.
    a uniform-allowance clause) would be handed overtime rules. Anything it doesn't
    recognize yields no proposal; real drafting requires Claude.
    """
    tmpl = {
        "9.1": {"needs": ("holiday", "premium"),
                "rule": {"id": "art9_1", "kind": "selector", "when": "True",
                         "compute": "effective_base * 2.5 * hours",
                         "human_readable": "Base + 1.5x holiday premium (2.5x) for hours worked."}},
        "9.2": {"needs": ("weekend", "holiday"),
                "rule": {"id": "art9_2", "kind": "selector", "when": "holiday_weekday in subject_schedule_days",
                         "compute": "effective_base * hours + 150",
                         "human_readable": "Weekend-shift exception: flat $150, no 1.5x.",
                         "flags": [{"when": "True",
                                    "message": "§9.2 silent on straight-time base for hours worked; confirm.",
                                    "alternate": "150"}]}},
        "9.3": {"needs": ("bilingual",),
                "rule": {"id": "art9_3", "kind": "modifier", "when": "subject_bilingual == True",
                         "set": {"effective_base": "subject_base_hourly * 1.05"},
                         "human_readable": "Bilingual +5% applied before the multiplier."}},
    }
    out = []
    for c in clauses:
        entry = tmpl.get(c.get("clause"))
        if not entry:
            continue
        text = (c.get("text") or "").lower()
        if not all(w in text for w in entry["needs"]):
            continue  # same number, different document -> do NOT propose
        r = dict(entry["rule"])
        r["citation"] = {"doc_id": doc_id, "clause": c.get("clause"),
                         "page": c.get("page", 1), "bbox": c.get("bbox", []),
                         "char_span": c.get("char_span", [])}
        r["status"] = "proposed"
        out.append(r)
    return out


# --------------------------------------------------------------------------- #
# tag_document (ingestion)
# --------------------------------------------------------------------------- #
def tag_document(text: str, taxonomy: dict) -> dict:
    if have_key():
        try:
            system = ("You classify a policy document. Return ONLY JSON with "
                      "keys: department (str), tags (list of strings from or "
                      "extending the taxonomy), summary (one paragraph), "
                      "proposed_tags (new tags not in the taxonomy). Taxonomy: "
                      + json.dumps(taxonomy))
            out = _claude_json(system, text[:6000])
            out["source"] = "claude"
            return out
        except Exception:
            pass
    return _tag_document_stub(text, taxonomy)


_NEGATION = ("no ", "not ", "without", "contains no", "excludes", "except",
             "other than", "rather than")


def _mentioned_affirmatively(needle: str, text_lower: str) -> bool:
    """True if `needle` appears in a sentence that is NOT negating it. Prevents a
    distractor doc that says 'contains no overtime or holdover rules' from being
    tagged with overtime/holdover."""
    hit = False
    for sentence in re.split(r"(?<=[.;])\s+", text_lower):
        if needle not in sentence:
            continue
        if any(neg in sentence for neg in _NEGATION):
            continue  # negated mention -> ignore
        hit = True
    return hit


def _tag_document_stub(text: str, taxonomy: dict) -> dict:
    t = text.lower()
    flat = [tag for group in taxonomy.values() for tag in group]
    tags = []
    for tag in flat:
        needle = tag.replace("-", " ")
        if _mentioned_affirmatively(needle, t) or _mentioned_affirmatively(tag, t):
            tags.append(tag)
    # light keyword nudges (also negation-aware)
    if _mentioned_affirmatively("overtime", t) and "overtime" not in tags:
        tags.append("overtime")
    if _mentioned_affirmatively("holiday", t) and "holiday-pay" not in tags:
        tags.append("holiday-pay")
    if any(_mentioned_affirmatively(k, t) for k in ("salary", "wage", "pay step")) \
            and "salary-schedule" not in tags:
        tags.append("salary-schedule")
    department = "public-works" if "public works" in t else \
        ("sheriff" if "sheriff" in t or "correctional" in t else "")
    summary = _first_sentences(text, 2)
    return {"department": department, "tags": sorted(set(tags)),
            "summary": summary, "proposed_tags": [], "source": "stub"}


def _first_sentences(text: str, n: int) -> str:
    clean = re.sub(r"\s+", " ", text).strip()
    parts = re.split(r"(?<=[.])\s+", clean)
    return " ".join(parts[:n])[:400]


# --------------------------------------------------------------------------- #
# rank_documents (retrieval)
# --------------------------------------------------------------------------- #
def rank_documents(query: str, catalog: list[dict]) -> list[dict]:
    """Return candidates: [{doc_id, score, reason}], ranked best-first."""
    if have_key():
        try:
            system = ("Given a user question and a catalog of documents "
                      "(id, tags, summary), rank which documents can answer it. "
                      "Return ONLY JSON: {\"candidates\": [{\"doc_id\":..., "
                      "\"score\": 0..1, \"reason\":...}]} best first.")
            user = f"Question: {query}\nCatalog: {json.dumps(catalog)}"
            out = _claude_json(system, user, label="rank_documents")
            cands = out.get("candidates", [])
            for c in cands:
                c["source"] = "claude"
            return cands
        except Exception:
            pass
    return _rank_documents_stub(query, catalog)


def _rank_documents_stub(query: str, catalog: list[dict]) -> list[dict]:
    q = query.lower()
    q_words = set(re.findall(r"[a-z]+", q))
    scored = []
    for doc in catalog:
        text = (" ".join(doc.get("tags", [])) + " " + doc.get("summary", "") + " "
                + doc.get("title", "")).lower()
        d_words = set(re.findall(r"[a-z]+", text))
        overlap = q_words & d_words
        score = len(overlap) / (len(q_words) or 1)
        # strong signals for costing/overtime questions
        if any(k in q for k in ("cost", "overtime", "shift", "holiday", "pay")) and \
           any(t in doc.get("tags", []) for t in ("overtime", "holiday-pay")):
            score += 0.5
        scored.append({"doc_id": doc.get("doc_id"), "score": round(min(score, 1.0), 3),
                       "reason": f"tag/keyword overlap: {sorted(overlap)[:6]}",
                       "source": "stub"})
    scored.sort(key=lambda c: c["score"], reverse=True)
    return scored
