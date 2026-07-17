"""Stamp a fresh, empty case bundle (PRD §4 — configuration over code).

    python scripts/new_case.py <name>

Creates cases/<name>/ with a skeleton case.yaml, empty data/rules/prompt folders,
and a starter taxonomy. Drop in a PDF + data + extraction config, ingest, approve
rules, and the same core serves a new page with zero code changes.
"""
import os
import sys

TEMPLATE_CASE_YAML = """name: {name} case
department: ""

sources: []
  # - id: my_doc
  #   file: sources/my_doc.pdf
  #   title: My Document

data:
  adapter: csv
  path: data/subjects.csv
  schema: {{}}   # field: str|float|int|bool|list

rules: rules/rules_ratified.json
proposed_rules: rules/rules_proposed.json
catalog: catalog.json
taxonomy: taxonomy.yaml
extraction: prompt/extraction.yaml
ledger: ledger.jsonl
snapshots: snapshots

rounding:
  places: 2
  mode: half_up
"""

TEMPLATE_TAXONOMY = """department: []
topic: []
doc-type: []
"""

TEMPLATE_EXTRACTION = """description: Describe what the LLM should extract from a prompt.
entities: {}
output_shape: {}
"""


def main(name: str) -> None:
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    case_dir = os.path.join(root, "cases", name)
    if os.path.exists(case_dir):
        sys.exit(f"case {name!r} already exists at {case_dir}")
    for sub in ("sources", "data", "rules", "prompt", "snapshots"):
        os.makedirs(os.path.join(case_dir, sub), exist_ok=True)
    _write(os.path.join(case_dir, "case.yaml"), TEMPLATE_CASE_YAML.format(name=name))
    _write(os.path.join(case_dir, "taxonomy.yaml"), TEMPLATE_TAXONOMY)
    _write(os.path.join(case_dir, "prompt", "extraction.yaml"), TEMPLATE_EXTRACTION)
    _write(os.path.join(case_dir, "rules", "rules_ratified.json"), '{"rules": []}\n')
    print(f"stamped new case at {case_dir}")
    print(f"run it with:  CASE={os.path.relpath(case_dir, os.getcwd())} uvicorn core.app:app")


def _write(path: str, content: str) -> None:
    with open(path, "w") as f:
        f.write(content)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: python scripts/new_case.py <name>")
    main(sys.argv[1])
