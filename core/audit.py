"""Audit assembly + snapshots (PRD 5.6).

`snapshot()` freezes the rule versions used to answer a query, so the number stays
reproducible even after the MOU is renegotiated. `trail()` reconstructs a query's
event chain from the ledger for the chat drill-down and the admin ledger view.
"""
from __future__ import annotations

import json
import os
import time
from typing import Any

from .ruledsl import Rule


def snapshot(snapshots_dir: str, query_id: str, params: dict,
             rules: list[Rule], result: dict) -> str:
    os.makedirs(snapshots_dir, exist_ok=True)
    path = os.path.join(snapshots_dir, f"{query_id}.json")
    payload = {
        "query_id": query_id,
        "frozen_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "params": params,
        "result": result,
        "rule_versions": [
            {"id": r.id, "kind": r.kind, "priority": r.priority,
             "when": r.when, "compute": r.compute, "set": r.set,
             "citation": r.citation.to_dict()}
            for r in rules
        ],
    }
    with open(path, "w") as f:
        json.dump(payload, f, indent=2)
    return path


def trail(ledger, query_id: str) -> list[dict]:
    return ledger.for_query(query_id)


def history(ledger) -> list[dict]:
    """One row per query: prompt + total + timestamp, newest first."""
    prompts: dict[str, dict] = {}
    for ev in ledger.read():
        qid = ev.get("query_id")
        if not qid:
            continue
        row = prompts.setdefault(qid, {"query_id": qid, "prompt": None,
                                       "total": None, "iso": ev.get("iso")})
        if ev["type"] == "chat.prompt":
            row["prompt"] = ev["payload"].get("text")
            row["iso"] = ev.get("iso")
        if ev["type"] == "answer.snapshot":
            row["total"] = ev["payload"].get("total")
    rows = list(prompts.values())
    rows.sort(key=lambda r: r.get("iso") or "", reverse=True)
    return rows
