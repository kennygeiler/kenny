"""Search-index tests (PRD §8B) — BM25 retrieval + chunking for large documents."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.index import LocalBM25Backend, tokenize  # noqa: E402
from core.ingest import chunk_clauses  # noqa: E402


def _clauses():
    return [
        {"clause": "12.1", "page": 1, "bbox": [1, 2, 3, 4],
         "text": "Graveyard shift differential of 8% applied to base hourly rate."},
        {"clause": "12.2", "page": 1, "bbox": [5, 6, 7, 8],
         "text": "Field Training Officer FTO flat premium of $250 per pay period."},
        {"clause": "12.3", "page": 2, "bbox": [9, 10, 11, 12],
         "text": "Stacking limitation: differential percentages never applied to the FTO premium."},
    ]


def test_bm25_ranks_relevant_clause_first(tmp_path):
    be = LocalBM25Backend(str(tmp_path / "idx.jsonl"))
    be.index("poa", chunk_clauses(_clauses()))
    hits = be.search("what is the graveyard shift differential", doc_ids=["poa"], k=3)
    assert hits, "expected at least one hit"
    assert hits[0]["clause"] == "12.1"
    assert hits[0]["page"] == 1 and hits[0]["bbox"] == [1, 2, 3, 4]  # citeable


def test_bm25_finds_fto(tmp_path):
    be = LocalBM25Backend(str(tmp_path / "idx.jsonl"))
    be.index("poa", chunk_clauses(_clauses()))
    hits = be.search("FTO training officer premium", doc_ids=["poa"], k=1)
    assert hits[0]["clause"] == "12.2"


def test_doc_scoping(tmp_path):
    be = LocalBM25Backend(str(tmp_path / "idx.jsonl"))
    be.index("a", chunk_clauses(_clauses()))
    be.index("b", [{"chunk_id": "0", "clause": "1", "page": 1, "bbox": [],
                    "text": "unrelated uniform allowance"}])
    hits = be.search("graveyard differential", doc_ids=["b"], k=5)
    assert hits == []  # scoped to doc b, which has no graveyard content


def test_delete_reindex(tmp_path):
    be = LocalBM25Backend(str(tmp_path / "idx.jsonl"))
    be.index("poa", chunk_clauses(_clauses()))
    be.delete("poa")
    assert be.search("graveyard", doc_ids=["poa"], k=5) == []


def test_long_clause_is_windowed():
    big = [{"clause": "X", "page": 1, "bbox": [], "text": "word " * 800}]  # ~4000 chars
    chunks = chunk_clauses(big, max_chars=1000, overlap=100)
    assert len(chunks) > 1
    assert all(c["clause"] == "X" and c["page"] == 1 for c in chunks)  # metadata preserved


def test_tokenizer_keeps_section_numbers_whole():
    """'§9.2' used to fragment into `9`,`2` — two high-df digit tokens — degrading
    exactly the exact-token query BM25 exists for (TICKETS.md D1)."""
    assert "9.2" in tokenize("see §9.2 for weekend shifts")
    assert "9.2" in tokenize("Section 9.2 applies")
    assert "53.00" in tokenize("Sergeant | $53.00")
    # singular/plural fold together; stopwords drop
    assert tokenize("the days of leave") == ["day", "leave"]
    assert tokenize("rate") == tokenize("rates")


def test_section_number_query_ranks_its_clause_first(tmp_path):
    be = LocalBM25Backend(str(tmp_path / "idx.jsonl"))
    be.index("poa", chunk_clauses(_clauses()))
    hits = be.search("what does §12.2 say", doc_ids=["poa"], k=3)
    assert hits and hits[0]["clause"] == "12.2"


def test_query_cache_sees_writes_and_no_stale_tokens(tmp_path):
    """Queries run from an in-memory cache (TICKETS.md D2); a subsequent index() must
    invalidate it, and tokens are derived at load time — never trusted from disk — so
    a tokenizer change can't silently mismatch a baked index."""
    import json
    be = LocalBM25Backend(str(tmp_path / "idx.jsonl"))
    be.index("a", chunk_clauses(_clauses()))
    assert be.search("graveyard differential", doc_ids=["a"], k=1)
    be.index("b", [{"chunk_id": "0", "clause": "7.7", "page": 1, "bbox": [],
                    "text": "canine handler stipend"}])
    assert be.search("canine stipend", doc_ids=["b"], k=1)      # cache refreshed
    with open(be.path) as f:
        stored = [json.loads(l) for l in f if l.strip()]
    assert all("_tokens" not in c for c in stored)              # tokens never persisted
    assert not os.path.exists(be.path + ".tmp")                 # atomic write cleaned up


def test_table_rows_become_searchable_lines():
    """Tables carry the money in an MOU (salary schedules, accrual charts). docling
    returns them as their own item type with no `.text`; a text-only reader drops them
    silently. Each row must become a line that carries its headers."""
    import pandas as pd
    from core.ingest import _table_rows

    class FakeTable:
        def export_to_dataframe(self):
            return pd.DataFrame([["Sergeant", "$53.00", "$58.00"]],
                                columns=["Classification", "Step A", "Step C"])

    rows = _table_rows(FakeTable(), caption="Appendix A — Police Rates")
    body = " ".join(rows)
    assert "Sergeant" in body and "$58.00" in body and "Step C" in body
    assert "Appendix A" in body   # caption travels with the row
