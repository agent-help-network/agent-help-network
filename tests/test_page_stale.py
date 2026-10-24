"""A minor page with no incident list (docs/decisions.md D54): pure tests, no database and no network.

The live shapes of 2026-10-24: gocardless.com showed "minor" with a page update time 17 days old and no incident list,
mediavine.com showed "minor" with no update time and no incident list. Both made every lookup wait. D45 never reached
them because it judges incident update times, and these feeds carry none.
"""

from __future__ import annotations

import itertools
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from ahn import signal_history, triage
from ahn.service import _provider_scope
from ahn.triage import (CHECKS, CHECKS_KO, PAGE_STALE_CHECK, PAGE_STALE_CHECK_KO, STALE_INCIDENT_DAYS, next_checks,
                        page_stale, page_time_unknown, provider_wait_reason, recommendation)

NOW = datetime(2026, 10, 24, 14, 30, tzinfo=timezone.utc)
WEEK = timedelta(days=STALE_INCIDENT_DAYS)
OFFICIAL = {"developer_portal": "https://dev.example.com/"}
ROBOTS_OK = {"robots_status": "ok", "robots": {"rules": []}}
WAIT = {"action": "wait_provider_incident", "confidence": "medium", "reasons": ["provider_scope"]}
UNSURE = {"action": "wait_provider_incident", "confidence": "low", "reasons": ["provider_scope", "provider_update_time_unknown"]}


def page(indicator="minor", updated=NOW - timedelta(days=17), **extra) -> dict:
    """A fresh statement from a feed with no incident list: no basis, no incident fields."""
    out = {"indicator": indicator, "freshness": "fresh", **extra}
    if updated is not None:
        out["source_updated_at"] = updated
    return out


def codes(checks) -> list[str]:
    return [item["code"] for item in checks]


def checks_for(status, **kwargs) -> list[dict]:
    return next_checks(None, status, "no_reports", now=NOW, **kwargs)


# --- the live shapes ---------------------------------------------------------------------------------------------

def test_gocardless_shape_no_longer_waits():
    status = page(updated=datetime(2026, 10, 7, 9, 0, tzinfo=timezone.utc))
    assert page_stale(status, NOW) and provider_wait_reason(status, NOW) is None
    assert recommendation(None, status, None, None, now=NOW) == recommendation(None, None, None, None, now=NOW)
    assert recommendation("site_access", status, ROBOTS_OK, OFFICIAL, now=NOW)["action"] == "use_official_path"
    checks = checks_for(status)
    assert "provider_incident_stale" in codes(checks) and "provider_scope" not in codes(checks)
    text = next(item["text"] for item in checks if item["code"] == "provider_incident_stale")
    assert text == PAGE_STALE_CHECK and "source_updated_at" in text and "incident_updated_at" not in text


def test_mediavine_shape_still_waits_with_low_confidence():
    status = page(updated=None)
    assert page_time_unknown(status, NOW) and not page_stale(status, NOW)
    assert recommendation(None, status, None, None, now=NOW) == UNSURE
    assert codes(checks_for(status)) == ["locate_failure_stage", "provider_scope", "provider_update_time_unknown",
                                         "reports_unknown"]


# --- the 7-day boundary ------------------------------------------------------------------------------------------

@pytest.mark.parametrize("age, stale", [(WEEK - timedelta(seconds=1), False), (WEEK, False),
                                        (WEEK + timedelta(seconds=1), True), (timedelta(days=400), True)])
def test_more_than_seven_days_is_stale_exactly_seven_is_not(age, stale):
    status = page(updated=NOW - age)
    assert page_stale(status, NOW) is stale and not page_time_unknown(status, NOW)
    assert recommendation(None, status, None, None, now=NOW) == (recommendation(None, None, None, None, now=NOW) if stale else WAIT)
    assert ("provider_incident_stale" if stale else "provider_scope") in codes(checks_for(status))


def test_an_iso_string_time_is_read_like_a_datetime():
    assert page_stale(page(updated="2026-10-07T09:00:00Z"), NOW)
    assert not page_stale(page(updated="2026-10-24T09:00:00+00:00"), NOW)


# --- indicators ----------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("indicator", ["major", "critical", "maintenance"])
@pytest.mark.parametrize("updated", [NOW - timedelta(days=30), None, "not a time"])
def test_only_minor_is_affected(indicator, updated):
    status = page(indicator, updated=updated)
    assert not page_stale(status, NOW) and not page_time_unknown(status, NOW)
    assert recommendation(None, status, None, None, now=NOW) == WAIT
    assert "provider_scope" in codes(checks_for(status)) and "provider_update_time_unknown" not in codes(checks_for(status))


# --- maintenance -------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("maintenances", [1, 3, True, "1"])
def test_a_maintenance_in_progress_or_an_odd_count_leaves_the_answer_alone(maintenances):
    for updated in (NOW - timedelta(days=30), None):
        status = page(updated=updated, active_maintenances=maintenances)
        assert not page_stale(status, NOW) and not page_time_unknown(status, NOW)
        assert recommendation(None, status, None, None, now=NOW) == WAIT


def test_a_zero_or_unread_maintenance_count_is_in_scope():
    for status in (page(), page(active_maintenances=0), page(active_incidents=0)):
        assert page_stale(status, NOW)


# --- times that cannot be used -------------------------------------------------------------------------------------

@pytest.mark.parametrize("updated", [None, "not a time", "2026-10-07T09:00:00", "", 1760000000, datetime(2026, 10, 7, 9, 0),
                                     NOW + timedelta(hours=1, seconds=1), NOW + timedelta(days=400)])
def test_a_missing_or_unreadable_time_is_not_old_the_wait_stays_low(updated):
    status = page(updated=updated)
    assert not page_stale(status, NOW) and page_time_unknown(status, NOW)
    assert recommendation(None, status, None, None, now=NOW) == UNSURE
    assert recommendation("login_auth", status, None, None, now=NOW) == UNSURE
    assert "provider_update_time_unknown" in codes(checks_for(status))


def test_a_time_slightly_ahead_is_a_usable_time():
    status = page(updated=NOW + timedelta(minutes=30))
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
    for status in (page(freshness="stale"), page(updated=None, freshness="stale")):
        assert recommendation(None, status, None, None, now=NOW) == recommendation(None, None, None, None, now=NOW)
        assert "provider_update_time_unknown" not in codes(checks_for(status))


# --- the other provider rows are untouched -------------------------------------------------------------------------

def scoped(indicator, *, updated, **row) -> dict:
    """A statement built from stored scope columns, as the lookup and the board build it."""
    stored = {"indicator": indicator, "max_incident_impact": None, **row}
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
    assert recommendation(None, none_time, None, None, now=NOW) == recommendation(None, None, None, None, now=NOW)


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


def test_an_empty_incident_list_with_an_unread_maintenance_count_is_judged_on_the_latest_time():
    # basis is absent (status_scope.basis needs both counts); the latest usable time decides, as on the board.
    status = scoped("minor", updated=NOW - timedelta(days=20), active_incidents=0, incident_updated_at=NOW - timedelta(days=9))
    assert "basis" not in status and page_stale(status, NOW)
    status = scoped("minor", updated=NOW - timedelta(days=20), active_incidents=0, incident_updated_at=NOW - timedelta(days=1))
    assert not page_stale(status, NOW) and recommendation(None, status, None, None, now=NOW) == WAIT


def test_a_history_atom_reading_is_never_a_d54_page():
    atom = {"indicator": None, "freshness": "fresh", "source_kind": "history_atom", "source_updated_at": None}
    assert not page_stale(atom, NOW) and not page_time_unknown(atom, NOW)
    assert recommendation(None, atom, None, None, now=NOW) == recommendation(None, None, None, None, now=NOW)
    assert "provider_unknown" in codes(checks_for(atom))
    # Even a reading carrying an indicator by mistake is not judged by D54.
    odd = {**atom, "indicator": "minor"}
    assert not page_stale(odd, NOW) and not page_time_unknown(odd, NOW)


# --- texts and switches --------------------------------------------------------------------------------------------

def test_texts_exist_in_both_languages():
    assert "provider_update_time_unknown" in CHECKS and "provider_update_time_unknown" in CHECKS_KO
    assert f"{STALE_INCIDENT_DAYS} days" in PAGE_STALE_CHECK and f"{STALE_INCIDENT_DAYS}일 넘게" in PAGE_STALE_CHECK_KO
    for text in (CHECKS["provider_update_time_unknown"], PAGE_STALE_CHECK):
        assert "source_updated_at" in text
    # The D45 text is unchanged for incidents.
    status = scoped("minor", updated=NOW, active_incidents=1, max_incident_impact="minor",
                    incident_updated_at=NOW - timedelta(days=30), active_maintenances=0)
    text = next(item["text"] for item in checks_for(status) if item["code"] == "provider_incident_stale")
    assert text == CHECKS["provider_incident_stale"]


def test_the_vendor_switch_does_not_change_d54():
    for status in (page(), page(updated=None)):
        assert (recommendation(None, status, None, None, now=NOW, vendor_aware=False)
                == recommendation(None, status, None, None, now=NOW))
        assert codes(next_checks(None, status, "no_reports", now=NOW, vendor_aware=False)) == codes(checks_for(status))


# --- the board and the lookup say the same thing about a host ------------------------------------------------------

AGES = [None, timedelta(minutes=5), WEEK, WEEK + timedelta(seconds=1), timedelta(days=30), -timedelta(days=400)]
SHAPES = [dict(), dict(active_incidents=0), dict(active_incidents=0, active_maintenances=0),
          dict(active_incidents=0, active_maintenances=1), dict(active_maintenances=1),
          dict(active_incidents=2, max_incident_impact="minor", incident_updated_at=NOW - timedelta(days=20), active_maintenances=0),
          dict(active_incidents=1, max_incident_impact="minor", incident_updated_at=NOW - timedelta(hours=1), active_maintenances=0)]


@pytest.mark.parametrize("indicator", ["minor", "major", "critical", "maintenance"])
@pytest.mark.parametrize("age, shape", list(itertools.product(AGES, SHAPES)))
def test_board_and_lookup_agree(indicator, age, shape):
    status = scoped(indicator, updated=NOW - age if age is not None else None, **shape)
    board = signal_history.board_provider(status, NOW)
    decision = recommendation(None, status, None, None, now=NOW)
    waits = decision["action"] == "wait_provider_incident"
    # A board line always implies a lookup wait (D53).
    if board is None:
        assert waits and provider_wait_reason(status, NOW) is not None
    # D54 and the board are judged by one helper: a minor page with no list the board calls stale is no lookup wait,
    # and one the board leaves out for having no time is a low-confidence lookup wait.
    if page_stale(status, NOW):
        assert board == "stale_update" and not waits
    if page_time_unknown(status, NOW):
        assert board == "no_update_time" and decision["confidence"] == "low"
        assert decision["reasons"][-1] == "provider_update_time_unknown"
    if indicator == "minor" and "basis" not in status and status.get("active_maintenances") in (None, 0):
        assert page_stale(status, NOW) or page_time_unknown(status, NOW) or (waits and board is None) or board == "no_update_time"
    # The time the board shows is the time the lookup judged.
    assert signal_history.provider_updated_at is triage.provider_updated_at


def test_the_search_page_shows_the_page_text_in_korean():
    root = Path(__file__).resolve().parents[1]
    template = (root / "ahn" / "templates" / "search.html").read_text(encoding="utf-8")
    main = (root / "ahn" / "main.py").read_text(encoding="utf-8")
    assert "'provider_page_stale' if item.code == 'provider_incident_stale'" in template
    assert '"provider_page_stale": PAGE_STALE_CHECK_KO' in main
