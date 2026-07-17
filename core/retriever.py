"""Retrieval / document routing (PRD 5.4).

`Retriever` is the interface. `CatalogLLMRetriever` ships now: the LLM ranks over
the catalog (tags + summaries), we search within the top candidates' clauses, and
select — with a confidence gate that asks the user which document when the choice
is ambiguous. It is a routing FALLBACK only; governance (unit + date) is the spine.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from . import llm


@dataclass
class Routing:
    chosen_doc_id: str | None
    candidates: list[dict]           # [{doc_id, score, reason}]
    needs_confirmation: bool
    within_doc_matches: list[dict] = field(default_factory=list)
    reason: str = ""


class Retriever(ABC):
    @abstractmethod
    def route(self, query: str, catalog) -> Routing:
        raise NotImplementedError


class CatalogLLMRetriever(Retriever):
    def __init__(self, confidence_threshold: float = 0.35, margin: float = 0.15):
        self.threshold = confidence_threshold
        self.margin = margin

    def route(self, query: str, catalog, backend=None) -> Routing:
        summaries = catalog.summaries()
        candidates = llm.rank_documents(query, summaries)
        if not candidates:
            return Routing(None, [], True, reason="no documents in catalog")

        top = candidates[0]
        runner = candidates[1] if len(candidates) > 1 else None

        # Confidence gate: too weak, or too close to the next candidate -> ask.
        low = top.get("score", 0) < self.threshold
        ambiguous = runner is not None and \
            (top.get("score", 0) - runner.get("score", 0)) < self.margin
        needs_confirmation = low or ambiguous

        matches = []
        chosen = None if needs_confirmation else top["doc_id"]
        if chosen:
            matches = self._within_doc(query, chosen, catalog, backend)

        reason = ("low confidence" if low else
                  "two documents scored too close" if ambiguous else
                  f"selected {chosen} (score {top.get('score')})")
        return Routing(chosen, candidates, needs_confirmation, matches, reason)

    def _within_doc(self, query: str, doc_id: str, catalog, backend) -> list[dict]:
        """Find the clauses in a document that answer the query. Uses the BM25 search
        index (scales to large PDFs); falls back to keyword overlap if no backend."""
        if backend is not None:
            hits = backend.search(query, doc_ids=[doc_id], k=5)
            if hits:
                return [{"clause": h["clause"], "page": h["page"], "bbox": h["bbox"],
                         "score": h["score"]} for h in hits]
        import re
        q = set(re.findall(r"[a-z]+", query.lower()))
        scored = []
        for c in catalog.clauses(doc_id):
            words = set(re.findall(r"[a-z]+", c.get("text", "").lower()))
            score = len(q & words)
            if score:
                scored.append({"clause": c.get("clause"), "page": c.get("page"),
                               "bbox": c.get("bbox"), "score": score})
        scored.sort(key=lambda m: m["score"], reverse=True)
        return scored[:5]

