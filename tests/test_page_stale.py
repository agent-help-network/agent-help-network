"""A minor page with no incident list (docs/decisions.md D54): pure tests, no database and no network.

The live shapes of 2026-10-24: gocardless.com (an incident.io page answering Statuspage's status.json) showed "minor"
with a page update time 17 days old and no incident list; mediavine.com (Instatus) showed "minor" with no update time
and no incident list. Both made every lookup wait. D45 never reached them because it judges incident update times.

The same day showed that an incident.io page's page.updated_at does not follow its incidents (status.openai.com kept
2026-10-16 while at least 8 incidents came and went on 2026-10-21 to 2026-10-23), so an old page time is a reason to
lift a wait only on feeds whose time is that of their active incidents and maintenances: Instatus and Google Gmail
(provider_status.source_time "active_items"). Every other feed keeps waiting exactly as before.
"""

from __future__ import annotations

import itertools
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from ahn import signal_history, status_refresh, triage
from ahn.models import ProviderStatus
from ahn.service import _provider_scope
from ahn.status_refresh import ACTIVE_ITEM_TIME_FEEDS, ACTIVE_ITEM_TIME_KINDS, SOURCES
from ahn.triage import (CHECKS, CHECKS_KO, PAGE_STALE_CHECK, PAGE_STALE_CHECK_KO, STALE_INCIDENT_DAYS, next_checks,
                        page_stale, page_time_unknown, provider_wait_reason, recommendation)

NOW = datetime(2026, 10, 24, 14, 30, tzinfo=timezone.utc)
WEEK = timedelta(days=STALE_INCIDENT_DAYS)
OFFICIAL = {"developer_portal": "https://dev.example.com/"}
ROBOTS_OK = {"robots_status": "ok", "robots": {"rules": []}}
WAIT = {"action": "wait_provider_incident", "confidence": "medium", "reasons": ["provider_scope"]}
UNSURE = {"action": "wait_provider_incident", "confidence": "low", "reasons": ["provider_scope", "provider_update_time_unknown"]}
ITEMS = {"source_time": "active_items"}
INSTATUS_URL = "https://status.mediavine.com/summary.json"
INCIDENT_IO_URL = "https://www.gocardless-status.com/api/v2/status.json"
BETTERSTACK_URL = "https://status.huggingface.co/index.json"
GMAIL_URL = "https://www.google.com/appsstatus/dashboard/incidents.json"


def page(indicator="minor", updated=NOW - timedelta(days=17), **extra) -> dict:
    """A fresh statement from a feed with no incident list: no basis, no incident fields. Pass **ITEMS for a feed whose
    time is that of its active items (Instatus, Google Gmail); without it the time is a page-wide one."""
    out = {"indicator": indicator, "freshness": "fresh", **extra}
    if updated is not None:
        out["source_updated_at"] = updated
    return out


def codes(checks) -> list[str]:
    return [item["code"] for item in checks]


def checks_for(status, **kwargs) -> list[dict]:
    return next_checks(None, status, "no_reports", now=NOW, **kwargs)


def no_provider(error_class=None) -> dict:
    return recommendation(error_class, None, None, None, now=NOW)


# --- which feeds carry an active-item time --------------------------------------------------------------------------

def test_only_instatus_and_gmail_addresses_are_active_item_feeds():
    assert ACTIVE_ITEM_TIME_KINDS == {"instatus", "google_gmail"}
    kinds: dict[str, set[str]] = {}
    for source in SOURCES.values():
        kinds.setdefault(source.api_url, set()).add(source.kind)
    # One address, one parser: matching a stored api_url is exact.
    assert all(len(found) == 1 for found in kinds.values())
    assert ACTIVE_ITEM_TIME_FEEDS == {url for url, found in kinds.items() if found & ACTIVE_ITEM_TIME_KINDS}
    assert INSTATUS_URL in ACTIVE_ITEM_TIME_FEEDS and GMAIL_URL in ACTIVE_ITEM_TIME_FEEDS
    assert INCIDENT_IO_URL not in ACTIVE_ITEM_TIME_FEEDS and BETTERSTACK_URL not in ACTIVE_ITEM_TIME_FEEDS
    assert not any(url.endswith(status_refresh.STATUS_JSON_SUFFIX) for url in ACTIVE_ITEM_TIME_FEEDS)


@pytest.mark.parametrize("api_url, marked", [
    (INSTATUS_URL, True), (GMAIL_URL, True), (INCIDENT_IO_URL, False), (BETTERSTACK_URL, False),
    ("https://www.cloudflarestatus.com/api/v2/status.json", False),
    ("https://status.example.com/api/v2/status.json", False),  # a discovered Statuspage page
    ("https://status.example.com/summary.json", False),  # an Instatus-looking address nobody listed: not assumed
    ("https://status.mediavine.com/summary.json?x=1", False), (None, False), ("", False)])
def test_the_stored_feed_address_alone_decides_source_time(api_url, marked):
    scope = _provider_scope({"indicator": "minor", "api_url": api_url})
    assert ("source_time" in scope) is marked
    if marked:
        assert scope["source_time"] == "active_items"


def test_source_time_is_a_public_field_left_out_when_absent():
    base = {"source_name": "S", "source_url": "https://status.example.com/", "indicator": "minor", "fetched_at": NOW,
            "source_updated_at": None, "freshness": "fresh"}
    assert ProviderStatus(**base, **ITEMS).model_dump(mode="json")["source_time"] == "active_items"
    assert "source_time" not in ProviderStatus(**base).model_dump(mode="json", exclude_none=False)
    with pytest.raises(ValueError):
        ProviderStatus(**base, source_time="page")


# --- the live shapes ---------------------------------------------------------------------------------------------

def test_gocardless_shape_incident_io_keeps_waiting_with_unchanged_confidence():
    # incident.io: page.updated_at did not follow incidents (D54), so an old one lifts nothing and lowers nothing.
    status = {**page(updated=datetime(2026, 10, 7, 10, 57, tzinfo=timezone.utc)),
              **_provider_scope({"indicator": "minor", "api_url": INCIDENT_IO_URL})}
    assert "source_time" not in status
    assert not page_stale(status, NOW) and not page_time_unknown(status, NOW)
    assert provider_wait_reason(status, NOW) == "provider_scope"
    assert recommendation(None, status, None, None, now=NOW) == WAIT
    assert recommendation("site_access", status, ROBOTS_OK, OFFICIAL, now=NOW) == WAIT
    assert codes(checks_for(status)) == ["locate_failure_stage", "provider_scope", "reports_unknown"]
    # The board keeps leaving it out as before (stale_update), and the lookup keeps waiting: a board line still
    # implies a lookup wait, never the other way round (D53).
    assert signal_history.board_provider(status, NOW) == "stale_update"


def test_an_instatus_page_with_an_old_active_item_time_no_longer_waits():
    status = page(updated=datetime(2026, 10, 7, 9, 0, tzinfo=timezone.utc), **ITEMS)
    assert page_stale(status, NOW) and provider_wait_reason(status, NOW) is None
    assert recommendation(None, status, None, None, now=NOW) == no_provider()
    assert recommendation("site_access", status, ROBOTS_OK, OFFICIAL, now=NOW)["action"] == "use_official_path"
    checks = checks_for(status)
    assert "provider_incident_stale" in codes(checks) and "provider_scope" not in codes(checks)
    text = next(item["text"] for item in checks if item["code"] == "provider_incident_stale")
    assert text == PAGE_STALE_CHECK and "source_updated_at" in text and "incident_updated_at" not in text
    assert signal_history.board_provider(status, NOW) == "stale_update"


def test_mediavine_shape_still_waits_with_low_confidence():
    for kind in ({}, ITEMS):
        status = page(updated=None, **kind)
        assert page_time_unknown(status, NOW) and not page_stale(status, NOW)
        assert recommendation(None, status, None, None, now=NOW) == UNSURE
        assert codes(checks_for(status)) == ["locate_failure_stage", "provider_scope", "provider_update_time_unknown",
                                             "reports_unknown"]


# --- feeds whose time is the page's own are never released -----------------------------------------------------------

@pytest.mark.parametrize("api_url", [INCIDENT_IO_URL, BETTERSTACK_URL, "https://www.cloudflarestatus.com/api/v2/status.json",
                                     "https://status.example.com/api/v2/status.json", None])
@pytest.mark.parametrize("age", [timedelta(hours=1), WEEK + timedelta(seconds=1), timedelta(days=60), timedelta(days=400)])
def test_a_page_wide_time_however_old_keeps_the_wait_and_its_confidence(api_url, age):
    status = {**page(updated=NOW - age), **_provider_scope({"indicator": "minor", "api_url": api_url})}
    assert not page_stale(status, NOW) and not page_time_unknown(status, NOW)
    assert recommendation(None, status, None, None, now=NOW) == WAIT
    assert recommendation(None, status, None, None, provider_regional=True, now=NOW)["confidence"] == "low"
    assert "provider_incident_stale" not in codes(checks_for(status))
    assert "provider_update_time_unknown" not in codes(checks_for(status))


# --- the 7-day boundary ------------------------------------------------------------------------------------------

@pytest.mark.parametrize("age, stale", [(WEEK - timedelta(seconds=1), False), (WEEK, False),
                                        (WEEK + timedelta(seconds=1), True), (timedelta(days=400), True)])
def test_more_than_seven_days_is_stale_exactly_seven_is_not(age, stale):
    status = page(updated=NOW - age, **ITEMS)
    assert page_stale(status, NOW) is stale and not page_time_unknown(status, NOW)
    assert recommendation(None, status, None, None, now=NOW) == (no_provider() if stale else WAIT)
    assert ("provider_incident_stale" if stale else "provider_scope") in codes(checks_for(status))


def test_an_iso_string_time_is_read_like_a_datetime():
    assert page_stale(page(updated="2026-10-07T09:00:00Z", **ITEMS), NOW)
    assert not page_stale(page(updated="2026-10-24T09:00:00+00:00", **ITEMS), NOW)


def test_an_unknown_source_time_value_is_not_active_items():
    for value in ("page", "ACTIVE_ITEMS", True, 1, None):
        assert not page_stale(page(source_time=value), NOW)


# --- indicators ----------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("indicator", ["major", "critical", "maintenance"])
@pytest.mark.parametrize("updated", [NOW - timedelta(days=30), None, "not a time"])
@pytest.mark.parametrize("kind", [{}, ITEMS])
def test_only_minor_is_affected(indicator, updated, kind):
    status = page(indicator, updated=updated, **kind)
    assert not page_stale(status, NOW) and not page_time_unknown(status, NOW)
    assert recommendation(None, status, None, None, now=NOW) == WAIT
    assert "provider_scope" in codes(checks_for(status)) and "provider_update_time_unknown" not in codes(checks_for(status))


# --- maintenance -------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("maintenances", [1, 3, True, False, "1", "0", 0.0])
def test_a_maintenance_in_progress_or_an_odd_count_leaves_the_answer_alone(maintenances):
    # False is not a count of 0 (bool is not a whole number here), and neither is "0" or 0.0.
    for updated in (NOW - timedelta(days=30), None):
        status = page(updated=updated, active_maintenances=maintenances, **ITEMS)
        assert not page_stale(status, NOW) and not page_time_unknown(status, NOW)
        assert recommendation(None, status, None, None, now=NOW) == WAIT


def test_a_zero_or_unread_maintenance_count_is_in_scope():
    for status in (page(**ITEMS), page(active_maintenances=0, **ITEMS), page(active_incidents=0, **ITEMS)):
        assert page_stale(status, NOW)


def test_the_indicator_does_not_say_maintenance_so_a_counted_one_is_what_holds():
    # 2026-10-22: www.cloudflarestatus.com showed major with two maintenances in progress and no incident. The
    # indicator is not read as "no maintenance": a counted one keeps the wait whatever the page shows.
    status = page(updated=NOW - timedelta(days=30), active_maintenances=2, **ITEMS)
    assert recommendation(None, status, None, None, now=NOW) == WAIT
    major = scoped("major", updated=NOW - timedelta(days=58), active_incidents=0, active_maintenances=2)
    assert major["basis"] == "maintenance" and recommendation(None, major, None, None, now=NOW) == WAIT


# --- times that cannot be used -------------------------------------------------------------------------------------

@pytest.mark.parametrize("updated", [None, "not a time", "2026-10-07T09:00:00", "", 1760000000, datetime(2026, 10, 7, 9, 0),
                                     NOW + timedelta(hours=1, seconds=1), NOW + timedelta(days=400)])
@pytest.mark.parametrize("kind", [{}, ITEMS])
def test_a_missing_or_unreadable_time_is_not_old_the_wait_stays_low(updated, kind):
    status = page(updated=updated, **kind)
    assert not page_stale(status, NOW) and page_time_unknown(status, NOW)
    assert recommendation(None, status, None, None, now=NOW) == UNSURE
    assert recommendation("login_auth", status, None, None, now=NOW) == UNSURE
    assert "provider_update_time_unknown" in codes(checks_for(status))


def test_a_time_slightly_ahead_is_a_usable_time():
    status = page(updated=NOW + timedelta(minutes=30), **ITEMS)
    assert not page_stale(status, NOW) and not page_time_unknown(status, NOW)
    assert recommendation(None, status, None, None, now=NOW) == WAIT


def test_regional_scope_and_unknown_time_both_show():
    status = page(updated=None)
    assert recommendation(None, status, None, None, provider_regional=True, now=NOW) == {
        "action": "wait_provider_incident", "confidence": "low",
        "reasons": ["provider_scope", "provider_scope_partial", "provider_update_time_unknown"]}
    assert codes(checks_for(status, provider_partial=True))[-3:] == ["provider_scope_partial", "provider_update_time_unknown",
                                                                     "reports_unknown"]


def test_a_stale_feed_is_never_judged():
    for status in (page(freshness="stale", **ITEMS), page(updated=None, freshness="stale")):
        assert recommendation(None, status, None, None, now=NOW) == no_provider()
        assert "provider_update_time_unknown" not in codes(checks_for(status))


# --- the other provider rows are untouched -------------------------------------------------------------------------

def scoped(indicator, *, updated, api_url=None, **row) -> dict:
    """A statement built from stored scope columns, as the lookup and the board build it."""
    stored = {"indicator": indicator, "max_incident_impact": None, "api_url": api_url, **row}
    return {"indicator": indicator, "source_updated_at": updated, "freshness": "fresh", **_provider_scope(stored)}


def test_components_only_keeps_the_d43_rule():
    old = NOW - timedelta(days=30)
    minor = scoped("minor", updated=old, active_incidents=0, active_maintenances=0)
    assert minor["basis"] == "components_only" and not page_stale(minor, NOW)
    assert codes(checks_for(minor))[1] == "provider_components_only"
    major = scoped("major", updated=old, active_incidents=0, active_maintenances=0)
    assert recommendation(None, major, None, None, now=NOW)["reasons"] == ["provider_components_only"]
    none_time = scoped("minor", updated=None, active_incidents=0, active_maintenances=0)
    assert not page_time_unknown(none_time, NOW)
    assert recommendation(None, none_time, None, None, now=NOW) == no_provider()


def test_announced_incidents_keep_the_d45_rule():
    # A fresh incident under an old page time: basis incident, D54 does not apply, the answer waits.
    fresh = scoped("minor", updated=NOW - timedelta(days=40), active_incidents=1, max_incident_impact="minor",
                   incident_updated_at=NOW - timedelta(hours=2), active_maintenances=0)
    assert fresh["basis"] == "incident" and not page_stale(fresh, NOW)
    assert recommendation(None, fresh, None, None, now=NOW) == WAIT
    # An incident list with no maintenance count and no incident time: D45 says unknown, D54 does not apply either.
    unknown = scoped("minor", updated=NOW - timedelta(days=40), active_incidents=1, max_incident_impact="minor")
    assert recommendation(None, unknown, None, None, now=NOW) == WAIT
    # A page in maintenance: out of D54's scope (indicator maintenance, basis maintenance).
    work = scoped("maintenance", updated=NOW - timedelta(days=57), active_incidents=0, active_maintenances=2)
    assert recommendation(None, work, None, None, now=NOW) == WAIT


@pytest.mark.parametrize("missed", [dict(), dict(active_incidents=0), dict(active_maintenances=0),
                                    dict(active_incidents=0, incident_updated_at=NOW - timedelta(days=9))])
def test_a_statuspage_read_that_missed_its_lists_keeps_the_wait(missed):
    # A Statuspage read whose lists failed, were skipped by the time budget, rest after failures or are disallowed
    # by robots.txt has no basis this time (D46). Its page time is the page's own, so D54 never lifts the wait.
    status = scoped("minor", updated=NOW - timedelta(days=20), api_url="https://www.cloudflarestatus.com/api/v2/status.json",
                    **missed)
    assert "basis" not in status and "source_time" not in status
    assert not page_stale(status, NOW)
    assert recommendation(None, status, None, None, now=NOW) == WAIT


def test_a_history_atom_reading_is_never_a_d54_page():
    atom = {"indicator": None, "freshness": "fresh", "source_kind": "history_atom", "source_updated_at": None}
    assert not page_stale(atom, NOW) and not page_time_unknown(atom, NOW)
    assert recommendation(None, atom, None, None, now=NOW) == no_provider()
    assert "provider_unknown" in codes(checks_for(atom))
    # Even a reading carrying an indicator and an active-item time by mistake is not judged by D54.
    odd = {**atom, "indicator": "minor", **ITEMS, "source_updated_at": NOW - timedelta(days=30)}
    assert not page_stale(odd, NOW) and not page_time_unknown(odd, NOW)


# --- texts and switches --------------------------------------------------------------------------------------------

def test_texts_exist_in_both_languages():
    assert "provider_update_time_unknown" in CHECKS and "provider_update_time_unknown" in CHECKS_KO
    assert f"{STALE_INCIDENT_DAYS} days" in PAGE_STALE_CHECK and f"{STALE_INCIDENT_DAYS}일 넘게" in PAGE_STALE_CHECK_KO
    for text in (CHECKS["provider_update_time_unknown"], PAGE_STALE_CHECK, PAGE_STALE_CHECK_KO):
        assert "source_updated_at" in text
    for text in (PAGE_STALE_CHECK, PAGE_STALE_CHECK_KO):
        assert "active_items" in text
    # The page text claims no maintenance count it does not have.
    assert "no maintenance" not in PAGE_STALE_CHECK and "점검도 없" not in PAGE_STALE_CHECK_KO
    # The D45 text is unchanged for incidents.
    status = scoped("minor", updated=NOW, active_incidents=1, max_incident_impact="minor",
                    incident_updated_at=NOW - timedelta(days=30), active_maintenances=0)
    text = next(item["text"] for item in checks_for(status) if item["code"] == "provider_incident_stale")
    assert text == CHECKS["provider_incident_stale"]


def test_the_vendor_switch_does_not_change_d54():
    for status in (page(**ITEMS), page(updated=None), page()):
        assert (recommendation(None, status, None, None, now=NOW, vendor_aware=False)
                == recommendation(None, status, None, None, now=NOW))
        assert codes(next_checks(None, status, "no_reports", now=NOW, vendor_aware=False)) == codes(checks_for(status))


# --- the board and the lookup say the same thing about a host ------------------------------------------------------

AGES = [None, timedelta(minutes=5), WEEK, WEEK + timedelta(seconds=1), timedelta(days=30), -timedelta(days=400)]
SHAPES = [dict(), dict(active_incidents=0), dict(active_incidents=0, active_maintenances=0),
          dict(active_incidents=0, active_maintenances=1), dict(active_maintenances=1), dict(active_maintenances=0),
          dict(active_incidents=2, max_incident_impact="minor", incident_updated_at=NOW - timedelta(days=20), active_maintenances=0),
          dict(active_incidents=1, max_incident_impact="minor", incident_updated_at=NOW - timedelta(hours=1), active_maintenances=0)]
FEEDS = [None, INCIDENT_IO_URL, BETTERSTACK_URL, INSTATUS_URL, GMAIL_URL]


@pytest.mark.parametrize("indicator", ["minor", "major", "critical", "maintenance"])
@pytest.mark.parametrize("age, shape, api_url", list(itertools.product(AGES, SHAPES, FEEDS)))
def test_board_and_lookup_agree(indicator, age, shape, api_url):
    status = scoped(indicator, updated=NOW - age if age is not None else None, api_url=api_url, **shape)
    board = signal_history.board_provider(status, NOW)
    decision = recommendation(None, status, None, None, now=NOW)
    waits = decision["action"] == "wait_provider_incident"
    # A board line always implies a lookup wait (D53).
    if board is None:
        assert waits and provider_wait_reason(status, NOW) is not None
    # D54 and the board are judged by one helper: a minor page the lookup no longer waits on is one the board calls
    # stale, and one the board leaves out for having no time is a low-confidence lookup wait.
    if page_stale(status, NOW):
        assert api_url in ACTIVE_ITEM_TIME_FEEDS
        assert board == "stale_update" and not waits
    if page_time_unknown(status, NOW):
        assert board == "no_update_time" and decision["confidence"] == "low"
        assert decision["reasons"][-1] == "provider_update_time_unknown"
    # A feed whose time is the page's own never stops waiting on a minor page with no basis, and its confidence is
    # what it was before D54 (medium), unless it has no usable time at all.
    if api_url not in ACTIVE_ITEM_TIME_FEEDS and indicator == "minor" and "basis" not in status:
        assert waits
        if not page_time_unknown(status, NOW):
            assert decision == WAIT
    # The time the board shows is the time the lookup judged.
    assert signal_history.provider_updated_at is triage.provider_updated_at


def test_the_search_page_shows_the_page_text_in_korean():
    root = Path(__file__).resolve().parents[1]
    template = (root / "ahn" / "templates" / "search.html").read_text(encoding="utf-8")
    main = (root / "ahn" / "main.py").read_text(encoding="utf-8")
    assert "'provider_page_stale' if item.code == 'provider_incident_stale'" in template
    assert '"provider_page_stale": PAGE_STALE_CHECK_KO' in main
