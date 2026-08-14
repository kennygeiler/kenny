"""Document catalog — the searchable index built at ingestion (PRD 5.1).

One entry per source document: department, tags, summary, page count, plus the
extracted clauses (with bounding boxes) used for within-document search. Stored as
catalog.json in the case bundle.
"""
from __future__ import annotations

import json
import os
from typing import Any


class Catalog:
    def __init__(self, path: str):
        self.path = path
        self._docs: dict[str, dict] = {}
        self._load()

    def _load(self) -> None:
        if os.path.exists(self.path):
            with open(self.path) as f:
                data = json.load(f)
            self._docs = {d["doc_id"]: d for d in data.get("documents", [])}

    def save(self) -> None:
        """Atomic write: temp file + rename. The catalog is hundreds of KB and a crash
        mid-write must leave the previous version, not a truncated JSON file."""
        os.makedirs(os.path.dirname(os.path.abspath(self.path)), exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w") as f:
            json.dump({"documents": list(self._docs.values())}, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, self.path)

    def upsert(self, entry: dict) -> None:
        self._docs[entry["doc_id"]] = entry
        self.save()

    def remove(self, doc_id: str) -> bool:
        """Drop a document from the catalog (reconciliation: a doc removed from
        case.yaml must not linger in the catalog forever). Returns whether it existed."""
        existed = self._docs.pop(doc_id, None) is not None
        if existed:
            self.save()
        return existed

    def get(self, doc_id: str) -> dict | None:
        return self._docs.get(doc_id)

    def documents(self) -> list[dict]:
        return list(self._docs.values())

    def summaries(self) -> list[dict]:
        """Lightweight view for retrieval ranking (no clause bodies)."""
        return [
            {"doc_id": d["doc_id"], "title": d.get("title", ""),
             "department": d.get("department", ""), "tags": d.get("tags", []),
             "summary": d.get("summary", "")}
            for d in self._docs.values()
        ]

    def clauses(self, doc_id: str) -> list[dict]:
        d = self._docs.get(doc_id) or {}
        return d.get("clauses", [])
