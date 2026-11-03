"""D82: the sentences of `cause` that name AHN's own measurement (pure parts, no database)."""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

import pytest

from ahn import cause


def hours(n: float) -> timedelta:
    return timedelta(hours=n)


@pytest.mark.parametrize("age,text", [
    (timedelta(seconds=0), "just now"), (timedelta(seconds=299), "just now"), (timedelta(seconds=300), "5 min ago"),
    (timedelta(minutes=44, seconds=40), "44 min ago"), (timedelta(seconds=3599), "59 min ago"), (timedelta(seconds=3600), "1 h ago"),
    (hours(1.99), "1 h ago"), (hours(19.9), "19 h ago"), (timedelta(seconds=86399), "23 h ago"),
    (hours(24), "1 day ago"), (hours(47.9), "1 day ago"), (hours(48), "2 days ago"), (hours(49), "2 days ago"),
    (timedelta(days=2, hours=23), "2 days ago"), (timedelta(days=8), "8 days ago"), (timedelta(seconds=-5), "just now"),
])
def test_the_age_is_cut_down_like_the_mcp_first_line(age, text):
    assert cause.age_text(age) == text


@pytest.mark.parametrize("seconds", [0, 1, 299, 300, 3599, 3600, 7199, 86399, 86400, 172799, 172800, 700000, 10 ** 7])
def test_the_cause_line_and_the_mcp_first_line_tell_the_same_age(seconds):
    from ahn import mcp_adapter
    from types import SimpleNamespace

    check = SimpleNamespace(status="measured", outcome="reachable", age_seconds=seconds, http_status=200, bot_defense=None)
    assert mcp_adapter.ahn_check_line(check).startswith(f"AHN server check {cause.age_text(timedelta(seconds=seconds))}: ")
    assert cause.gate_line("refused", True, timedelta(seconds=seconds)).startswith(f"Not determined: AHN's check {cause.age_text(timedelta(seconds=seconds))} saw")


def test_the_provider_names_are_one_table():
    from ahn import check_text, mcp_adapter

    assert cause.VENDOR_NAMES is check_text.VENDOR_NAMES and mcp_adapter.VENDOR_NAMES is check_text.VENDOR_NAMES


def test_the_age_follows_the_age_seconds_of_ahn_check():
    from ahn import site_probe

    now = datetime(2026, 11, 3, 12, 0, tzinfo=timezone.utc)
    row = {"probed_at": now - hours(19.2), "outcome": "bot_challenge", "http_status": 403, "bot_vendor": "datadome", "bot_signal": "header",
           "bot_confidence": "high", "edge_vendors": [], "redirect": None, "login_signal": None, "response_ms": None, "fingerprint_version": "v1"}
    check = site_probe.view(row, now)
    assert check["age_seconds"] == int(hours(19.2).total_seconds())
    assert "19 h ago" in cause.gate_line("bot_challenge", True, timedelta(seconds=check["age_seconds"]), "datadome")
    from ahn import mcp_adapter
    from ahn.models import AhnCheck
    assert "19 h ago" in mcp_adapter.ahn_check_line(AhnCheck(**check))


def test_the_gate_sentence_names_age_and_the_provider_only_when_known():
    assert cause.gate_line("bot_challenge", True, hours(19), "datadome") == (
        "Not determined: AHN's check 19 h ago saw a DataDome bot-challenge refusal; recheck_after is AHN's next check, not an instruction.")
    assert cause.gate_line("bot_challenge", False, hours(49), "cloudflare") == (
        "Not determined: AHN's check 2 days ago saw a Cloudflare bot-challenge refusal; AHN checks again. That is AHN's check, not an instruction.")
    assert cause.gate_line("bot_challenge", True, hours(3), None) == (
        "Not determined: AHN's check 3 h ago saw a bot-challenge refusal; recheck_after is AHN's next check, not an instruction.")
    # a code that names no provider is left out, never guessed
    for code in ("unknown", "generic_captcha", "", "somethingnew"):
        assert cause.gate_line("bot_challenge", True, hours(3), code) == cause.gate_line("bot_challenge", True, hours(3), None)
    assert cause.gate_line("bot_challenge", True, hours(3), "aws_waf").startswith("Not determined: AHN's check 3 h ago saw an AWS WAF bot-challenge refusal;")
    # only a bot challenge carries a provider
    assert cause.gate_line("refused", True, hours(3), "cloudflare").startswith("Not determined: AHN's check 3 h ago saw an HTTP refusal;")
    assert cause.gate_line("login_wall", False, timedelta(0)).startswith("Not determined: AHN's check just now saw a login-wall refusal;")


def test_the_longest_combinations_stay_within_200_characters():
    longest = 0
    for outcome in cause.HOME_GATE_KINDS:
        for vendor in [None, *cause.VENDOR_NAMES]:
            for dated in (True, False):
                for age in (timedelta(0), hours(1), hours(48), timedelta(days=8), timedelta(days=100)):
                    line = cause.gate_line(outcome, dated, age, vendor)
                    longest = max(longest, len(line))
                    assert re.fullmatch(r"[ -~]+", line) and len(line) <= cause.LINE_MAX, line
    start, end = datetime(2026, 12, 31, 23, 59, tzinfo=timezone.utc), datetime(2027, 1, 2, 23, 59, tzinfo=timezone.utc)
    for key in ("site_errors", "recovered_site"):
        for line in (cause.line_for(key), cause.line_for(key, start), cause.line_for(key, start, end)):
            longest = max(longest, len(line))
            assert re.fullmatch(r"[ -~]+", line) and len(line) <= cause.LINE_MAX, line
            assert "Retry" not in line and "own checks" in line and "AHN's network path" in line and "may still reach" not in line
    assert longest <= 200
    assert "(12-31 23:59 to 01-02 23:59 UTC)" in cause.line_for("site_errors", start, end)
    # the window is kept in the longest case: nothing was dropped to fit
    assert "(12-31 23:59 to 01-02 23:59 UTC)" in cause.line_for("recovered_site", start, end)


def test_the_provider_names_are_the_codes_the_home_check_can_store():
    from ahn import probe_fingerprints

    assert set(cause.VENDOR_NAMES) == set(probe_fingerprints.VENDOR_CODES)
    assert set(cause.UNNAMED_VENDORS) == {"generic_captcha", "unknown"}
