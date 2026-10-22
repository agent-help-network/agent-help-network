"""Interim stop (D49 stage 0): a status feed is requested only when its host is on the robots-checked allowlist.

No network: stub transports count every request; a feed outside the list must see none.
"""

from __future__ import annotations

import httpx
import pytest

from ahn import status_refresh
from ahn.status_refresh import (FEED_ALLOWLIST_ENV, FEED_ALLOWLIST_FILE, FeedNotAllowed, SOURCES, Source,
                                _read_source, discover_source, feed_host_allowed)

QUIET = {"page": {"updated_at": "2026-10-21T11:00:00Z"}, "status": {"indicator": "none"}}


@pytest.fixture
def listed(monkeypatch):
    monkeypatch.setenv(FEED_ALLOWLIST_ENV, "1")
    monkeypatch.setattr(status_refresh, "_allowed_feed_hosts", lambda: frozenset({"ok.example"}))


def counting_client():
    seen: list[str] = []

    def handler(request):
        seen.append(str(request.url))
        return httpx.Response(200, json=QUIET)

    return httpx.Client(transport=httpx.MockTransport(handler)), seen


def test_the_shipped_list_has_121_hosts_and_gates_the_curated_table(monkeypatch):
    monkeypatch.setenv(FEED_ALLOWLIST_ENV, "1")
    status_refresh._allowed_feed_hosts.cache_clear()
    hosts = status_refresh._allowed_feed_hosts()
    assert len(hosts) == 121 and all(h == h.lower() and "/" not in h for h in hosts)
    assert "www.google.com" in hosts and "status.barchart.com" in hosts  # allowed by RFC 9309 (longest match; 403 = unavailable)
    assert "discordstatus.com" not in hosts and "www.githubstatus.com" not in hosts
    allowed = {s.api_url for s in SOURCES.values() if feed_host_allowed(s.api_url)}
    assert len(allowed) == 121
    assert FEED_ALLOWLIST_FILE.read_text(encoding="utf-8").startswith("#")


def test_host_check_and_the_switch(listed, monkeypatch):
    assert feed_host_allowed("https://ok.example/api/v2/status.json")
    assert feed_host_allowed("OK.example") and feed_host_allowed("https://OK.EXAMPLE/x")
    assert not feed_host_allowed("https://other.example/api/v2/status.json")
    assert not feed_host_allowed("https://ok.example.evil.test/api/v2/status.json")
    assert not feed_host_allowed("") and not feed_host_allowed(None)
    monkeypatch.setenv(FEED_ALLOWLIST_ENV, "0")  # emergency switch: read everything as before
    assert feed_host_allowed("https://other.example/api/v2/status.json") and feed_host_allowed(None)


def test_a_feed_off_the_list_gets_no_request_and_one_on_the_list_is_read(listed):
    client, seen = counting_client()
    with client:
        with pytest.raises(FeedNotAllowed):
            _read_source(Source("X", "https://other.example/", "https://other.example/api/v2/status.json"), client)
        assert seen == []
        assert _read_source(Source("Y", "https://ok.example/", "https://ok.example/api/v2/status.json"), client)[0] == "none"
        assert seen == ["https://ok.example/api/v2/status.json"]


def test_not_allowed_is_not_a_read_error():
    assert not issubclass(FeedNotAllowed, status_refresh.READ_ERRORS)


def test_discovery_of_a_status_subdomain_not_on_the_list_does_no_dns_and_no_request(listed, monkeypatch):
    def boom(*_a, **_k):
        raise AssertionError("no lookup of any kind before the host is decided")

    monkeypatch.setattr(status_refresh, "_statuspage_target", boom)
    assert discover_source("example.org") is None
    monkeypatch.setenv(FEED_ALLOWLIST_ENV, "0")
    with pytest.raises(AssertionError):
        discover_source("example.org")  # switch off: the old path runs


def test_refresh_one_skips_a_blocked_feed_without_touching_the_database(listed, monkeypatch):
    blocked = Source("B", "https://other.example/", "https://other.example/api/v2/status.json")
    monkeypatch.setitem(SOURCES, "blocked.test", blocked)
    client, seen = counting_client()
    with client:
        assert status_refresh.refresh_one(None, "blocked.test", client=client) is False  # settings None: a DB touch would raise
    assert seen == []
