"""ahn-client 0.6.1: ``diagnose(response)`` turns a response object into the lookup's codes, and the body stays local.

Pure: no network. Fake objects stand in for Playwright, httpx and requests responses (the client never imports them).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "clients" / "python" / "src"))
sys.path.insert(0, str(ROOT / "tests"))

import ahn_client as ahn  # noqa: E402
from ahn_client import _client, _diagnose  # noqa: E402
from ahn_client._result import MAX_SUMMARY, Result  # noqa: E402
from test_ahn_client import ANSWER, FakeResponse  # noqa: E402


class Playwrightish:
    """Playwright's Response: ``status``, a ``headers`` dict, ``text()`` as a method."""

    __module__ = "playwright.sync_api._generated"

    def __init__(self, status, headers=None):
        self.status, self.headers = status, headers or {}
        self.text_called = False

    def text(self):
        self.text_called = True
        return "captcha"


class Httpxish:
    __module__ = "httpx._models"

    def __init__(self, status, headers=None, text=""):
        self.status_code, self.headers, self.text = status, headers or {}, text


class Requestsish:
    __module__ = "requests.models"

    def __init__(self, status, headers=None, content=b""):
        self.status_code, self.headers, self.content = status, headers or {}, content


@pytest.fixture(autouse=True)
def fresh_cache():
    ahn.clear_cache()
    yield
    ahn.clear_cache()


@pytest.mark.parametrize("response,expected", [
    (Playwrightish(403), ("site_access", "response_observed", {"http_status": 403, "via": "browser"})),
    (Playwrightish(403, {"a": "b"}), ("site_access", "response_observed", {"http_status": 403, "challenge": "none", "via": "browser"})),
    (Playwrightish(403, {"cf-mitigated": "challenge"}), ("human_verification", "response_observed", {"http_status": 403, "challenge": "header", "via": "browser"})),
    (Httpxish(401, {"a": "b"}), ("login_auth", "response_observed", {"http_status": 401, "challenge": "none", "via": "http_fetch"})),
    (Requestsish(429, {"Retry-After": "30"}), ("service_error", "response_observed", {"http_status": 429, "challenge": "none", "retry_after": "lt_60", "via": "http_fetch"})),
    (Httpxish(503), ("service_error", "response_observed", {"http_status": 503, "via": "http_fetch"})),
    (Httpxish(451), ("site_access", "response_observed", {"http_status": 451, "via": "http_fetch"})),
    (Httpxish(402), ("site_access", "response_observed", {"http_status": 402, "via": "http_fetch"})),
])
def test_status_and_header_rules(response, expected):
    assert tuple(ahn.diagnose(response)) == expected


@pytest.mark.parametrize("response", [Playwrightish(404), Httpxish(405, {"x": "y"}), Httpxish(200, {}, "hello"), Httpxish(301),
                                      Playwrightish(202, {"x-amzn-waf-action": "allow"}), Playwrightish(202, {"x-amzn-waf-action": "block"}),
                                      object(), None, "403", 403])
def test_no_lookup_for_what_is_not_a_block(response):
    result = ahn.diagnose(response)
    assert tuple(result) == (None, None, {}) and result.candidate is False


@pytest.mark.parametrize("status,value", [(202, "challenge"), (405, "captcha"), (202, " Challenge "), (405, "CAPTCHA")])
def test_aws_waf_marker_makes_202_and_405_a_challenge(status, value):
    error_class, stage, found = ahn.diagnose(Playwrightish(status, {"X-Amzn-Waf-Action": value}))
    assert (error_class, stage) == ("human_verification", "response_observed")
    assert found == {"challenge": "header", "via": "browser"}  # 202 and 405 are not lookup statuses, so no http_status


def test_aws_waf_marker_on_a_403():
    assert tuple(ahn.diagnose(Httpxish(403, {"x-amzn-waf-action": "captcha"}))) == (
        "human_verification", "response_observed", {"http_status": 403, "challenge": "header", "via": "http_fetch"})


def test_a_403_with_a_body_marker_is_human_verification_but_challenge_stays_a_header_code():
    error_class, _, found = ahn.diagnose(Httpxish(403, {"a": "b"}, "<title>Just a moment...</title>"))
    assert error_class == "human_verification" and found["challenge"] == "none"
    assert ahn.diagnose(Httpxish(403, {"a": "b"}, "Forbidden"))[0] == "site_access"


def test_body_is_read_from_text_content_or_the_body_argument_and_only_the_head():
    assert ahn.diagnose(Requestsish(403, {"a": "b"}, b"Please solve the CAPTCHA"))[0] == "human_verification"
    assert ahn.diagnose(Playwrightish(403, {"a": "b"}), body="Verify you are human")[0] == "human_verification"
    assert ahn.diagnose(Httpxish(403, {"a": "b"}, "x" * 4000 + "captcha"))[0] == "site_access"
    assert ahn.diagnose(Httpxish(200, {}, "x" * 4000 + "captcha")).candidate is False


def test_a_method_named_text_is_never_called():
    response = Playwrightish(403, {"a": "b"})
    assert ahn.diagnose(response)[0] == "site_access" and response.text_called is False


def test_a_quiet_200_is_only_a_candidate():
    result = ahn.diagnose(Httpxish(200, {"a": "b"}, "<p>Are you a robot?</p>"))
    assert tuple(result)[:2] == ("human_verification", "response_observed") and result.candidate is True
    assert result[2] == {"via": "http_fetch"} and "challenge" not in result[2]
    # A normal page that mentions cloudflare or a challenge is not flagged.
    assert ahn.diagnose(Httpxish(200, {}, "Protected by Cloudflare. A coding challenge for you.")).candidate is False
    # Candidate is for 200 only.
    assert ahn.diagnose(Httpxish(201, {}, "captcha")).candidate is False and ahn.diagnose(Httpxish(404, {}, "captcha"))[0] is None


def test_diagnose_never_raises_and_never_returns_text():
    class Boom:
        @property
        def status_code(self):
            raise RuntimeError("x")

        @property
        def headers(self):
            raise RuntimeError("x")

    assert tuple(ahn.diagnose(Boom())) == (None, None, {})
    for response in (Playwrightish(403, {"cf-mitigated": "challenge"}), Httpxish(200, {}, "captcha secret-body")):
        assert "secret-body" not in json.dumps(ahn.diagnose(response))


def test_the_same_signals_as_the_exception_path_for_the_same_headers():
    class Err(Exception):
        pass

    response = Httpxish(429, {"Retry-After": "120", "cf-mitigated": "challenge"})
    exc = Err("x")
    exc.response = response
    assert ahn.diagnose(response)[2]["retry_after"] == ahn.signals(exc)["retry_after"] == "60_3600"
    assert ahn.diagnose(response)[2]["challenge"] == ahn.signals(exc)["challenge"] == "header"


def test_the_body_never_reaches_the_lookup_request(monkeypatch):
    calls = []

    def fake(request, timeout):
        calls.append(request)
        return FakeResponse(json.dumps(ANSWER).encode("utf-8"))

    monkeypatch.setattr(_client, "_urlopen", fake)
    marker = "UNIQUE-BODY-FRAGMENT-8f3a"
    response = Httpxish(403, {"x-amzn-waf-action": "challenge", "set-cookie": "sid=UNIQUE-COOKIE-77"},
                        f"<html>captcha {marker}</html>")
    error_class, stage, found = ahn.diagnose(response)
    assert ahn.check("https://example.com/private/path?q=1", error_class, stage, **found) is not None
    assert len(calls) == 1
    sent = (calls[0].full_url + repr(calls[0].headers) + repr(calls[0].data)).lower()
    assert "challenge=header" in sent and "error_class=human_verification" in sent
    for fragment in (marker, "unique-cookie-77", "captcha", "private", "path", "q=1"):
        assert fragment.lower() not in sent


# --- crawl-delay in the summary line (E5) ---------------------------------------------------------------

def parse(robots, **over):
    body = json.loads(json.dumps(ANSWER))
    body["access_profile"]["robots"].update(robots)
    body.update(over)
    result = Result.from_response("example.com", "https://agenthelpnetwork.com/v1/incidents/check?site_host=example.com", body)
    assert result is not None
    return result


@pytest.mark.parametrize("delay,text", [(10, "robots.txt asks 10 s between requests."), (2.5, "robots.txt asks 2.5 s between requests."),
                                        (10.0, "robots.txt asks 10 s between requests.")])
def test_crawl_delay_ends_the_summary(delay, text):
    result = parse({"wildcard_crawl_delay": delay})
    assert result.crawl_delay_seconds == float(delay)
    line = result.summary()
    assert line.endswith(text) and len(line) <= MAX_SUMMARY


@pytest.mark.parametrize("delay", [None, 0, -1, 3601, True, "10", float("nan")])
def test_no_crawl_delay_note_for_other_values(delay):
    result = parse({"wildcard_crawl_delay": delay})
    assert result.crawl_delay_seconds is None and "robots.txt asks" not in result.summary()


def test_crawl_delay_fits_with_a_long_service_line_and_the_safety_tail():
    result = parse({"wildcard_crawl_delay": 5, "agents_blocked": ["Agent%02d" % i for i in range(40)]},
                   summary_line="S" * 400, next_action={"do": "no_clear_action", "url": None, "retry_after_seconds": None})
    line = result.summary()
    assert len(line) <= MAX_SUMMARY and line.endswith("robots.txt asks 5 s between requests.")
    assert "bypassing site controls is not advised" in line


def test_version_and_exports():
    assert ahn.__version__ == "0.6.1" and "diagnose" in ahn.__all__
    assert _diagnose.AWS_WAF_HEADER == "x-amzn-waf-action" and _diagnose.AWS_WAF_VALUES == ("challenge", "captcha")


FORBIDDEN = ("user-agent", "spoof", "captcha solv", "solve the captcha", "bypass the", "evade", "fake header", "forge")


def test_no_sentence_reads_as_a_way_around_a_control():
    code = (ROOT / "clients/python/src/ahn_client/_diagnose.py").read_text(encoding="utf-8").lower()
    readme = (ROOT / "clients/python/README.md").read_text(encoding="utf-8").lower()
    section = readme.split("## diagnose a response")[1].split("\n## ")[0]
    changelog = (ROOT / "clients/python/CHANGELOG.md").read_text(encoding="utf-8").lower().split("## 0.6.0")[0]
    for source in (code.replace("solve the captcha", ""), changelog, section):
        for word in FORBIDDEN:
            assert word not in source, word
