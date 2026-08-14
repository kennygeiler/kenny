"""Reset a case to a clean, blind state for a live test run.

Nothing is deleted. The ratified library is hand-reviewed work and the ledger is an audit
trail, so both are copied to a timestamped backup first, and source PDFs are MOVED rather
than removed.

After this the app knows NOTHING it was not given: no catalog, no search index, no drafted
rules, no ratified rules, no ledger. Chat must refuse to cost anything until documents are
ingested and rules are reviewed and ratified by a person.

Two shapes of blind, differing only in whether the upload path is exercised:

  --keep-sources   the PDFs stay in sources/ — a customer drops a bundle and presses
                   Ingest. One click for the whole corpus.
  (default)        the PDFs are staged to a folder for re-upload through the admin panel.
                   Slower, but it tests the path a real user takes on day one.

Usage:  python -m scripts.reset_case cases/santacruz                 # stage PDFs out
        python -m scripts.reset_case cases/santacruz --keep-sources  # leave them for Ingest
"""
from __future__ import annotations

import glob
import json
import os
import shutil
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Rebuilt by ingest; safe to drop once backed up.
ARTIFACTS = ["catalog.json", "search_index.jsonl", "ledger.jsonl",
             "rules/rules_proposed.json"]


def main(case_rel: str, stage_dir: str, keep_sources: bool = False) -> int:
    case_dir = os.path.join(ROOT, case_rel)
    if not os.path.exists(os.path.join(case_dir, "case.yaml")):
        print(f"not a case directory: {case_dir}", file=sys.stderr)
        return 1

    backup = os.path.expanduser(f"~/holly_backup_{time.strftime('%Y%m%d-%H%M%S')}")
    os.makedirs(backup, exist_ok=True)
    for rel in ["rules", "ledger.jsonl", "catalog.json", "snapshots"]:
        src = os.path.join(case_dir, rel)
        if os.path.exists(src):
            dst = os.path.join(backup, os.path.basename(rel))
            (shutil.copytree if os.path.isdir(src) else shutil.copy2)(src, dst)
    print(f"backup    -> {backup}")

    stage = os.path.abspath(os.path.expanduser(stage_dir))
    pdfs = sorted(glob.glob(os.path.join(case_dir, "sources", "*.pdf")))

    if keep_sources:
        # The documents stay put: this is a customer dropping a bundle of contracts and
        # pressing Ingest. Everything Holly LEARNED from them is still wiped — catalog,
        # index, drafts, ratified rules, ledger — so the run is blind either way. What
        # differs is only whether the upload path is exercised.
        print(f"sources   -> kept in place ({len(pdfs)} PDFs) — Ingest will read them")
    else:
        # Stage the PDFs OUT of the case so the upload path is genuinely exercised.
        #
        # This branch once ran `rmtree(stage)` to start the staging folder clean. On a
        # SECOND run, sources/ was already empty, so it deleted the 13 PDFs the FIRST run
        # had staged there and replaced them with nothing — a reset that destroyed the
        # corpus while reporting success. Never delete a directory this script did not
        # create, and never move zero files into a directory as if that were a reset.
        if not pdfs:
            print(f"no PDFs in {case_dir}/sources — already reset. Nothing moved.",
                  file=sys.stderr)
            print("if you want them back: restore them from the staging folder "
                  "(sources_staged/) or from git")
            return 1
        if os.path.exists(stage) and os.listdir(stage):
            print(f"staging folder is not empty: {stage}\nmove or delete it yourself, then "
                  f"re-run. Refusing to touch files this script did not put there.",
                  file=sys.stderr)
            return 1
        os.makedirs(stage, exist_ok=True)
        for p in pdfs:
            shutil.move(p, os.path.join(stage, os.path.basename(p)))

        # The sidecars are docling's offline fallback. Left in place they would answer for
        # a parse that never happened, which is the opposite of a blind test.
        for s in glob.glob(os.path.join(case_dir, "sources", "*.clauses.json")):
            shutil.move(s, os.path.join(backup, os.path.basename(s)))

    for rel in ARTIFACTS:
        path = os.path.join(case_dir, rel)
        if os.path.exists(path):
            os.remove(path)
    for snap in glob.glob(os.path.join(case_dir, "snapshots", "*.json")):
        os.remove(snap)
    for bak in glob.glob(os.path.join(case_dir, "rules", "*.bak")):
        os.remove(bak)

    with open(os.path.join(case_dir, "rules", "rules_ratified.json"), "w") as f:
        json.dump({"rules": []}, f, indent=2)

    if not keep_sources:
        print(f"staged    -> {stage} ({len(pdfs)} PDFs)")
    print(f"ratified  -> 0 rules (chat cannot cost anything until you ratify)")
    print(f"learned   -> nothing: catalog, index, drafts and ledger all cleared")
    print(f"sources/  -> {len(os.listdir(os.path.join(case_dir, 'sources')))} files")
    return 0


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    keep = "--keep-sources" in sys.argv
    case = args[0] if args else "cases/santacruz"
    stage = args[1] if len(args) > 1 else "~/Holly_Test_Documents"
    raise SystemExit(main(case, stage, keep_sources=keep))
