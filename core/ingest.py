"""Ingestion: PDF -> clauses (+ bounding boxes) -> catalog entry (PRD 5.1).

Real path uses docling to parse the PDF (OCR on, explicitly — see _converter) and read
layout provenance (page + bbox). Because docling is heavy (torch + model download),
ingestion can fall back to an OPTIONAL sidecar `<pdf>.clauses.json` — a generated
artifact, not committed to the repo — but only when the sidecar embeds the SHA-256 of
the exact PDF it was extracted from; an unbound sidecar is untrusted provenance and is
ignored. Either way we then tag + summarize the document (llm.tag_document) and write a
catalog entry carrying the source PDF's own SHA-256, which is what later lets the app
detect a swapped or edited source file.
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import re
from typing import Any

from . import llm
from .catalog import Catalog

log = logging.getLogger("kenny.ingest")


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _sidecar_path(pdf_path: str) -> str:
    base, _ = os.path.splitext(pdf_path)
    return base + ".clauses.json"


# The line below which a page's OCR output is flagged "low confidence" (OCR-7).
# 0.9 is docling's own QualityGrade cutoff for EXCELLENT (base_models._score_to_grade).
# Probed against this pinned docling (2.113.0): a clean generated scan OCRs at ~0.96
# with every word correct, while the same page under a mild Gaussian blur scores ~0.85
# and the text is visibly garbled ("Hotiday Pnemium Pey" for "Holiday Premium Pay") —
# so "anything below EXCELLENT" is the honest line, not an arbitrary one.
LOW_OCR_CONFIDENCE = 0.9


def parse_pdf(pdf_path: str, doc_id: str) -> tuple[list[dict], str, str, dict[int, float]]:
    """Return (clauses, full_text, source, page_confidence).

    page_confidence maps page number -> docling's per-page OCR confidence score
    (OCR_TICKETS.md OCR-7), present ONLY for pages the OCR engine actually read.
    Digital pages report no ocr_score (docling leaves it NaN), so a born-digital
    document yields {} — which is what keeps confidence markings off documents whose
    text was never OCR'd. Non-docling tiers have no confidence to report and also
    return {}. Every clause from a page scoring below LOW_OCR_CONFIDENCE is stamped
    `low_confidence: True` so each downstream surface (X-ray, Compare, scorecard)
    can warn without re-deriving the threshold.

    Tiers, because a document that ingests as EMPTY is worse than one that ingests
    roughly: it looks fine in the catalog and is silently unanswerable.
      1. docling  — layout-aware: clauses, tables, real bounding boxes.
      1b. docling's `doc.texts` — the same parse, read directly, when the layout model
          writes a page off as a Picture (see _from_doc_texts). Still real bboxes.
      2. sidecar  — committed extraction (offline / docling unavailable).
      3. raw text — pypdfium2 page text. Coarse (page-level bbox) but never empty.

    Tier 3 is now the SCAN tier, not the table tier: a page with no text layer has
    nothing for 1b to recover. Table-only pages used to land here and lose their row
    citations, which is what 1b fixed.

    A MISSING file is not tier-3 material. Every tier here answers "we could not extract
    text from this PDF", and each one swallowed a nonexistent path into the same "empty"
    result — so ingesting a case.yaml that declares a document nobody has uploaded yet
    produced a catalogued document with zero clauses, indistinguishable from a scan. The
    library then lists a contract Kenny cannot answer from and never says why. Missing is
    a hard error: it is fixed by supplying the file, not by degrading the extraction.
    """
    if not os.path.exists(pdf_path):
        raise FileNotFoundError(
            f"{doc_id}: no PDF at {pdf_path}. It is declared in case.yaml but not on "
            f"disk — upload it via Admin → Upload, or place the file and re-ingest."
        )

    parsed = _parse_with_docling(pdf_path, doc_id)
    if parsed:
        clauses, page_confidence = parsed
        low = {p for p, s in page_confidence.items() if s < LOW_OCR_CONFIDENCE}
        for c in clauses:
            if c.get("page") in low:
                c["low_confidence"] = True
        text = "\n".join(c.get("text", "") for c in clauses)
        return clauses, text, "docling", page_confidence

    data = _load_sidecar(pdf_path)
    if data is not None:
        clauses = data.get("clauses", [])
        text = data.get("text") or "\n".join(c.get("text", "") for c in clauses)
        return clauses, text, "sidecar", {}

    clauses = _parse_with_text(pdf_path)
    if clauses:
        text = "\n".join(c.get("text", "") for c in clauses)
        return clauses, text, "raw-text-fallback", {}
    return [], "", "empty", {}


def _load_sidecar(pdf_path: str) -> dict | None:
    """Load `<pdf>.clauses.json` ONLY if it is bound to this exact PDF.

    A sidecar is provenance from outside the repo's own parse, so it must carry
    `pdf_sha256` matching the file on disk. Without that binding a stale or hand-edited
    sidecar silently becomes the document's official clause map — worse than degrading
    to the raw-text tier, because it *looks* exact.
    """
    sidecar = _sidecar_path(pdf_path)
    if not os.path.exists(sidecar):
        return None
    try:
        with open(sidecar) as f:
            data = json.load(f)
    except Exception:
        log.exception("unreadable sidecar %s — ignoring", sidecar)
        return None
    bound = data.get("pdf_sha256", "")
    actual = sha256_file(pdf_path)
    if bound != actual:
        log.warning("sidecar %s is not bound to %s (embedded sha %s, file sha %s) — "
                    "ignoring it", sidecar, pdf_path, bound[:12] or "<missing>",
                    actual[:12])
        return None
    if not isinstance(data.get("clauses"), list):
        log.warning("sidecar %s has no 'clauses' list — ignoring it", sidecar)
        return None
    return data


def _parse_with_text(pdf_path: str) -> list[dict]:
    """Last-resort extraction: raw page text via pypdfium2. No layout, no clause
    numbers, page-level bbox — the citation opens the right page rather than boxing
    the exact sentence. Degraded but honest, and never silently empty."""
    try:
        import pypdfium2 as pdfium
    except Exception:
        return []
    try:
        pdf = pdfium.PdfDocument(pdf_path)
        out: list[dict] = []
        for i in range(len(pdf)):
            page = pdf[i]
            width, height = page.get_size()
            text = page.get_textpage().get_text_range().strip()
            if not text:
                continue
            out.append({"clause": _clause_number(text), "text": text, "page": i + 1,
                        "bbox": [0, height, width, 0], "char_span": [0, len(text)],
                        "kind": "page-text"})
        return out
    except Exception:
        log.exception("raw-text extraction failed for %s", pdf_path)
        return []


def _converter():
    """DocumentConverter with the pipeline configured EXPLICITLY (never defaults):
    OCR on — a true scan gets its text layer here, in-band, not by an undocumented
    preprocessing step — and table structure on. Pinning the options (and the docling
    version, in requirements.txt) means an upgrade cannot silently change what
    ingestion extracts. Falls back to a default converter if this docling version
    lays its options out differently, and says so."""
    from docling.document_converter import DocumentConverter
    try:
        from docling.datamodel.base_models import InputFormat
        from docling.datamodel.pipeline_options import PdfPipelineOptions
        from docling.document_converter import PdfFormatOption
        opts = PdfPipelineOptions()
        opts.do_ocr = True
        opts.do_table_structure = True
        return DocumentConverter(
            format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=opts)})
    except Exception:
        log.warning("could not pin docling pipeline options; using defaults",
                    exc_info=True)
        return DocumentConverter()


def _parse_with_docling(pdf_path: str, doc_id: str):
    """Best-effort docling parse -> (clauses with page + bbox, per-page OCR confidence).
    Returns None if docling is unavailable, errors, or extracts nothing, so callers
    fall back — but an ERROR (crash on a corrupt PDF, OOM) is logged with its
    traceback, so it is distinguishable from docling simply not being installed."""
    try:
        from docling.document_converter import DocumentConverter  # noqa: F401
    except Exception:
        log.debug("docling not installed; falling back for %s", doc_id)
        return None
    try:
        conv = _converter()
        result = conv.convert(pdf_path)
        doc = result.document
        heights = _page_heights(doc)
        clauses: list[dict] = []
        caption = ""
        for item, _level in doc.iterate_items():
            page, bbox = _provenance(item, heights)

            # TABLES. docling returns tables as their own item type with no `.text`, so
            # a text-only reader drops them silently — and MOUs put their money in
            # tables (salary schedules, holiday lists, accrual charts). Flatten each
            # row into a searchable line that carries its headers, so "Sergeant Step C"
            # is retrievable. Each row gets its OWN bbox from docling's cell geometry
            # when available, so citing a salary row highlights that row, not the
            # whole table; rows whose cells carry no geometry fall back to the table's.
            if hasattr(item, "export_to_dataframe"):
                row_boxes = _table_row_bboxes(item, heights.get(page))
                for ri, row_text in enumerate(_table_rows(item, caption)):
                    # ri == 0 is the synthetic whole-table line; data rows are 1-based
                    rb = row_boxes.get(ri - 1) if ri > 0 else None
                    clauses.append({"clause": "", "text": row_text, "page": page,
                                    "bbox": rb or bbox,
                                    "char_span": [0, len(row_text)],
                                    "kind": "table-row"})
                continue

            text = (getattr(item, "text", "") or "").strip()
            if not text:
                continue
            label = str(getattr(item, "label", "") or "")
            # remember the nearest heading so table rows can be labelled by it
            if label in ("section_header", "title") or len(text) < 90:
                caption = text
            clauses.append({
                "clause": _clause_number(text),
                "text": text,
                "page": page,
                "bbox": bbox,
                "char_span": [0, len(text)],
                # kept so the document can name ITSELF (see extract_title): docling
                # already knows which item is the title, and a hand-written name in
                # case.yaml can drift from what the contract's own cover page says.
                "label": label,
            })
        clauses = clauses or _from_doc_texts(doc, heights)
        if not clauses:
            return None
        return clauses, _ocr_page_confidence(result)
    except Exception:
        log.exception("docling parse failed for %s (%s)", doc_id, pdf_path)
        return None


def _ocr_page_confidence(result: Any) -> dict[int, float]:
    """Per-page OCR confidence from docling's ConfidenceReport (`result.confidence`).

    What the pinned docling (2.113.0) actually exposes, verified by probing real
    conversions: page-LEVEL scores only — result.confidence.pages[n].ocr_score,
    alongside parse/layout/table scores and aggregate grades. It does NOT expose
    per-word or per-cell OCR confidence (page.cells is empty once the pipeline
    finishes), so page granularity is the finest this pin can record; the ticket's
    original per-token tinting is not possible until docling grows that surface.

    ocr_score is NaN on pages where OCR never ran (the page had a digital text
    layer). Those pages are OMITTED rather than recorded as 1.0: absence means
    "nothing was OCR'd here", which is a different fact from "OCR'd perfectly".
    The aggregate mean/low grades are deliberately NOT used — they fold in
    layout_score, which sits in the FAIR band even on clean born-digital text, so
    they would flag documents whose extraction is exact."""
    pages = getattr(getattr(result, "confidence", None), "pages", None) or {}
    out: dict[int, float] = {}
    for no, scores in pages.items():
        try:
            score = float(getattr(scores, "ocr_score", None))
            page = int(no)
        except (TypeError, ValueError):
            continue
        if math.isnan(score):
            continue
        out[page] = round(score, 4)
    return out


def _from_doc_texts(doc, page_heights: dict[int, float] | None = None) -> list[dict] | None:
    """Recover a page docling's layout model wrote off as a Picture.

    `iterate_items()` walks the body tree. When the layout model classifies a whole page
    as a picture — which it does to the salary schedules, whose pages are one big table —
    it yields a single empty PictureItem and the page looks unreadable. But the text is
    NOT missing: `doc.texts` still holds every span WITH its provenance, 27 items for a
    page that iterate_items reported as one picture. The parser was dropping the entire
    document to the raw-text tier (page-level bbox, one blob) while docling had the
    content and real bounding boxes all along.

    Cells arrive as separate spans — 'Sergeant', '$53.00', '$55.65' — so they are
    regrouped into rows by vertical position. A rate is only an answer with its row:
    "$58.00" means nothing; "Sergeant | ... | Step C $58.00" is a citable fact.
    """
    texts = getattr(doc, "texts", None) or []
    if not texts:
        return None

    spans = []
    for t in texts:
        text = (getattr(t, "text", "") or "").strip()
        if not text:
            continue
        page, bbox = _provenance(t, page_heights)
        spans.append({"text": text, "page": page, "bbox": bbox})
    if not spans:
        return None

    # Group spans into rows: same page, vertical centres within a line's height. Sort by
    # x so the columns come back in reading order.
    rows: list[list[dict]] = []
    for s in sorted(spans, key=lambda s: (s["page"], -_ycentre(s["bbox"]), s["bbox"][0] if s["bbox"] else 0)):
        placed = False
        for row in rows:
            if row[0]["page"] == s["page"] and \
               abs(_ycentre(row[0]["bbox"]) - _ycentre(s["bbox"])) <= _ROW_TOLERANCE:
                row.append(s)
                placed = True
                break
        if not placed:
            rows.append([s])

    clauses = []
    for row in rows:
        row.sort(key=lambda s: s["bbox"][0] if s["bbox"] else 0)
        text = " | ".join(s["text"] for s in row) if len(row) > 1 else row[0]["text"]
        clauses.append({
            "clause": _clause_number(text),
            "text": text,
            "page": row[0]["page"],
            "bbox": _union_bbox([s["bbox"] for s in row]),
            "char_span": [0, len(text)],
            "label": "text",
            "kind": "recovered-row" if len(row) > 1 else "recovered-text",
        })
    return clauses or None


_ROW_TOLERANCE = 6.0  # points; a table row's cells share a baseline within about this


def _ycentre(bbox: list[float]) -> float:
    return (bbox[1] + bbox[3]) / 2 if bbox and len(bbox) >= 4 else 0.0


def _union_bbox(boxes: list[list[float]]) -> list[float]:
    """One box around a whole row, so the citation highlights the row, not one cell."""
    real = [b for b in boxes if b and len(b) >= 4]
    if not real:
        return []
    return [min(b[0] for b in real), max(b[1] for b in real),
            max(b[2] for b in real), min(b[3] for b in real)]


def _table_rows(item, caption: str = "") -> list[str]:
    """One searchable line per table row, each carrying its column headers and the
    table's caption: 'Appendix A — Classification: Sergeant | Step C: $58.00 | ...'."""
    try:
        df = item.export_to_dataframe()
    except Exception:
        return []
    out: list[str] = []
    cols = [str(c).strip() for c in df.columns]
    for _, row in df.iterrows():
        cells = [f"{c}: {str(v).strip()}" for c, v in zip(cols, row.tolist())
                 if str(v).strip()]
        if not cells:
            continue
        line = " | ".join(cells)
        out.append(f"{caption} — {line}" if caption else line)
    # a whole-table line too, so "salary schedule" style queries match the table itself
    if out:
        out.insert(0, f"{caption} (table: {', '.join(cols)}; {len(out)} rows)"
                   if caption else f"table: {', '.join(cols)}")
    return out


def _page_heights(doc) -> dict[int, float]:
    """Page number -> height in points, needed to normalize a TOPLEFT-origin bbox."""
    out: dict[int, float] = {}
    try:
        for no, page in (getattr(doc, "pages", None) or {}).items():
            h = getattr(getattr(page, "size", None), "height", None)
            if h:
                out[int(no)] = float(h)
    except Exception:
        pass
    return out


def _normalize_bbox(bb, page_height: float | None) -> list[float]:
    """Read a docling BoundingBox into this codebase's convention: bottom-left origin,
    [l, t, r, b] with t measured from the bottom of the page.

    docling declares each box's own `coord_origin`; the renderer (pdfview) hard-assumes
    bottom-left. ASSERTING the origin here — instead of assuming it — is what stops a
    docling upgrade that emits TOPLEFT boxes from silently mirroring every highlight."""
    l = float(getattr(bb, "l", 0))
    t = float(getattr(bb, "t", 0))
    r = float(getattr(bb, "r", 0))
    b = float(getattr(bb, "b", 0))
    origin = str(getattr(bb, "coord_origin", "") or "").upper()
    if "TOP" in origin:
        if not page_height:
            log.warning("TOPLEFT bbox with unknown page height — highlight may be "
                        "mirrored")
            return [l, t, r, b]
        t, b = page_height - t, page_height - b
    return [l, t, r, b]


def _provenance(item: Any, page_heights: dict[int, float] | None = None
                ) -> tuple[int, list[float]]:
    prov = getattr(item, "prov", None) or []
    if prov:
        p = prov[0]
        page = getattr(p, "page_no", 1)
        bb = getattr(p, "bbox", None)
        if bb is not None:
            return page, _normalize_bbox(bb, (page_heights or {}).get(page))
    return 1, []


def _table_row_bboxes(item, page_height: float | None) -> dict[int, list[float]]:
    """Best-effort per-row bbox from docling's own cell geometry.

    Returns {dataframe_row_index: bbox}. The dataframe's rows are the table's BODY rows
    in order, so header rows (docling flags them column_header) are excluded from the
    mapping. Rows whose cells carry no geometry are simply absent — the caller falls
    back to the whole-table bbox for those."""
    try:
        cells = list(item.data.table_cells)
    except Exception:
        return {}
    header_rows: set[int] = set()
    boxes_by_row: dict[int, list[list[float]]] = {}
    for cell in cells:
        r = getattr(cell, "start_row_offset_idx", None)
        if r is None:
            continue
        if getattr(cell, "column_header", False):
            header_rows.add(int(r))
            continue
        bb = getattr(cell, "bbox", None)
        if bb is None:
            continue
        boxes_by_row.setdefault(int(r), []).append(_normalize_bbox(bb, page_height))
    body = sorted(r for r in boxes_by_row if r not in header_rows)
    return {i: _union_bbox(boxes_by_row[r]) for i, r in enumerate(body)
            if boxes_by_row[r]}


# A section number, not a dollar figure (TICKETS.md F2). Anchored to a token boundary:
# never mid-number ("1660.80"), never right after "$" ("$53.00" in a salary row —
# which previously became clause "53.00" and could bind a citation to the wrong text).
_CLAUSE_RE = re.compile(
    r"(?:^|(?<=[\s(]))"          # token start: beginning, whitespace, or open paren
    r"(?:§\s*)?"
    r"(\d{1,2}\.\d{1,2})"
    r"(?!\d)"
    r"(?=$|[\s:).,\-–—|])"       # token end
)


def _clause_number(text: str) -> str:
    head = (text or "")[:40]
    for m in _CLAUSE_RE.finditer(head):
        pre = head[:m.start()]
        if pre.rstrip().endswith("$") or (pre and pre.rstrip()[-1:].isdigit()):
            continue  # "$53.00" / "Step 2 53.00" are figures, not sections
        return m.group(1)
    return ""


def chunk_clauses(clauses: list[dict], max_chars: int = 1000, overlap: int = 120) -> list[dict]:
    """Turn clauses into retrieval chunks that keep citation metadata (clause, page,
    bbox). Long clauses are windowed so a 50-page doc indexes cleanly (PRD §8B)."""
    chunks: list[dict] = []
    for ci, c in enumerate(clauses):
        text = c.get("text", "")
        base = {"clause": c.get("clause"), "page": c.get("page"), "bbox": c.get("bbox")}
        # `kind` (table-row / recovered-* / page-text; absent = normal text) travels with
        # the chunk so a search hit can say what KIND of extraction produced its text
        # (OCR-4 extraction-tier chips). Only stamped when present — older baked indexes
        # without it must keep loading unchanged.
        if c.get("kind"):
            base["kind"] = c["kind"]
        # Same convention for the OCR-7 flag: a hit from a low-confidence OCR page can
        # say so at citation time; absent everywhere else so older indexes load as-is.
        if c.get("low_confidence"):
            base["low_confidence"] = True
        if len(text) <= max_chars:
            chunks.append({"chunk_id": f"{ci}", "text": text, **base})
            continue
        start, part = 0, 0
        while start < len(text):
            piece = text[start:start + max_chars]
            chunks.append({"chunk_id": f"{ci}-{part}", "text": piece, **base})
            start += max_chars - overlap
            part += 1
    return chunks


def extract_title(clauses: list[dict], pdf_path: str, fallback: str = "") -> str:
    """What the document calls ITSELF, taken from its own cover page.

    A title hand-written in case.yaml ("Administrative Policy 09 — Tuition
    Reimbursement") is a name someone invented for a filing system. It drifts from the
    contract, it has to be authored for every upload, and a user searching the library
    for the words printed on the document they are holding will not find it. Read the
    header instead, in order of how much the source actually knows:

      1. docling's `title` item — it ran a layout model and identified the cover heading.
      2. The cover page's own lines, read top-down.
      3. The PDF's embedded metadata title.
      4. The caller's fallback (case.yaml / the filename), so this never returns nothing.

    Metadata ranks BELOW the visible cover on purpose. It is set by whatever produced the
    file and is regularly wrong: every PDF in the reference corpus carries the metadata
    title "City of Sand City" — the publisher, identical across all thirteen documents,
    naming none of them. What is printed on the page is what a person would call the
    document, and it is what they will search the library for.
    """
    for c in clauses:
        if c.get("label") == "title" and (c.get("text") or "").strip():
            cleaned = _clean_title(c["text"])
            if cleaned:
                return cleaned

    # Cover pages read: ISSUING BODY / DOCUMENT NAME / effective date. The org line is
    # first and is the same on every document in a corpus, so it cannot identify any of
    # them — prefer the LAST line before the contract's first article.
    header: list[str] = []
    for c in clauses:
        if c.get("page", 1) != 1:
            break
        text = " ".join((c.get("text") or "").split())
        if c.get("clause") or _ARTICLE_RE.match(text):
            break  # the body has started; everything useful is behind us
        # The cap is generous because docling sometimes flattens a whole cover into one
        # item; _clean_title cuts the body back off. Too tight a cap silently drops the
        # only line that names the document.
        if 4 < len(text) <= 400:
            header.append(text)
        if len(header) >= 4:
            break
    for line in reversed(header):
        cleaned = _clean_title(line)
        if cleaned:
            return cleaned

    try:
        import pypdfium2 as pdfium
        pdf = pdfium.PdfDocument(pdf_path)
        meta = (pdf.get_metadata_dict() or {}).get("Title", "")
        pdf.close()
        # Producers leave junk here: the source filename, "Microsoft Word - foo.doc",
        # or an empty string. Only trust it if it reads like a name.
        meta = " ".join((meta or "").split())
        if meta and len(meta) > 4 and not meta.lower().endswith((".pdf", ".doc", ".docx")):
            cleaned = _clean_title(meta)
            if cleaned:
                return cleaned
    except Exception:
        pass

    return fallback


_ARTICLE_RE = re.compile(r"^\s*(article|section|appendix)\b", re.I)
# Covers append their validity to the name: "... — Effective July 1, 2025". That is
# metadata (governance already holds the real dates, from case.yaml), not part of what
# the document is called.
_TITLE_TAIL_RE = re.compile(
    r"\s*[-–—·|,]?\s*\b(effective|adopted|revised|approved|dated|amended)\b.*$", re.I)
# When docling flattens a cover into one item, the body follows the name in the same
# string. Cut at the first structural heading so the title stops where the contract starts.
_TITLE_BODY_RE = re.compile(r"\s+\b(article|appendix|section|preamble|witnesseth)\b.*$", re.I)


def _clean_title(text: str) -> str:
    """Normalise one header line into a document name."""
    text = " ".join((text or "").split())
    text = _TITLE_BODY_RE.sub("", text)
    text = _TITLE_TAIL_RE.sub("", text).strip(" -–—·|,;:")
    if len(text) <= 4:
        return ""
    return text[:160]


def ingest_document(pdf_path: str, doc_id: str, title: str, taxonomy: dict,
                    catalog: Catalog, page_count: int | None = None,
                    backend=None, progress=None) -> dict:
    # `progress` (optional callable taking a stage string) is called at each phase
    # boundary — "parsing", "tagging", "indexing" — so an async job can show a live
    # staged view of an ingest that may spend minutes inside docling (OCR-6). It is
    # observability only: a callback failure must never abort the ingest itself.
    def _stage(name: str) -> None:
        if progress is not None:
            try:
                progress(name)
            except Exception:
                log.exception("ingest progress callback failed at %s", name)

    _stage("parsing")
    clauses, text, source, page_confidence = parse_pdf(pdf_path, doc_id)
    _stage("tagging")
    meta = llm.tag_document(text, taxonomy)
    entry = {
        "doc_id": doc_id,
        "title": extract_title(clauses, pdf_path, fallback=title),
        # Kept so the library can show that the document's own header disagrees with the
        # name the case declares for it — a signal the wrong file was filed, not noise.
        "declared_title": title,
        "file": pdf_path,
        # Binds every downstream citation to the exact bytes that were parsed. Serving
        # and answering verify against this, so a swapped or edited source PDF is
        # DETECTED rather than silently rendered under old citations (TICKETS.md A1).
        "pdf_sha256": sha256_file(pdf_path),
        "department": meta.get("department", ""),
        "tags": meta.get("tags", []),
        "proposed_tags": meta.get("proposed_tags", []),
        "summary": meta.get("summary", ""),
        "page_count": page_count or (max((c.get("page", 1) for c in clauses), default=1)),
        "clauses": clauses,
        "parse_source": source,
        "tag_source": meta.get("source", "stub"),
    }
    # Per-page OCR confidence (OCR-7), only when the parse produced any: string keys
    # so the entry reads back from catalog.json exactly as it was written (JSON has no
    # int keys), and no key at all for digital documents or non-docling tiers — older
    # catalog entries and born-digital docs stay byte-identical in shape.
    if page_confidence:
        entry["page_confidence"] = {str(p): s for p, s in page_confidence.items()}
    # Index chunks for scalable retrieval (large PDFs). Optional — the catalog still
    # works without it; the index just makes within-doc search rank properly. A failure
    # is RECORDED on the entry (a doc catalogued but absent from search is silently
    # unanswerable) and surfaced by the admin ingest warnings.
    _stage("indexing")
    if backend is not None:
        try:
            backend.index(doc_id, chunk_clauses(clauses))
        except Exception as e:
            log.exception("indexing failed for %s", doc_id)
            entry["index_error"] = f"{type(e).__name__}: {e}"
    catalog.upsert(entry)
    return entry
