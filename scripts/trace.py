"""Verbose data-flow trace: shows exactly what happens at each stage of INGESTION
and CHAT for the Santa Cruz case. Run:  python scripts/trace.py
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import governance, ingest, llm
from core.caseio import load_case
from core.catalog import Catalog
from core.engine import calculate

CASE_DIR = sys.argv[1] if len(sys.argv) > 1 else "cases/santacruz"
PROMPTS = {
    "cases/santacruz": "Cost an 8-hour overtime shift for a Firefighter/Paramedic "
                       "(56 hr, top step).",
}


def line(c="─"):
    print(c * 78)


def head(n, t):
    print(f"\n{'█' * 3} {n}. {t}")


def main():
    case = load_case(CASE_DIR)
    prompt = PROMPTS.get(CASE_DIR.rstrip("/"), PROMPTS["cases/santacruz"])
    print(f"CASE: {case.manifest['name']}   (LLM: {'Claude' if llm.have_key() else 'stub'})")

    # ================= INGESTION =================
    print("\n" + "=" * 78 + "\n===  INGESTION DATA FLOW  " + " " * 51 + "===\n" + "=" * 78)
    cat = Catalog(case.path("catalog", "catalog.json"))
    import yaml
    tax = yaml.safe_load(open(case.path("taxonomy"))) if case.path("taxonomy") else {}

    for src in case.manifest.get("sources", []):
        pdf = src["file"] if os.path.isabs(src["file"]) else os.path.join(case.dir, src["file"])
        head("I", f"SOURCE PDF  →  {src['id']}  ({src.get('doc_type')})")
        print(f"    file: {pdf}")

        head("I.1", "docling parse  →  clauses + bounding boxes")
        clauses, text, source = ingest.parse_pdf(pdf, src["id"])
        print(f"    parser: {source}   clauses extracted: {len(clauses)}")
        for c in clauses[:4]:
            box = c.get("bbox")
            print(f"      · §{c.get('clause') or '—':5} page {c.get('page')}  bbox {box}")
            print(f"        text: \"{c.get('text','')[:88]}...\"")

        head("I.2", "LLM tag + summarize  →  catalog metadata")
        meta = llm.tag_document(text, tax)
        print(f"    department: {meta.get('department')!r}")
        print(f"    tags:       {meta.get('tags')}")
        print(f"    summary:    {meta.get('summary','')[:110]}...")

        entry = ingest.ingest_document(pdf, src["id"], src.get("title", src["id"]), tax, cat)
        head("I.3", "catalog entry written")
        print(f"    catalog.json ← {{doc_id:{entry['doc_id']}, tags:{entry['tags']}, "
              f"clauses:{len(entry['clauses'])}}}")
        print(f"    governance metadata (from case.yaml): unit={src.get('bargaining_unit')} "
              f"type={src.get('doc_type')} effective={src.get('effective_start')}..{src.get('effective_end')}")

    # ================= CHAT =================
    print("\n" + "=" * 78 + "\n===  CHAT MESSAGE INTERPRETATION  " + " " * 43 + "===\n" + "=" * 78)
    print(f'\nPROMPT: "{prompt}"')

    subjects_all = case.subjects()
    head("C.1", "LLM parse_intent  →  structured params (LLM reads language only)")
    params = llm.parse_intent(prompt, {}, subjects_all)
    print(f"    subjects:        {params.get('subjects')}")
    print(f"    hours:           {params.get('hours')}")
    print(f"    date (raw):      {params.get('date')!r}")
    print(f"    holiday_weekday: {params.get('holiday_weekday')!r}")
    print(f"    source:          {params.get('source')}")

    head("C.2", "join structured data  →  who + their bargaining unit + date")
    named = set(params.get("subjects") or [])
    subjects = [s for s in subjects_all if s.get("name") in named] or subjects_all
    units = sorted({s.get("bargaining_unit") for s in subjects if s.get("bargaining_unit")})
    date_iso = governance.parse_date(params.get("date"), 2026)
    for s in subjects:
        print(f"    · {s.get('name'):10} unit={s.get('bargaining_unit')}  base=${s.get('base_hourly')}")
    print(f"    derived bargaining_units: {units}")
    print(f"    shift_date (ISO):         {date_iso}")

    head("C.3", "GOVERNANCE resolve  →  unit + date  →  governing MOU (DETERMINISTIC)")
    gov = governance.resolve(units, date_iso, case.manifest.get("sources", []))
    print(f"    resolved: {gov.resolved}")
    for m in gov.matched:
        print(f"    · MOU {m['doc_id']}  ({m.get('mou_version')})  — {m['why']}")
    print(f"    reason: {gov.reason}")
    if not gov.resolved:
        print("    → would FALL BACK to LLM document retrieval here")

    head("C.4", "load rules of the governing doc  →  deterministic engine")
    chosen = gov.doc_ids
    rules = [r for r in case.rules() if (not r.citation.doc_id) or r.citation.doc_id in chosen]
    print(f"    governing doc(s): {chosen}   rules loaded: {[r.id for r in rules]}")
    eng = {"hours": params.get("hours", 0.0), "holiday_weekday": params.get("holiday_weekday", ""),
           "date_iso": date_iso or ""}
    result = calculate(eng, subjects, rules, case.rounding_places())
    print("    per-employee evaluation:")
    for li in result.line_items:
        chosen_step = next((t for t in li.trace if t.kind == "selector-chosen"), None)
        cites = ",".join(f"§{c['clause']}" for c in li.citations)
        flag = "  ⚑FLAG" if li.needs_human_confirmation else ""
        print(f"      · {li.subject:10} rule={li.rule_id:8} = ${li.total:<8} cites {cites}{flag}")
        if chosen_step:
            math_step = next((t for t in li.trace if t.kind == "math"), None)
            if math_step:
                print(f"        math: {math_step.detail}")

    head("C.5", "ANSWER")
    print(f"    TOTAL = ${result.total}")
    line()


if __name__ == "__main__":
    main()
