"""Load a case bundle (manifest + data + rules + ledger) — PRD §4 template model.

A "case" is a self-contained folder described by case.yaml. The core reads a case
through this loader only; nothing about a specific case is hardcoded in core.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

import yaml

from .dataadapter import make_adapter
from .ledger import Ledger
from .ruledsl import Rule, load_rules

# Document authority → precedence rank. Higher = more specific = wins a tie or an override.
# The ladder is legal: a statute is the floor everyone shares; a citywide policy refines
# it; a bargaining unit's MOU overrides the policy for its members; a side letter amends
# the MOU. Salary schedules sit with their MOU (unit-specific data, not a general rule).
SCOPE_RANK = {
    "statute": 0, "law": 0,
    "policy": 1,
    "MOU": 2, "salary-schedule": 2,
    "amendment": 3, "side-letter": 3,
    "_default": 1,
}


@dataclass
class CaseContext:
    dir: str
    manifest: dict

    # ---- resolved paths ----
    def path(self, key: str, default: str | None = None) -> str | None:
        val = self.manifest.get(key, default)
        if val is None:
            return None
        return val if os.path.isabs(val) else os.path.join(self.dir, val)

    # ---- data ----
    def subjects(self) -> list[dict[str, Any]]:
        data_cfg = self.manifest.get("data", {})
        adapter = make_adapter(data_cfg, self.dir)
        records = adapter.records(data_cfg.get("schema", {}))
        # The subject key is a CLASSIFICATION, not a person (PRD §6a) — the roster column
        # is `classification`. Internally the key stays `name` so every matcher and the
        # engine keep one identifier field; a legacy named roster still works unchanged.
        for r in records:
            if "classification" in r and not r.get("name"):
                r["name"] = r["classification"]
        return records

    # ---- rules ----
    def rules(self) -> list[Rule]:
        rules_path = self.path("rules")
        return self.assign_scope(load_rules(rules_path)) if rules_path else []

    def scope_rank(self, doc_id: str) -> int:
        """A document's authority, from its declared type. More specific overrides more
        general (lex specialis): a side letter beats the MOU it amends, an MOU beats a
        citywide policy, a policy beats a statute. This — not an LLM-invented priority —
        is what makes 'everyone off on a federal holiday EXCEPT police on an assigned
        shift' resolve correctly: the police MOU outranks the citywide holiday policy."""
        doc_type = (self.source_by_id(doc_id) or {}).get("doc_type", "")
        return SCOPE_RANK.get(doc_type, SCOPE_RANK["_default"])

    def assign_scope(self, rules: list[Rule]) -> list[Rule]:
        """Stamp each rule with the precedence derived from its document. Precedence is a
        property of provenance, computed here, never authored into the rule file."""
        for r in rules:
            r.scope_rank = self.scope_rank(r.citation.doc_id)
        return rules

    # ---- audit ----
    def ledger(self) -> Ledger:
        return Ledger(self.path("ledger", "ledger.jsonl"))

    def rounding_places(self) -> int:
        return int(self.manifest.get("rounding", {}).get("places", 2))

    def field_values(self, max_vals: int = 10) -> dict[str, list[str]]:
        """Distinct values for each categorical field, taken from the real data. Given
        to the LLM when drafting so a rule compares against values that actually exist
        ('Graveyard', not 'graveyard') — a class of silent bug static validation cannot
        catch, because the fact name is correct and only the value is wrong."""
        schema = (self.manifest.get("data", {}) or {}).get("schema", {}) or {}
        try:
            subs = self.subjects()
        except Exception:
            return {}
        out: dict[str, list[str]] = {}
        for k, t in schema.items():
            # booleans are reported separately (see bool_facts) — listing them as
            # values would tempt a string comparison like subject_x == 'True'
            if k == "name" or t != "str":
                continue
            vals = sorted({str(s.get(k)) for s in subs if s.get(k) not in (None, "")})
            if 0 < len(vals) <= max_vals:
                out[f"subject_{k}"] = vals
        return out

    def bool_facts(self) -> list[str]:
        """Boolean-typed facts. Called out explicitly so a rule compares with
        `== True` / `== False`, never against the strings 'True'/'False'."""
        schema = (self.manifest.get("data", {}) or {}).get("schema", {}) or {}
        return sorted(f"subject_{k}" for k, t in schema.items() if t == "bool")

    def golden_case(self) -> dict | None:
        """A hand-verified scenario the rule set MUST reproduce before it can be
        ratified (PRD §8A). This is what catches semantically-wrong-but-valid rules."""
        return self.manifest.get("golden_case")

    def golden_cases(self) -> list[dict]:
        """All golden scenarios. A corpus needs one per bargaining unit (and one per
        date boundary where an amendment takes effect) — a rule no golden exercises is
        an unverified rule."""
        many = self.manifest.get("golden_cases")
        if many:
            return list(many)
        one = self.golden_case()
        return [one] if one else []

    def known_facts(self) -> set[str]:
        """The exact vocabulary a rule may reference: `subject_<field>` for every
        declared data-schema field, plus the query params. This is the contract given
        to the LLM when drafting AND the checklist used to validate before ratify —
        one source of truth, so a drafted rule can't invent a field name."""
        schema = (self.manifest.get("data", {}) or {}).get("schema", {}) or {}
        facts = {f"subject_{k}" for k in schema}
        facts |= {"hours", "date", "date_iso", "holiday_weekday", "effective_base"}
        import os as _os
        ex = self.path("extraction")
        if ex and _os.path.exists(ex):
            try:
                import yaml as _yaml
                with open(ex) as f:
                    cfg = _yaml.safe_load(f) or {}
                for k, v in (cfg.get("output_shape") or {}).items():
                    if not isinstance(v, list):  # scalars are usable as facts
                        facts.add(k)
            except Exception:
                pass
        return facts

    def departments(self) -> list[str]:
        """Departments present in the corpus, excluding the citywide catch-all."""
        return sorted({s.get("department") for s in self.manifest.get("sources", [])
                       if s.get("department") and s.get("department") != "citywide"})

    def docs_for_department(self, department: str | None) -> list[str]:
        """Candidate documents for a department: its own documents plus any citywide
        policy that applies to everyone. None -> the whole corpus."""
        out = []
        for s in self.manifest.get("sources", []):
            d = s.get("department")
            if department is None or d == department or d == "citywide":
                out.append(s["id"])
        return out

    def source_by_id(self, doc_id: str) -> dict | None:
        for s in self.manifest.get("sources", []):
            if s.get("id") == doc_id:
                return s
        return None


def load_case(case_dir: str) -> CaseContext:
    manifest_path = os.path.join(case_dir, "case.yaml")
    with open(manifest_path) as f:
        manifest = yaml.safe_load(f)
    return CaseContext(dir=os.path.abspath(case_dir), manifest=manifest)


def default_case_dir() -> str:
    """The active case is always Santa Cruz — the only corpus this build ships.

    env CASE may still relocate WHERE that case lives (the deploy seeds an ingested
    copy onto a persistent volume and points CASE at its absolute path, so the ledger
    survives redeploys). Resolving against the repo root — not the process cwd — means
    the app works regardless of where uvicorn is launched from."""
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    env = os.environ.get("CASE")
    if env:
        return env if os.path.isabs(env) else os.path.join(repo_root, env)
    return os.path.join(repo_root, "cases", "santacruz")
