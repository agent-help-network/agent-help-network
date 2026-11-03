"""D80: marks that say what status fields mean (provider_status.source_time "page", official_status_page.covers "api"),
and the doc sentences for ahn_check.freshness and current_site_status. Pure tests: no database, no network.
None of it changes a verdict, so the verdict tests below run the same input with and without the marks."""

from __future__ import annotations

import itertools
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from ahn import response_guide, site_probe, status_refresh, triage
from ahn.models import OfficialStatusPage, ProviderStatus
from ahn.service import _atom_scope, _provider_scope, official_page_view
from ahn.status_refresh import ACTIVE_ITEM_TIME_FEEDS, OFFICIAL_STATUS_PAGES, SOURCES

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 11, 3, 12, 0, tzinfo=timezone.utc)
UPDATED = datetime(2026, 7, 14, 9, 0, tzinfo=timezone.utc)


def feed_url(kind: str) -> str:
    return next(source.api_url for source in SOURCES.values() if source.kind == kind)


# --- B1: source_time ---------------------------------------------------------------------------------------------

def test_a_statuspage_feed_with_a_time_is_marked_page():
    url = feed_url("statuspage")
    assert url not in ACTIVE_ITEM_TIME_FEEDS
    assert _provider_scope({"indicator": "none", "api_url": url, "source_updated_at": UPDATED})["source_time"] == "page"


def test_other_page_wide_kinds_are_marked_page_too():
    for kind in sorted({source.kind for source in SOURCES.values()} - status_refresh.ACTIVE_ITEM_TIME_KINDS):
        scope = _provider_scope({"indicator": "none", "api_url": feed_url(kind), "source_updated_at": UPDATED})
        assert scope["source_time"] == "page", kind


@pytest.mark.parametrize("kind", sorted(status_refresh.ACTIVE_ITEM_TIME_KINDS))
def test_instatus_and_gmail_stay_active_items(kind):
    for updated in (UPDATED, None):
        scope = _provider_scope({"indicator": "none", "api_url": feed_url(kind), "source_updated_at": updated})
        assert scope["source_time"] == "active_items"


def test_no_stored_time_means_no_mark():
    assert "source_time" not in _provider_scope({"indicator": "none", "api_url": feed_url("statuspage"), "source_updated_at": None})
    assert "source_time" not in _provider_scope({"indicator": "none", "api_url": None, "source_updated_at": None})


def test_an_unlisted_address_with_a_time_is_a_page_time():
    scope = _provider_scope({"indicator": "none", "api_url": "https://status.example.com/api/v2/status.json",
                             "source_updated_at": UPDATED})
    assert scope["source_time"] == "page"


def test_the_atom_history_reading_has_no_source_time():
    scope = _atom_scope({"active_incidents": 1, "incident_updated_at": NOW, "active_maintenances": 0, "source_updated_at": None})
    assert "source_time" not in scope and scope["source_kind"] == "history_atom"


def test_the_model_takes_page_and_leaves_none_out():
    base = {"source_name": "S", "source_url": "https://status.example.com/", "indicator": "none", "fetched_at": NOW,
            "source_updated_at": UPDATED, "freshness": "fresh"}
    assert ProviderStatus(**base, source_time="page").model_dump(mode="json")["source_time"] == "page"
    assert "source_time" not in ProviderStatus(**base).model_dump(mode="json")
    with pytest.raises(ValueError):
        ProviderStatus(**base, source_time="other")


# --- C: covers ---------------------------------------------------------------------------------------------------

def test_dhl_status_page_covers_api_and_other_hosts_have_no_key():
    view = official_page_view(OFFICIAL_STATUS_PAGES["dhl.com"], "dhl.com")
    assert view == {"source_name": "DHL API Service Status", "source_url": "https://status.api.dhl.com/", "covers": "api"}
    for host, page in OFFICIAL_STATUS_PAGES.items():
        if host != "dhl.com":
            assert "covers" not in official_page_view(page, host), host
    assert official_page_view(None, "dhl.com") is None


def test_the_members_are_curated_keys_and_named_api():
    assert status_refresh.API_ONLY_STATUS_PAGES == frozenset({"dhl.com"})
    for site in status_refresh.API_ONLY_STATUS_PAGES:
        assert site in OFFICIAL_STATUS_PAGES and "API" in OFFICIAL_STATUS_PAGES[site][0]


def test_covers_is_left_out_not_null_and_sign_in_is_independent():
    plain = OfficialStatusPage(source_name="N", source_url="https://status.example.com/")
    assert plain.model_dump(mode="json") == {"source_name": "N", "source_url": "https://status.example.com/"}
    both = OfficialStatusPage(source_name="N", source_url="https://s.example.com/", covers="api", requires_sign_in=True)
    assert both.model_dump(mode="json")["covers"] == "api"
    with pytest.raises(ValueError):
        OfficialStatusPage(source_name="N", source_url="https://s.example.com/", covers="site")
    workday = official_page_view(OFFICIAL_STATUS_PAGES["workday.com"], "workday.com")
    assert "covers" not in workday and workday["requires_sign_in"] is True


# --- verdict unchanged -------------------------------------------------------------------------------------------

def statuses():
    for indicator, updated in itertools.product(("none", "minor", "major", "critical", "maintenance"),
                                                (NOW - timedelta(hours=3), NOW - timedelta(days=30), None)):
        yield {"indicator": indicator, "freshness": "fresh", "source_updated_at": updated}
    yield {"indicator": "minor", "freshness": "stale", "source_updated_at": UPDATED}


def test_the_page_mark_changes_no_recommendation_or_check():
    for status in statuses():
        marked = {**status, "source_time": "page"} if status["source_updated_at"] is not None else status
        for error_class in (None, "http_403", "timeout"):
            assert (triage.recommendation(error_class, status, None, None, now=NOW)
                    == triage.recommendation(error_class, marked, None, None, now=NOW))
            assert (triage.next_checks("site_access", status, "no_reports", now=NOW)
                    == triage.next_checks("site_access", marked, "no_reports", now=NOW))
        assert triage.provider_wait_reason(status, NOW) == triage.provider_wait_reason(marked, NOW)
        assert triage.page_stale(status, NOW) == triage.page_stale(marked, NOW)


def test_the_covers_mark_changes_no_status_row():
    page = OFFICIAL_STATUS_PAGES["dhl.com"]
    plain = {"source_name": page[0], "source_url": page[1]}
    assert response_guide.status_row(None, plain, page[1]) == response_guide.status_row(None, {**plain, "covers": "api"}, page[1])


# --- docs state the same numbers as the code ---------------------------------------------------------------------

def test_freshness_sentence_matches_fresh_hours():
    en = (ROOT / "docs" / "api-contract.en.md").read_text(encoding="utf-8")
    ko = (ROOT / "docs" / "api-contract.md").read_text(encoding="utf-8")
    llms = (ROOT / "ahn" / "llms.txt").read_text(encoding="utf-8")
    for name, text, pattern in (("en", en, r"`fresh` when `measured_at` is within (\d+) hours"),
                                ("ko", ko, r"`measured_at` 이 (\d+)시간 안이면 `fresh`"),
                                ("llms", llms, r"`freshness` is `fresh` when `measured_at` is within (\d+) hours")):
        assert re.findall(pattern, text) == [str(site_probe.FRESH_HOURS)], name


def test_docs_and_llms_state_the_meaning_marks():
    en = (ROOT / "docs" / "api-contract.en.md").read_text(encoding="utf-8")
    ko = (ROOT / "docs" / "api-contract.md").read_text(encoding="utf-8")
    llms = (ROOT / "ahn" / "llms.txt").read_text(encoding="utf-8")
    for text in (en, ko, llms):
        assert "source_time: page" in text or 'source_time: "page"' in text
        assert 'covers: "api"' in text or "covers: api" in text
        assert "current_site_status" in text and "`ahn_check`" in text
    for text in (en, llms):
        assert "need not move with incidents" in text
        assert "AHN does not assert whether a site is up" in text
