"""Search index for large-document retrieval + policy Q&A (PRD §8B).

Backends behind one `SearchBackend` interface:
  - `LocalBM25Backend`   — dependency-free BM25 (always available).
  - `LocalHybridBackend` — BM25 + local sentence-transformer embeddings, fused with
                           Reciprocal Rank Fusion. Semantic + keyword, no cluster. Default
                           when sentence-transformers is installed.
  - `OpenSearchBackend`  — production: hybrid BM25 + kNN over OpenSearch/Elasticsearch.

Every chunk carries citation metadata (doc_id, clause, page, bbox) so a hit is directly
citeable back to the source PDF — the same defensibility guarantee as costing answers.
"""
from __future__ import annotations

import json
import math
import os
import re
from abc import ABC, abstractmethod
from collections import Counter

_MODEL = None
_MODEL_NAME = "all-MiniLM-L6-v2"


def tokenize(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", (text or "").lower())


def embedder():
    """Lazily load the local embedding model (first call downloads ~80MB)."""
    global _MODEL
    if _MODEL is None:
        from sentence_transformers import SentenceTransformer
        _MODEL = SentenceTransformer(_MODEL_NAME)
    return _MODEL


def embeddings_available() -> bool:
    try:
        import sentence_transformers  # noqa: F401
        return True
    except Exception:
        return False


def bm25_scores(query: str, chunks: list[dict]) -> dict[int, float]:
    q_terms = tokenize(query)
    N = len(chunks)
    if not N:
        return {}
    avgdl = sum(len(c.get("_tokens", [])) for c in chunks) / N
    df = Counter()
    for c in chunks:
        for t in set(c.get("_tokens", [])):
            df[t] += 1
    out: dict[int, float] = {}
    for i, c in enumerate(chunks):
        toks = c.get("_tokens", [])
        tf = Counter(toks)
        dl = len(toks) or 1
        score = 0.0
        for t in q_terms:
            if t not in tf:
                continue
            idf = math.log(1 + (N - df[t] + 0.5) / (df[t] + 0.5))
            score += idf * (tf[t] * 2.5) / (tf[t] + 1.5 * (1 - 0.75 + 0.75 * dl / avgdl))
        if score > 0:
            out[i] = score
    return out


class SearchBackend(ABC):
    @abstractmethod
    def index(self, doc_id: str, chunks: list[dict]) -> None: ...
    @abstractmethod
    def search(self, query: str, doc_ids: list[str] | None = None, k: int = 5) -> list[dict]: ...
    @abstractmethod
    def delete(self, doc_id: str) -> None: ...


class LocalBM25Backend(SearchBackend):
    """In-process BM25 over a per-case JSONL of chunks. No external services."""

    def __init__(self, path: str):
        self.path = path
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)

    def _load(self) -> list[dict]:
        if not os.path.exists(self.path):
            return []
        with open(self.path) as f:
            return [json.loads(l) for l in f if l.strip()]

    def _save(self, chunks: list[dict]) -> None:
        with open(self.path, "w") as f:
            for c in chunks:
                f.write(json.dumps(c) + "\n")

    def _prepare(self, chunks: list[dict]) -> list[dict]:
        out = []
        for c in chunks:
            c = dict(c)
            c["_tokens"] = tokenize(c.get("text", ""))
            out.append(c)
        return out

    def index(self, doc_id: str, chunks: list[dict]) -> None:
        existing = [c for c in self._load() if c.get("doc_id") != doc_id]
        for c in self._prepare(chunks):
            c["doc_id"] = doc_id
            existing.append(c)
        self._save(existing)

    def delete(self, doc_id: str) -> None:
        self._save([c for c in self._load() if c.get("doc_id") != doc_id])

    def _scope(self, doc_ids):
        chunks = self._load()
        if doc_ids:
            chunks = [c for c in chunks if c.get("doc_id") in doc_ids]
        return chunks

    def _hit(self, c: dict, score: float) -> dict:
        return {"doc_id": c.get("doc_id"), "clause": c.get("clause"), "page": c.get("page"),
                "bbox": c.get("bbox"), "score": round(float(score), 4),
                "text": c.get("text", "")[:400]}

    def search(self, query: str, doc_ids=None, k: int = 5) -> list[dict]:
        chunks = self._scope(doc_ids)
        scores = bm25_scores(query, chunks)
        ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)[:k]
        return [self._hit(chunks[i], s) for i, s in ranked]


class LocalHybridBackend(LocalBM25Backend):
    """BM25 + local embeddings, fused with Reciprocal Rank Fusion (RRF). Semantic
    recall (finds 'graveyard' from 'night shift') plus exact keyword precision."""

    RRF_K = 60

    def index(self, doc_id: str, chunks: list[dict]) -> None:
        prepared = self._prepare(chunks)
        texts = [c.get("text", "") for c in prepared]
        if texts:
            vecs = embedder().encode(texts, normalize_embeddings=True)
            for c, v in zip(prepared, vecs):
                c["_vec"] = [round(float(x), 5) for x in v]
        existing = [c for c in self._load() if c.get("doc_id") != doc_id]
        for c in prepared:
            c["doc_id"] = doc_id
            existing.append(c)
        self._save(existing)

    def search(self, query: str, doc_ids=None, k: int = 5) -> list[dict]:
        chunks = self._scope(doc_ids)
        if not chunks:
            return []
        bm = bm25_scores(query, chunks)
        bm_ranked = [i for i, _ in sorted(bm.items(), key=lambda kv: kv[1], reverse=True)]

        vec_ranked = []
        if any("_vec" in c for c in chunks):
            import numpy as np
            qv = embedder().encode([query], normalize_embeddings=True)[0]
            sims = []
            for i, c in enumerate(chunks):
                v = c.get("_vec")
                if v is not None:
                    sims.append((i, float(np.dot(qv, np.array(v)))))
            vec_ranked = [i for i, _ in sorted(sims, key=lambda kv: kv[1], reverse=True)]

        # Reciprocal Rank Fusion across the two rankings.
        fused: dict[int, float] = {}
        for rank, i in enumerate(bm_ranked):
            fused[i] = fused.get(i, 0.0) + 1.0 / (self.RRF_K + rank)
        for rank, i in enumerate(vec_ranked):
            fused[i] = fused.get(i, 0.0) + 1.0 / (self.RRF_K + rank)
        ranked = sorted(fused.items(), key=lambda kv: kv[1], reverse=True)[:k]
        return [self._hit(chunks[i], s) for i, s in ranked]


class OpenSearchBackend(SearchBackend):  # pragma: no cover - needs a running cluster
    """Production: hybrid BM25 (`match`) + kNN vector search over OpenSearch/Elastic.
    Same interface. Requires a reachable cluster (opensearch-py) + embeddings."""

    def __init__(self, hosts, index_name="holly", dim=384):
        self.hosts = hosts
        self.index_name = index_name
        self.dim = dim

    def _client(self):
        from opensearchpy import OpenSearch
        c = OpenSearch(self.hosts)
        if not c.indices.exists(self.index_name):
            c.indices.create(self.index_name, body={
                "settings": {"index.knn": True},
                "mappings": {"properties": {
                    "doc_id": {"type": "keyword"}, "clause": {"type": "keyword"},
                    "page": {"type": "integer"}, "bbox": {"type": "float"},
                    "text": {"type": "text"},
                    "vec": {"type": "knn_vector", "dimension": self.dim}}}})
        return c

    def index(self, doc_id: str, chunks: list[dict]) -> None:
        from opensearchpy.helpers import bulk
        c = self._client()
        vecs = embedder().encode([ch.get("text", "") for ch in chunks],
                                 normalize_embeddings=True)
        actions = [{"_index": self.index_name,
                    "_id": f"{doc_id}:{ch.get('chunk_id')}",
                    "_source": {"doc_id": doc_id, "clause": ch.get("clause"),
                                "page": ch.get("page"), "bbox": ch.get("bbox"),
                                "text": ch.get("text", ""), "vec": v.tolist()}}
                   for ch, v in zip(chunks, vecs)]
        bulk(c, actions)
        c.indices.refresh(self.index_name)

    def search(self, query: str, doc_ids=None, k: int = 5) -> list[dict]:
        c = self._client()
        qv = embedder().encode([query], normalize_embeddings=True)[0].tolist()
        flt = [{"terms": {"doc_id": doc_ids}}] if doc_ids else []
        body = {"size": k, "query": {"bool": {"filter": flt, "should": [
            {"match": {"text": query}},
            {"knn": {"vec": {"vector": qv, "k": k}}}]}}}
        res = c.search(index=self.index_name, body=body)
        hits = []
        for h in res["hits"]["hits"]:
            s = h["_source"]
            hits.append({"doc_id": s["doc_id"], "clause": s["clause"], "page": s["page"],
                         "bbox": s["bbox"], "score": h["_score"], "text": s["text"][:400]})
        return hits

    def delete(self, doc_id: str) -> None:
        c = self._client()
        c.delete_by_query(self.index_name, body={"query": {"term": {"doc_id": doc_id}}})


def make_backend(case) -> SearchBackend:
    cfg = (case.manifest.get("search") or {})
    kind = os.environ.get("SEARCH_BACKEND", cfg.get("backend", "auto"))
    if kind == "opensearch":
        return OpenSearchBackend(cfg.get("hosts", ["http://localhost:9200"]),
                                 cfg.get("index", "holly"))
    path = case.path("search_index", "search_index.jsonl")
    if kind in ("auto", "hybrid") and embeddings_available():
        return LocalHybridBackend(path)
    return LocalBM25Backend(path)
