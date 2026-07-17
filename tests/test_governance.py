"""Governance resolver tests (PRD §4A) — deterministic unit+date -> MOU lookup."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import governance  # noqa: E402

SOURCES = [
    {"id": "mou_2022", "doc_type": "MOU", "bargaining_unit": "public-works-maintenance",
     "effective_start": "2022-01-01", "effective_end": "2023-12-31"},
    {"id": "mou_2024", "doc_type": "MOU", "bargaining_unit": "public-works-maintenance",
     "effective_start": "2024-01-01", "effective_end": "2027-12-31"},
    {"id": "sheriff_mou", "doc_type": "MOU", "bargaining_unit": "correctional-officers",
     "effective_start": "2025-01-01", "effective_end": "2028-12-31"},
    {"id": "salary", "doc_type": "salary-schedule"},
]


def test_parse_date_variants():
    assert governance.parse_date("July 4th (a Saturday)", 2026) == "2026-07-04"
    assert governance.parse_date("Jul 4 2025") == "2025-07-04"
    assert governance.parse_date("2026-07-04") == "2026-07-04"
    assert governance.parse_date("a High-Security Day", 2026) is None


def test_resolves_correct_version_by_date():
    r = governance.resolve(["public-works-maintenance"], "2026-07-04", SOURCES)
    assert r.resolved and r.doc_ids == ["mou_2024"]  # not the expired 2022 MOU


def test_resolves_older_version_for_older_date():
    r = governance.resolve(["public-works-maintenance"], "2022-06-01", SOURCES)
    assert r.doc_ids == ["mou_2022"]


def test_unit_only_when_no_date():
    r = governance.resolve(["correctional-officers"], None, SOURCES)
    assert r.resolved and r.doc_ids == ["sheriff_mou"]


def test_no_match_falls_back():
    r = governance.resolve(["fire-suppression"], "2026-07-04", SOURCES)
    assert not r.resolved and r.doc_ids == []


def test_salary_schedule_never_governs():
    # a salary-schedule is not an MOU and must never be selected as governing
    r = governance.resolve(["public-works-maintenance"], "2026-07-04", SOURCES)
    assert "salary" not in r.doc_ids
