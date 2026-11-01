"""D67: the AHN server check of a home page, the parts that need no database (fingerprints, classification, backoff,
the request's headers, the answer's view and the MCP line). Fixtures are synthetic; no real page is copied."""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

import pytest

from ahn import access_profile, probe_fingerprints, site_probe
from ahn.mcp_adapter import ahn_check_line
from ahn.models import AhnCheck

NOW = datetime(2026, 11, 1, 12, 0, tzinfo=timezone.utc)


def fetched(status=200, headers=None, cookies=(), body=b"", complete=True, ms=120):
    return {"label": None, "status": status, "headers": headers or {}, "cookies": list(cookies), "body": body,
            "complete": complete, "ms": ms, "bytes_read": len(body)}


def run(host="example.com", **kwargs):
    return site_probe.classify(host, fetched(**kwargs), NOW)


# --- fingerprints (T-F1..T-F8) ---------------------------------------------------------------------------------


def test_f1_edge_vendor_markers_on_a_normal_page_are_not_a_block():
    out = run(headers={"cf-ray": "abc-IAD", "server": "cloudflare"}, cookies=["__cf_bm"], body=b"<html>ok</html>")
    assert out["outcome"] == "reachable" and out["edge_vendors"] == ["cloudflare"] and out["bot_vendor"] is None


def test_f2_bot_manager_cookies_without_a_challenge_are_reachable():
    out = run(cookies=["_abck", "bm_sz", "_px3", "datadome"], body=b"<html>ok</html>")
    assert out["outcome"] == "reachable" and out["bot_vendor"] is None
    assert out["edge_vendors"] == ["datadome", "perimeterx"]


def test_f3_cloudflare_challenge_header_with_403():
    out = run(status=403, headers={"cf-mitigated": "challenge", "cf-ray": "x"}, body=b"<html>Just a moment</html>")
    assert (out["outcome"], out["bot_vendor"], out["bot_signal"], out["bot_confidence"]) == ("bot_challenge", "cloudflare", "header", "high")


def test_f4_datadome_marker_in_a_blocked_body():
    out = run(status=403, body=b"<script src='https://geo.captcha-delivery.com/captcha/'></script>")
    assert (out["outcome"], out["bot_vendor"], out["bot_signal"]) == ("bot_challenge", "datadome", "body")


def test_f5_a_long_200_page_that_only_mentions_captcha_is_reachable():
    body = b"<html>" + b"a" * 9000 + b" google.com/recaptcha/api.js </html>"
    assert run(status=200, body=body, complete=False)["outcome"] == "reachable"
    # Even a complete 200 page with an ordinary widget is read at medium confidence only where it is the challenge's resource.
    small = run(status=200, body=b"<html>captcha</html>", complete=True)
    assert small["outcome"] == "reachable"


def test_f5b_a_complete_small_200_challenge_page_counts_at_medium():
    out = run(status=200, body=b"<script>var _cf_chl_opt={cvId:1}</script>", complete=True)
    assert (out["outcome"], out["bot_vendor"], out["bot_confidence"]) == ("bot_challenge", "cloudflare", "medium")


def test_f6_403_without_a_marker_is_refused_and_names_no_provider():
    out = run(status=403, body=b"forbidden")
    assert out["outcome"] == "refused" and out["bot_vendor"] is None


def test_f7_redirect_to_a_sign_in_host_is_a_login_wall_with_codes_only():
    out = run(status=302, headers={"location": "https://accounts.example.com/signin?next=/private/path"})
    assert out["outcome"] == "login_wall" and out["login_signal"] == "redirect"
    assert out["redirect"] == {"followed_www": False, "to_relation": "same_site", "path_class": "login"}
    assert "private" not in repr(out)


def test_f8_every_marker_names_its_source_and_checked_date():
    for vendor in probe_fingerprints.VENDORS:
        assert vendor.code in probe_fingerprints.VENDOR_CODES
        for marker in vendor.markers:
            assert marker.source.startswith("https://") and re.fullmatch(r"\d{4}-\d\d-\d\d", marker.checked)
            assert marker.role in ("presence", "challenge")
            assert marker.kind in ("header", "cookie", "body")


def test_aws_waf_action_headers_are_challenges_only_with_their_documented_status():
    assert run(status=405, headers={"x-amzn-waf-action": "captcha"})["bot_vendor"] == "aws_waf"
    assert run(status=202, headers={"x-amzn-waf-action": "challenge"})["bot_vendor"] == "aws_waf"
    assert run(status=200, headers={"x-amzn-waf-action": "captcha"})["outcome"] == "reachable"


# --- other outcomes ------------------------------------------------------------------------------------------


@pytest.mark.parametrize("status,headers,outcome", [
    (200, {}, "reachable"), (429, {}, "rate_limited"), (500, {}, "server_error"), (503, {}, "server_error"),
    (401, {"www-authenticate": "Basic"}, "login_wall"), (401, {}, "refused"), (404, {}, "other_status"),
    (302, {"location": "/en-us/"}, "redirected_same_host"),
    (301, {"location": "https://www.example.com/"}, "redirected_same_host"),
    (301, {"location": "https://other.org/x"}, "redirected_offsite"),
])
def test_outcome_by_status(status, headers, outcome):
    assert run(status=status, headers=headers)["outcome"] == outcome


def test_a_password_field_in_a_complete_page_is_a_login_signal_and_in_a_cut_page_is_not():
    page = b"<form><input type='password' name='p'></form>"
    assert run(body=page)["login_signal"] == "form"
    assert run(body=page, complete=False)["outcome"] == "reachable"


def test_no_answer_is_unreachable():
    out = site_probe.classify("example.com", {**fetched(), "status": None, "label": "dns_failed"}, NOW)
    assert out["outcome"] == "unreachable"


def test_redirect_codes_carry_no_path():
    out = site_probe.classify_redirect("example.com", "https://login.example.com/ap/signin?x=secret")
    assert out == {"to_relation": "same_site", "path_class": "login"}
    assert site_probe.classify_redirect("example.com", "/de-de/")["path_class"] == "locale"
    assert site_probe.classify_redirect("example.co.uk", "https://shop.example.co.uk/")["to_relation"] == "same_site"
    assert site_probe.classify_redirect("example.com", "https://example.net/")["to_relation"] == "other_site"


# --- backoff (T-E4) ---------------------------------------------------------------------------------------------


def previous(refusals=0, failures=0):
    return {"consecutive_refusals": refusals, "consecutive_failures": failures}


def test_e4_a_refusal_waits_72_hours_doubles_and_stops_at_7_days():
    waits = []
    prev = None
    for _ in range(5):
        wait, refusals, failures = site_probe.schedule("bot_challenge", prev, None)
        waits.append(wait)
        prev = previous(refusals, failures)
    assert waits[0] == timedelta(hours=72) and waits[1] == timedelta(hours=144) and waits[2] == timedelta(days=7)
    assert max(waits) == timedelta(days=7)


def test_e4_retry_after_longer_than_the_backoff_is_followed_up_to_7_days():
    assert site_probe.schedule("rate_limited", None, 5 * 86400)[0] == timedelta(days=5)
    assert site_probe.schedule("rate_limited", None, 40 * 86400)[0] == timedelta(days=7)
    assert site_probe.schedule("refused", None, 10 * 3600)[0] == timedelta(hours=72)
    assert site_probe._retry_after_seconds("120", NOW) == 120
    assert site_probe._retry_after_seconds("Sun, 01 Nov 2026 14:00:00 GMT", NOW) == 7200


def test_failing_hosts_are_checked_again_after_6_to_24_hours_and_a_success_resets():
    assert site_probe.schedule("unreachable", None, None)[0] == timedelta(hours=6)
    assert site_probe.schedule("server_error", previous(0, 1), None)[0] == timedelta(hours=12)
    assert site_probe.schedule("server_error", previous(0, 5), None)[0] == timedelta(hours=24)
    wait, refusals, failures = site_probe.schedule("reachable", previous(3, 3), None)
    assert (wait, refusals, failures) == (timedelta(hours=24), 0, 0)


# --- the request (T-B1, T-E5) ----------------------------------------------------------------------------------


class _Headers(dict):
    def getlist(self, name):
        return [self[name]] if name in self else []


class _Response:
    status = 200

    def __init__(self):
        self.headers = _Headers({"set-cookie": "sid=SECRETVALUE; Path=/", "content-type": "text/html", "server": "x"})
        self._chunks = [b"<html>hello</html>"]

    def read1(self, n):
        return self._chunks.pop(0) if self._chunks else b""

    def release_conn(self):
        pass


def test_b1_the_request_carries_host_user_agent_and_accept_only_and_stores_no_cookie_value(monkeypatch):
    seen = []

    class Pool:
        def __init__(self, address, **kw):
            seen.append(("pool", address, kw["server_hostname"]))

        def request(self, method, path, **kw):
            seen.append((method, path, kw["headers"], kw["redirect"]))
            return _Response()

        def close(self):
            pass

    monkeypatch.setattr(site_probe.urllib3, "HTTPSConnectionPool", Pool)
    monkeypatch.setattr(access_profile, "_resolve", lambda host, timeout: {"93.184.216.34"})
    out = site_probe.fetch_home("example.com")
    request, = [item for item in seen if item[0] == "GET"]
    assert request[1] == "/" and request[3] is False
    assert request[2] == {"Host": "example.com", "User-Agent": access_profile.USER_AGENT, "Accept": "text/html,*/*;q=0.5"}
    assert out["cookies"] == ["sid"] and "SECRETVALUE" not in repr(out)
    assert len([item for item in seen if item[0] == "GET"]) == 1


def test_a_private_address_is_never_requested(monkeypatch):
    monkeypatch.setattr(access_profile, "_resolve", lambda host, timeout: {"10.0.0.5"})
    monkeypatch.setattr(site_probe.urllib3, "HTTPSConnectionPool", lambda *a, **k: pytest.fail("connected"))
    assert site_probe.fetch_home("example.com")["label"] == "not_public"


class _Trickle:
    """A local TCP server that answers one request by sending a header one byte every ``gap`` seconds."""

    def __init__(self, gap=0.5):
        import socket
        import threading
        self.server = socket.socket()
        self.server.bind(("127.0.0.1", 0))
        self.server.listen(5)
        self.port = self.server.getsockname()[1]
        self.gap = gap
        self.stop = threading.Event()
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self):
        import time
        try:
            conn, _ = self.server.accept()
            conn.recv(4096)
            for byte in b"HTTP/1.1 200 OK\r\nX-Pad: " + b"a" * 4000:
                if self.stop.is_set():
                    return
                conn.send(bytes([byte]))
                time.sleep(self.gap)
        except OSError:
            return

    def close(self):
        self.stop.set()
        self.server.close()


def _local_pool_class(port):
    import urllib3

    class Pool(urllib3.HTTPConnectionPool):
        def __init__(self, address, port=None, server_hostname=None, assert_hostname=None, **kw):
            super().__init__("127.0.0.1", port=PORT[0], **kw)
    PORT = [port]
    return Pool


def test_h2_a_server_that_trickles_its_header_is_cut_off_at_the_wall_clock_limit():
    import time
    import urllib3
    server = _Trickle(gap=0.5)             # under the 3 second receive timeout: urllib3 alone would wait for a minute
    try:
        pool = access_profile.tracked_pool(urllib3.HTTPConnectionPool)("127.0.0.1", port=server.port, retries=False,
                                                                       timeout=urllib3.Timeout(connect=1, read=3))
        started = time.monotonic()
        with access_profile.WallClock(1.0) as wall:
            wall.watch(pool)
            with pytest.raises(Exception):
                pool.request("GET", "/", preload_content=False)
        assert wall.fired and time.monotonic() - started < 2.5
    finally:
        server.close()


def test_h2_fetch_home_ends_within_the_wall_clock_limit_whatever_the_server_does(monkeypatch):
    import time
    server = _Trickle(gap=0.5)
    try:
        monkeypatch.setattr(site_probe.urllib3, "HTTPSConnectionPool", _local_pool_class(server.port))
        monkeypatch.setattr(access_profile, "_resolve", lambda host, timeout: {"93.184.216.34"})
        monkeypatch.setattr(site_probe, "WALL_SECONDS", 1.0)
        started = time.monotonic()
        out = site_probe.fetch_home("example.com")
        assert time.monotonic() - started < 2.5
        assert out["status"] is None and out["label"] == "read_timeout"
    finally:
        server.close()


def test_h2_the_robots_reader_has_the_same_wall_clock_limit(monkeypatch):
    import time
    server = _Trickle(gap=0.5)
    try:
        monkeypatch.setattr(access_profile.urllib3, "HTTPSConnectionPool", _local_pool_class(server.port))
        monkeypatch.setattr(access_profile, "_resolve", lambda host, timeout: {"93.184.216.34"})
        monkeypatch.setattr(access_profile, "WALL_SECONDS", 1.0)
        started = time.monotonic()
        status, body, detail = access_profile.fetch_public_file("example.com", "/robots.txt", cap_bytes=1000)
        assert time.monotonic() - started < 2.5
        assert (status, body, detail) == ("error", None, "read_timeout")
    finally:
        server.close()


def test_h2_the_wall_clock_limit_bounds_the_stated_worst_case():
    assert site_probe.REQUEST_WORST_SECONDS == site_probe.WALL_SECONDS == access_profile.WALL_SECONDS
    assert site_probe.PROBE_WORST_SECONDS == site_probe.WALL_SECONDS + site_probe.PAUSE_SECONDS


@pytest.mark.parametrize("status", [600, 999, 1000, 99, 0, -1])
def test_h1_a_status_outside_100_to_599_is_other_status_with_no_code_kept(status):
    out = run(status=status)
    assert out["outcome"] == "other_status" and out["http_status"] is None and out["bot_vendor"] is None
    assert out["redirect"] is None and out["login_signal"] is None
    assert AhnCheck(**site_probe.view(site_probe_row(out), NOW))      # the answer schema accepts what is shown


def site_probe_row(classified):
    return {"probed_at": NOW, "outcome": classified["outcome"], "http_status": classified["http_status"], "bot_vendor": None,
            "bot_signal": None, "bot_confidence": None, "edge_vendors": [], "redirect": None, "login_signal": None,
            "response_ms": 10, "not_measured_reason": None, "fingerprint_version": "ahn-fp/1", "vantage": None}


def test_h1_an_invalid_view_is_dropped_not_raised():
    assert site_probe._valid({"status": "measured", "http_status": 999}) is None
    assert site_probe._valid(None) is None


def test_m5_a_widget_script_on_a_page_that_was_served_is_not_a_challenge():
    for widget in (b"https://www.google.com/recaptcha/api.js", b"https://js.hcaptcha.com/1/api.js",
                   b"https://challenges.cloudflare.com/turnstile/v0/api.js"):
        page = b"<html><form><script src='" + widget + b"'></script></form></html>"
        assert run(status=200, body=page)["outcome"] == "reachable"
        assert run(status=403, body=page)["outcome"] == "bot_challenge"        # on a blocked response it still counts


def test_l7_a_pending_view_says_when_to_ask_again():
    assert site_probe.pending_view()["retry_after_seconds"] == 60
    assert AhnCheck(**site_probe.pending_view()).retry_after_seconds == 60


# --- the stored view, the MCP line, the words (T-S, T-B2, T-B4) -------------------------------------------------


def row(**kw):
    base = {"probed_at": NOW - timedelta(hours=2), "outcome": "bot_challenge", "http_status": 403, "bot_vendor": "datadome",
            "bot_signal": "body", "bot_confidence": "high", "edge_vendors": ["datadome"], "redirect": None, "login_signal": None,
            "response_ms": 210, "not_measured_reason": None, "fingerprint_version": "ahn-fp/1",
            "vantage": {"kind": "datacenter", "region": "US"}}
    return {**base, **kw}


def test_s1_fresh_stale_and_hidden_at_the_24_hour_and_8_day_edges():
    assert site_probe.view(row(probed_at=NOW - timedelta(hours=24)), NOW)["freshness"] == "fresh"
    assert site_probe.view(row(probed_at=NOW - timedelta(hours=24, seconds=1)), NOW)["freshness"] == "stale"
    assert site_probe.view(row(probed_at=NOW - timedelta(days=8)), NOW)["freshness"] == "stale"
    assert site_probe.view(row(probed_at=NOW - timedelta(days=8, seconds=1)), NOW) is None


def test_s3_pending_and_not_measured_carry_no_measurement():
    for shown in (site_probe.pending_view(), site_probe.view(row(outcome="not_measured", not_measured_reason="robots_disallowed"), NOW)):
        model = AhnCheck(**shown)
        assert model.outcome is None and model.http_status is None and model.measured_at is None and model.age_seconds is None
    assert AhnCheck(**site_probe.pending_view()).status == "pending"


def test_b4_the_view_holds_only_literals_numbers_and_times():
    model = AhnCheck(**site_probe.view(row(), NOW))
    dumped = model.model_dump(mode="json", exclude_none=True)
    assert dumped["bot_defense"] == {"vendor": "datadome", "signal": "body", "confidence": "high"}
    assert dumped["basis"] == "ahn_server" and dumped["ua_token"] == "AgentHelpNetwork" and dumped["age_seconds"] == 7200
    assert all(not isinstance(value, bytes) for value in dumped.values())


def test_s2_the_mcp_line_has_the_age_the_vendor_and_the_caveat():
    shown = AhnCheck(**site_probe.view(row(probed_at=NOW - timedelta(seconds=30)), NOW))
    line = ahn_check_line(shown)
    assert line.startswith("AHN server check just now: 403, bot challenge (DataDome).")
    assert "data-center" in line and "may get a different answer" in line
    assert "3 h ago" in ahn_check_line(AhnCheck(**site_probe.view(row(probed_at=NOW - timedelta(hours=3)), NOW)))
    assert "2 days ago" in ahn_check_line(AhnCheck(**site_probe.view(row(probed_at=NOW - timedelta(days=2)), NOW)))
    assert ahn_check_line(AhnCheck(**site_probe.pending_view())) is None and ahn_check_line(None) is None


FORBIDDEN = ("proxy", "residential", "rotate", "headless", "stealth", "bypass", "different user agent", "try again from", "retry")


def test_b2_no_word_that_reads_as_a_workaround_in_any_fixed_sentence():
    lines = [site_probe.SHOW_VIEW_NOTE, site_probe.__doc__ or ""]
    for outcome in site_probe.OUTCOMES:
        shown = site_probe.view(row(outcome=outcome, http_status=403 if outcome != "unreachable" else None,
                                    bot_vendor="datadome" if outcome == "bot_challenge" else None), NOW)
        lines.append(ahn_check_line(AhnCheck(**shown)) or "")
    for text in lines:
        for word in FORBIDDEN:
            assert word not in text.lower()


# --- the switch and the caps -----------------------------------------------------------------------------------------


def test_the_check_is_off_unless_the_switch_is_one(monkeypatch):
    monkeypatch.delenv("AHN_SITE_PROBE", raising=False)
    assert site_probe.enabled() is False
    for value in ("0", "true", "yes", ""):
        monkeypatch.setenv("AHN_SITE_PROBE", value)
        assert site_probe.enabled() is False
    monkeypatch.setenv("AHN_SITE_PROBE", "1")
    assert site_probe.enabled() is True


def test_the_daily_cap_can_only_be_lowered(monkeypatch):
    monkeypatch.delenv("AHN_PROBE_DAILY_CAP", raising=False)
    assert site_probe.daily_cap() == 800
    monkeypatch.setenv("AHN_PROBE_DAILY_CAP", "50")
    assert site_probe.daily_cap() == 50
    monkeypatch.setenv("AHN_PROBE_DAILY_CAP", "5000")
    assert site_probe.daily_cap() == 800
    monkeypatch.setenv("AHN_PROBE_DAILY_CAP", "junk")
    assert site_probe.daily_cap() == 800


def test_the_numbers_the_policy_states():
    assert (site_probe.PROBE_SET_MAX, site_probe.SET_REFUSED_MAX, site_probe.SET_CURATED_MAX) == (500, 300, 150)
    assert (site_probe.HOURLY_CAP, site_probe.DAILY_CAP, site_probe.LOOKUP_DAILY_CAP, site_probe.LOOKUP_HOURLY_CAP) == (60, 800, 300, 40)
    assert (site_probe.BODY_CAP, site_probe.BODY_CAP_CHALLENGE) == (16384, 32768)
    assert (site_probe.MIN_GAP_HOURS, site_probe.FRESH_HOURS, site_probe.STALE_DAYS) == (6, 24, 8)
    assert (site_probe.SYNC_WAIT_SECONDS, site_probe.SYNC_CONCURRENCY) == (3.0, 4)


def test_the_curated_list_holds_valid_public_hosts_within_its_cap():
    hosts = site_probe.curated_hosts()
    assert 0 < len(hosts) <= site_probe.SET_CURATED_MAX * 2 and len(set(hosts)) == len(hosts)
    assert not set(hosts) & {"example.com", "example.net", "example.org"}
    assert site_probe.tranco_hosts().isdisjoint({"localhost"})


# --- the robots verdict for "/" (pure) ----------------------------------------------------------------------------


@pytest.mark.parametrize("status,body,detail,allowed,reason", [
    ("ok", b"User-agent: *\nAllow: /\n", "http_200", True, None),
    ("ok", b"User-agent: *\nDisallow: /\n", "http_200", False, "robots_disallowed"),
    ("ok", b"User-agent: AgentHelpNetwork\nDisallow: /\n\nUser-agent: *\nAllow: /\n", "http_200", False, "robots_disallowed"),
    ("absent", None, "http_404", True, None),
    ("absent", None, "http_410", True, None),            # an absent file is not a prohibition
    ("ok", b"User-agent: GPTBot\nDisallow: /\n", "http_200", True, None),   # only another agent is told no
    ("ok", b"User-agent: AgentHelpNetwork\nAllow: /\n\nUser-agent: *\nDisallow: /\n", "http_200", True, None),
    ("ok", b"User-agent: *\nDisallow: /private\n", "http_200", True, None),  # another path is not "/"
    ("unavailable", None, "http_403", True, None),
    ("refused", None, "http_403", False, "robots_refused"),
    ("error", None, "http_503", False, "robots_unreadable"),
    ("error", None, "connect_timeout", False, "robots_unreadable"),
    ("not_public", None, "not_public", False, "not_eligible"),
])
def test_home_gate_follows_the_same_interpreter_as_the_other_paths(status, body, detail, allowed, reason):
    gate = access_profile.home_gate(status, body, detail, "example.com")
    assert (gate["allowed"], gate["reason"]) == (allowed, reason)
    assert gate["read_host"] == "example.com" and (gate["rule"] is None or len(gate["rule"]) <= 80)
