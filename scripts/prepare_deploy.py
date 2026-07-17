"""Bake a case's corpus into the image at BUILD time, so the container starts answering.

Ingesting the 13-document reference corpus takes minutes (docling parses each PDF) and
downloads model weights on first use. Doing that at container start means the first
visitor to the shared link waits several minutes, every redeploy repeats it, and a
platform health check kills the instance before it finishes. So it runs once, here, in
`docker build`, and the resulting catalog + search index ship inside the image.

Deliberately runs with NO api key: ingest only extracts + indexes (rules are authored
per scenario at runtime and ship in rules_ratified.json as approved), so the build is
reproducible, spends no money, and bakes no key into a layer.

Usage:  python -m scripts.prepare_deploy cases/citywide
"""
from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

os.environ.pop("ANTHROPIC_API_KEY", None)  # before core.app imports llm


def _baked(case_rel: str) -> bool:
    """True when a committed catalog already covers every declared document, with clauses.

    A real corpus (scanned MOUs, OCR'd) is ingested ONCE on a workstation — the parse
    peaks ~4GB RSS, more than a cloud builder gets — and the catalog + search index ship
    with the source. The build then only has to VERIFY the bake, not repeat it.
    """
    import json
    import yaml
    case_dir = os.path.join(ROOT, case_rel)
    cat_path = os.path.join(case_dir, "catalog.json")
    idx_path = os.path.join(case_dir, "search_index.jsonl")
    if not (os.path.exists(cat_path) and os.path.exists(idx_path)):
        return False
    manifest = yaml.safe_load(open(os.path.join(case_dir, "case.yaml")))
    declared = {s["id"] for s in manifest.get("sources", [])}
    docs = {d["doc_id"]: d for d in json.load(open(cat_path)).get("documents", [])}
    missing = [d for d in declared if d not in docs or not docs[d].get("clauses")]
    if missing:
        print(f"[prepare] baked catalog incomplete (missing/empty: {missing}) — re-ingesting")
        return False
    print(f"[prepare] using baked artifacts: {len(declared)} documents already "
          f"extracted + indexed")
    return True


def main(case_rel: str) -> int:
    os.environ["CASE"] = case_rel

    if not _baked(case_rel):
        from core.app import _JOBS, _ingest_worker  # imported after CASE is set
        job_id = "build"
        _JOBS[job_id] = {"status": "running", "total": 0, "done": 0, "current": None,
                         "result": None, "error": None}
        print(f"[prepare] ingesting {case_rel} ...", flush=True)
        _ingest_worker(job_id)

        job = _JOBS[job_id]
        if job["status"] != "done" or job["error"]:
            print(f"[prepare] FAILED: {job['error']}", file=sys.stderr)
            return 1

        result = job["result"] or {}
        docs = result.get("ingested", [])
        empty = [d for d in docs if not d.get("clauses")]
        print(f"[prepare] {len(docs)} documents extracted + indexed (rules ship as ratified)")
        if empty:
            # A document that parses to zero clauses is invisible to search and silently
            # unanswerable. Fail the build rather than ship a corpus with a hole in it.
            print(f"[prepare] FAILED: parsed 0 clauses: {[d['doc_id'] for d in empty]}",
                  file=sys.stderr)
            return 1

    if _goldens_fail(case_rel):
        return 1

    # Warm the embedding model into the image layer. Left cold, the first real query
    # pays a multi-hundred-MB download.
    try:
        from sentence_transformers import SentenceTransformer
        SentenceTransformer("sentence-transformers/all-MiniLM-L6-v2").encode(["warm"])
        print("[prepare] embedding model cached")
    except Exception as e:
        print(f"[prepare] embeddings unavailable, BM25-only at runtime: {e}")

    return 0


def _goldens_fail(case_rel: str) -> bool:
    """Refuse to build an image whose known answers do not come out right.

    The image ships whatever `rules_ratified.json` happens to hold — so a local blind-test
    state (zero ratified rules) silently becomes the DEPLOYED state: chat refuses every
    question and the Verification tab is red for every visitor. The build succeeds either
    way, which is exactly the failure the golden gate exists to prevent, one layer up. If
    a human's approval is not sufficient to make a rule live (PRD §9), a green build is not
    sufficient to make an image shippable.
    """
    from core.app import _case, _check_golden, _ratified_dicts

    case = _case()
    goldens = case.manifest.get("golden_cases") or []
    rules = case.rules()  # ratified-only by construction
    if not goldens:
        print("[prepare] no golden cases declared — nothing to verify", file=sys.stderr)
        return False

    if not rules:
        print(f"\n[prepare] FAILED: {case_rel} has 0 ratified rules.\n"
              f"  The image would deploy a demo that cannot answer anything and shows "
              f"{len(goldens)} failing checks.\n"
              f"  Ratify the rules (Admin -> Review queue) and rebuild, or restore a "
              f"library from a backup.", file=sys.stderr)
        return True

    # The ratify gate tolerates "pending" — a scenario no live rule covers yet — because
    # a half-built library must still be approvable one unit at a time. A RELEASE must
    # not: pending means unproven, and shipping a demo whose Verification tab is amber
    # for every visitor is the failure this gate exists to stop. Require an explicit pass.
    failed = []
    for g in goldens:
        _ok, detail = _check_golden(case, _ratified_dicts(case), g)
        status = detail.get("status", "fail")
        print(f"[prepare] golden {status.upper()}: {g.get('name')} "
              f"(expected {detail.get('expected')}, got {detail.get('actual')})")
        if status != "pass":
            failed.append((g.get("name"), detail))

    if failed:
        print(f"\n[prepare] FAILED: {len(failed)} of {len(goldens)} golden cases do not "
              f"reproduce. Refusing to build an image whose own known answers are wrong.",
              file=sys.stderr)
        for name, d in failed:
            print(f"  - {name}: expected {d.get('expected')}, got {d.get('actual')}"
                  f"{' — ' + d['error'] if d.get('error') else ''}", file=sys.stderr)
        return True

    print(f"[prepare] all {len(goldens)} golden cases pass against "
          f"{len(rules)} ratified rules")
    return False


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1] if len(sys.argv) > 1 else "cases/citywide"))
