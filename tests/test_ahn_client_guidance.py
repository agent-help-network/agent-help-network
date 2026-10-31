"""ahn-client 0.6.0: the service's own guidance (D51 summary_line, next_action, links) reaches the caller.

Pure: no network. The nine ``next_action.do`` values, an older service's answer without the three keys, the merge of the
site's Retry-After with the service's wait (done locally, never sent), and ``stop_if_listed`` kept apart from a plain
``unknown``.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "clients" / "python" / "src"))

import ahn_client as ahn  # noqa: E402
from ahn_client import _adapter, _client, _result  # noqa: E402
from ahn_client._result import DO_VALUES, SAFETY_TAIL, WAIT_DO, MAX_SUMMARY, NextAction, Result  # noqa: E402

BASE = {
    "site_host": "example.com", "evidence_level": "no_reports", "observations": [], "provider_status": None,
    "access_profile": {"robots_status": "ok", "llms_txt_status": "absent",
                       "robots": {"agents_blocked": ["GPTBot"], "agents_allowed": [], "rules": []}},
    "official_paths": {"name": "Example", "api_docs": "https://developer.example.com/apis", "developer_portal": None, "note": None},
    "recommendation": {"action": "use_official_path", "confidence": "high", "reasons": ["official_path_available"]},
    "next_checks": [{"code": "official_path_available", "text": "..."}],
}
LINKS = {"feedback": "https://github.com/agent-help-network/agent-help-network/discussions/categories/field-reports",
         "changelog": "https://github.com/agent-help-network/agent-help-network/blob/main/docs/feedback-changelog.md"}
STATUS = "https://status.example.com/"

# do -> (recommendation.action, next_action) as the service sends them (docs/api-contract.en.md, "Summary line and next action").
CASES = {
    "wait_for_provider": ("wait_provider_incident", {"do": "wait_for_provider", "url": STATUS, "retry_after_seconds": 900}),
    "hand_off_to_human": ("hand_off_to_human", {"do": "hand_off_to_human", "url": None, "retry_after_seconds": None}),
    "use_official_path": ("use_official_path", {"do": "use_official_path", "url": "https://developer.example.com/apis", "retry_after_seconds": None}),
    "stop_automation": ("stop_declared_block", {"do": "stop_automation", "url": None, "retry_after_seconds": None}),
    "proceed": ("proceed", {"do": "proceed", "url": None, "retry_after_seconds": None}),
    "retry_lookup_once": ("unknown", {"do": "retry_lookup_once", "url": None, "retry_after_seconds": 300}),
    "stop_if_listed": ("unknown", {"do": "stop_if_listed", "url": None, "retry_after_seconds": None, "stop_if_you_are": ["GPTBot", "ClaudeBot"]}),
    "lookup_apex_host": ("unknown", {"do": "lookup_apex_host", "url": None, "retry_after_seconds": None, "lookup_host": "example.com"}),
    "no_clear_action": ("unknown", {"do": "no_clear_action", "url": None, "retry_after_seconds": None}),
}


def server_lines():
    from ahn.response_guide import ROWS
    first = {}
    for do, sentence in ROWS.values():
        first.setdefault(do, sentence.replace("{n}", "300"))
    return first


def answer(do, **over):
    action, next_action = CASES[do]
    body = {**BASE, "recommendation": {**BASE["recommendation"], "action": action},
            "summary_line": server_lines()[do], "next_action": dict(next_action), "links": dict(LINKS)}
    body.update(over)
    return body


def parse(body, host="example.com"):
    result = Result.from_response(host, "https://agenthelpnetwork.com/v1/incidents/check?site_host=" + host, body)
    assert result is not None
    return result


def test_the_client_knows_the_services_nine_values():
    from ahn.response_guide import DO_VALUES as SERVER_DO
    assert set(DO_VALUES) == set(SERVER_DO) == set(CASES)


@pytest.mark.parametrize("do", sorted(CASES))
def test_each_do_is_kept_and_leads_the_summary(do):
    result = parse(answer(do))
    expected = CASES[do][1]
    assert result.next_action.do == do
    assert result.summary_line == server_lines()[do]
    assert result.links == LINKS
    assert result.recommendation == CASES[do][0]
    assert result.next_action.url == expected["url"]
    assert result.next_action.retry_after_seconds == expected["retry_after_seconds"]
    assert result.next_action.stop_if_you_are == tuple(expected.get("stop_if_you_are", ()))
    assert result.next_action.lookup_host == expected.get("lookup_host")
    summary = result.summary()
    assert summary.startswith(f"AHN example.com: {server_lines()[do]}")
    assert "\n" not in summary and len(summary) <= MAX_SUMMARY
    assert "recommendation=" in summary  # the detail line still follows when it fits


def test_next_action_values_are_in_the_line():
    assert "Stop if you are: GPTBot, ClaudeBot." in parse(answer("stop_if_listed")).summary()
    assert "Look up: example.com." in parse(answer("lookup_apex_host")).summary()
    assert "URL: https://developer.example.com/apis" in parse(answer("use_official_path")).summary()


@pytest.mark.parametrize("do,expected", [
    ("use_official_path", "For example.com, start with the official path: https://developer.example.com/apis"),
    ("wait_for_provider", None),  # a state that passes: not a permanent note
    ("hand_off_to_human", None),
    ("stop_automation", "For example.com, the site declares agents blocked: do not automate its pages."),
    ("proceed", "For example.com, no declared block was found for the agents checked: continue with modest requests (not an access guarantee)."),
    ("stop_if_listed", "For example.com, if your requests send GPTBot, ClaudeBot, do not automate its pages."),
    ("lookup_apex_host", "For example.com, look up example.com for the site-wide policy."),
    ("retry_lookup_once", None),
    ("no_clear_action", None),
])
def test_remember_is_one_fixed_sentence_from_next_action(do, expected):
    assert parse(answer(do)).remember == expected


def test_stop_if_listed_is_not_read_as_a_plain_unknown():
    listed, plain = parse(answer("stop_if_listed")), parse(answer("no_clear_action"))
    assert listed.recommendation == plain.recommendation == "unknown"
    assert listed.next_action.do == "stop_if_listed" and plain.next_action.do == "no_clear_action"
    assert listed.next_action.stop_if_you_are == ("GPTBot", "ClaudeBot")
    assert "Stop if you are" in listed.summary() and "Stop if you are" not in plain.summary()
    assert listed.remember and plain.remember is None


# --- an older service: no D51 keys ---------------------------------------------------------------------------

def test_an_answer_without_the_keys_works_as_before():
    result = parse(BASE)
    assert result.summary_line is None and result.next_action is None and result.links is None
    assert result.remember is None
    assert result.recommendation == "use_official_path"
    assert result.summary().startswith("AHN example.com: recommendation=use_official_path (high).")


GOLDEN_050 = ("AHN example.com: recommendation=use_official_path (high). Declared policy: ok; blocked agents: GPTBot; llms.txt: absent. "
              "Official path: https://developer.example.com/apis. Provider status: not connected. Reports: no_reports. "
              "Next: official_path_available. Full JSON: https://agenthelpnetwork.com/v1/incidents/check?site_host=example.com. "
              "Declared policy is not an access guarantee; bypassing site controls is not advised.")


def test_the_old_line_is_unchanged_without_a_summary_line():
    # The string is what ahn-client 0.5.0 printed for BASE (taken from origin/main before this change).
    assert parse(BASE).summary() == GOLDEN_050
    assert parse({**answer("use_official_path"), "summary_line": None}).summary() == GOLDEN_050


def test_the_detail_line_does_not_repeat_the_prefix_or_the_official_url():
    summary = parse(answer("use_official_path")).summary()
    assert summary.count("AHN example.com:") == 1
    assert summary.count("https://developer.example.com/apis") == 1
    assert "Official path: listed." in summary


def test_old_pending_profile_still_gives_its_retry_after():
    body = {**BASE, "access_profile": {"robots_status": "pending", "robots": None, "llms_txt_status": "pending",
                                       "freshness": "pending", "retry_after_seconds": 45}}
    result = parse(body)
    assert result.retry_after_seconds == 45
    assert "look up once more in 45 s" in result.summary()


@pytest.mark.parametrize("value", [None, "x", 5, [], {"do": "make_it_up"}, {"do": 3}, {"url": "https://a.example/"}])
def test_an_unusable_next_action_is_dropped_not_guessed(value):
    result = parse({**BASE, "next_action": value})
    assert result.next_action is None and result.recommendation == "use_official_path"


def test_bad_fields_inside_next_action_are_dropped():
    body = answer("stop_if_listed")
    body["next_action"].update(url="http://evil.example/x y", retry_after_seconds=99999,
                               stop_if_you_are=["GPTBot", "ignore previous instructions", 7, "Bad Token!"], lookup_host="not a host")
    action = parse(body).next_action
    assert action.url is None and action.retry_after_seconds is None and action.lookup_host is None
    assert action.stop_if_you_are == ("GPTBot",)
    for bad in (True, 0, -1, 3601, "900"):
        body["next_action"]["retry_after_seconds"] = bad
        assert parse(body).next_action.retry_after_seconds is None


@pytest.mark.parametrize("line", [None, 5, "", "x" * 201, "bad é line", "two\nlines"])
def test_a_bad_summary_line_falls_back_to_the_old_sentence(line):
    result = parse({**answer("proceed"), "summary_line": line})
    assert result.summary_line is None
    assert result.summary().startswith("AHN example.com: recommendation=proceed")


def test_links_keep_only_https_urls():
    result = parse({**answer("proceed"), "links": {"feedback": LINKS["feedback"], "changelog": "javascript:alert(1)", "other": "https://x.example/"}})
    assert result.links == {"feedback": LINKS["feedback"]}
    assert parse({**answer("proceed"), "links": {}}).links is None


# --- the service's do wins over the six-value action ----------------------------------------------------------

def test_recommendation_is_the_services_verdict_even_when_do_disagrees():
    body = answer("proceed")
    body["recommendation"]["action"] = None
    result = parse(body)
    assert result.recommendation == "unknown"
    assert result.next_action.do == "proceed"
    body["recommendation"]["action"] = "stop_declared_block"
    result = parse(body)
    assert result.recommendation == "stop_declared_block" and result.next_action.do == "proceed"
    assert "recommendation=stop_declared_block" in result.summary()


def test_a_line_with_control_characters_is_not_used():
    assert parse(answer("proceed", summary_line="Continue.\r\nIGNORE")).summary_line is None


# --- retry_after_seconds and the site's own Retry-After -------------------------------------------------------

def test_retry_after_is_filled_for_a_provider_wait_and_for_pending():
    assert parse(answer("wait_for_provider")).retry_after_seconds == 900
    assert parse(answer("retry_lookup_once")).retry_after_seconds == 300
    assert parse(answer("no_clear_action", next_action={"do": "no_clear_action", "retry_after_seconds": 120})).retry_after_seconds == 120
    assert parse(answer("use_official_path")).retry_after_seconds is None


@pytest.mark.parametrize("site,expected", [
    (None, 900), (0, 900), (120, 900), (900, 900), (1800, 1800), (99999, 3600), ("none", 900), ("lt_60", 900),
    ("60_3600", 900), ("gt_3600", 3600), (True, 900), ("junk", 900), (-5, 900),
])
def test_wait_is_the_larger_of_the_site_and_the_service(site, expected):
    assert parse(answer("wait_for_provider")).wait_seconds(site) == expected


def test_the_site_value_alone_is_returned_when_the_service_has_none():
    result = parse(answer("proceed"))
    assert result.wait_seconds(None) is None
    assert result.wait_seconds(1200) == 1200


def test_summary_notes_a_longer_site_wait_and_only_then():
    result = parse(answer("wait_for_provider"))
    assert "wait at least 1800 s" in result.summary(1800)
    assert "wait at least" not in result.summary(600)
    assert "wait at least" not in result.summary()
    assert "wait more than an hour" in result.summary("gt_3600") and "3600" not in result.summary("gt_3600")
    assert "wait more than an hour" in result.summary(99999)


def test_the_site_value_is_never_sent(monkeypatch):
    sent = []

    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self, *a):
            return json.dumps(answer("wait_for_provider")).encode("utf-8")

    def fake(request, timeout):
        sent.append(request.full_url)
        return Response()

    ahn.clear_cache()
    monkeypatch.delenv("AHN_CLIENT_DISABLE", raising=False)
    monkeypatch.setattr(_client, "_urlopen", fake)
    result = ahn.check("example.com", "service_error", retry_after="gt_3600")
    assert "retry_after=gt_3600" in sent[0]  # the existing optional lookup code, unchanged
    assert result.wait_seconds(3000) == 3000 and result.wait_seconds(10) == 900
    assert result.raw["next_action"]["retry_after_seconds"] == 900  # the service's value is not rewritten
    assert len(sent) == 1
    ahn.clear_cache()


def test_the_adapter_note_uses_the_retry_after_band_it_already_holds(monkeypatch):
    result = parse(answer("wait_for_provider"))
    monkeypatch.setattr(_adapter, "failure_target", lambda error, args=None: ("example.com", "service_error", None))
    monkeypatch.setattr(_adapter, "failure_signals", lambda error: {"http_status": 503, "retry_after": "gt_3600"})
    seen = {}

    def fake_check(host, error_class, stage, adapter, timeout, extra):
        seen["extra"] = extra
        return result

    monkeypatch.setattr(_adapter, "bounded_check", fake_check)
    note = _adapter.note_for(RuntimeError("503"), None, adapter="langchain")
    assert note.startswith("AHN example.com: The provider reports an active issue.")
    assert "wait more than an hour" in note
    assert seen["extra"] == {"http_status": 503, "retry_after": "gt_3600"}  # the same codes as before; nothing new is sent


def test_summary_stays_within_700_with_long_values():
    body = answer("stop_if_listed")
    body["next_action"]["stop_if_you_are"] = ["A" * 40] * 16
    body["access_profile"]["robots"]["agents_blocked"] = ["B" * 40] * 30
    assert len(parse(body, "a" * 60 + ".example.com").summary()) <= MAX_SUMMARY


def test_next_action_is_exported():
    assert ahn.NextAction is NextAction and ahn.__version__ == "0.6.0"
    assert _result.NextAction is NextAction


# --- a stop, a hand-off or an official path is not "retry in an hour" -----------------------------------------

NO_WAIT = ("stop_automation", "stop_if_listed", "hand_off_to_human", "use_official_path")


def test_the_do_sets_are_the_complement_of_each_other():
    assert set(NO_WAIT) | WAIT_DO == set(DO_VALUES) and not set(NO_WAIT) & WAIT_DO


@pytest.mark.parametrize("do", NO_WAIT)
@pytest.mark.parametrize("site", ["gt_3600", "60_3600", 1800, 99999])
def test_the_sites_retry_after_is_not_merged_into_a_stop_or_hand_off(do, site):
    result = parse(answer(do))
    assert result.retry_after_seconds is None
    assert result.wait_seconds(site) is None
    summary = result.summary(site)
    assert "Retry-After" not in summary and "wait at least" not in summary and "an hour" not in summary
    assert summary == result.summary()


def test_stop_automation_with_gt_3600_and_stop_if_listed_with_a_429():
    stop = parse(answer("stop_automation"))
    assert stop.wait_seconds("gt_3600") is None and stop.summary("gt_3600") == stop.summary()
    listed = parse(answer("stop_if_listed"))
    assert listed.wait_seconds("gt_3600") is None
    note = listed.summary("gt_3600")
    assert "Stop if you are: GPTBot, ClaudeBot." in note and "Retry-After" not in note


def test_a_server_retry_on_use_official_path_is_not_carried():
    body = answer("use_official_path")
    body["next_action"]["retry_after_seconds"] = 45
    result = parse(body)
    assert result.next_action.retry_after_seconds == 45  # as sent
    assert result.retry_after_seconds is None and result.wait_seconds() is None
    assert "retry_after_seconds" not in result.next_action.to_dict()


@pytest.mark.parametrize("do", sorted(WAIT_DO))
def test_the_merge_still_applies_to_the_come_back_later_answers(do):
    assert parse(answer(do)).wait_seconds(1800) == 1800


OLD_STOPS = ("stop_declared_block", "hand_off_to_human", "use_official_path")


def old_answer(action, **over):
    """An answer from a service without next_action (older, or AHN_RESPONSE_GUIDE=0)."""
    return {**BASE, "recommendation": {**BASE["recommendation"], "action": action}, **over}


@pytest.mark.parametrize("action", ["proceed", "unknown", "wait_provider_incident"])
def test_an_older_service_still_merges_the_site_value_for_come_back_later_verdicts(action):
    assert parse(old_answer(action)).wait_seconds(1800) == 1800


@pytest.mark.parametrize("action", OLD_STOPS)
@pytest.mark.parametrize("site", ["gt_3600", "60_3600", 1800])
def test_an_older_service_with_a_stop_verdict_never_gets_a_site_wait(action, site):
    result = parse(old_answer(action))
    assert result.next_action is None
    assert result.wait_seconds(site) is None
    assert "Retry-After" not in result.summary(site) and result.summary(site) == result.summary()
    # the same with the service's sentence but no readable next_action
    with_line = parse(old_answer(action, summary_line=server_lines()["stop_automation"]))
    assert with_line.wait_seconds(site) is None
    assert "Retry-After" not in with_line.summary(site) and with_line.summary(site) == with_line.summary()


def test_a_next_action_with_an_unknown_do_never_merges_the_site_value():
    body = answer("proceed", next_action={"do": "a_tenth_value", "url": None, "retry_after_seconds": 900})
    result = parse(body)
    assert result.next_action is None
    assert result.wait_seconds("gt_3600") is None
    assert "Retry-After" not in result.summary("gt_3600")


def test_the_pending_profile_value_survives_a_hand_off_or_an_official_path():
    profile = {"robots_status": "pending", "robots": None, "llms_txt_status": "pending", "freshness": "pending",
               "retry_after_seconds": 45}
    for do in ("use_official_path", "hand_off_to_human", "stop_if_listed"):
        result = parse(answer(do, access_profile=profile))
        assert result.retry_after_seconds is None and result.lookup_again_seconds == 45
        assert result.wait_seconds("gt_3600") is None
    old = parse(old_answer("use_official_path", access_profile=profile))
    assert old.retry_after_seconds == 45 and old.lookup_again_seconds == 45  # as 0.5.0
    assert parse(BASE).lookup_again_seconds is None


# --- the cache keeps a pending profile only for its own retry_after_seconds --------------------------------------

def test_a_pending_profile_is_cached_for_its_own_seconds_even_after_a_hand_off(monkeypatch):
    body = answer("hand_off_to_human")
    body["access_profile"] = {"robots_status": "pending", "robots": None, "llms_txt_status": "pending",
                              "freshness": "pending", "retry_after_seconds": 45}
    result = parse(body)
    assert result.retry_after_seconds is None  # hand_off_to_human carries no wait
    calls = []
    clock = [1000.0]

    def fake_fetch(*args):
        calls.append(args)
        return result

    ahn.clear_cache()
    monkeypatch.delenv("AHN_CLIENT_DISABLE", raising=False)
    monkeypatch.setattr(_client, "_fetch", fake_fetch)
    monkeypatch.setattr(_client.time, "monotonic", lambda: clock[0])
    assert ahn.check("example.com", "human_verification") is result
    clock[0] += 30
    ahn.check("example.com", "human_verification")
    assert len(calls) == 1  # still cached
    clock[0] += 30  # 60 s: past the profile's 45 s, far short of 600 s
    ahn.check("example.com", "human_verification")
    assert len(calls) == 2
    ahn.clear_cache()


# --- length and the closing caution ------------------------------------------------------------------------------

def test_the_closing_caution_survives_a_long_head():
    body = answer("stop_if_listed")
    body["next_action"]["stop_if_you_are"] = ["A" * 40] * 16
    body["next_action"]["url"] = "https://" + "a" * 60 + ".example.com/" + "b" * 100
    summary = parse(body, "a" * 60 + ".example.com").summary()
    assert len(summary) <= MAX_SUMMARY and summary.endswith(SAFETY_TAIL)
    assert parse(answer("proceed")).summary().endswith(SAFETY_TAIL)
