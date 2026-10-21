"""Signal history rules that need no database: classification, hold rule, curated scope, retention numbers."""

from __future__ import annotations

import inspect
import os
from datetime import datetime, timedelta, timezone

os.environ.setdefault("AHN_DATABASE_URL", "postgresql://invalid/unused")
os.environ.setdefault("AHN_KEY_PEPPER", "x" * 32)

import pytest

from ahn import access_profile, bundle, platform_metrics, signal_history
from ahn.official_paths import OFFICIAL_PATHS
from ahn.signal_history import FAILURE_OUTCOMES, classify, hold_decision
from ahn.status_refresh import OFFICIAL_STATUS_PAGES, SOURCES


@pytest.mark.parametrize("status,detail,outcome", [
    ("ok", "http_200", "responding"), ("absent", "http_404", "responding"), ("absent", "http_410", "responding"),
    ("redirected", "redirect:example.net", "responding"), ("invalid", "html_body", "responding"),
    ("too_large", "too_large", "responding"),
    ("error", "dns_failed", "no_response:dns"), ("error", "dns_timeout", "no_response:dns"),
    ("error", "connect_error", "no_response:connect"), ("error", "connect_timeout", "no_response:connect"),
    ("error", "read_timeout", "no_response:timeout"), ("error", "read_error", "no_response:timeout"),
    ("error", "tls_error", "tls_error"),
    ("error", "http_500", "server_error"), ("error", "http_502", "server_error"), ("error", "http_504", "server_error"),
    ("error", "http_520", "server_error"), ("error", "http_530", "server_error"),
    ("error", "http_400", "responding"), ("error", "http_451", "responding"),
    ("refused", "http_401", "refused"), ("refused", "http_403", "refused"), ("refused", "http_429", "refused"),
    ("refused", "http_503", "refused"),
    ("error", "something_new", "other"),
    ("not_public", "not_public", None), ("error", "invalid_input", None), ("pending", None, None),
])
def test_read_outcomes_follow_the_design_table(status, detail, outcome):
    assert classify(status, detail) == outcome


def test_refusals_are_never_failures():
    # 401, 403, 429 and 503 are the reader's refusals (D27) and are never counted as a problem with the site.
    assert access_profile.REFUSAL_STATUSES == frozenset({401, 403, 429, 503})
    for code in access_profile.REFUSAL_STATUSES:
        assert classify("refused", f"http_{code}") not in FAILURE_OUTCOMES
    assert classify("error", "http_503") != "server_error"


def _events(failing: int, total: int, outcome: str = "no_response:connect") -> list[dict]:
    rows = [{"site_host": f"fail{i}.example", "a_outcome": outcome} for i in range(failing)]
    rows += [{"site_host": f"ok{i}.example", "a_outcome": "responding"} for i in range(total - failing)]
    return rows


def test_hold_rule_for_simultaneous_failures():
    assert hold_decision(_events(5, 10), False) == (False, None)            # 5 is not more than max(5, 2)
    assert hold_decision(_events(6, 10), False) == (True, "simultaneous_failures")
    assert hold_decision(_events(6, 40), False) == (False, None)            # 20% of 40 reads is 8
    assert hold_decision(_events(9, 40), False) == (True, "simultaneous_failures")
    # A shared infrastructure provider's own incident explains it: not held, but marked.
    assert hold_decision(_events(9, 40), True) == (False, "infra_provider_reported")


def test_hold_rule_for_dns_and_tls_clusters():
    assert hold_decision(_events(5, 40, "no_response:dns"), False) == (True, "dns_cluster")
    assert hold_decision(_events(4, 40, "no_response:dns"), False) == (False, None)
    assert hold_decision(_events(5, 40, "tls_error"), False) == (True, "tls_cluster")
    assert hold_decision(_events(5, 40, "tls_error"), True) == (False, "infra_provider_reported")
    # Many reads of one host count once.
    same_host = [{"site_host": "one.example", "a_outcome": "no_response:dns"}] * 8
    assert hold_decision(same_host, False) == (False, None)


def test_hold_rule_counts_only_hosts_with_a_baseline():
    # Seven dead names fail with DNS next to one real host: nothing in the window with a baseline points at us.
    events = _events(7, 7, "no_response:dns") + [{"site_host": "real.example", "a_outcome": "no_response:connect"}]
    assert hold_decision(events, False) == (True, "simultaneous_failures")  # counting every host
    assert hold_decision(events, False, {"real.example"}) == (False, None)
    assert hold_decision(events, False, {f"fail{i}.example" for i in range(5)}) == (True, "dns_cluster")
    assert hold_decision(events, False, set()) == (False, None)


@pytest.mark.parametrize("profile,expected", [
    ({"robots_status": "error", "robots_detail": "connect_timeout", "read_host": "x.org",
      "www_attempt": {"robots_status": "refused", "robots_detail": "http_403"}}, ("refused", "http_403", "www.x.org", "refused")),
    ({"robots_status": "error", "robots_detail": "tls_error", "read_host": "x.org",
      "www_attempt": {"robots_status": "redirected", "robots_detail": "redirect:y.org"}},
     ("redirected", "redirect:y.org", "www.x.org", "responding")),
    ({"robots_status": "error", "robots_detail": "connect_timeout", "read_host": "x.org",
      "www_attempt": {"robots_status": "error", "robots_detail": "dns_failed"}}, ("error", "connect_timeout", "x.org", "no_response:connect")),
    ({"robots_status": "refused", "robots_detail": "http_403", "read_host": "x.org",
      "www_attempt": {"robots_status": "error", "robots_detail": "connect_timeout"}}, ("refused", "http_403", "x.org", "refused")),
    ({"robots_status": "ok", "robots_detail": "http_200", "read_host": "www.x.org", "www_attempt": None},
     ("ok", "http_200", "www.x.org", "responding")),
    ({"robots_status": "not_public", "robots_detail": "not_public", "read_host": "x.org", "www_attempt": None}, None),
])
def test_read_outcome_lets_an_answer_from_the_www_alias_decide(profile, expected):
    assert signal_history.read_outcome("x.org", profile) == expected


def test_build_profile_hands_over_the_www_attempt_without_extra_requests():
    calls = []

    def reader(host, path, *, cap_bytes, **_):
        calls.append((host, path))
        if path != "/robots.txt":
            return ("absent", None, "http_404")
        return ("refused", None, "http_403") if host.startswith("www.") else ("error", None, "connect_timeout")

    profile = access_profile.build_profile("x.org", reader=reader)
    assert calls == [("x.org", "/robots.txt"), ("www.x.org", "/robots.txt"), ("x.org", "/llms.txt")]
    assert profile["robots_status"] == "error" and profile["robots_detail"] == "connect_timeout"
    assert profile["www_attempt"] == {"robots_status": "refused", "robots_detail": "http_403"}
    ok = access_profile.build_profile("x.org", reader=lambda h, p, *, cap_bytes, **_: ("ok", b"User-agent: *\nAllow: /\n", "http_200"))
    assert ok["www_attempt"] is None


def test_board_hosts_are_the_curated_tables_without_the_tranco_block():
    hosts = signal_history.board_hosts()
    assert set(OFFICIAL_PATHS) <= hosts and set(SOURCES) <= hosts and set(OFFICIAL_STATUS_PAGES) <= hosts
    assert set(bundle.bundle_seed_hosts()) <= hosts
    from ahn.profile_warm import load_seed_hosts
    tranco_only = set(load_seed_hosts()) - set(bundle.bundle_seed_hosts()) - set(OFFICIAL_PATHS) - set(SOURCES) - set(OFFICIAL_STATUS_PAGES)
    assert tranco_only, "expected seed hosts that come only from the Tranco block"
    assert not tranco_only & hosts


def test_window_start_is_a_fixed_quarter_hour():
    at = datetime(2026, 10, 21, 14, 29, 59, tzinfo=timezone.utc)
    assert signal_history.window_start(at) == datetime(2026, 10, 21, 14, 15, tzinfo=timezone.utc)
    assert signal_history.window_start(at + timedelta(seconds=1)) == datetime(2026, 10, 21, 14, 30, tzinfo=timezone.utc)


def test_stage_thresholds():
    assert signal_history.STAGES["1"] == signal_history.Thresholds(2, timedelta(minutes=45), 1)
    assert signal_history.STAGES["3"] == signal_history.Thresholds(3, timedelta(minutes=30), 2)


def test_retention_constants_match_the_cleanup_job():
    source = inspect.getsource(platform_metrics.prune_analytics)
    assert f"DELETE FROM robots_read_events WHERE read_at < now() - interval '{signal_history.RAW_RETENTION_DAYS} days'" in source
    for table, column in (("provider_status_events", "at"), ("outage_episodes", "last_event_at")):
        assert f"DELETE FROM {table} WHERE {column} < now() - interval '{signal_history.ROLLUP_RETENTION_DAYS} days'" in source
    assert f"DELETE FROM robots_read_daily WHERE day < CURRENT_DATE - {signal_history.ROLLUP_RETENTION_DAYS - 1}" in source


def test_recording_happens_only_at_the_existing_write_points():
    # The history is written where get_profile and refresh_one already store a result; nothing new reads a site.
    assert "record_read(conn, site_host, profile, now, trigger)" in inspect.getsource(access_profile.get_profile)
    from ahn import status_refresh
    # The feed result is stored by _store_read, which refresh_one calls (D43).
    assert "record_provider_status(conn, site_host, indicator, updated_at)" in inspect.getsource(status_refresh._store_read)
    assert "_store_read(" in inspect.getsource(status_refresh.refresh_one)
    module = inspect.getsource(signal_history)
    for forbidden in ("httpx", "urllib3", "fetch_public_file", "socket"):
        assert forbidden not in module.replace("no network", "")
