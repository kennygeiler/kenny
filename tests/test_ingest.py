"""Parse-path, provenance-chain and revalidation tests (TICKETS.md F1 / A1 / A3 / A4).

The ingest slice used to ride entirely on the committed catalog.json — no test ever
ran a PDF through parse_pdf, the sidecar loader, or the coordinate math. These do,
against a small real PDF generated with reportlab (already a dependency).
"""
import json
import os

import pytest
import yaml

from core import ingest
from core.catalog import Catalog
from core.ingest import (_clause_number, _load_sidecar, _normalize_bbox, _union_bbox,
                         parse_pdf, sha256_file)


# --------------------------------------------------------------------------- #
# fixture PDF
# --------------------------------------------------------------------------- #
@pytest.fixture
def mini_pdf(tmp_path):
    """Two-page text PDF with section-numbered clauses."""
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas
    path = str(tmp_path / "mini_mou.pdf")
    c = canvas.Canvas(path, pagesize=letter)
    c.drawString(72, 700, "MEMORANDUM OF UNDERSTANDING")
    c.drawString(72, 660, "9.1 Holiday Premium Pay: 2.5x base for hours worked.")
    c.drawString(72, 620, "9.3 Bilingual premium: 5% of base rate.")
    c.showPage()
    c.drawString(72, 700, "11.3 Bereavement leave: 5 days per occurrence.")
    c.showPage()
    c.save()
    return path


@pytest.fixture
def scan_pdf(tmp_path):
    """Image-only PDF — no text layer, the honest 'scan' case."""
    from PIL import Image, ImageDraw
    img = Image.new("RGB", (612, 792), "white")
    ImageDraw.Draw(img).text((72, 72), "9.1 Holiday Premium Pay 2.5x", fill="black")
    path = str(tmp_path / "scan.pdf")
    img.save(path, "PDF")
    return path


# --------------------------------------------------------------------------- #
# parse tiers
# --------------------------------------------------------------------------- #
def test_missing_file_is_a_hard_error(tmp_path):
    with pytest.raises(FileNotFoundError):
        parse_pdf(str(tmp_path / "nope.pdf"), "nope")


def test_raw_text_tier_extracts_pages(mini_pdf, monkeypatch):
    # Force past docling and the sidecar so tier 3 is what runs.
    monkeypatch.setattr(ingest, "_parse_with_docling", lambda p, d: None)
    clauses, text, source, page_conf = parse_pdf(mini_pdf, "mini")
    assert source == "raw-text-fallback"
    assert page_conf == {}                        # no OCR ran, no confidence to report
    assert len(clauses) == 2                      # one per page
    assert clauses[0]["page"] == 1 and clauses[1]["page"] == 2
    assert "Holiday Premium" in text
    assert clauses[0]["clause"] == "9.1"          # first section on the page
    assert all(len(c["bbox"]) == 4 for c in clauses)


def test_scan_pdf_yields_empty_not_garbage(scan_pdf, monkeypatch):
    monkeypatch.setattr(ingest, "_parse_with_docling", lambda p, d: None)
    clauses, text, source, page_conf = parse_pdf(scan_pdf, "scan")
    assert source == "empty"
    assert clauses == [] and text == "" and page_conf == {}


@pytest.mark.skipif(not os.environ.get("KENNY_TEST_OCR"),
                    reason="set KENNY_TEST_OCR=1 to run the in-band docling OCR tier "
                           "(downloads models, takes minutes)")
def test_docling_ocr_reads_a_scan_in_band(scan_pdf):
    clauses, text, source, _ = parse_pdf(scan_pdf, "scan")
    assert source == "docling"
    assert "Holiday" in text


# --------------------------------------------------------------------------- #
# sidecar binding (A4)
# --------------------------------------------------------------------------- #
def _sidecar_for(pdf, tmp_clauses, sha=None):
    payload = {"clauses": tmp_clauses,
               "pdf_sha256": sha if sha is not None else sha256_file(pdf)}
    path = os.path.splitext(pdf)[0] + ".clauses.json"
    with open(path, "w") as f:
        json.dump(payload, f)
    return path


def test_bound_sidecar_is_used(mini_pdf, monkeypatch):
    monkeypatch.setattr(ingest, "_parse_with_docling", lambda p, d: None)
    _sidecar_for(mini_pdf, [{"clause": "9.1", "text": "sidecar text", "page": 1,
                             "bbox": [1, 2, 3, 4]}])
    clauses, _, source, _ = parse_pdf(mini_pdf, "mini")
    assert source == "sidecar"
    assert clauses[0]["text"] == "sidecar text"


def test_unbound_sidecar_is_ignored(mini_pdf, monkeypatch):
    """A sidecar with a wrong (or missing) hash is untrusted provenance — the parse
    must fall through to raw text rather than adopt it."""
    monkeypatch.setattr(ingest, "_parse_with_docling", lambda p, d: None)
    _sidecar_for(mini_pdf, [{"clause": "6.6", "text": "stale", "page": 9}],
                 sha="0" * 64)
    clauses, _, source, _ = parse_pdf(mini_pdf, "mini")
    assert source == "raw-text-fallback"
    assert all(c["text"] != "stale" for c in clauses)
    assert _load_sidecar(mini_pdf) is None


# --------------------------------------------------------------------------- #
# clause numbers (F2)
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("text,expected", [
    ("9.1 Holiday Premium Pay", "9.1"),
    ("Section 12.3 Grievance deadline", "12.3"),
    ("§9.2 Weekend shift exception", "9.2"),
    ("(9.3) Bilingual premium", "9.3"),
    ("Sergeant | $53.00 | $55.65 | $58.00", ""),          # dollar figure, not a section
    ("Salary Schedule — Step 2 53.00 hourly", ""),        # figure after a digit token
    ("Appendix A — Classification: Sergeant", ""),
    ("Total due 1660.80 for the shift", ""),              # >2-digit money never matches
])
def test_clause_number(text, expected):
    assert _clause_number(text) == expected


# --------------------------------------------------------------------------- #
# coordinate origin (A6)
# --------------------------------------------------------------------------- #
class _BB:
    def __init__(self, l, t, r, b, origin):
        self.l, self.t, self.r, self.b = l, t, r, b
        self.coord_origin = origin


def test_bottomleft_bbox_passes_through():
    assert _normalize_bbox(_BB(10, 700, 200, 680, "CoordOrigin.BOTTOMLEFT"),
                           792) == [10, 700, 200, 680]


def test_topleft_bbox_is_flipped():
    """A docling upgrade emitting TOPLEFT boxes must not silently mirror highlights:
    a box 92pt from the top of a 792pt page is 700pt from the bottom."""
    assert _normalize_bbox(_BB(10, 92, 200, 112, "CoordOrigin.TOPLEFT"),
                           792) == [10, 700, 200, 680]


def test_union_bbox_wraps_a_row():
    assert _union_bbox([[10, 700, 50, 680], [60, 702, 120, 681]]) == [10, 702, 120, 680]


# --------------------------------------------------------------------------- #
# ingest_document: hashing + atomic catalog (A1 / F4)
# --------------------------------------------------------------------------- #
def test_ingest_document_records_pdf_sha256(mini_pdf, tmp_path, monkeypatch):
    monkeypatch.setattr(ingest, "_parse_with_docling", lambda p, d: None)
    cat = Catalog(str(tmp_path / "catalog.json"))
    entry = ingest.ingest_document(mini_pdf, "mini", "Mini MOU", {}, cat)
    assert entry["pdf_sha256"] == sha256_file(mini_pdf)
    assert cat.get("mini")["pdf_sha256"] == entry["pdf_sha256"]


def test_catalog_remove_and_atomicity(tmp_path):
    cat = Catalog(str(tmp_path / "catalog.json"))
    cat.upsert({"doc_id": "a", "clauses": []})
    cat.upsert({"doc_id": "b", "clauses": []})
    assert cat.remove("a") is True
    assert cat.remove("a") is False
    reloaded = Catalog(str(tmp_path / "catalog.json"))
    assert [d["doc_id"] for d in reloaded.documents()] == ["b"]
    assert not os.path.exists(str(tmp_path / "catalog.json") + ".tmp")


# --------------------------------------------------------------------------- #
# citation revalidation after re-ingest (A3)
# --------------------------------------------------------------------------- #
def _mini_case(tmp_path, rules):
    case_dir = tmp_path / "case"
    (case_dir / "rules").mkdir(parents=True)
    with open(case_dir / "case.yaml", "w") as f:
        yaml.safe_dump({"name": "t", "rules": "rules/rules_ratified.json",
                        "ledger": "ledger.jsonl", "sources": []}, f)
    with open(case_dir / "rules" / "rules_ratified.json", "w") as f:
        json.dump({"rules": rules}, f)
    from core.caseio import load_case
    return load_case(str(case_dir))


def _rule(sha="", clause="9.1", page=1):
    return {"id": "r1", "kind": "selector", "when": "True", "compute": "1",
            "status": "ratified", "approver": "t",
            "citation": {"doc_id": "d1", "clause": clause, "page": page,
                         "doc_sha256": sha}}


class _FakeCat:
    def __init__(self, entry):
        self._e = entry
    def get(self, doc_id):
        return self._e if self._e and self._e["doc_id"] == doc_id else None


def test_revalidation_marks_sha_mismatch_stale(tmp_path):
    from core.app import _raw_ratified, _revalidate_citations
    case = _mini_case(tmp_path, [_rule(sha="a" * 64)])
    cat = _FakeCat({"doc_id": "d1", "pdf_sha256": "b" * 64,
                    "clauses": [{"clause": "9.1", "page": 1}]})
    stale = _revalidate_citations(case, cat, ["d1"], case.ledger())
    assert len(stale) == 1 and "changed" in stale[0]["reason"]
    lib = _raw_ratified(case)
    assert lib[0]["status"] == "stale"
    assert case.rules() == []                     # engine no longer loads it


def test_revalidation_marks_missing_clause_stale(tmp_path):
    from core.app import _revalidate_citations
    case = _mini_case(tmp_path, [_rule(sha="a" * 64, clause="9.9")])
    cat = _FakeCat({"doc_id": "d1", "pdf_sha256": "a" * 64,
                    "clauses": [{"clause": "9.1", "page": 1}]})
    stale = _revalidate_citations(case, cat, ["d1"], case.ledger())
    assert len(stale) == 1 and "no longer exists" in stale[0]["reason"]


def test_revalidation_backfills_legacy_citations(tmp_path):
    """A rule ratified before hashing existed gets bound to the current file when its
    evidence still checks out — no false stale, and future swaps become detectable."""
    from core.app import _raw_ratified, _revalidate_citations
    case = _mini_case(tmp_path, [_rule(sha="")])
    cat = _FakeCat({"doc_id": "d1", "pdf_sha256": "c" * 64,
                    "clauses": [{"clause": "9.1", "page": 1}]})
    stale = _revalidate_citations(case, cat, ["d1"], case.ledger())
    assert stale == []
    lib = _raw_ratified(case)
    assert lib[0]["status"] == "ratified"
    assert lib[0]["citation"]["doc_sha256"] == "c" * 64


def test_intact_evidence_stays_ratified(tmp_path):
    from core.app import _revalidate_citations
    case = _mini_case(tmp_path, [_rule(sha="c" * 64)])
    cat = _FakeCat({"doc_id": "d1", "pdf_sha256": "c" * 64,
                    "clauses": [{"clause": "9.1", "page": 1}]})
    assert _revalidate_citations(case, cat, ["d1"], case.ledger()) == []
    assert len(case.rules()) == 1
