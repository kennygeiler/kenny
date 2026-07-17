"""Governance resolver — deterministic 'who is governed by which document' (PRD §4A).

In the real world the rules that apply to an employee are NOT guessed from the words
of a question. They are fixed by two facts:

    WHO  -> the employee's bargaining unit  -> which MOU
    WHEN -> the shift date                  -> which *version* of that MOU is in effect

This module does that lookup against the case's declared documents. It replaces
keyword/summary guessing as the PRIMARY routing path; LLM retrieval (retriever.py)
remains only as a fallback when governance can't resolve a document (e.g. a free-text
question with no employees, or no matching MOU).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date


@dataclass
class GovResult:
    doc_ids: list[str]                     # governing MOU document(s)
    matched: list[dict] = field(default_factory=list)  # per-doc reasoning
    units: list[str] = field(default_factory=list)
    date: str | None = None
    resolved: bool = False
    reason: str = ""


_MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}


def parse_date(text: str, default_year: int | None = None) -> str | None:
    """Best-effort date extraction -> ISO 'YYYY-MM-DD', or None. Handles 'July 4',
    'Jul 4 2026', '2026-07-04'. Missing year falls back to default_year."""
    if not text:
        return None
    t = text.strip().lower()
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", t)
    if m:
        return m.group(0)
    m = re.search(r"(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s*(\d{1,2})"
                  r"(?:,?\s*(\d{4}))?", t)
    if m:
        mon = _MONTHS[m.group(1)]
        day = int(m.group(2))
        year = int(m.group(3)) if m.group(3) else default_year
        if year:
            try:
                return date(year, mon, day).isoformat()
            except ValueError:
                return None
    return None


def _covers(src: dict, iso: str | None) -> tuple[bool, str]:
    start = src.get("effective_start")
    end = src.get("effective_end")
    if not iso:
        return True, "no date given; unit match only"
    if start and iso < start:
        return False, f"date {iso} before effective_start {start}"
    if end and iso > end:
        return False, f"date {iso} after effective_end {end}"
    return True, f"date {iso} within [{start or '-inf'} .. {end or '+inf'}]"


def apply_supersession(rules: list, sources: list[dict],
                       doc_ids: list[str]) -> tuple[list, list[dict]]:
    """Drop rules that a governing amendment replaces.

    Real MOUs are amended mid-term by side letters: the base MOU says the graveyard
    differential is 5.5%, a 2025 side letter says 6.5%. BOTH documents govern the same
    unit on the same date, so `resolve()` returns both — and without this step the
    engine would load two graveyard modifiers and STACK them, silently producing a
    number that is too high.

    An amendment declares what it replaces:
        supersedes: {doc_id: <base doc>, clauses: ["6.1"]}   # omit clauses = whole doc

    Returns (kept_rules, dropped) where `dropped` explains each removal for the ledger.
    """
    active = [s for s in sources if s.get("id") in doc_ids and s.get("supersedes")]
    if not active:
        return rules, []
    # Only supersede if the amendment actually CONTRIBUTES a live rule. An amendment
    # whose replacement rule was never ratified must not delete the base rule — that
    # would silently drop the premium entirely instead of updating it (caught by the
    # golden gate: the graveyard differential vanished and the total came out LOW).
    contributing = {s["id"] for s in active
                    if any(getattr(r, "citation", None) and r.citation.doc_id == s["id"]
                           for r in rules)}
    active = [s for s in active if s["id"] in contributing]
    if not active:
        return rules, []
    kill: set[int] = set()
    dropped: list[dict] = []
    for src in active:
        sup = src["supersedes"] or {}
        target = sup.get("doc_id")
        clauses = set(str(c) for c in (sup.get("clauses") or []))
        for r in rules:
            cit = getattr(r, "citation", None)
            if not cit or cit.doc_id != target:
                continue
            if clauses and str(cit.clause) not in clauses:
                continue
            kill.add(id(r))
            dropped.append({"rule_id": getattr(r, "id", "?"),
                            "superseded_doc": target, "clause": cit.clause,
                            "superseded_by": src["id"],
                            "effective_start": src.get("effective_start")})
    return [r for r in rules if id(r) not in kill], dropped


def resolve(units: list[str], date_iso: str | None, sources: list[dict]) -> GovResult:
    """Return the governing MOU document(s) for the given bargaining unit(s) + date.

    Amendments (doc_type: amendment) are governing documents too — they carry rules
    that replace clauses of their base MOU. See apply_supersession().
    """
    units = [u for u in units if u]
    matched: list[dict] = []
    for src in sources:
        if src.get("doc_type") not in ("MOU", "amendment"):
            continue
        unit = src.get("bargaining_unit")
        if unit not in units:
            continue
        ok, why = _covers(src, date_iso)
        if not ok:
            continue
        matched.append({"doc_id": src["id"], "bargaining_unit": unit,
                        "effective_start": src.get("effective_start"),
                        "effective_end": src.get("effective_end"),
                        "why": f"unit '{unit}' matches; {why}",
                        "mou_version": src.get("mou_version")})
    doc_ids = [m["doc_id"] for m in matched]
    if doc_ids:
        reason = (f"resolved by governance: units {units} + date {date_iso} -> "
                  f"MOU(s) {doc_ids}")
        return GovResult(doc_ids, matched, units, date_iso, True, reason)
    reason = (f"no MOU governs units {units} on {date_iso}; "
              "falling back to document retrieval")
    return GovResult([], [], units, date_iso, False, reason)
