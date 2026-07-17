"""Ingestion: PDF -> clauses (+ bounding boxes) -> catalog entry (PRD 5.1).

Real path uses docling to parse the PDF and read layout provenance (page + bbox).
Because docling is heavy (torch + model download), ingestion falls back to a
committed sidecar `<pdf>.clauses.json` — produced by scripts/make_reference_pdfs.py
with exact bboxes — so the app runs offline. Either way we then tag + summarize the
document (llm.tag_document) and write a catalog entry.
"""
from __future__ import annotations

import json
import os
import re
from typing import Any

from . import llm
from .catalog import Catalog


def _sidecar_path(pdf_path: str) -> str:
    base, _ = os.path.splitext(pdf_path)
    return base + ".clauses.json"


def parse_pdf(pdf_path: str, doc_id: str) -> tuple[list[dict], str, str]:
    """Return (clauses, full_text, source).

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
    library then lists a contract Holly cannot answer from and never says why. Missing is
    a hard error: it is fixed by supplying the file, not by degrading the extraction.
    """
    if not os.path.exists(pdf_path):
        raise FileNotFoundError(
            f"{doc_id}: no PDF at {pdf_path}. It is declared in case.yaml but not on "
            f"disk — upload it, or run scripts/make_citywide_corpus.py to rebuild the "
            f"reference corpus."
        )

    clauses = _parse_with_docling(pdf_path, doc_id)
    if clauses:
        text = "\n".join(c.get("text", "") for c in clauses)
        return clauses, text, "docling"

    sidecar = _sidecar_path(pdf_path)
    if os.path.exists(sidecar):
        with open(sidecar) as f:
            data = json.load(f)
        clauses = data.get("clauses", [])
        text = data.get("text") or "\n".join(c.get("text", "") for c in clauses)
        return clauses, text, "sidecar"

    clauses = _parse_with_text(pdf_path)
    if clauses:
        text = "\n".join(c.get("text", "") for c in clauses)
        return clauses, text, "raw-text-fallback"
    return [], "", "empty"


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
        return []


def _parse_with_docling(pdf_path: str, doc_id: str):
    """Best-effort docling parse -> clauses with page + bbox. Returns None if docling
    is unavailable or errors, so callers fall back."""
    try:
        from docling.document_converter import DocumentConverter
    except Exception:
        return None
    try:
        conv = DocumentConverter()
        result = conv.convert(pdf_path)
        doc = result.document
        clauses: list[dict] = []
        caption = ""
        for item, _level in doc.iterate_items():
            page, bbox = _provenance(item)

            # TABLES. docling returns tables as their own item type with no `.text`, so
            # a text-only reader drops them silently — and MOUs put their money in
            # tables (salary schedules, holiday lists, accrual charts). Flatten each
            # row into a searchable line that carries its headers, so "Sergeant Step C"
            # is retrievable.
            if hasattr(item, "export_to_dataframe"):
                for row_text in _table_rows(item, caption):
                    clauses.append({"clause": "", "text": row_text, "page": page,
                                    "bbox": bbox, "char_span": [0, len(row_text)],
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
        return clauses or _from_doc_texts(doc)
    except Exception:
        return None


def _from_doc_texts(doc) -> list[dict] | None:
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
        page, bbox = _provenance(t)
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


def _provenance(item: Any) -> tuple[int, list[float]]:
    prov = getattr(item, "prov", None) or []
    if prov:
        p = prov[0]
        page = getattr(p, "page_no", 1)
        bb = getattr(p, "bbox", None)
        if bb is not None:
            return page, [getattr(bb, "l", 0), getattr(bb, "t", 0),
                          getattr(bb, "r", 0), getattr(bb, "b", 0)]
    return 1, []


def _clause_number(text: str) -> str:
    import re
    m = re.search(r"\b(\d+\.\d+)\b", text[:40])
    return m.group(1) if m else ""


def chunk_clauses(clauses: list[dict], max_chars: int = 1000, overlap: int = 120) -> list[dict]:
    """Turn clauses into retrieval chunks that keep citation metadata (clause, page,
    bbox). Long clauses are windowed so a 50-page doc indexes cleanly (PRD §8B)."""
    chunks: list[dict] = []
    for ci, c in enumerate(clauses):
        text = c.get("text", "")
        base = {"clause": c.get("clause"), "page": c.get("page"), "bbox": c.get("bbox")}
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
                    backend=None) -> dict:
    clauses, text, source = parse_pdf(pdf_path, doc_id)
    meta = llm.tag_document(text, taxonomy)
    entry = {
        "doc_id": doc_id,
        "title": extract_title(clauses, pdf_path, fallback=title),
        # Kept so the library can show that the document's own header disagrees with the
        # name the case declares for it — a signal the wrong file was filed, not noise.
        "declared_title": title,
        "file": pdf_path,
        "department": meta.get("department", ""),
        "tags": meta.get("tags", []),
        "proposed_tags": meta.get("proposed_tags", []),
        "summary": meta.get("summary", ""),
        "page_count": page_count or (max((c.get("page", 1) for c in clauses), default=1)),
        "clauses": clauses,
        "parse_source": source,
        "tag_source": meta.get("source", "stub"),
    }
    catalog.upsert(entry)
    # Index chunks for scalable retrieval (large PDFs). Optional — the catalog still
    # works without it; the index just makes within-doc search rank properly.
    if backend is not None:
        try:
            backend.index(doc_id, chunk_clauses(clauses))
        except Exception:
            pass
    return entry
