"""Scope of a provider's active status (D43, migration 025) against the dedicated ahn_test database.

Stub feeds only (MockTransport over tests/fixtures/status_scope); no real status page is read.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest

TEST_URL = os.environ.get("AHN_TEST_DATABASE_URL", "")
if not TEST_URL:
    pytest.skip("AHN_TEST_DATABASE_URL is required", allow_module_level=True)

from fastapi.testclient import TestClient  # noqa: E402

from ahn import background_reads, status_refresh  # noqa: E402
from ahn.db import connect, migrate  # noqa: E402
from ahn.main import create_app  # noqa: E402
from ahn.settings import Settings  # noqa: E402
from ahn.status_refresh import refresh_batch, refresh_one  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures" / "status_scope"
SCOPE_COLUMNS = ("active_incidents", "max_incident_impact", "incident_updated_at", "active_maintenances", "affected", "affected_regional")


@pytest.fixture
def lookup():
    settings = Settings(TEST_URL, "test-pepper-must-be-at-least-thirty-two-chars", public_mode=True, collect_lookup_metrics=True)
    with connect(settings) as conn:
        if conn.info.dbname != "ahn_test":
            raise RuntimeError("Refusing to reset a database other than ahn_test")
    migrate(settings)
    with connect(settings) as conn:
        conn.execute("TRUNCATE lookup_metrics, request_metrics, mcp_check_metrics, status_cache, status_sources, queried_hosts, "
                     "host_access_profile, background_reads, provider_status_events RESTART IDENTITY")
    with TestClient(create_app(settings)) as client:
        yield settings, client


@pytest.fixture(autouse=True)
def unpaced(monkeypatch):
    monkeypatch.setattr(background_reads, "MIN_READ_INTERVAL", 0.0)
    monkeypatch.setattr(background_reads, "discover", lambda _host, _digest=None: None)
    monkeypatch.setattr(status_refresh, "_sleep", lambda _seconds: None)  # the 1 s gap between reads of one status page


class Feeds:
    """Stub status pages: host of the status page -> fixture; records every path asked for."""

    def __init__(self, **pages):
        self.pages = {host: json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8")) for host, name in pages.items()}
        self.seen: list[str] = []
        self.refuse: str | None = None  # a path answered 429

    def set_indicator(self, host, indicator):
        self.pages[host]["status"]["status"]["indicator"] = indicator

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.seen.append(f"{request.url.host}{request.url.path}")
        if request.url.path == self.refuse:
            return httpx.Response(429)
        page = self.pages.get(request.url.host)
        key = {"/api/v2/status.json": "status", "/api/v2/incidents/unresolved.json": "unresolved",
               "/api/v2/scheduled-maintenances/active.json": "maintenances"}.get(request.url.path)
        if page is None or key not in page:
            return httpx.Response(404, text="<html></html>")
        return httpx.Response(200, json=page[key])


def use(monkeypatch, feeds: Feeds) -> httpx.Client:
    remote = httpx.Client(transport=httpx.MockTransport(feeds))
    monkeypatch.setattr(background_reads, "refresh_feed", lambda s, host: refresh_one(s, host, client=remote, wait=True))
    return remote


def check(client, host):
    response = client.get("/v1/incidents/check", params={"site_host": host})
    assert response.status_code == 200
    return response.json()


def queued(settings):
    with connect(settings) as conn:
        return sorted((row["kind"], row["site_host"]) for row in conn.execute("SELECT kind,site_host FROM background_reads"))


def age(settings, host, minutes):
    with connect(settings) as conn:
        conn.execute("""UPDATE status_cache SET fetched_at=fetched_at - make_interval(mins => %s),
                        last_error_at=last_error_at - make_interval(mins => %s) WHERE site_host=%s""", (minutes, minutes, host))


def scope_row(settings, host):
    with connect(settings) as conn:
        return conn.execute(f"SELECT {','.join(SCOPE_COLUMNS)} FROM status_cache WHERE site_host=%s", (host,)).fetchone()


def test_components_only_is_stored_and_answered_without_wait(lookup, monkeypatch):
    settings, client = lookup
    feeds = Feeds(**{"status.rubygems.org": "rubygems"})
    with use(monkeypatch, feeds):
        assert check(client, "rubygems.org")["provider_status"]["freshness"] == "pending"
        background_reads.process_due(settings, limit=10)
        answer = check(client, "rubygems.org")
    assert feeds.seen == ["status.rubygems.org/api/v2/status.json", "status.rubygems.org/api/v2/incidents/unresolved.json",
                          "status.rubygems.org/api/v2/scheduled-maintenances/active.json"]
    status = answer["provider_status"]
    assert status["indicator"] == "maintenance" and status["freshness"] == "fresh"
    assert status["basis"] == "components_only" and status["active_incidents"] == 0 and status["active_maintenances"] == 0
    assert status["max_incident_impact"] == "none"
    assert "affected" not in status and "incident_updated_at" not in status  # omitted, not null
    assert answer["recommendation"]["action"] != "wait_provider_incident"
    codes = [item["code"] for item in answer["next_checks"]]
    assert "provider_components_only" in codes and "provider_scope" not in codes
    korean = client.get("/ko/search", params={"site_host": "rubygems.org"}).text
    assert "발표한 사건이나 점검은 없습니다" in korean


def test_an_announced_incident_keeps_waiting_and_shows_its_scope(lookup, monkeypatch):
    settings, client = lookup
    feeds = Feeds(**{"www.cloudflarestatus.com": "cloudflare"})
    with use(monkeypatch, feeds):
        check(client, "cloudflare.com")
        background_reads.process_due(settings, limit=10)
        answer = check(client, "cloudflare.com")
    status = answer["provider_status"]
    assert status["basis"] == "incident" and status["active_incidents"] == 1 and status["max_incident_impact"] == "minor"
    assert status["affected"] == ["WARP", "Paris, France - (CDG)", "Nashville, United States - (BNA)"]
    assert status["incident_updated_at"].startswith("2026-09-01T08:29:56")  # UTC
    assert datetime.fromisoformat(status["source_updated_at"].replace("Z", "+00:00")) == datetime(2026, 8, 27, tzinfo=timezone.utc)
    assert answer["recommendation"]["action"] == "wait_provider_incident"
    codes = [item["code"] for item in answer["next_checks"]]
    assert codes[codes.index("provider_scope") + 1] == "provider_scope_partial"
    assert scope_row(settings, "cloudflare.com")["affected_regional"] is False


def test_a_quiet_read_clears_the_old_scope_and_costs_one_request(lookup, monkeypatch):
    settings, client = lookup
    feeds = Feeds(**{"www.cloudflarestatus.com": "cloudflare"})
    with use(monkeypatch, feeds) as remote:
        check(client, "cloudflare.com")
        background_reads.process_due(settings, limit=10)
        assert scope_row(settings, "cloudflare.com")["active_incidents"] == 1
        feeds.set_indicator("www.cloudflarestatus.com", "none")
        feeds.seen.clear()
        assert refresh_one(settings, "cloudflare.com", client=remote, min_interval=0, wait=True)
    assert feeds.seen == ["www.cloudflarestatus.com/api/v2/status.json"]
    assert all(value is None for value in scope_row(settings, "cloudflare.com").values())
    status = check(client, "cloudflare.com")["provider_status"]
    assert status["indicator"] == "none" and not set(SCOPE_COLUMNS + ("basis",)) & set(status)


def test_an_active_cached_indicator_is_read_again_after_fifteen_minutes(lookup, monkeypatch):
    settings, client = lookup
    feeds = Feeds(**{"www.cloudflarestatus.com": "cloudflare", "www.githubstatus.com": "single_incident"})
    feeds.set_indicator("www.githubstatus.com", "none")
    with use(monkeypatch, feeds) as remote:
        for host in ("cloudflare.com", "github.com"):
            check(client, host)
        background_reads.process_due(settings, limit=10)
        # Fourteen minutes: nothing is due on either path.
        for host in ("cloudflare.com", "github.com"):
            age(settings, host, 14)
            check(client, host)
        assert queued(settings) == []
        # Sixteen minutes: the active feed is due behind the lookup; the quiet one waits for its two hours.
        for host in ("cloudflare.com", "github.com"):
            age(settings, host, 2)
            check(client, host)
        assert queued(settings) == [("status_feed", "cloudflare.com")]
        background_reads.process_due(settings, limit=10)
        assert queued(settings) == []
        # The cron applies the same rule (its idle interval is one hour).
        age(settings, "cloudflare.com", 16)
        age(settings, "github.com", 16)
        feeds.seen.clear()
        assert refresh_batch(settings, client=remote) == {"checked": 1, "updated": 1, "failed": 0, "remaining": 0}
        assert [path for path in feeds.seen if path.endswith("status.json")] == ["www.cloudflarestatus.com/api/v2/status.json"]
        age(settings, "github.com", 50)
        assert refresh_batch(settings, client=remote) == {"checked": 1, "updated": 1, "failed": 0, "remaining": 0}
        # A cached active value that the provider has since resolved stops reading as active on the next read.
        feeds.set_indicator("www.cloudflarestatus.com", "none")
        age(settings, "cloudflare.com", 16)
        check(client, "cloudflare.com")
        background_reads.process_due(settings, limit=10)
    answer = check(client, "cloudflare.com")
    assert answer["provider_status"]["indicator"] == "none"
    assert answer["recommendation"]["action"] != "wait_provider_incident"


def q_counters(settings, host):
    with connect(settings) as conn:
        return conn.execute("SELECT scope_failures,scope_error FROM status_cache WHERE site_host=%s", (host,)).fetchone()


def age_scope(settings, host, minutes):
    with connect(settings) as conn:
        conn.execute("UPDATE status_cache SET scope_failed_at=scope_failed_at - make_interval(mins => %s) WHERE site_host=%s",
                     (minutes, host))


def test_failed_lists_rest_and_a_refusal_stops_the_other_list(lookup, monkeypatch):
    settings, client = lookup
    feeds = Feeds(**{"status.twilio.com": "twilio"})
    original = feeds.pages["status.twilio.com"]["unresolved"]
    feeds.pages["status.twilio.com"]["unresolved"] = dict(original, padding="x" * 40_000)  # like the live 112 KB list
    with use(monkeypatch, feeds) as remote:
        check(client, "twilio.com")
        assert refresh_one(settings, "twilio.com", client=remote, min_interval=0, wait=True)
        assert len(feeds.seen) == 3
        assert q_counters(settings, "twilio.com") == {"scope_failures": 1, "scope_error": "too_large"}
        assert scope_row(settings, "twilio.com")["active_maintenances"] == 1
        # While the lists rest, a refresh reads status.json only, shows no scope and keeps the count.
        feeds.seen.clear()
        assert refresh_one(settings, "twilio.com", client=remote, min_interval=0, wait=True)
        assert feeds.seen == ["status.twilio.com/api/v2/status.json"]
        assert q_counters(settings, "twilio.com")["scope_failures"] == 1
        assert all(value is None for value in scope_row(settings, "twilio.com").values())
        # After the rest the lists are asked for again; a refusal of the first one stops the second.
        age_scope(settings, "twilio.com", 16)
        feeds.refuse = "/api/v2/incidents/unresolved.json"
        feeds.seen.clear()
        assert refresh_one(settings, "twilio.com", client=remote, min_interval=0, wait=True)
        assert feeds.seen == ["status.twilio.com/api/v2/status.json", "status.twilio.com/api/v2/incidents/unresolved.json"]
        assert q_counters(settings, "twilio.com") == {"scope_failures": 2, "scope_error": "http_429"}
        age_scope(settings, "twilio.com", 60)  # a second failure after a refusal rests two hours
        feeds.seen.clear()
        assert refresh_one(settings, "twilio.com", client=remote, min_interval=0, wait=True) and len(feeds.seen) == 1
        # Reading both lists again clears the count.
        age_scope(settings, "twilio.com", 61)
        feeds.refuse = None
        feeds.pages["status.twilio.com"]["unresolved"] = original
        assert refresh_one(settings, "twilio.com", client=remote, min_interval=0, wait=True)
    assert q_counters(settings, "twilio.com") == {"scope_failures": None, "scope_error": None}
    assert scope_row(settings, "twilio.com")["active_incidents"] == 7
