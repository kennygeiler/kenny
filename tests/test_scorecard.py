"""Extraction scorecard (OCR_TICKETS.md OCR-3): per-document "how well did we read
this?" stats on /admin/coverage. The arithmetic lives in the pure helper
core.app._extraction_stats so the histogram / empty-page / recovered-page math is
testable on a synthetic catalog entry without the HTTP stack."""
import json
import os
import shutil
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import app as core_app  # noqa: E402
from core.app import _extraction_stats  # noqa: E402


# --------------------------------------------------------------------------- #
# pure math on a synthetic entry
# --------------------------------------------------------------------------- #
def _entry():
    return {
        "doc_id": "synthetic",
        "page_count": 6,
        "pdf_sha256": "deadbeefcafe0123456789abcdef0123456789abcdef0123456789abcdef0123",
        "clauses": [
            # page 1: normal text — one clause omits `kind`, one has it empty; both
            # must count as "text" (the catalog omits kind for normal text).
            {"clause": "1.1", "text": "a", "page": 1},
            {"clause": "1.2", "text": "b", "page": 1, "kind": ""},
            {"clause": "1.3", "text": "c", "page": 1, "kind": "table-row"},
            # page 2: the layout-model rescue — ALL clauses recovered-*.
            {"clause": "", "text": "r1", "page": 2, "kind": "recovered-row"},
            {"clause": "", "text": "r2", "page": 2, "kind": "recovered-text"},
            # page 4: mixed — a recovered row beside normal text is NOT a rescued
            # page; the layout model read most of it fine.
            {"clause": "4.1", "text": "d", "page": 4, "kind": "recovered-row"},
            {"clause": "4.2", "text": "e", "page": 4},
            # page 6: page-level only.
            {"clause": "", "text": "f", "page": 6, "kind": "page-text"},
        ],
    }


def test_kinds_histogram_defaults_missing_kind_to_text():
    stats = _extraction_stats(_entry())
    assert stats["kinds"] == {"text": 3, "table-row": 1, "recovered-row": 2,
                              "recovered-text": 1, "page-text": 1}
    assert sum(stats["kinds"].values()) == len(_entry()["clauses"])


def test_pages_empty_is_sorted_gap_list_within_page_count():
    stats = _extraction_stats(_entry())
    assert stats["pages_empty"] == [3, 5]


def test_recovered_pages_requires_all_clauses_recovered():
    stats = _extraction_stats(_entry())
    assert stats["recovered_pages"] == [2]  # page 4 is mixed, not rescued


def test_sha_short_and_index_error_passthrough():
    stats = _extraction_stats(_entry())
    assert stats["pdf_sha256_short"] == "deadbeefcafe"
    assert stats["index_error"] is None
    bad = dict(_entry(), index_error="opensearch down")
    assert _extraction_stats(bad)["index_error"] == "opensearch down"


def test_degenerate_entry_no_sha_no_clauses_no_pages():
    stats = _extraction_stats({"doc_id": "empty"})
    assert stats == {"kinds": {}, "pages_empty": [], "recovered_pages": [],
                     "pdf_sha256_short": "", "index_error": None,
                     "low_confidence_pages": []}


def test_every_page_empty_when_nothing_extracted():
    stats = _extraction_stats({"doc_id": "scan", "page_count": 3, "clauses": []})
    assert stats["pages_empty"] == [1, 2, 3]
    assert stats["recovered_pages"] == []


# --------------------------------------------------------------------------- #
# the endpoint, against the real corpus
# --------------------------------------------------------------------------- #
@pytest.fixture
def client(tmp_path, monkeypatch):
    src = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "cases", "santacruz")
    case = tmp_path / "santacruz"
    shutil.copytree(src, case)
    monkeypatch.setattr(core_app, "CASE_DIR", str(case))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    from fastapi.testclient import TestClient
    return TestClient(core_app.app), str(case)


def test_coverage_carries_scorecard_fields_for_real_docs(client):
    c, case_dir = client
    with open(os.path.join(case_dir, "catalog.json")) as f:
        catalog = {d["doc_id"]: d for d in json.load(f)["documents"]}
    docs = c.get("/admin/coverage").json()["documents"]
    assert docs
    for d in docs:
        assert isinstance(d["kinds"], dict)
        # the histogram accounts for every clause the doc card counts
        assert sum(d["kinds"].values()) == d["clauses"]
        assert d["kinds"], "an ingested contract must have a non-empty histogram"
        assert isinstance(d["pages_empty"], list)
        assert isinstance(d["recovered_pages"], list)
        assert isinstance(d["pdf_sha256_short"], str)
        assert len(d["pdf_sha256_short"]) <= 12
        # histogram normalisation: "text" for the catalog's kind-less clauses
        entry = catalog[d["doc_id"]]
        kindless = sum(1 for cl in entry["clauses"] if not cl.get("kind"))
        assert d["kinds"].get("text", 0) == kindless
