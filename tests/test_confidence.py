"""Confidence-aware highlighting (OCR_TICKETS.md OCR-7).

What the pinned docling (2.113.0) actually exposes — established by probing real
conversions, not by reading docs: `result.confidence` is a ConfidenceReport with
PER-PAGE scores (`pages[n].ocr_score` / parse / layout / table, plus aggregate
grades). ocr_score is NaN on pages where OCR never ran (digital text layer) and a
float in (0, 1] where it did. There is NO per-word or per-cell confidence in this
version (page.cells is empty once the pipeline finishes), so page granularity is
the finest the catalog can honestly record.

These tests cover the synthetic flow end to end (report -> parse_pdf stamping ->
catalog entry -> /doc/{id}/clauses -> scorecard stats -> shipped UI), plus two
in-band docling runs — cheap here because the OCR models are cached: a generated
scan must carry a real confidence score, and a born-digital PDF must carry none.
"""
import json
import math
import os
import shutil
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import app as core_app  # noqa: E402
from core import ingest  # noqa: E402
from core.app import _extraction_stats  # noqa: E402
from core.catalog import Catalog  # noqa: E402
from core.ingest import (LOW_OCR_CONFIDENCE, _ocr_page_confidence,  # noqa: E402
                         chunk_clauses, parse_pdf)


# --------------------------------------------------------------------------- #
# reading docling's ConfidenceReport
# --------------------------------------------------------------------------- #
class _Scores:
    def __init__(self, ocr_score):
        self.ocr_score = ocr_score


class _Report:
    def __init__(self, pages):
        self.pages = pages


class _Result:
    def __init__(self, confidence):
        self.confidence = confidence


def test_page_confidence_reads_scores_and_drops_nan_pages():
    """NaN means "OCR never ran here" (a digital page) — a different fact from
    "OCR'd perfectly", so the page is omitted, not recorded as 1.0."""
    res = _Result(_Report({1: _Scores(0.8549), 2: _Scores(math.nan),
                           3: _Scores(0.96898)}))
    assert _ocr_page_confidence(res) == {1: 0.8549, 3: 0.969}


def test_page_confidence_survives_missing_or_garbage_report():
    assert _ocr_page_confidence(_Result(None)) == {}
    assert _ocr_page_confidence(object()) == {}
    assert _ocr_page_confidence(_Result(_Report({1: _Scores(None),
                                                 2: _Scores("junk")}))) == {}


# --------------------------------------------------------------------------- #
# parse_pdf stamps clauses from low-scoring pages
# --------------------------------------------------------------------------- #
def test_parse_pdf_stamps_low_confidence_on_clauses_from_low_pages(tmp_path, monkeypatch):
    pdf = tmp_path / "x.pdf"
    pdf.write_bytes(b"%PDF-1.4 stub")
    clauses = [{"clause": "9.1", "text": "a", "page": 1, "bbox": [0, 1, 1, 0]},
               {"clause": "9.2", "text": "b", "page": 2, "bbox": [0, 1, 1, 0]}]
    monkeypatch.setattr(ingest, "_parse_with_docling",
                        lambda p, d: (clauses, {1: 0.84, 2: 0.97}))
    out, _, source, page_conf = parse_pdf(str(pdf), "x")
    assert source == "docling"
    assert page_conf == {1: 0.84, 2: 0.97}
    assert out[0]["low_confidence"] is True          # 0.84 < 0.9
    assert "low_confidence" not in out[1]            # absent, not False — catalog stays lean


def test_threshold_is_doclings_excellent_cutoff():
    """0.9 is docling's own QualityGrade line for EXCELLENT; the probe showed a
    clean scan at ~0.96 with correct text and a mildly blurred one at ~0.85 with
    visibly garbled text, so the cutoff separates real cases, not hypotheticals."""
    assert LOW_OCR_CONFIDENCE == 0.9


def test_ingest_document_records_page_confidence_with_string_keys(tmp_path, monkeypatch):
    """String keys so the entry reads back from catalog.json exactly as written;
    the key is absent entirely for digital docs (parse_pdf returns {})."""
    pdf = tmp_path / "x.pdf"
    pdf.write_bytes(b"%PDF-1.4 stub")
    monkeypatch.setattr(ingest, "parse_pdf",
                        lambda p, d: ([{"clause": "", "text": "t", "page": 1,
                                        "bbox": [], "low_confidence": True}],
                                      "t", "docling", {1: 0.84}))
    cat = Catalog(str(tmp_path / "catalog.json"))
    entry = ingest.ingest_document(str(pdf), "x", "X", {}, cat)
    assert entry["page_confidence"] == {"1": 0.84}
    reloaded = Catalog(str(tmp_path / "catalog.json")).get("x")
    assert reloaded["page_confidence"] == {"1": 0.84}

    monkeypatch.setattr(ingest, "parse_pdf",
                        lambda p, d: ([{"clause": "", "text": "t", "page": 1,
                                        "bbox": []}], "t", "docling", {}))
    entry = ingest.ingest_document(str(pdf), "y", "Y", {}, cat)
    assert "page_confidence" not in entry


def test_chunks_carry_the_flag_for_future_citation_surfaces():
    chunks = chunk_clauses([{"clause": "", "text": "a", "page": 1, "bbox": [],
                             "low_confidence": True},
                            {"clause": "", "text": "b", "page": 2, "bbox": []}])
    assert chunks[0]["low_confidence"] is True
    assert "low_confidence" not in chunks[1]


# --------------------------------------------------------------------------- #
# scorecard stats (OCR-3 strip gains the amber signal)
# --------------------------------------------------------------------------- #
def test_extraction_stats_lists_low_pages_from_string_keyed_entry():
    stats = _extraction_stats({"doc_id": "d", "page_count": 3, "clauses": [],
                               "page_confidence": {"3": 0.42, "1": 0.97, "2": 0.85}})
    assert stats["low_confidence_pages"] == [2, 3]


def test_extraction_stats_low_pages_empty_without_confidence_data():
    """Catalogs ingested before confidence capture (the shipped santacruz corpus)
    and digital documents both lack page_confidence — no signal, no badge."""
    assert _extraction_stats({"doc_id": "d"})["low_confidence_pages"] == []


# --------------------------------------------------------------------------- #
# the endpoint passes the data through to the X-ray/Compare client
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


def _mark_first_doc_low(case_dir):
    """Stamp low confidence on page 1 of the first catalog doc, as ingest would."""
    path = os.path.join(case_dir, "catalog.json")
    with open(path) as f:
        data = json.load(f)
    doc = data["documents"][0]
    doc["page_confidence"] = {"1": 0.42}
    for c in doc["clauses"]:
        if c.get("page") == 1:
            c["low_confidence"] = True
    with open(path, "w") as f:
        json.dump(data, f)
    return doc["doc_id"], data["documents"][1]["doc_id"]


def test_clauses_endpoint_carries_confidence(client):
    c, case_dir = client
    marked, unmarked = _mark_first_doc_low(case_dir)
    body = c.get(f"/doc/{marked}/clauses", params={"page": 1}).json()
    assert body["ocr_confidence"] == 0.42
    assert body["low_confidence"] is True
    assert body["clauses"] and all(cl["low_confidence"] is True
                                   for cl in body["clauses"])
    # a page with no recorded score reports null, never a guess
    if body["page_count"] > 1:
        body2 = c.get(f"/doc/{marked}/clauses", params={"page": 2}).json()
        assert body2["ocr_confidence"] is None and body2["low_confidence"] is False


def test_digital_documents_show_nothing(client):
    """The acceptance line of OCR-7: a document whose text was never OCR'd must
    carry no confidence markings anywhere."""
    c, case_dir = client
    _, unmarked = _mark_first_doc_low(case_dir)
    body = c.get(f"/doc/{unmarked}/clauses", params={"page": 1}).json()
    assert body["ocr_confidence"] is None
    assert body["low_confidence"] is False
    assert all(cl["low_confidence"] is False for cl in body["clauses"])
    docs = {d["doc_id"]: d for d in c.get("/admin/coverage").json()["documents"]}
    assert docs[unmarked]["low_confidence_pages"] == []


def test_coverage_carries_low_confidence_pages(client):
    c, case_dir = client
    marked, _ = _mark_first_doc_low(case_dir)
    docs = {d["doc_id"]: d for d in c.get("/admin/coverage").json()["documents"]}
    assert docs[marked]["low_confidence_pages"] == [1]


def test_admin_page_ships_the_confidence_surfaces(client):
    """The client side is frontend-only over the OCR-1 endpoint, so the meaningful
    server assertion is that the admin page ships it: the amber scorecard badge,
    the box/row tint class, the tooltip wording, the legend entry, and a bumped
    stylesheet version (stale cached CSS would drop the amber signal silently)."""
    c, _ = client
    html = c.get("/admin").text
    assert "score-lowconf" in html, "scorecard + upload card badge"
    assert "low OCR confidence on this page" in html, "X-ray tooltip / count strip"
    assert "xlow" in html, "box, Compare row and table-row tint class"
    assert "styles.css?v=12" in html
    css = c.get("/static/styles.css").text
    assert ".score-lowconf" in css and ".xlow" in css


# --------------------------------------------------------------------------- #
# in-band docling: the real ConfidenceReport, on cached models
# --------------------------------------------------------------------------- #
@pytest.fixture
def scan_pdf(tmp_path):
    """Image-only PDF — no text layer, so docling must OCR it (same recipe as
    tests/test_ingest.py)."""
    from PIL import Image, ImageDraw
    img = Image.new("RGB", (612, 792), "white")
    ImageDraw.Draw(img).text((72, 72), "9.1 Holiday Premium Pay 2.5x", fill="black")
    path = str(tmp_path / "scan.pdf")
    img.save(path, "PDF")
    return path


@pytest.fixture
def digital_pdf(tmp_path):
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas
    path = str(tmp_path / "mini.pdf")
    c = canvas.Canvas(path, pagesize=letter)
    c.drawString(72, 700, "9.1 Holiday Premium Pay: 2.5x base for hours worked.")
    c.showPage()
    c.save()
    return path


def test_docling_reports_a_real_ocr_score_for_a_scan(scan_pdf):
    clauses, _, source, page_conf = parse_pdf(scan_pdf, "scan")
    assert source == "docling", "docling must be importable in this venv"
    assert set(page_conf) == {1}
    assert 0 < page_conf[1] <= 1
    # the stamped flags must agree with the recorded scores — one threshold, applied once
    for c in clauses:
        assert bool(c.get("low_confidence")) == \
            (page_conf[c["page"]] < LOW_OCR_CONFIDENCE)


def test_docling_reports_no_ocr_score_for_digital_text(digital_pdf):
    clauses, _, source, page_conf = parse_pdf(digital_pdf, "mini")
    assert source == "docling"
    assert page_conf == {}, "ocr_score is NaN when OCR never ran — must be omitted"
    assert all("low_confidence" not in c for c in clauses)
