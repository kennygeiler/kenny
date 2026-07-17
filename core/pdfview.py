"""Render a source PDF page with a docling bounding box highlighted (PRD 4.4).

This is what makes a citation verifiable: the user sees the exact clause boxed on
the original page. Uses pypdfium2 to rasterize + PIL to draw. Degrades to None if
the libraries or file are unavailable (the UI then shows the citation without the
image).

Coordinate mapping: docling bboxes are in PDF points with a bottom-left origin
(l, t, r, b measured from the bottom). pypdfium2 renders top-left origin pixels, so
y is flipped and both axes scaled by the render scale.
"""
from __future__ import annotations

import hashlib
import io
import os
import threading

# pypdfium2 is NOT thread-safe, and FastAPI runs sync endpoints in a threadpool. The
# audit drawer requests several citation images at once, so concurrent renders land in
# different threads and fail — every citation silently showing "page render
# unavailable". Serialize rendering, and cache the result so repeat clicks are free.
_RENDER_LOCK = threading.Lock()
_CACHE: dict[str, bytes] = {}
_CACHE_MAX = 64


def render_page_with_bbox(pdf_path: str, page: int, bbox: list[float],
                          scale: float = 2.0) -> bytes | None:
    if not os.path.exists(pdf_path):
        return None
    key = hashlib.sha1(
        f"{pdf_path}|{os.path.getmtime(pdf_path)}|{page}|{bbox}|{scale}".encode()
    ).hexdigest()
    hit = _CACHE.get(key)
    if hit is not None:
        return hit
    try:
        import pypdfium2 as pdfium
        from PIL import Image, ImageDraw
    except Exception:
        return None
    with _RENDER_LOCK:
        png = _render(pdfium, ImageDraw, pdf_path, page, bbox, scale)
    if png is not None:
        if len(_CACHE) >= _CACHE_MAX:
            _CACHE.pop(next(iter(_CACHE)))
        _CACHE[key] = png
    return png


def _render(pdfium, ImageDraw, pdf_path, page, bbox, scale) -> bytes | None:
    try:
        pdf = pdfium.PdfDocument(pdf_path)
        idx = max(0, (page or 1) - 1)
        idx = min(idx, len(pdf) - 1)
        pg = pdf[idx]
        page_height_pt = pg.get_size()[1]
        bitmap = pg.render(scale=scale)
        img = bitmap.to_pil().convert("RGB")
        if bbox and len(bbox) == 4:
            l, t, r, b = bbox
            # docling: origin bottom-left, t is distance of top edge from bottom.
            x0 = l * scale
            x1 = r * scale
            y0 = (page_height_pt - t) * scale
            y1 = (page_height_pt - b) * scale
            draw = ImageDraw.Draw(img, "RGBA")
            draw.rectangle([x0, y0, x1, y1], outline=(200, 40, 40, 255), width=4)
            draw.rectangle([x0, y0, x1, y1], fill=(255, 220, 0, 60))
        out = io.BytesIO()
        img.save(out, format="PNG")
        pdf.close()  # don't leak the handle; pdfium warns and holds the file open
        return out.getvalue()
    except Exception:
        return None
