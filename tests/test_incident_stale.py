"""Stale provider incidents (docs/decisions.md D45): pure tests, no database and no network.

An incident a provider opened weeks ago and never updated is not a reason for every agent to wait. The sample
below is the shape of public /v1/incidents/check answers read on 2026-10-21 (only the provider_status fields
the rule reads; host names as the lookup answered them).
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from ahn import status_scope
from ahn.service import _provider_scope
from ahn.triage import (CHECKS, CHECKS_KO, STALE_INCIDENT_DAYS, incident_stale, next_checks, page_above_incidents,
                        recommendation)

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 10, 21, 14, 25, tzinfo=timezone.utc)
OFFICIAL = {"developer_portal": "https://dev.example.com/"}
ROBOTS_OK = {"robots_status": "ok", "robots": {"rules": []}}


def status(indicator="minor", *, impact="minor", incidents=1, maintenances=0, updated=NOW - timedelta(days=30),
           freshness="fresh", **extra) -> dict:
    out = {"indicator": indicator, "freshness": freshness, "active_incidents": incidents,
           "max_incident_impact": impact, "basis": status_scope.basis(indicator, incidents, maintenances), **extra}
    if maintenances is not None:
        out["active_maintenances"] = maintenances
    if updated is not None:
        out["incident_updated_at"] = updated
    return out


def codes(checks) -> list[str]:
    return [item["code"] for item in checks]


def test_the_threshold_is_seven_days_and_the_texts_say_so():
    assert STALE_INCIDENT_DAYS == 7
    assert "more than 7 days" in CHECKS["provider_incident_stale"]
    assert "7일 넘게" in CHECKS_KO["provider_incident_stale"]
    for text in (CHECKS["provider_incident_stale"], CHECKS_KO["provider_incident_stale"]):
        assert "provider_status.affected" in text


@pytest.mark.parametrize("age,stale", [
    (timedelta(days=7), False),                          # exactly seven days is not stale
    (timedelta(days=7, seconds=1), True),                # one second more is
    (timedelta(days=6, hours=23), False),
    (timedelta(days=231), True),
    (timedelta(seconds=-60), False),                     # an update time ahead of our clock is fresh
])
def test_boundary(age, stale):
    s = status(updated=NOW - age)
    assert incident_stale(s, NOW) is stale
    rec = recommendation(None, s, None, None, now=NOW)
    if stale:
        assert rec == recommendation(None, None, None, None)  # as if no provider were active
        assert "provider_incident_stale" in codes(next_checks(None, s, "no_reports", now=NOW))
    else:
        assert rec == {"action": "wait_provider_incident", "confidence": "medium", "reasons": ["provider_scope"]}
        assert "provider_scope" in codes(next_checks(None, s, "no_reports", now=NOW))


def test_an_update_time_given_as_text_is_read_the_same_way():
    assert incident_stale(status(updated="2026-10-14T14:24:59Z"), NOW)
    assert not incident_stale(status(updated="2026-10-14T14:25:00Z"), NOW)


@pytest.mark.parametrize("updated", [None, "not a time", "2026-09-01T08:29:56", 12345])
def test_an_unknown_update_time_keeps_the_wait(updated):
    s = status(updated=updated)
    assert not incident_stale(s, NOW)
    assert recommendation(None, s, None, None, now=NOW)["action"] == "wait_provider_incident"
    assert "provider_scope" in codes(next_checks(None, s, "no_reports", now=NOW))


def test_one_fresh_incident_among_old_ones_keeps_the_wait():
    page = "abcdefgh1234"
    body = {"page": {"id": page}, "incidents": [
        {"status": "investigating", "impact": "critical", "updated_at": "2026-03-04T07:38:24Z", "name": "Old A"},
        {"status": "identified", "impact": "minor", "updated_at": "2026-10-21T13:00:00Z", "name": "New"},
        {"status": "monitoring", "impact": "major", "updated_at": "2026-06-17T15:02:09Z", "name": "Old B"}]}
    parsed = status_scope.parse_incidents(body, page)
    assert parsed["updated_at"] == datetime(2026, 10, 21, 13, 0, tzinfo=timezone.utc)  # the latest of all
    s = status("critical", impact="critical", incidents=3, updated=parsed["updated_at"])
    assert recommendation(None, s, None, None, now=NOW)["action"] == "wait_provider_incident"


def test_an_incident_without_an_update_time_makes_the_latest_update_unknown():
    page = "abcdefgh1234"
    body = {"page": {"id": page}, "incidents": [
        {"status": "investigating", "impact": "minor", "updated_at": "2026-03-04T07:38:24Z", "name": "Old"},
        {"status": "investigating", "impact": "minor", "name": "No time"}]}
    assert status_scope.parse_incidents(body, page)["updated_at"] is None
    # Resolved items are skipped before they count.
    body["incidents"][1]["status"] = "resolved"
    assert status_scope.parse_incidents(body, page)["updated_at"] == datetime(2026, 3, 4, 7, 38, 24, tzinfo=timezone.utc)


@pytest.mark.parametrize("maintenances", [1, 3, None])
def test_a_maintenance_in_progress_or_an_unread_maintenance_list_keeps_the_wait(maintenances):
    # Cloudflare on 2026-10-21: WARP incident 50 days old, three data-center maintenances in progress.
    s = status("major", impact="minor", maintenances=maintenances)
    assert not incident_stale(s, NOW)
    assert recommendation(None, s, None, None, now=NOW)["reasons"][0] == "provider_scope"
    assert "provider_incident_stale" not in codes(next_checks(None, s, "no_reports", now=NOW))


@pytest.mark.parametrize("indicator,impact,waits", [
    ("major", "minor", True), ("critical", "major", True), ("critical", "none", True), ("major", None, True),
    ("major", "major", False), ("critical", "critical", False), ("major", "critical", False),
    ("minor", "none", False), ("minor", "major", False), ("maintenance", "minor", False),
])
def test_a_major_or_critical_page_above_stale_incidents_still_waits(indicator, impact, waits):
    s = status(indicator, impact=impact)
    assert incident_stale(s, NOW) and page_above_incidents(s) is waits
    partial = status_scope.scope_partial(indicator, 1, impact, False)
    rec = recommendation(None, s, None, None, now=NOW)
    if waits:
        # Not regional: medium, even though "worst incident below the page" makes the scope partial.
        assert rec == {"action": "wait_provider_incident", "confidence": "medium", "reasons": ["provider_incident_stale"]}
    else:
        assert rec["action"] != "wait_provider_incident"
    checks = codes(next_checks(None, s, "no_reports", provider_partial=partial, now=NOW))
    assert "provider_incident_stale" in checks and "provider_scope" not in checks


def test_stale_incidents_fall_through_to_the_same_rows_as_no_provider():
    s = status("major", impact="major")
    for error_class, profile, official in [(None, ROBOTS_OK, None), ("site_access", None, OFFICIAL),
                                           ("human_verification", None, None), ("login_auth", None, None)]:
        assert recommendation(error_class, s, profile, official, now=NOW) == recommendation(error_class, None, profile, official)


def test_components_only_is_unchanged():
    for indicator in ("minor", "maintenance"):
        s = status(indicator, impact="none", incidents=0, updated=None)
        assert s["basis"] == "components_only"
        assert recommendation(None, s, None, None, now=NOW + timedelta(days=400)) == recommendation(None, None, None, None)
    for indicator in ("major", "critical"):
        s = status(indicator, impact="none", incidents=0, updated=None)
        expected = {"action": "wait_provider_incident", "confidence": "medium", "reasons": ["provider_components_only"]}
        assert recommendation(None, s, None, None, now=NOW + timedelta(days=400)) == expected
        # The partial flag is never set for components_only (no incident to compare), and the row ignores it anyway.
        assert recommendation(None, s, None, None, provider_regional=True, now=NOW) == expected
        assert codes(next_checks("site_access", s, "no_reports", now=NOW))[1] == "provider_components_only"


MEDIUM = {"action": "wait_provider_incident", "confidence": "medium"}


def test_confidence_is_never_lower_with_an_incident_than_without_one():
    # Review of PR #67: a major page with nothing announced waits with medium confidence (A); a fresh minor incident
    # under the same page must not lower that (B), and neither may a stale one under it (C). The scope is "partial"
    # (worst incident below the page) in B and C, which next_checks reports, but only a regional scope lowers it.
    a = status("major", impact="none", incidents=0, updated=None)
    assert a["basis"] == "components_only"
    assert recommendation(None, a, None, None, now=NOW) == {**MEDIUM, "reasons": ["provider_components_only"]}
    for age, reason in ((timedelta(minutes=10), "provider_scope"), (timedelta(days=30), "provider_incident_stale")):
        s = status("major", impact="minor", updated=NOW - age)
        assert status_scope.scope_partial("major", 1, "minor", False)
        assert recommendation(None, s, None, None, provider_regional=False, now=NOW) == {**MEDIUM, "reasons": [reason]}
        checks = codes(next_checks("site_access", s, "no_reports", provider_partial=True, now=NOW))
        assert checks[1:3] == [reason, "provider_scope_partial"]


def test_only_a_regional_scope_lowers_a_wait_to_low():
    fresh = status("major", impact="major", updated=NOW - timedelta(hours=3))
    assert recommendation(None, fresh, None, None, now=NOW) == {**MEDIUM, "reasons": ["provider_scope"]}
    assert recommendation(None, fresh, None, None, provider_regional=True, now=NOW) == {
        "action": "wait_provider_incident", "confidence": "low", "reasons": ["provider_scope", "provider_scope_partial"]}
    stale_above = status("critical", impact="minor")
    assert recommendation(None, stale_above, None, None, provider_regional=True, now=NOW)["confidence"] == "low"
    # The reasons stay codes that next_checks also carries.
    checks = codes(next_checks("site_access", fresh, "no_reports", provider_partial=True, now=NOW))
    assert checks[1:3] == ["provider_scope", "provider_scope_partial"]


def test_a_stale_feed_is_not_read_at_all():
    s = status(freshness="stale", updated=NOW - timedelta(hours=1))
    assert recommendation(None, s, None, None, now=NOW)["action"] != "wait_provider_incident"
    assert "provider_unknown" in codes(next_checks(None, s, "no_reports", now=NOW))


def test_the_lookup_reads_the_stored_row_and_its_own_clock():
    row = {"indicator": "major", "active_incidents": 1, "max_incident_impact": "major", "active_maintenances": 0,
           "incident_updated_at": NOW - timedelta(days=29), "affected": ["AWS EC2 Health: me-south-1"]}
    s = {"freshness": "fresh", "indicator": "major", **_provider_scope(row)}
    assert incident_stale(s, NOW) and not incident_stale(s, NOW - timedelta(days=25))
    assert recommendation(None, s, None, None) == recommendation(None, None, None, None)  # default clock: now


# The 2026-10-21 public sample (provider_status fields only). Hosts whose answer leaves wait_provider_incident.
SAMPLE = {
    "checkpoint.com": ("major", "critical", 8, 0, "2026-03-04T07:38:24Z", False),
    "tenable.com": ("major", "major", 1, 0, "2026-06-17T15:02:09Z", False),
    "kraken.com": ("minor", "minor", 2, 1, "2026-08-19T14:00:55Z", True),
    "cloudflare.com": ("major", "minor", 1, 3, "2026-09-01T08:29:56Z", True),
    "mypurecloud.com": ("major", "critical", 1, 0, "2026-09-18T19:39:39Z", False),
    "elastic.co": ("major", "major", 1, 0, "2026-09-21T15:21:45Z", False),
    "ledger.com": ("minor", "minor", 1, 0, "2026-09-25T12:03:48Z", False),
    "eset.com": ("minor", "minor", 1, None, "2026-09-29T18:14:12Z", True),
    "gsu.edu": ("minor", "minor", 2, 0, "2026-09-30T13:20:31Z", False),
    "heapanalytics.com": ("minor", "major", 1, 0, "2026-10-02T21:57:15Z", False),
    "archiveofourown.org": ("major", "critical", 1, 0, "2026-10-21T04:42:18Z", True),
    "datto.com": ("minor", "critical", 4, 0, "2026-10-20T19:55:44Z", True),
    "flutterwave.com": ("minor", "critical", 3, 0, "2026-10-21T13:17:23Z", True),
    "netlify.com": ("minor", "minor", 1, 0, "2026-10-21T14:20:31Z", True),
    "godaddy.com": ("minor", "minor", 1, 0, "2026-10-20T16:39:44Z", True),
}


@pytest.mark.parametrize("host", sorted(SAMPLE))
def test_the_live_sample(host):
    indicator, impact, incidents, maintenances, updated, waits = SAMPLE[host]
    s = status(indicator, impact=impact, incidents=incidents, maintenances=maintenances, updated=updated)
    rec = recommendation(None, s, None, None, now=NOW)  # none of these is regional-only (Cloudflare names WARP)
    assert (rec["action"] == "wait_provider_incident") is waits
    if waits:
        assert rec["confidence"] == "medium"


def test_contract_readme_and_plugin_carry_the_code_and_the_threshold():
    ko = (ROOT / "docs" / "api-contract.md").read_text(encoding="utf-8")
    en = (ROOT / "docs" / "api-contract.en.md").read_text(encoding="utf-8")
    assert "모두 35개 코드" in ko and "`provider_incident_stale`" in ko and "7일(`STALE_INCIDENT_DAYS`" in ko
    assert "| `provider_incident_stale` |" in en and "**more than** 7 days (`STALE_INCIDENT_DAYS`" in en
    for name in ("README.md", "README.ko.md"):
        assert "provider_incident_stale" in (ROOT / name).read_text(encoding="utf-8")
    plugin = ROOT / "plugins" / "claude-code" / "ahn"
    assert "| `provider_incident_stale` |" in (plugin / "README.md").read_text(encoding="utf-8")
    version = json.loads((plugin / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))["version"]
    assert f"version {version} |" in (plugin / "README.md").read_text(encoding="utf-8")
    decisions = (ROOT / "docs" / "decisions.md").read_text(encoding="utf-8")
    assert "| D45 (" in decisions and "후속 별도 결정" in decisions
    # Only a regional-only scope lowers confidence, and the stale-incident trade-off is written down (review of #67).
    assert en.count("medium (low when regional-only)") == 2 and ko.count("medium(지역·거점 전용이면 low)") == 2
    assert "231-day-old `critical` incident: no wait" in en and "231일 된 `critical` 사건 → 대기 없음" in ko
    assert "`provider_scope_partial` is information only" not in en and "never changes `recommendation`" not in en.split("5. `provider_scope`")[1][:2000]
