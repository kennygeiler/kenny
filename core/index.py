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
import threading
from abc import ABC, abstractmethod
from collections import Counter

try:
    import fcntl  # POSIX advisory locking, shared with the ingest worker
except Exception:  # pragma: no cover - non-POSIX
    fcntl = None

_MODEL = None
_MODEL_NAME = "all-MiniLM-L6-v2"

# Section numbers survive as single tokens (TICKETS.md D1): "§9.2" used to fragment
# into `9`,`2` — two high-df digits — degrading exactly the exact-token queries BM25
# exists to serve. "\d+(?:\.\d+)+" also keeps "$53.00" -> "53.00" whole, so a figure
# query matches the cell that prints it.
_TOKEN_RE = re.compile(r"\d+(?:\.\d+)+|[a-z0-9]+")

# Function words carry no ranking signal in contract text and inflate document length.
_STOPWORDS = frozenset(
    "the a an of for and or to in on at by is are be been was were shall will "
    "with as this that these those any all such".split())


def _stem(tok: str) -> str:
    """Conservative plural folding: 'days' matches 'day', 'rates' matches 'rate'.
    Applied identically to queries and documents, so an over-strip is harmless."""
    if len(tok) > 3 and tok.endswith("s") and not tok.endswith(("ss", "us", "is")):
        return tok[:-1]
    return tok


def tokenize(text: str) -> list[str]:
    return [_stem(t) for t in _TOKEN_RE.findall((text or "").lower())
            if t not in _STOPWORDS]


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


# Okapi BM25 parameters, standard values (Robertson & Walker). k1 controls term-
# frequency saturation (1.2–2.0 is the customary range; the old inline 2.5 was outside
# it and unexplained), b the strength of document-length normalization.
BM25_K1 = 1.5
BM25_B = 0.75


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
            score += idf * (tf[t] * (BM25_K1 + 1)) / (
                tf[t] + BM25_K1 * (1 - BM25_B + BM25_B * dl / avgdl))
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
    """In-process BM25 over a per-case JSONL of chunks. No external services.

    Queries run against an in-memory cache invalidated by the file's (mtime, size)
    signature (TICKETS.md D2) — the old implementation re-read and re-tokenized the
    whole JSONL on EVERY query. Tokens are computed at load time, never persisted, so
    a tokenizer improvement applies to an already-baked index instead of silently
    mismatching it. Writes go to a temp file + atomic rename under a file lock, so a
    crash mid-index leaves the previous index, not a truncated one.
    """

    def __init__(self, path: str):
        self.path = path
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        self._mem_lock = threading.Lock()
        self._cache: list[dict] | None = None
        self._cache_sig: tuple | None = None

    def _sig(self) -> tuple | None:
        try:
            st = os.stat(self.path)
            return (st.st_mtime_ns, st.st_size)
        except OSError:
            return None

    def _load(self) -> list[dict]:
        if not os.path.exists(self.path):
            return []
        with open(self.path) as f:
            return [json.loads(l) for l in f if l.strip()]

    def _save(self, chunks: list[dict]) -> None:
        tmp = self.path + ".tmp"
        with open(tmp, "w") as f:
            if fcntl is not None:
                fcntl.flock(f.fileno(), fcntl.LOCK_EX)
            for c in chunks:
                c = {k: v for k, v in c.items() if k != "_tokens"}
                f.write(json.dumps(c) + "\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, self.path)
        with self._mem_lock:
            self._cache = None          # next query re-reads the new file

    def _prepare(self, chunks: list[dict]) -> list[dict]:
        out = []
        for c in chunks:
            c = dict(c)
            c["_tokens"] = tokenize(c.get("text", ""))
            # A clause's own number lives in metadata, not its text — index it too, or
            # "what does §12.2 say" can never rank clause 12.2 (TICKETS.md D1).
            if c.get("clause"):
                c["_tokens"] += tokenize(str(c["clause"]))
            out.append(c)
        return out

    def _chunks(self) -> list[dict]:
        with self._mem_lock:
            sig = self._sig()
            if self._cache is None or sig != self._cache_sig:
                self._cache = self._prepare(self._load())
                self._cache_sig = sig
            return self._cache

    def index(self, doc_id: str, chunks: list[dict]) -> None:
        existing = [c for c in self._load() if c.get("doc_id") != doc_id]
        for c in self._prepare(chunks):
            c["doc_id"] = doc_id
            existing.append(c)
        self._save(existing)

    def delete(self, doc_id: str) -> None:
        self._save([c for c in self._load() if c.get("doc_id") != doc_id])

    def _scope(self, doc_ids):
        chunks = self._chunks()
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
