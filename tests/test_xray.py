"""X-ray clause endpoint (OCR_TICKETS.md OCR-1): page-scoped clause payload with the
page's point dimensions — without width/height the overlay cannot scale bboxes onto
the rendered image."""
import json
import os
import shutil
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import app as core_app  # noqa: E402


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


def _doc_ids(case_dir):
    with open(os.path.join(case_dir, "catalog.json")) as f:
        return [d["doc_id"] for d in json.load(f)["documents"]]


def test_clauses_are_page_scoped_with_page_metrics(client):
    c, case_dir = client
    doc_id = _doc_ids(case_dir)[0]
    res = c.get(f"/doc/{doc_id}/clauses", params={"page": 1})
    assert res.status_code == 200
    body = res.json()
    assert body["doc_id"] == doc_id
    assert body["page"] == 1
    assert body["page_count"] >= 1
    assert body["width"] > 0 and body["height"] > 0
    assert body["clauses"], "page 1 of an ingested contract must have clauses"
    for cl in body["clauses"]:
        assert cl["page"] == 1
        assert cl["kind"], "kind must be normalised, never absent or empty"
        assert len(cl["bbox"]) == 4
    # The catalog omits `kind` for normal text; the endpoint must default it.
    assert any(cl["kind"] == "text" for cl in body["clauses"])


def test_page_is_clamped_into_range(client):
    c, case_dir = client
    doc_id = _doc_ids(case_dir)[0]
    body = c.get(f"/doc/{doc_id}/clauses", params={"page": 9999}).json()
    assert body["page"] == body["page_count"]
    body = c.get(f"/doc/{doc_id}/clauses", params={"page": 0}).json()
    assert body["page"] == 1


def test_unknown_doc_is_404(client):
    c, _ = client
    assert c.get("/doc/no-such-doc/clauses").status_code == 404


def test_admin_page_ships_the_compare_view(client):
    """OCR-2 is frontend-only over the OCR-1 endpoint, so the meaningful server
    assertion is that the admin page actually ships the Compare surface: the entry
    point, the split-pane containers, and a bumped stylesheet version (stale cached
    CSS would render the split layout as a broken single column)."""
    c, _ = client
    html = c.get("/admin").text
    assert "openCompare(" in html, "every document card needs a Compare entry point"
    assert 'id="xrayText"' in html and 'class="xsplit"' in html
    css = c.get("/static/styles.css").text
    assert ".xsplit" in css and ".xtext" in css and ".xrow" in css


def test_admin_page_ships_the_table_xray(client):
    """OCR-5 is frontend-only over the OCR-1 endpoint: a majority-table page renders
    as a structured HTML table in the Compare pane. The meaningful server assertion
    is that the admin page ships the machinery — the majority-rule row parser, the
    table renderer, the count-strip mode indicator — and a bumped stylesheet version
    (stale cached CSS would render the grid unstyled and unhighlightable)."""
    c, _ = client
    html = c.get("/admin").text
    assert "function tableModel(" in html, "majority-table detection + row-text parsing"
    assert "renderXTable(" in html and 'class="xtable"' in html
    assert "table page —" in html, "count-strip mode indicator"
    assert "styles.css?v=9" in html
    css = c.get("/static/styles.css").text
    assert ".xtable" in css and ".xtable-wrap" in css
