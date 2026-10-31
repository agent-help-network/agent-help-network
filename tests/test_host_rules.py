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
    ("notion.so", True), ("who.int", True), ("mastodon.social", True), ("deepmind.google", True), ("cloud.microsoft", True),
    ("workspace.notion.so", False), ("docs.deepmind.google", False), ("example.click", False),
])
def test_aggregate_eligibility_is_apex_only(host, eligible):
    from ahn.service import aggregate_eligible_host
    assert aggregate_eligible_host(host) is eligible


@pytest.mark.parametrize("host,expected", [
    # A single leading www. in front of an eligible apex is that apex.
    ("www.nytimes.com", "nytimes.com"), ("www.bbc.co.uk", "bbc.co.uk"), ("www.naver.co.kr", "naver.co.kr"),
    # Apex and every other host stay as given.
    ("nytimes.com", "nytimes.com"), ("bbc.co.uk", "bbc.co.uk"), ("news.ycombinator.com", "news.ycombinator.com"),
    ("www2.example.com", "www2.example.com"), ("wwwexample.com", "wwwexample.com"), ("www.com", "www.com"),
    # Only one label is removed, and only when what remains is eligible: no tenant name becomes a key.
    ("www.www.example.com", "www.www.example.com"), ("www.user123.example.org", "www.user123.example.org"),
    ("www.account.bbc.co.uk", "www.account.bbc.co.uk"), ("www.xn--p1ai.com", "www.xn--p1ai.com"),
    ("www.example.test", "www.example.test"), ("api.www.example.com", "api.www.example.com"),
    # "www" as the registrable label under a two-label suffix is already an apex, never a public suffix.
    ("www.co.uk", "www.co.uk"), ("www.com.au", "www.com.au"),
])
def test_policy_host_maps_only_a_leading_www_label_to_its_eligible_apex(host, expected):
    from ahn.service import aggregate_eligible_host, policy_host
    assert valid_host(host) == host  # valid_host itself is unchanged: www. names stay valid lookups
    assert policy_host(host) == expected
    # Never widens what is read or counted: a changed host is always an eligible apex.
    if expected != host:
        assert aggregate_eligible_host(expected) and not aggregate_eligible_host(host)


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


def test_www_fallback_read_never_spends_a_second_redirect_hop(monkeypatch):
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
                return Response(503)
            if calls[-1] == "www.example.com":
                return Response(301, {"Location": "https://www.www.example.com" + path})
            pytest.fail("a second redirect hop was followed")
        def close(self):
            pass
    monkeypatch.setattr(access_profile, "_resolve", lambda host, timeout: {"93.184.216.34"})
    monkeypatch.setattr(access_profile.urllib3, "HTTPSConnectionPool", Pool)
    # With the hop already spent, a www host that redirects again is reported, not followed.
    assert access_profile.fetch_public_file("www.example.com", "/robots.txt", cap_bytes=1000, _hop=1) == ("redirected", None, "redirect:www.www.example.com")
    assert calls == ["www.example.com"]
    calls.clear()
    # End to end: apex refuses, www redirects onward, so the apex refusal stands; robots.txt is unreadable, so llms.txt is not requested (D56) and only two connections are made.
    profile = access_profile.build_profile("example.com")
    assert profile["robots_status"] == "refused" and profile["robots_detail"] == "http_503" and profile["read_host"] == "example.com"
    assert calls == ["example.com", "www.example.com"]


def _pool_class(behaviour: dict, calls: list):
    """HTTPSConnectionPool stand-in: behaviour maps an address to a status, a body, or an exception to raise."""
    import urllib3

    class Response:
        def __init__(self, status, body=b""):
            self.status, self.headers, self._body = status, {}, body
        def read(self, _limit):
            return self._body
        def release_conn(self):
            pass

    class Pool:
        def __init__(self, ip, port, server_hostname, **_kw):
            calls.append(ip)
            self.ip = ip
        def request(self, _method, path, **_kw):
            outcome = behaviour[self.ip]
            if isinstance(outcome, BaseException):
                raise outcome
            return Response(200, outcome)
        def close(self):
            pass
    return Pool


def test_fetch_tries_the_next_address_only_when_a_connection_cannot_be_made(monkeypatch):
    import urllib3
    from ahn import access_profile

    body = b"User-agent: *\nDisallow:\n"
    addresses = {"93.184.216.36", "93.184.216.34", "93.184.216.35", "93.184.216.37"}
    monkeypatch.setattr(access_profile, "_resolve", lambda host, timeout: set(addresses))
    # First address refuses, second times out on connect, third answers (fast clock: budget not consumed).
    calls = []
    monkeypatch.setattr(access_profile.urllib3, "HTTPSConnectionPool", _pool_class({
        "93.184.216.34": urllib3.exceptions.NewConnectionError(None, "connection refused"),
        "93.184.216.35": urllib3.exceptions.ConnectTimeoutError("connect timed out"),
        "93.184.216.36": body, "93.184.216.37": body,
    }, calls))
    assert access_profile.fetch_public_file("example.com", "/robots.txt", cap_bytes=1000) == ("ok", body, "http_200")
    assert calls == ["93.184.216.34", "93.184.216.35", "93.184.216.36"]
    # Every address refuses at once: at most three are tried and the last connect failure is the label.
    calls.clear()
    monkeypatch.setattr(access_profile.urllib3, "HTTPSConnectionPool", _pool_class({
        ip: urllib3.exceptions.NewConnectionError(None, "connection refused") for ip in addresses}, calls))
    assert access_profile.fetch_public_file("example.com", "/robots.txt", cap_bytes=1000) == ("error", None, "connect_error")
    assert calls == ["93.184.216.34", "93.184.216.35", "93.184.216.36"]
    # A host that drops every packet: each attempt burns the 2 s connect timeout, and the attempts share a
    # budget of twice that, so the third address is never tried.
    calls.clear()
    ticks = iter([0.0, 2.0, 2.0, 4.0, 4.0, 6.0])
    monkeypatch.setattr(access_profile, "_clock", lambda: next(ticks))
    monkeypatch.setattr(access_profile.urllib3, "HTTPSConnectionPool", _pool_class({
        ip: urllib3.exceptions.ConnectTimeoutError("connect timed out") for ip in addresses}, calls))
    assert access_profile.fetch_public_file("example.com", "/robots.txt", cap_bytes=1000) == ("error", None, "connect_timeout")
    assert calls == ["93.184.216.34", "93.184.216.35"]
    monkeypatch.setattr(access_profile, "_clock", __import__("time").monotonic)  # the fake clock is exhausted
    # Once connected, a slow or broken answer is not retried on another address, and it is labelled for what it was.
    for exc, label in ((urllib3.exceptions.ReadTimeoutError(None, "/robots.txt", "read timed out"), "read_timeout"),
                       (urllib3.exceptions.SSLError("handshake failure"), "tls_error"),
                       (urllib3.exceptions.ProtocolError("connection broken"), "read_error")):
        calls.clear()
        monkeypatch.setattr(access_profile.urllib3, "HTTPSConnectionPool", _pool_class({ip: exc for ip in addresses}, calls))
        assert access_profile.fetch_public_file("example.com", "/robots.txt", cap_bytes=1000) == ("error", None, label)
        assert calls == ["93.184.216.34"]
    # IPv4 is tried before IPv6.
    calls.clear()
    monkeypatch.setattr(access_profile, "_resolve", lambda host, timeout: {"2606:2800:220:1:248:1893:25c8:1946", "93.184.216.34"})
    monkeypatch.setattr(access_profile.urllib3, "HTTPSConnectionPool", _pool_class({"93.184.216.34": body}, calls))
    assert access_profile.fetch_public_file("example.com", "/robots.txt", cap_bytes=1000)[0] == "ok"
    assert calls == ["93.184.216.34"]


@pytest.mark.parametrize("typed,canonical", [
    ("Amazon.com", "amazon.com"), ("  GitHub.COM ", "github.com"), ("example.org.", "example.org"),
    ("https://Example.org/", "example.org"), ("http://example.org", "example.org"),
])
def test_typed_hosts_are_normalised_before_validation(typed, canonical):
    from ahn.models import normalize_host_input
    assert normalize_host_input(typed) == canonical
    assert valid_host(normalize_host_input(typed)) == canonical


@pytest.mark.parametrize("typed", ["https://example.org/path", "user@example.org", "example.org/a", "Exa mple.org", ""])
def test_normalisation_never_turns_a_url_or_account_into_a_host(typed):
    from ahn.models import normalize_host_input
    with pytest.raises(ValueError):
        valid_host(normalize_host_input(typed))


def test_hostname_inputs_turn_off_phone_autocapitalisation():
    from pathlib import Path
    templates = Path(__file__).resolve().parents[1] / "ahn" / "templates"
    for name in ("home.html", "human.html", "report.html", "search.html"):
        text = (templates / name).read_text(encoding="utf-8")
        tag = text[text.index('name="site_host"') - 60:text.index('name="site_host"') + 260]
        assert 'autocapitalize="none"' in tag and 'autocorrect="off"' in tag, name


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
    # Changed on purpose by D63: a missing robots.txt declares no restriction.
    assert recommendation("site_access", None, _profile([], [], status="absent"), None)["action"] == "proceed"
    assert recommendation("site_access", None, None, None) == {"action": "unknown", "confidence": "low", "reasons": []}
    assert recommendation("human_verification", None, _profile(["ChatGPT-User"], []), official)["action"] == "hand_off_to_human"
    incident = {"freshness": "fresh", "indicator": "major"}
    assert recommendation("site_access", incident, _profile(["ChatGPT-User"], []), official)["action"] == "wait_provider_incident"


def test_refused_robots_read_is_a_hint_not_a_declaration():
    official = {"name": "Example", "api_docs": "https://developer.example.com/", "developer_portal": None, "note": None}
    refused = {"robots_status": "refused", "robots": None, "robots_detail": "http_503", "llms_txt_status": "refused", "freshness": "fresh"}
    assert recommendation("site_access", None, refused, official) == {"action": "use_official_path", "confidence": "medium", "reasons": ["robots_fetch_refused", "official_path_available"]}
    assert recommendation("site_access", None, refused, None) == {"action": "unknown", "confidence": "medium", "reasons": ["robots_fetch_refused"]}
    codes = [c["code"] for c in next_checks("site_access", None, "no_reports", None, refused, None)]
    assert codes == ["locate_failure_stage", "robots_fetch_refused", "provider_unknown", "reports_unknown"]
    # A human-verification step still hands off first.
    assert recommendation("human_verification", None, refused, official)["action"] == "hand_off_to_human"
    # A subdomain lookup says why the layer is empty instead of staying silent.
    assert recommendation("site_access", None, None, None, profile_not_read=True) == {"action": "unknown", "confidence": "low", "reasons": ["profile_not_read"]}
    assert [c["code"] for c in next_checks("site_access", None, "no_reports", None, None, None, profile_not_read=True)] == ["locate_failure_stage", "profile_not_read", "provider_unknown", "reports_unknown"]
    assert "profile_not_read" not in [c["code"] for c in next_checks("site_access", None, "no_reports", None, None, None)]


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


def test_wildcard_only_root_block_is_neither_a_declared_block_nor_permission():
    from ahn.access_profile import parse_robots

    official = {"name": "Example", "api_docs": "https://developer.example.com/", "developer_portal": None, "note": None}
    wildcard = _profile([], [])
    wildcard["robots"]["wildcard_root_allowed"] = False
    wildcard["robots"]["rules"] = [
        {"agent": "ChatGPT-User", "kind": "user_agent", "explicit_group": False, "root_allowed": False, "disallow_count": 1},
        {"agent": "Googlebot", "kind": "search_crawler", "explicit_group": False, "root_allowed": False, "disallow_count": 1},
        {"agent": "GPTBot", "kind": "training_crawler", "explicit_group": False, "root_allowed": False, "disallow_count": 1},
    ]
    assert recommendation("site_access", None, wildcard, None) == {"action": "unknown", "confidence": "low", "reasons": ["robots_wildcard_disallow"]}
    assert recommendation("site_access", None, wildcard, official) == {"action": "use_official_path", "confidence": "medium",
                                                                        "reasons": ["robots_wildcard_disallow", "official_path_available"]}
    codes = [c["code"] for c in next_checks("site_access", None, "no_reports", None, wildcard, None)]
    assert codes == ["locate_failure_stage", "robots_wildcard_disallow", "provider_unknown", "reports_unknown"]
    # Human-verification and sign-in steps still hand off first.
    assert recommendation("human_verification", None, wildcard, official)["action"] == "hand_off_to_human"
    # Once every agent token is named and allowed at the root, the wildcard no longer covers any of them.
    for rule in wildcard["robots"]["rules"][:2]:
        rule.update(explicit_group=True, root_allowed=True)
    assert recommendation("site_access", None, wildcard, None) == {"action": "proceed", "confidence": "low", "reasons": ["robots_allows_agents"]}
    # The same from a real file, end to end through the parser.
    parsed = parse_robots("User-agent: *\nDisallow: /\n")
    real = {"robots_status": "ok", "robots": parsed, "llms_txt_status": "absent", "freshness": "fresh"}
    assert parsed["agents_blocked"] == [] and recommendation("site_access", None, real, None)["action"] == "unknown"
    assert [c["code"] for c in next_checks("site_access", None, "no_reports", None, real, None)][1] == "robots_wildcard_disallow"
    # An explicit per-agent block still wins over the wildcard reason for the blocked vendor (D48). Without a vendor
    # only some vendors are named, so the answer says so and keeps the wildcard reason for the others.
    parsed_explicit = parse_robots("User-agent: *\nDisallow: /\n\nUser-agent: ChatGPT-User\nDisallow: /\n")
    explicit = {"robots_status": "ok", "robots": parsed_explicit, "llms_txt_status": "absent", "freshness": "fresh"}
    assert recommendation("site_access", None, explicit, None, agent_vendor="openai")["reasons"] == ["robots_blocks_agents"]
    assert recommendation("site_access", None, explicit, None, agent_vendor="anthropic")["reasons"] == ["robots_wildcard_disallow"]
    assert recommendation("site_access", None, explicit, None) == {"action": "unknown", "confidence": "low",
                                                                   "reasons": ["robots_blocks_some_agents", "robots_wildcard_disallow"]}
    assert recommendation("site_access", None, explicit, None, vendor_aware=False)["reasons"] == ["robots_blocks_agents"]
