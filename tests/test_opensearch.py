"""OpenSearch backend integration test (TICKETS.md D3).

The production-scale backend was `pragma: no cover` and had never been executed.
This exercises index/search/scoping/delete parity with the local backends against a
real cluster. Opt-in: set OPENSEARCH_URL (e.g. http://localhost:9200) —

    docker run -d -p 9200:9200 -e discovery.type=single-node \
        -e DISABLE_SECURITY_PLUGIN=true opensearchproject/opensearch:2

then: OPENSEARCH_URL=http://localhost:9200 pytest tests/test_opensearch.py
"""
import os
import uuid

import pytest

OPENSEARCH_URL = os.environ.get("OPENSEARCH_URL", "")

pytestmark = pytest.mark.skipif(
    not OPENSEARCH_URL, reason="set OPENSEARCH_URL to run against a live cluster")


@pytest.fixture
def backend():
    from core.index import OpenSearchBackend, embeddings_available
    if not embeddings_available():
        pytest.skip("sentence-transformers not installed (kNN needs embeddings)")
    be = OpenSearchBackend([OPENSEARCH_URL],
                           index_name=f"holly-test-{uuid.uuid4().hex[:8]}")
    yield be
    try:
        be._client().indices.delete(be.index_name)
    except Exception:
        pass


CHUNKS_A = [
    {"chunk_id": "0", "clause": "9.1", "page": 1, "bbox": [1, 2, 3, 4],
     "text": "Holiday premium of 2.5x base rate for hours worked."},
    {"chunk_id": "1", "clause": "9.3", "page": 2, "bbox": [5, 6, 7, 8],
     "text": "Bilingual certification premium of 5% of base rate."},
]
CHUNKS_B = [
    {"chunk_id": "0", "clause": "11.3", "page": 4, "bbox": [],
     "text": "Bereavement leave of five days per occurrence."},
]


def test_index_search_scope_delete_parity(backend):
    backend.index("mou_a", CHUNKS_A)
    backend.index("mou_b", CHUNKS_B)

    hits = backend.search("holiday premium rate", k=3)
    assert hits and hits[0]["doc_id"] == "mou_a"
    assert hits[0]["clause"] == "9.1"
    assert hits[0]["page"] == 1 and hits[0]["bbox"] == [1, 2, 3, 4]

    scoped = backend.search("premium", doc_ids=["mou_b"], k=3)
    assert all(h["doc_id"] == "mou_b" for h in scoped)

    backend.delete("mou_a")
    backend._client().indices.refresh(backend.index_name)
    assert all(h["doc_id"] != "mou_a"
               for h in backend.search("holiday premium", k=5))


def test_reindex_replaces_not_duplicates(backend):
    backend.index("mou_a", CHUNKS_A)
    backend.index("mou_a", CHUNKS_A)          # same ids -> upsert, not duplicate
    hits = backend.search("bilingual certification", k=10)
    ids = [(h["doc_id"], h["clause"]) for h in hits if h["doc_id"] == "mou_a"]
    assert len(ids) == len(set(ids))
