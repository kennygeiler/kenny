# Kenny — OCR Showcase Backlog

Goal: make the extraction layer *visible*. Kenny's OCR/parsing pipeline already
produces clause-level provenance (1,861 clauses with `page`, `bbox`, `kind`, per-doc
`parse_source`, per-PDF `pdf_sha256`) — these tickets turn that data into surfaces a
viewer can see and trust. Build in numbered order; each ticket is self-contained.

> **STATUS (2026-08-14):** OCR-1 (X-ray, `eeb617d`), OCR-3 (scorecard, `8b23884`),
> OCR-4 (tier chips, `c71ccc6`), OCR-2 (side-by-side, `8bea6f8`), OCR-5
> (table X-ray, `e26394e`), OCR-6 (live scan demo, `0d37370`) and OCR-7
> (confidence capture, `f4727bc` — page-level, see the ticket for what the pinned
> docling exposes) are DONE. The backlog is complete.
> Known data limitation: the shipped santacruz catalog predates per-row table bboxes,
> sha recording and per-page OCR confidence — a re-ingest with docling refreshes all
> three; the X-ray merges shared-bbox rows into one box until then (and, being a
> digital-text corpus, santacruz will correctly show no confidence markings even
> after re-ingest).

Shared data facts (read before building):
- `cases/<case>/catalog.json` — per doc: `parse_source` (`docling` | `sidecar` |
  `raw-text-fallback` | `empty`), `pdf_sha256`, `page_count`, `clauses[]`. Each clause:
  `clause` (section no. or ""), `text`, `page` (1-based), `bbox` ([l,t,r,b] PDF points,
  bottom-left origin, t measured from page bottom), `kind` (`table-row` |
  `recovered-row` | `recovered-text` | `page-text` | absent = normal text), `label`.
- `GET /doc/{doc_id}/page/{page}?bbox=l,t,r,b` renders a PNG (2x scale) with one box.
  Plain page: omit bbox. `core/pdfview.py` holds the coordinate flip math.
- Search hits (`core/index.py::_hit`) carry doc_id/clause/page/bbox/score/text.
- UI: `core/templates/admin.html` (tabbed admin), `chat.html` + `app.js` (chat),
  `styles.css`. No framework — vanilla JS, `el()`/`esc()` helpers in app.js.
- Backend: FastAPI, `core/app.py`. Auth default-open; nothing new needs auth wiring.

---

## OCR-1 — Document X-ray overlay (build first) — DONE when merged
**What.** A per-document "X-ray" view: the rendered PDF page with EVERY extracted
clause drawn as a translucent, color-coded box; hovering a box shows the extracted
text; a page selector steps through the document. One glance proves "the machine read
this document, here is exactly what it saw."
**How.**
- New endpoint `GET /doc/{doc_id}/clauses?page=N` → `{page, width, height, clauses:
  [{clause, kind, bbox, text}]}` for that page (width/height in PDF points via
  pypdfium2 `get_size()`; needed to scale bboxes onto the rendered image client-side).
- Serve the plain page image with the existing `/doc/{doc_id}/page/{page}` (no bbox).
- New view: an "X-ray" button per document in the admin Documents tab opens an overlay
  panel: `<img>` of the page + absolutely-positioned divs per clause, `left/top/width/
  height` computed from bbox vs page size in % (remember bottom-left origin: top% =
  (H - t)/H). Color by kind: text=blue, table-row=amber, recovered-row=purple,
  page-text=gray. Hover → tooltip with clause number + first ~200 chars.
- Legend + count strip: "page 3 of 41 — 62 clauses (48 text, 12 table rows, 2
  recovered)".
**Accept.** Every clause on the chosen page shows a box; hover shows its text;
kinds are visually distinct; works on a salary-schedule page (row boxes, not one
table blob). Endpoint has a test (page scoping + width/height present).

## OCR-3 — Extraction scorecard (build second)
**What.** Admin card answering "how well did we read each document?" — per document:
parse tier badge, clause count, table rows, recovered-row count (the layout-model
rescue story), pages with no extraction, sha-bound status, index status.
**How.**
- Extend `GET /admin/coverage` (core/app.py) per-doc payload with: `kinds` histogram
  (count clauses by kind), `pages_empty` (pages in 1..page_count with zero clauses),
  `recovered_pages` (pages whose clauses are all recovered-*), `pdf_sha256` (short),
  `index_error` passthrough.
- Documents tab: render a compact scorecard row per doc — tier badge
  (docling=green / sidecar=amber / raw-text=orange / empty=red), counts, and a
  "recovered from layout misclassification" badge when recovered_pages non-empty
  (tooltip explains: docling's layout model called the page a Picture; spans were
  regrouped into rows from raw geometry).
**Accept.** Santa Cruz corpus shows 5 docs all docling-green with real counts;
a doc with `index_error` or empty pages surfaces it. Endpoint test covers the
histogram + empty-page math on a synthetic catalog entry.

## OCR-4 — Extraction-tier chips on citations (build third)
**What.** Every source chip in chat says where its text came from: "digital text
layer" vs "OCR'd scan" vs "page-level only" — the trust signal at the moment of
reading an answer.
**How.**
- `core/index.py::_hit` additionally carries `kind`; `_policy_answer` sources gain
  `parse_source` (from the catalog entry of the hit's doc) and `kind`.
- `_enrich_citations` (costing path) stamps the same onto citations.
- app.js source chips render a small tier chip: parse_source `docling` + kind
  normal/table-row → "text layer"; kind recovered-* → "recovered layout";
  parse_source raw-text-fallback → "page-level"; sidecar → "sidecar extract".
  Title attribute explains the tier in one sentence.
**Accept.** A policy answer over Santa Cruz shows "text layer" chips; a
raw-text-ingested doc shows "page-level". Unit test: hit → chip-label mapping.

## OCR-2 — Side-by-side fidelity view — DONE (`8bea6f8`)
**What.** Split view per page: rendered PDF left, extracted text right; hover either
side highlights the counterpart. The human "verify the extraction" surface — OCR's
analog of the rule-review gate.
**How.** Reuse OCR-1's endpoint + overlay machinery; right pane lists clauses in
reading order (sort by page, then -t); shared hover state by clause index. Entry
point: "Compare" button beside X-ray.
**Accept.** Hovering a paragraph lights its box and vice versa; mismatched OCR text
is findable by eye in under a minute on any page.

## OCR-5 — Table X-ray — DONE (`e26394e`)
**What.** Salary schedule as a *structured table* beside the page: each recovered row
rendered as an HTML row; clicking one highlights its bbox band on the page image.
**How.** Filter OCR-1 clause payload to kind=table-row/recovered-row for the page;
split flattened "a | b | c" text back into cells for display; click → same overlay
highlight. Entry: automatic when a page's clauses are majority table rows.
**Accept.** Master salary schedule page renders as a table whose rows highlight
their exact source band.

## OCR-6 — Live scan demo — DONE (`0d37370`)
**What.** Upload an image-only PDF and *watch* the tiers run: job progress shows
parse tier chosen, clause count ticking, then boxes appear in X-ray.
**How.** `_ingest_worker`/upload already async-job'd; add per-doc `stage` field to
the job payload (parsing → tagging → indexing → done) and have the upload flow poll +
display. Link straight into the new doc's X-ray on completion.
**Accept.** Uploading `tests`' scan fixture (PIL image-PDF) shows the staged
progress and lands in X-ray with whatever tier extracted it.

## OCR-7 — Confidence-aware highlighting (last; needs new data) — DONE (page-level)
**What.** Low-confidence OCR tokens tinted amber in X-ray tooltips and quoted
answers, so a reader knows which characters the OCR was unsure about.
**How.** Requires capturing per-cell/word confidence from docling's OCR output at
ingest (not currently recorded) — extend `_parse_with_docling` to store
`ocr_confidence` per clause when present; render tint client-side. Investigate what
the pinned docling version exposes before committing to schema.
**Accept.** A deliberately blurry scan fixture shows amber spans; digital-text
documents show none.
**Built (what the pinned docling actually allows).** docling 2.113.0 exposes OCR
confidence per PAGE, not per token: `result.confidence` is a `ConfidenceReport`
whose `pages[n].ocr_score` is a float in (0, 1] on OCR'd pages and NaN on pages
with a digital text layer (verified by probing real conversions; `page.cells` is
empty once the pipeline finishes, so there is no per-word surface to read). The
ticket's per-character amber spans are therefore not possible on this pin — the
shipped granularity is the page:
- Ingest records `page_confidence` ({page: ocr_score}) on the catalog entry and
  stamps `low_confidence: true` on every clause from a page scoring below
  `ingest.LOW_OCR_CONFIDENCE` (0.9 — docling's own EXCELLENT cutoff; probe: a
  clean scan OCRs at ~0.96 with correct text, a mildly blurred one at ~0.85 with
  visibly garbled text). The flag also travels into index chunks, so citation
  surfaces can adopt it later without re-ingesting.
- `/doc/{id}/clauses` carries per-clause `low_confidence` plus page-level
  `ocr_confidence`/`low_confidence`; X-ray boxes and Compare rows/table rows wear
  an amber hatch/tint with a "low OCR confidence on this page" tooltip, the count
  strip names the score, and the scorecard + upload result gain an amber
  "low OCR confidence" badge listing the pages. Digital documents (NaN ocr_score)
  record nothing and show nothing.
If a docling upgrade ever exposes word-level confidence, the seam is
`_ocr_page_confidence()` in core/ingest.py plus the already-plumbed
`low_confidence` clause field.
