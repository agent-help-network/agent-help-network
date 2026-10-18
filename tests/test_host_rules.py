"""Pure hostname and limiter-key rules; no database or network."""

from __future__ import annotations

import pytest
from starlette.requests import Request

from ahn.models import valid_host
from ahn.rate_limit import RequestLimiter
from ahn.triage import next_checks, recommendation


@pytest.mark.parametrize("host", ["127.1", "0x7f.1", "1.1", "a.1", "0177.0.0.1", "foo.lan", "foo.corp", "foo.home.arpa",
                                  "a.in-addr.arpa", "ab.onion", "localhost.localdomain", "x.intranet", "Example.com", "example"])
def test_valid_host_rejects_addresses_and_special_use_names(host):
    with pytest.raises(ValueError):
        valid_host(host)


@pytest.mark.parametrize("host", ["example.com", "foo.test", "xn--p1ai.com", "bbc.co.uk", "a-b.example.org"])
def test_valid_host_accepts_public_names(host):
    assert valid_host(host) == host


@pytest.mark.parametrize("host,eligible", [
    ("github.com", True), ("twitch.tv", True), ("bbc.co.uk", True), ("naver.co.kr", True), ("news.ycombinator.com", False),
    ("user123.example.org", False), ("account.bbc.co.uk", False), ("xn--p1ai.com", False), ("example.test", False),
])
def test_aggregate_eligibility_is_apex_only(host, eligible):
    from ahn.service import aggregate_eligible_host
    assert aggregate_eligible_host(host) is eligible


def test_only_same_host_www_redirect_is_followed(monkeypatch):
    from ahn import access_profile

    calls = []
    class Response:
        def __init__(self, status, headers=None, body=b""):
            self.status, self.headers, self._body = status, headers or {}, body
        def read(self, _limit):
            return self._body
        def release_conn(self):
            pass
    class Pool:
        def __init__(self, ip, port, server_hostname, **_kw):
            calls.append(server_hostname)
        def request(self, _method, path, **_kw):
            if calls[-1] == "example.com":
                return Response(301, {"Location": "https://www.example.com" + path})
            if calls[-1] == "www.example.com":
                return Response(200, body=b"User-agent: *\nDisallow:\n")
            return Response(301, {"Location": "https://elsewhere.example.net/robots.txt"})
        def close(self):
            pass
    monkeypatch.setattr(access_profile, "_resolve", lambda host, timeout: {"93.184.216.34"})
    monkeypatch.setattr(access_profile.urllib3, "HTTPSConnectionPool", Pool)
    assert access_profile.read_public_file("example.com", "/robots.txt", cap_bytes=1000) == ("ok", b"User-agent: *\nDisallow:\n")
    assert calls == ["example.com", "www.example.com"]
    calls.clear()
    assert access_profile.read_public_file("other.org", "/robots.txt", cap_bytes=1000) == ("redirected", None)
    assert calls == ["other.org"]


def _request(headers: dict, client=("10.0.0.9", 1234)) -> Request:
    raw = [(k.lower().encode(), v.encode()) for k, v in headers.items()]
    return Request({"type": "http", "method": "GET", "path": "/", "headers": raw, "client": client, "query_string": b""})


def test_limiter_key_prefers_edge_header_over_forwarded_for():
    assert RequestLimiter.client_key(_request({"CF-Connecting-IP": "198.51.100.7", "X-Forwarded-For": "127.0.0.1"})) == "198.51.100.7"
    assert RequestLimiter.client_key(_request({"X-Forwarded-For": "203.0.113.5, 198.51.100.7"})) == "203.0.113.5"
    assert RequestLimiter.client_key(_request({"CF-Connecting-IP": "garbage"})) == "10.0.0.9"
    assert RequestLimiter.client_key(_request({})) == "10.0.0.9"


def _profile(blocked: list[str], allowed: list[str], status: str = "ok", llms: str = "absent") -> dict:
    rules = [{"agent": a, "kind": "user_agent", "explicit_group": True, "root_allowed": False, "disallow_count": 1} for a in blocked]
    rules += [{"agent": a, "kind": "training_crawler", "explicit_group": True, "root_allowed": True, "disallow_count": 0} for a in allowed]
    return {"robots_status": status, "robots": {"rules": rules, "wildcard_root_allowed": True, "sitemap": False,
                                                 "agents_blocked": blocked, "agents_allowed": allowed},
            "llms_txt_status": llms, "freshness": "fresh"}


def test_recommendation_prefers_declared_policy_and_official_paths():
    official = {"name": "Example", "api_docs": "https://developer.example.com/", "developer_portal": None, "note": None}
    assert recommendation("site_access", None, _profile(["ChatGPT-User"], []), official)["action"] == "use_official_path"
    assert recommendation("site_access", None, _profile(["ChatGPT-User"], []), None)["action"] == "stop_declared_block"
    assert recommendation("site_access", None, _profile([], ["GPTBot"]), None) == {"action": "proceed", "confidence": "low", "reasons": ["robots_allows_agents"]}
    assert recommendation("site_access", None, _profile([], [], status="absent"), None)["action"] == "unknown"
    assert recommendation("site_access", None, None, None) == {"action": "unknown", "confidence": "low", "reasons": []}
    assert recommendation("human_verification", None, _profile(["ChatGPT-User"], []), official)["action"] == "hand_off_to_human"
    incident = {"freshness": "fresh", "indicator": "major"}
    assert recommendation("site_access", incident, _profile(["ChatGPT-User"], []), official)["action"] == "wait_provider_incident"


def test_next_checks_lists_declared_policy_before_provider_and_reports():
    codes = [c["code"] for c in next_checks("site_access", None, "no_reports", None, _profile([], ["GPTBot"], llms="present"),
                                            {"name": "E", "api_docs": "https://d.example/", "developer_portal": None, "note": None})]
    assert codes == ["locate_failure_stage", "robots_allows_agents", "llms_txt_present", "official_path_available", "provider_unknown", "reports_unknown"]
    # A training-crawler-only block is not a declared block for user-directed agents.
    crawler_only = _profile([], [])
    crawler_only["robots"]["rules"] = [{"agent": "GPTBot", "kind": "training_crawler", "explicit_group": True, "root_allowed": False, "disallow_count": 1}]
    crawler_only["robots"]["agents_blocked"] = ["GPTBot"]
    assert recommendation("site_access", None, crawler_only, None)["action"] == "proceed"
    assert "robots_allows_agents" in [c["code"] for c in next_checks("site_access", None, "no_reports", None, crawler_only, None)]
