"""Guided demo tour: one shared step engine (tour.js) served like app.js, included by
BOTH surfaces. The tour is frontend-only, so the meaningful server assertions are that
the pieces actually ship: the route serves, chat carries the start button, admin carries
the ?tour=1 resume hook (the script include), and the script defines both step lists.
A missing include or a stale cache-buster is exactly the failure that would make the
"Take the tour" promise in the README false."""
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
    return TestClient(core_app.app)


def test_tour_js_route_serves(client):
    res = client.get("/static/tour.js")
    assert res.status_code == 200
    assert "javascript" in res.headers["content-type"]
    assert "startTour" in res.text


def test_chat_page_ships_the_tour(client):
    html = client.get("/").text
    assert '/static/tour.js?v=1' in html, "chat must load the tour engine"
    assert 'id="tourStart"' in html and "Take the tour" in html
    assert 'data-page="chat"' in html, "the engine keys its step list off this"
    assert "styles.css?v=6" in html, "stale cached CSS would ship no coach-mark styles"


def test_admin_page_ships_the_tour_resume_hook(client):
    html = client.get("/admin").text
    assert '/static/tour.js?v=1' in html, "admin must load the engine so ?tour=1 resumes"
    assert 'data-page="admin"' in html
    assert "styles.css?v=12" in html


def test_tour_js_defines_both_step_lists_and_the_handoff(client):
    js = client.get("/static/tour.js").text
    assert "chat: [" in js and "admin: [" in js, "per-page step lists"
    assert "/admin?tour=1" in js, "chat hands off to the admin leg"
    assert "tour=1" in js and "startTour" in js, "admin resume hook"
    css = client.get("/static/styles.css").text
    assert ".tour-card" in css and ".tour-glow" in css and ".tour-start" in css
