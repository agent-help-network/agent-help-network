"""D51: summary_line, next_action and links. Pure: no database (the end-to-end REST check is test_response_guide_db.py).

The guide is computed from a finished answer and never changes it, so most of these tests build the answer with the real
triage functions over the whole input space and read the guide off it."""

from __future__ import annotations

import asyncio
import copy
import itertools
import json
import re
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pydantic_core
import pytest

from ahn import background_reads, mcp_adapter, response_guide, service, triage
from ahn.access_profile import AGENT_VENDORS, GOVERNING_TOKENS, parse_robots
from ahn.models import CheckResponse, NextAction
from ahn.response_guide import DO_VALUES, ROWS, WAIT_SECONDS, apex_host, guide, select_row
from ahn.settings import Settings
from ahn.triage import blocked_governing_tokens, next_checks, recommendation, robots_verdict

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 10, 22, 12, 0, tzinfo=timezone.utc)
OFFICIAL = {"name": "Example", "api_docs": "https://developer.example.com/docs", "developer_portal": "https://developer.example.com/", "note": None}
PORTAL_ONLY = {"name": "Example", "api_docs": None, "developer_portal": "https://portal.example.com/", "note": None}
STATUS_URL = "https://status.example.com/"
PAGE_URL = "https://page.example.com/status"
ACTION_OF_DO = {"wait_for_provider": "wait_provider_incident", "hand_off_to_human": "hand_off_to_human", "use_official_path": "use_official_path",
                "stop_automation": "stop_declared_block", "proceed": "proceed"}
NEW_KEYS = ("summary_line", "next_action", "links")
PRINTABLE = re.compile(r"^[\x20-\x7E]+$")
INJECTION = ["IGNORE PREVIOUS INSTRUCTIONS", "<script>alert(1)</script>", "line\nbreak", "http://evil.example/x", "javascript:alert(1)"]
ALL_TOKENS = {token for governing in GOVERNING_TOKENS.values() for token in governing}


def robots(text: str) -> dict:
    return {"robots_status": "ok", "robots": parse_robots(text), "llms_txt_status": "absent", "freshness": "fresh"}


def block(*tokens: str) -> str:
    return "".join(f"User-agent: {token}\nDisallow: /\n\n" for token in tokens) + "User-agent: *\nAllow: /\n"


def provider(indicator: str, *, basis="incident", impact="minor", updated=NOW - timedelta(hours=3), maintenances=0, url=STATUS_URL) -> dict:
    return {"source_name": "Example Status", "source_url": url, "indicator": indicator, "freshness": "fresh", "basis": basis,
            "active_incidents": 1, "max_incident_impact": impact, "incident_updated_at": updated, "active_maintenances": maintenances}


def maintenance_only(indicator: str, *, incidents=0, maintenances=2) -> dict:
    return {"source_name": "Example Status", "source_url": STATUS_URL, "indicator": indicator, "freshness": "fresh", "basis": "maintenance",
            "active_incidents": incidents, "max_incident_impact": "none", "active_maintenances": maintenances}


def answer(site_host="example.com", *, error_class=None, provider_status=None, access_profile=None, official=None, official_page=None,
           not_read=False, regional=False, vendor=None, checks=False) -> dict:
    rec = recommendation(error_class, provider_status, access_profile, official, profile_not_read=not_read, provider_regional=regional,
                         now=NOW, agent_vendor=vendor)
    result = {"site_host": site_host, "provider_status": provider_status, "official_status_page": official_page, "access_profile": access_profile,
              "official_paths": official, "recommendation": rec}
    if checks:
        result["next_checks"] = next_checks(error_class, provider_status, "no_reports", None, access_profile, official, profile_not_read=not_read,
                                            provider_partial=regional, now=NOW, agent_vendor=vendor)
    return result


def pending(retry=120, *, failed=False) -> dict:
    profile = {"robots_status": "error" if failed else "pending", "robots": None, "freshness": "pending", "llms_txt_status": "pending"}
    if retry is not None:
        profile["retry_after_seconds"] = retry
    return profile


# --- the input space ------------------------------------------------------------------------------------------------

# D57: the incident times an Atom reading can carry, and whether each makes the answer wait (with a window that is not
# blind and at least one open incident): within the week, exactly at its edge, just past it, far older, no time, a time
# within the hour-ahead slack, and one further ahead (no usable time).
ATOM_TIMES = ((NOW - timedelta(hours=3), True), (NOW - timedelta(days=7), True), (NOW - timedelta(days=7, seconds=1), False),
              (NOW - timedelta(days=40), False), (None, False), (NOW + timedelta(minutes=30), True), (NOW + timedelta(hours=2), False))


def atom_providers():
    """D52: a reading taken from a page's Atom history: no indicator, an open-incident count only when there is one.
    Yields (status, waits): D57 makes the answer wait exactly when an open incident updated within the week is listed in
    a window that is not blind."""
    for incidents, blind, maintenances, (updated, fresh_time) in itertools.product((0, 1, 3), (False, True), (None, 0, 2), ATOM_TIMES):
        status = {"source_name": "S", "source_url": STATUS_URL, "indicator": None, "freshness": "fresh", "source_kind": "history_atom"}
        if incidents:
            status["active_incidents"] = incidents
            if updated is not None:
                status["incident_updated_at"] = updated
        elif updated is not None:
            continue  # no open incident carries no incident time
        if blind:
            status["window_blind"] = True
        if maintenances is not None:
            status["active_maintenances"] = maintenances
        yield status, bool(incidents and not blind and fresh_time)
    # A stale read of a reading that would wait, and counts that are not whole numbers of 1 or more: never a wait.
    base = {"source_name": "S", "source_url": STATUS_URL, "indicator": None, "source_kind": "history_atom",
            "incident_updated_at": NOW - timedelta(hours=3)}
    yield {**base, "freshness": "stale", "active_incidents": 1}, False
    for odd in (True, 1.0, "1", -1):
        yield {**base, "freshness": "fresh", "active_incidents": odd}, False
    yield {**base, "freshness": "fresh", "active_incidents": 1, "window_blind": "yes"}, False  # anything but false/absent is blind


def providers():
    yield None
    yield {"source_name": "S", "source_url": STATUS_URL, "indicator": "major", "freshness": "stale", "basis": "incident"}
    yield {"source_name": "S", "source_url": STATUS_URL, "indicator": "none", "freshness": "fresh"}
    # The grid takes a slice of the Atom readings (one maintenance count, at most one incident); the D57 tests walk them all.
    yield from (status for status, _waits in atom_providers()
                if status.get("active_maintenances", 0) == 0 and status.get("active_incidents", 1) in (1, 0)
                and status.get("freshness") == "fresh")
    # D54: a minor page with no incident list (no basis), its update time fresh, over a week old, or missing, from a
    # feed whose time is the page's own and from one whose time is that of its active items (Instatus, Google Gmail).
    for updated in (NOW - timedelta(hours=3), NOW - timedelta(days=30), None):
        for kind in ({}, {"source_time": "active_items"}):
            yield {"source_name": "S", "source_url": STATUS_URL, "indicator": "minor", "freshness": "fresh",
                   "source_updated_at": updated, **kind}
    for indicator in ("minor", "major", "critical", "maintenance"):
        for basis in ("incident", "components_only", "maintenance"):
            for impact in ("minor", "critical"):
                for updated in (NOW - timedelta(hours=3), NOW - timedelta(days=30)):
                    for maintenances in (0, 1):
                        yield provider(indicator, basis=basis, impact=impact, updated=updated, maintenances=maintenances)
    # D55: an active status that rests only on maintenance in progress, with no incident (the shape status_scope.basis gives).
    for indicator in ("minor", "major", "critical", "maintenance"):
        yield maintenance_only(indicator)


PROFILES = [
    None, pending(120), pending(None), pending(60, failed=True),
    {"robots_status": "error", "robots": None, "freshness": "stale", "llms_txt_status": "absent"},
    {"robots_status": "refused", "robots": None, "freshness": "fresh", "llms_txt_status": "absent"},
    {"robots_status": "absent", "robots": None, "freshness": "fresh", "llms_txt_status": "absent"},
    {"robots_status": "ok", "robots": None, "freshness": "fresh", "llms_txt_status": "absent"},
    robots("User-agent: *\nAllow: /\n"),
    robots(block("Claude-User")),
    robots(block("ChatGPT-User")),
    robots(block("Claude-SearchBot")),
    robots(block("claude-web")),
    robots("User-agent: *\nDisallow: /\n"),
    robots(block(*sorted(ALL_TOKENS))),
    robots(block("Claude-User", "ChatGPT-User") + "User-agent: Amazonbot\nDisallow: /\n"),
]
ERROR_CLASSES = (None, "site_access", "login_auth", "human_verification", "service_error")


def main_space():
    for prov, regional in [(p, r) for p in providers() for r in (False, True) if r is False or (p and p.get("indicator") not in (None, "none"))]:
        for error_class, profile, official, vendor in itertools.product(ERROR_CLASSES, PROFILES, (None, OFFICIAL), (None, "anthropic", "other")):
            yield dict(error_class=error_class, provider_status=prov, access_profile=profile, official=official, regional=regional, vendor=vendor)


OFFICIAL_N = {**OFFICIAL, "access": {"auth": [{"method": "api_key", "source_url": STATUS_URL}, {"method": "none", "source_url": STATUS_URL}]}}
OFFICIAL_K = {**OFFICIAL, "access": {"auth": [{"method": "api_key", "source_url": STATUS_URL}]}}


def edge_cases():
    # D63: the two auth splits of O7 (no sign-up option / key needed) need an `access` entry on the official path.
    for official in (OFFICIAL_N, OFFICIAL_K):
        yield dict(error_class="site_access", official=official, access_profile=robots("User-agent: *\nAllow: /\n"))
    for error_class, official in itertools.product(ERROR_CLASSES, (None, OFFICIAL)):
        yield dict(site_host="app.example.com", error_class=error_class, official=official, not_read=True)
        yield dict(error_class=error_class, official=official)


def space():
    yield from main_space()
    yield from edge_cases()


def sample():
    # Every 11th case of the grid plus the sub-domain cases at its end: the full grid is walked once, in the T2 test.
    yield from itertools.islice(main_space(), 0, None, 11)
    yield from edge_cases()


def lines(n: int) -> dict:
    return {row: sentence.replace("{n}", str(n)) for row, (_do, sentence) in ROWS.items()}


EXTRA_TABLES = (response_guide.PROCEED_OFFICIAL_ROWS, response_guide.PROCEED_CONSUMER_ROWS, response_guide.CONSUMER_ROWS)
# D63: the tables beside ROWS are held to the same length, character and forbidden-phrase checks.
ALL_LINES = {text for n in (0, 5, 120, 300, 3600) for text in lines(n).values()}
ALL_LINES |= {text.replace("{n}", str(n)) for n in (0, 5, 120, 300, 3600) for table in EXTRA_TABLES for text in table.values()}
TEMPLATES = [re.compile(re.escape(sentence).replace(re.escape("{n}"), r"[0-9]{1,4}"))
             for sentence in [sentence for _do, sentence in ROWS.values()] + list(response_guide.PROCEED_OFFICIAL_ROWS.values())
             + list(response_guide.PROCEED_CONSUMER_ROWS.values()) + list(response_guide.CONSUMER_ROWS.values())]


def from_table(text: str) -> bool:
    return any(template.fullmatch(text) for template in TEMPLATES)


def test_d57_an_atom_reading_waits_low_only_on_a_fresh_open_incident_and_is_otherwise_the_d52_answer():
    # Changed on purpose by D57 (this was D52's "never changes the recommendation" test). A reading of the Atom history
    # that lists an open incident updated within the week, in a window that is not blind, now waits with low confidence
    # whatever the rest of the answer says (as any provider wait comes first). Every other reading (no open incident,
    # no usable time, older than the week, a blind window, a stale read, an odd count) still gives exactly the answer
    # for a host with no provider status at all, including its next_checks and guide: D52's invariant, narrowed.
    waits_seen = rests_seen = 0
    for case in main_space():
        if case["provider_status"] is not None:
            continue
        for atom, waits in atom_providers():
            with_atom = answer(**{**case, "provider_status": atom}, checks=True)
            without = answer(**case, checks=True)
            assert triage.history_incident(atom, NOW) is waits, atom
            if not waits:
                rests_seen += 1
                assert with_atom["recommendation"] == without["recommendation"], (atom, case)
                # D63: a fresh Atom reading says why indicator is null (same code, other sentence).
                assert [c["code"] for c in with_atom["next_checks"]] == [c["code"] for c in without["next_checks"]], (atom, case)
                assert [c for c in with_atom["next_checks"] if c["code"] != "provider_unknown"] == \
                    [c for c in without["next_checks"] if c["code"] != "provider_unknown"], (atom, case)
                assert guide(copy.deepcopy(with_atom)) == guide(copy.deepcopy(without)), (atom, case)
                continue
            waits_seen += 1
            # No regional scope either: the feed names no components, so provider_regional is ignored.
            rec = {key: value for key, value in with_atom["recommendation"].items() if key != "agent_vendor"}
            assert rec == {"action": "wait_provider_incident", "confidence": "low",
                           "reasons": ["provider_scope", "provider_history_incident"]}, (atom, case)
            codes = [check["code"] for check in with_atom["next_checks"]]
            assert codes[-3:-1] == ["provider_scope", "provider_history_incident"], codes
            assert "provider_unknown" not in codes and "provider_scope_partial" not in codes
            # Only the provider codes differ from the answer without a provider.
            assert [c for c in codes if c not in ("provider_scope", "provider_history_incident")] == \
                [c["code"] for c in without["next_checks"] if c["code"] != "provider_unknown"]
            extra = guide(copy.deepcopy(with_atom))
            assert extra["summary_line"] == ROWS["W8"][1] and extra["next_action"]["do"] == "wait_for_provider"
    assert waits_seen and rests_seen


def test_d57_the_atom_wait_is_the_wait_the_board_shares():
    # The board (D53) lists what provider_wait_reason says waits; D57 adds the Atom reading through that one function.
    for atom, waits in atom_providers():
        assert (triage.provider_wait_reason(atom, NOW) == "provider_scope") is waits, atom
        assert (triage.provider_wait_reason(atom, NOW) is None) is (not waits), atom


# --- T2: every answer the triage can give is on a row of the table, and every row is used --------------------------

def test_every_answer_is_on_a_listed_row_and_every_row_is_used():
    used = set()
    count = 0
    for case in space():
        result = answer(**case)
        row, listed = select_row(result["recommendation"], result["access_profile"], result["provider_status"], result["site_host"],
                                 result["official_paths"])
        assert listed, (row, result["recommendation"], case)
        used.add(row)
        count += 1
        extra = guide(copy.deepcopy(result))
        do = ROWS[row][0]
        assert extra["next_action"]["do"] == do
        # do is a function of the action: 1:1 except for the four values that split "unknown"
        action = result["recommendation"]["action"]
        if do in ACTION_OF_DO:
            assert action == ACTION_OF_DO[do], (row, action)
        else:
            assert action == "unknown" and row[0] == "U", (row, action)
        assert row[0] == {"wait_provider_incident": "W", "hand_off_to_human": "H", "use_official_path": "O", "stop_declared_block": "S",
                          "proceed": "P", "unknown": "U"}[action]
    assert count > 20000
    assert used == set(ROWS), set(ROWS) - used


def test_the_table_has_the_34_rows_and_the_nine_do_values():
    assert len(ROWS) == 34  # W8 came with D57; P1-V, P2-V, P3, O7-N and O7-K with D63
    assert set(DO_VALUES) == set(NextAction.model_fields["do"].annotation.__args__) and len(DO_VALUES) == 9


# --- T1: the guide never changes the verdict ------------------------------------------------------------------------

def test_the_guide_reads_the_answer_and_changes_nothing_in_it():
    for case in sample():
        result = answer(**case)
        before = copy.deepcopy(result)
        guide(result)
        assert result == before


# --- T3 and T6: length, characters, forbidden phrases ---------------------------------------------------------------

@pytest.mark.parametrize("n", [0, 5, 300, 3600])
def test_every_sentence_is_short_printable_ascii_on_one_line(n):
    for row, text in lines(n).items():
        assert 1 <= len(text) <= 200, (row, len(text))
        assert PRINTABLE.fullmatch(text), row
        assert "{" not in text and "}" not in text


@pytest.mark.parametrize("n", [0, 5, 300, 3600])
def test_the_tables_beside_rows_are_short_printable_ascii_on_one_line(n):
    for table in EXTRA_TABLES:
        for row, text in table.items():
            text = text.replace("{n}", str(n))
            assert 1 <= len(text) <= 200, (row, len(text))
            assert PRINTABLE.fullmatch(text), row
            assert "{" not in text and "}" not in text


def test_no_sentence_helps_get_around_a_control():
    for text in ALL_LINES:
        assert not re.search(r"proxy|solver|rotate|residential|stealth|spoof|disguise|headless|evade|circumvent", text, re.I), text
        if re.search(r"bypass", text, re.I):
            assert re.search(r"do not [a-z ]*bypass", text), text
    # the wait rows state the retry interval from the constant, in minutes
    assert f"about {background_reads.FEED_RETRY_MINUTES} minutes" in ROWS["W1"][1]
    assert WAIT_SECONDS == background_reads.FEED_RETRY_MINUTES * 60


def test_a_sentence_never_says_more_than_its_action():
    for row, (do, text) in ROWS.items():
        low = text.lower()
        if do in ("no_clear_action", "retry_lookup_once", "lookup_apex_host", "stop_if_listed"):
            assert "stop automated retries" not in low and "explicitly blocks" not in low, row  # unknown never says "stop everything"
            assert "is allowed" not in low and "permits" not in low, row
        if do != "proceed":
            assert "continue with" not in low, row
        # D63: P2 names where a caller finds out whether its own token is blocked instead of saying "guarantee".
        assert ("guarantee" in low or "agents_blocked" in low) if do == "proceed" else True


# --- T4: nothing from a site or a provider reaches the answer -------------------------------------------------------

def test_site_and_provider_text_never_reaches_the_sentence_or_the_action():
    hostile = INJECTION
    long_host = "a" * 60 + ".example.com"
    for bad in hostile:
        official = {"name": bad, "api_docs": "https://developer.example.com/docs", "developer_portal": None, "note": bad,
                    "access": {"auth": [{"method": "api_key", "source_url": bad}], "terms": {"url": bad, "quote": bad}}}
        prov = provider("minor", url=STATUS_URL) | {"source_name": bad, "affected": [bad], "error_detail": bad}
        prof = robots(block("Claude-User")) | {"llms_detail": bad, "robots_detail": bad, "llms_txt_status": "present"}
        prof["robots"]["rules"][0]["agent"] = "Claude-User"  # token names come from the table; the file's own text is not read
        for case in (dict(official=official, access_profile=prof), dict(provider_status=prov, official=official),
                     dict(access_profile=pending(90), official=official), dict(access_profile=pending(90)),
                     dict(access_profile=prof, vendor="anthropic")):
            result = answer(long_host, **case)
            result["observations"] = [{"task_step": bad}]
            extra = guide(result)
            assert from_table(extra["summary_line"])
            dump = json.dumps(extra)
            for needle in ("IGNORE", "<script", "evil.example", "javascript", "\\n", long_host):
                assert needle not in dump, (needle, case)
        # a hostile URL in a curated field is dropped, never repeated
        result = answer(official={"name": "x", "api_docs": bad, "developer_portal": None, "note": None},
                        access_profile=robots("User-agent: *\nAllow: /\n"), error_class="site_access")
        assert guide(result)["next_action"]["url"] is None


# --- T5: url ---------------------------------------------------------------------------------------------------------

def test_url_comes_only_from_the_allowed_sources_and_must_be_a_short_https_url():
    ok = robots("User-agent: *\nAllow: /\n")
    # D63: a lookup that reports no failure is proceed (no url); these walk the use_official_path url sources.
    assert guide(answer(official=OFFICIAL, access_profile=ok, error_class="site_access"))["next_action"]["url"] == OFFICIAL["api_docs"]
    assert guide(answer(official=PORTAL_ONLY, access_profile=ok, error_class="site_access"))["next_action"]["url"] == PORTAL_ONLY["developer_portal"]
    waiting = guide(answer(provider_status=provider("major"), official=OFFICIAL, access_profile=ok))
    assert waiting["next_action"]["do"] == "wait_for_provider" and waiting["next_action"]["url"] == STATUS_URL
    only_page = answer(provider_status=provider("major", url=None), official=OFFICIAL, official_page={"source_name": "x", "source_url": PAGE_URL})
    assert guide(only_page)["next_action"]["url"] == PAGE_URL
    for do_not in (guide(answer(access_profile=ok)), guide(answer(error_class="human_verification")), guide(answer(access_profile=pending(60)))):
        assert do_not["next_action"]["url"] is None
    for bad in ("http://developer.example.com/", "https://developer.example.com/ x", "javascript:alert(1)", "https://x/" + "a" * 200,
                "https://exa mple.com/", "ftp://example.com/", "", "https://user@example.com/", "https://example.com/\n"):
        result = answer(official={"name": "x", "api_docs": bad or None, "developer_portal": None, "note": None}, access_profile=ok)
        assert guide(result)["next_action"]["url"] is None, bad
    # the url, when there is one, is on a use_official_path or wait_for_provider row only
    for case in sample():
        extra = guide(answer(**case))["next_action"]
        assert (extra["url"] is not None) == (extra["do"] in ("use_official_path", "wait_for_provider")), case
        assert extra["url"] in (None, OFFICIAL["api_docs"], STATUS_URL)


# --- T7: stop_if_you_are ----------------------------------------------------------------------------------------------

def test_stop_if_you_are_lists_only_table_tokens_and_only_for_the_some_agents_reason():
    some = robots(block("Claude-User", "ChatGPT-User", "Googlebot"))
    result = answer(access_profile=some)
    assert "robots_blocks_some_agents" in result["recommendation"]["reasons"]
    extra = guide(result)
    # Googlebot is a search crawler, not a governing token (google's governing tokens are the user agents), so it is not listed
    assert extra["next_action"]["stop_if_you_are"] == ["ChatGPT-User", "Claude-User"] and extra["summary_line"] == ROWS["U3"][1]
    assert extra["next_action"]["do"] == "stop_if_listed"
    with_official = guide(answer(access_profile=some, official=OFFICIAL))
    assert with_official["next_action"]["do"] == "use_official_path" and with_official["next_action"]["stop_if_you_are"] == ["ChatGPT-User", "Claude-User"]
    assert with_official["summary_line"] == ROWS["O3"][1]
    # a vendor that is sent gets its own answer, and no list
    for vendor in ("anthropic", "other", "openai"):
        assert "stop_if_you_are" not in guide(answer(access_profile=some, vendor=vendor))["next_action"]
    for case in sample():
        result = answer(**case)
        extra = guide(result)["next_action"]
        assert ("stop_if_you_are" in extra) == ("robots_blocks_some_agents" in result["recommendation"]["reasons"]), case
        if "stop_if_you_are" in extra:
            assert 1 <= len(extra["stop_if_you_are"]) <= 16 and set(extra["stop_if_you_are"]) <= ALL_TOKENS
            assert len(set(extra["stop_if_you_are"])) == len(extra["stop_if_you_are"])
            assert extra["stop_if_you_are"] == blocked_governing_tokens(case["access_profile"])


def test_a_regional_wait_is_w3_and_the_token_table_fits_the_list_cap():
    result = answer(provider_status=provider("major"), regional=True)
    assert select_row(result["recommendation"], None, result["provider_status"], "example.com") == ("W3", True)
    assert guide(result)["summary_line"] == ROWS["W3"][1]
    assert len(ALL_TOKENS) <= response_guide.MAX_STOP_TOKENS == 16


def test_a_low_wait_on_a_minor_page_with_no_time_is_w5_not_the_plain_w1():
    # D54 (2): the answer waits with low confidence and provider_update_time_unknown; the line says the issue may be
    # old and when to wait, as W3 does for a regional scope. A regional scope still reads W3 first.
    for kind in ({}, {"source_time": "active_items"}):
        status = {"source_name": "S", "source_url": STATUS_URL, "indicator": "minor", "freshness": "fresh",
                  "source_updated_at": None, **kind}
        result = answer(provider_status=status)
        assert result["recommendation"]["confidence"] == "low"
        assert select_row(result["recommendation"], None, result["provider_status"], "example.com") == ("W5", True)
        assert guide(result)["summary_line"] == ROWS["W5"][1] and "may be old" in ROWS["W5"][1]
        regional = answer(provider_status=status, regional=True)
        assert select_row(regional["recommendation"], None, regional["provider_status"], "example.com") == ("W3", True)
    # A page with a usable time keeps W1.
    fresh = answer(provider_status={"source_name": "S", "source_url": STATUS_URL, "indicator": "minor", "freshness": "fresh",
                                    "source_updated_at": NOW - timedelta(days=30)})
    assert select_row(fresh["recommendation"], None, fresh["provider_status"], "example.com") == ("W1", True)


def test_an_invalid_api_docs_url_is_not_replaced_by_the_portal():
    bad = {"name": "x", "api_docs": "http://developer.example.com/", "developer_portal": "https://portal.example.com/", "note": None}
    result = answer(official=bad, access_profile=robots("User-agent: *\nAllow: /\n"), error_class="site_access")
    assert guide(result)["next_action"]["do"] == "use_official_path" and guide(result)["next_action"]["url"] is None


def test_the_token_list_and_the_verdict_read_the_same_vendor_states():
    import random
    rng = random.Random(51)
    for _ in range(300):
        tokens = rng.sample(sorted(ALL_TOKENS) + ["claude-web", "Googlebot", "Amazonbot", "GPTBot"], rng.randint(0, 9))
        prof = robots(block(*tokens) if rng.random() < .8 else block(*tokens).replace("Allow: /", "Disallow: /"))
        listed = blocked_governing_tokens(prof)
        reasons = robots_verdict(prof)[1]
        assert bool(listed) == bool({"robots_blocks_some_agents", "robots_blocks_agents"} & set(reasons)), (tokens, listed, reasons)
        assert ("robots_blocks_agents" in reasons) == (set(listed) == ALL_TOKENS)  # every vendor blocked, or not
        for vendor in AGENT_VENDORS:
            assert set(GOVERNING_TOKENS[vendor]) <= set(listed) or not set(GOVERNING_TOKENS[vendor]) & set(listed)
    # a profile whose vendor states cannot be read (parsed before D48) never yields a list
    old = robots(block("Claude-User"))
    del old["robots"]["vendor_aliases_blocked"]
    assert blocked_governing_tokens(old) == []


# --- T8: retry_after_seconds -----------------------------------------------------------------------------------------

def test_retry_after_seconds_is_the_profiles_own_value_on_three_rows_only():
    for case in sample():
        result = answer(**case)
        extra = guide(result)
        row, _ = select_row(result["recommendation"], result["access_profile"], result["provider_status"], result["site_host"])
        retry = extra["next_action"]["retry_after_seconds"]
        if row in ("O1", "U1", "U2"):
            assert retry == result["access_profile"]["retry_after_seconds"] and 0 <= retry <= 3600
            assert str(retry) in extra["summary_line"]
        elif row[0] == "W":
            assert retry == background_reads.FEED_RETRY_MINUTES * 60 == 900
        else:
            assert retry is None, row
    # a pending read with nothing queued has no retry: the answer is "unknown", not "ask again"
    assert guide(answer(access_profile=pending(None)))["next_action"]["retry_after_seconds"] is None
    assert guide(answer(access_profile=pending(10 ** 9)))["next_action"]["retry_after_seconds"] == 3600


def test_the_first_lookup_says_ask_once_more_and_a_failed_earlier_read_says_no_clear_action():
    first = guide(answer(access_profile=pending(120)))
    assert first["next_action"] == {"do": "retry_lookup_once", "url": None, "retry_after_seconds": 120}
    assert "once more in 120 s" in first["summary_line"] and "Do not poll" in first["summary_line"]
    again = guide(answer(access_profile=pending(60, failed=True)))
    assert again["next_action"] == {"do": "no_clear_action", "url": None, "retry_after_seconds": 60} and again["summary_line"] == lines(60)["U2"]
    with_path = guide(answer(access_profile=pending(120), official=OFFICIAL))
    assert with_path["next_action"] == {"do": "use_official_path", "url": OFFICIAL["api_docs"], "retry_after_seconds": 120}
    assert "look up once more in 120 s" in with_path["summary_line"]
    # pending is never turned into permission
    assert answer(access_profile=pending(120))["recommendation"] == {"action": "unknown", "confidence": "low", "reasons": ["profile_pending"]}


def test_the_failed_earlier_read_is_told_apart_by_the_stored_robots_status():
    # access_profile.get_profile stores a transport failure with robots_status 'error' and no fetched_at: freshness 'pending'
    from ahn.access_profile import _as_profile, pending_profile
    row = {"fetched_at": None, "robots_status": "error", "robots": None, "llms_txt_status": "unknown", "last_error_at": NOW, "robots_detail": "timeout",
           "llms_detail": None, "read_host": None, "content_use": None, "robots_sha256": None}
    stored = _as_profile("example.com", row, NOW, timedelta(hours=24))
    assert stored["freshness"] == "pending" and stored["robots_status"] == "error"
    assert pending_profile("example.com", 60)["robots_status"] == "pending"


# --- T9: lookup_host ---------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("host, apex", [("app.example.com", "example.com"), ("a.b.example.com", "example.com"), ("x.bbc.co.uk", "bbc.co.uk"),
                                        ("deep.x.bbc.co.uk", "bbc.co.uk"), ("tenant.xn--abc.com", None), ("example.com", None), ("www.example.com", "example.com"),
                                        ("host.localdomain", None), ("a.co.uk", None)])
def test_apex_host(host, apex):
    assert apex_host(host) == apex


def test_lookup_host_appears_only_with_lookup_apex_host():
    sub = guide(answer("app.example.com", not_read=True))
    assert sub["next_action"] == {"do": "lookup_apex_host", "url": None, "retry_after_seconds": None, "lookup_host": "example.com"}
    assert sub["summary_line"] == ROWS["U8"][1]
    unlistable = guide(answer("tenant.xn--abc.com", not_read=True))
    assert unlistable["next_action"]["do"] == "no_clear_action" and "lookup_host" not in unlistable["next_action"] and unlistable["summary_line"] == ROWS["U9"][1]
    for case in sample():
        extra = guide(answer(**case))["next_action"]
        assert ("lookup_host" in extra) == (extra["do"] == "lookup_apex_host")
        if "lookup_host" in extra:
            assert extra["lookup_host"] != case.get("site_host", "example.com") and service.aggregate_eligible_host(extra["lookup_host"])
    # www.<apex> is answered from the apex, so it never gets the sub-domain row
    result = answer("www.example.com", access_profile=robots("User-agent: *\nAllow: /\n"))
    assert guide(result)["next_action"]["do"] == "proceed"


# --- T10: links --------------------------------------------------------------------------------------------------------

def test_links_are_two_https_urls_and_a_blank_is_left_out(monkeypatch):
    extra = guide(answer())
    assert set(extra["links"]) == {"feedback", "changelog"} and all(value.startswith("https://") for value in extra["links"].values())
    assert extra["links"]["changelog"].endswith("docs/feedback-changelog.md#d64")  # the section that dates the wording of the verdicts
    assert guide(answer(), False)["links"]["changelog"].endswith("docs/feedback-changelog.md")  # D63 off: the page itself
    assert extra["links"] == guide(answer("other.example.org", error_class="site_access"))["links"]  # the same for every lookup
    monkeypatch.setattr(response_guide, "CHANGELOG_URL", "")
    assert guide(answer())["links"] == {"feedback": response_guide.FEEDBACK_URL}
    monkeypatch.setattr(response_guide, "FEEDBACK_URL", "")
    assert "links" not in guide(answer())
    model = CheckResponse.model_validate({**_minimal(), **guide(answer())})
    assert "links" not in model.model_dump(mode="json")  # left out, never null


def _minimal() -> dict:
    return {"site_host": "example.com", "generated_at": NOW, "synthetic": False, "window_hours": 24, "data_horizon_hours": 168,
            "current_site_status": "unknown", "evidence_level": "no_reports", "summary_code": "X", "summary": "Y", "failures": 0, "successes": 0,
            "reporting_sources": 0, "observations": [], "provider_source_state": "not_verified_by_current_methods"}


def test_the_links_stay_in_step_with_the_public_pages():
    text = (ROOT / "ahn" / "llms.txt").read_text(encoding="utf-8") + (ROOT / "ahn" / "agent_instructions.txt").read_text(encoding="utf-8")
    assert text.count(response_guide.FEEDBACK_URL) >= 2 and text.count(response_guide.CHANGELOG_URL) >= 2
    assert (ROOT / "docs" / "feedback-changelog.md").exists()


# --- T11: MCP ---------------------------------------------------------------------------------------------------------

def rest_answer(on: bool, **case) -> dict:
    result = answer(checks=True, **case)
    result = {**_minimal(), "generated_at": NOW.isoformat().replace("+00:00", "Z"), "site_host": result["site_host"], "provider_status": None,
              "official_paths": result["official_paths"], "access_profile": None, "recommendation": result["recommendation"],
              "next_checks": result["next_checks"], "failure_stage": None, "lookup_activity": None, "official_status_page": None,
              "provider_discovery_attempted_at": None, "provider_source_state": "not_verified_by_current_methods"}
    if on:
        result.update(guide({**result, "access_profile": case.get("access_profile")}))
    return result


def call_tool(monkeypatch, payload: dict):
    async def fake(method, path, *, params=None, extra_headers=None, **_):
        return payload
    monkeypatch.setattr(mcp_adapter, "call_api", fake)
    return asyncio.run(mcp_adapter.mcp.call_tool("check_incident", {"site_host": "example.com"}))


def test_mcp_result_is_a_summary_block_then_the_same_json(monkeypatch):
    payload = rest_answer(True, official=OFFICIAL, access_profile=robots("User-agent: *\nAllow: /\n"), error_class="site_access")
    result = call_tool(monkeypatch, payload)
    first, last = result.content[0].text, result.content[-1].text
    assert len(result.content) == 2
    assert first.splitlines()[0] == payload["summary_line"] and first.splitlines()[1] == "URL: " + OFFICIAL["api_docs"] and len(first.splitlines()) == 2
    assert json.loads(last) == result.structured_content  # the last block is pure JSON, equal to the structured content
    assert result.structured_content["summary_line"] == payload["summary_line"] and result.structured_content["next_action"] == payload["next_action"]
    assert result.structured_content["recommendation"] == payload["recommendation"]
    assert not result.is_error
    # no url: the first block is the sentence alone
    no_url = call_tool(monkeypatch, rest_answer(True, access_profile=pending(90)))
    assert no_url.content[0].text == lines(90)["U1"] and len(no_url.content) == 2


def test_mcp_without_a_summary_is_the_single_block_it_was_before(monkeypatch):
    payload = rest_answer(False, official=OFFICIAL)
    result = call_tool(monkeypatch, payload)
    assert len(result.content) == 1
    expected = pydantic_core.to_json(CheckResponse(**payload), indent=2).decode()
    assert result.content[0].text == expected and json.loads(expected) == result.structured_content
    assert not set(NEW_KEYS) & set(result.structured_content)


def test_mcp_tool_description_has_the_added_sentence_and_the_old_ones():
    tool = next(t for t in asyncio.run(mcp_adapter.mcp.list_tools()) if t.name == "check_incident")
    assert tool.description.endswith("The first text block is a one-line summary; next_action gives the step to take, and recommendation stays the verdict.")
    assert "A no_reports result means no observations, not a healthy site." in tool.description
    assert set(NEW_KEYS) <= set(tool.output_schema["properties"])
    assert not set(NEW_KEYS) & set(tool.output_schema.get("required", []))


# --- T12: size ----------------------------------------------------------------------------------------------------------

def test_the_added_keys_cost_at_most_800_bytes_compact():
    worst = 0
    for case in itertools.islice(sample(), 0, None, 5):
        payload = rest_answer(True, **{k: v for k, v in case.items() if k in ("official", "error_class", "vendor", "regional")})
        model = CheckResponse(**payload)
        full = len(model.model_dump_json().encode())
        without = len(CheckResponse(**{k: v for k, v in payload.items() if k not in NEW_KEYS}).model_dump_json().encode())
        worst = max(worst, full - without)
        assert len(payload["summary_line"]) <= 200
    assert worst <= 800, worst
    tokens = {"do": "stop_if_listed", "url": None, "retry_after_seconds": None, "stop_if_you_are": sorted(ALL_TOKENS)}
    assert len(json.dumps(tokens, separators=(",", ":"))) < 400


# --- T1, T14 and T15 through service.check (the database is replaced by a recording stand-in) ----------------------

class FakeResult:
    def __init__(self, one=None, many=()):
        self.one, self.many = one, list(many)

    def fetchone(self):
        return self.one

    def fetchall(self):
        return self.many


class FakeConnection:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    @contextmanager
    def transaction(self):
        yield

    def execute(self, sql, params=None):
        if "count(*) AS n FROM queried_hosts" in sql:
            return FakeResult({"n": 0})
        if sql.lstrip().startswith("INSERT INTO background_reads"):
            return FakeResult({"kind": params[0]})
        if sql.lstrip().startswith("WITH me AS"):
            return FakeResult({"not_before": datetime.now(timezone.utc), "ahn_ahead": 0, "ahead": 0})
        return FakeResult()

    def commit(self):
        pass


def settings(**kw) -> Settings:
    return Settings("postgresql://invalid/unused", "x" * 32, public_mode=True, reporting_enabled=True, **kw)


def lookup(monkeypatch, host, *, profile=None, due=False, official=None, on=True, **kw):
    monkeypatch.setattr(service, "connect", lambda _s: FakeConnection())
    if profile is not None:  # what the model needs of a stored profile
        profile = {"source_urls": {"robots_txt": f"https://{host}/robots.txt", "llms_txt": f"https://{host}/llms.txt"}, **profile}
    monkeypatch.setattr(service, "cached_profile", lambda _conn, _host, **_kw: (profile, due))
    monkeypatch.setattr(service, "official_paths", lambda _host: official)
    monkeypatch.setattr(background_reads, "worker_running", lambda: False)
    return service.check(settings(response_guide=on, **kw.pop("settings", {})), host, **kw)


SCENARIOS = [
    dict(host="example.com", profile=None, due=True, official=OFFICIAL),
    dict(host="example.com", profile=None, due=True),
    dict(host="example.com", profile=robots("User-agent: *\nAllow: /\n")),
    dict(host="example.com", profile=robots(block("Claude-User", "ChatGPT-User")), official=OFFICIAL),
    dict(host="example.com", profile=robots(block("Claude-User", "ChatGPT-User"))),
    dict(host="example.com", profile=robots(block(*sorted(ALL_TOKENS)))),
    dict(host="example.com", profile={"robots_status": "error", "robots": None, "freshness": "pending", "llms_txt_status": "error"}, due=True),
    dict(host="app.example.com"),
    dict(host="example.com", profile=robots("User-agent: *\nAllow: /\n"), official=OFFICIAL, error_class="human_verification"),
    dict(host="example.com", profile=robots("User-agent: *\nDisallow: /\n"), official=OFFICIAL, error_class="login_auth"),
]


EXPECTED_DO = ["use_official_path", "retry_lookup_once", "proceed", "use_official_path", "stop_if_listed", "stop_automation",
               "no_clear_action", "lookup_apex_host", "hand_off_to_human", "hand_off_to_human"]


@pytest.mark.parametrize("scenario", range(len(SCENARIOS)))
def test_switch_off_is_the_answer_before_d51_and_on_only_adds_three_keys(monkeypatch, scenario):
    spec = dict(SCENARIOS[scenario])
    host = spec.pop("host")
    off = lookup(monkeypatch, host, on=False, **copy.deepcopy(spec))
    on = lookup(monkeypatch, host, on=True, **copy.deepcopy(spec))
    assert not set(NEW_KEYS) & set(off)
    assert set(NEW_KEYS) <= set(on)
    assert on["next_action"]["do"] == EXPECTED_DO[scenario], (on["recommendation"], on["next_action"])
    strip = lambda d: {k: v for k, v in d.items() if k not in NEW_KEYS and k != "generated_at"}  # noqa: E731
    assert strip(on) == strip(off)
    assert on["recommendation"] == off["recommendation"] and on["next_checks"] == off["next_checks"]
    # the model accepts both, and serialises the old answer without any of the new keys (never null)
    for result in (on, off):
        CheckResponse(**result)
    dumped_off = CheckResponse(**off).model_dump(mode="json")
    assert not set(NEW_KEYS) & set(dumped_off)
    dumped_on = CheckResponse(**on).model_dump(mode="json")
    assert {k: v for k, v in dumped_on.items() if k not in NEW_KEYS and k != "generated_at"} == {k: v for k, v in dumped_off.items() if k != "generated_at"}
    assert list(dumped_on)[:5] == ["site_host", "generated_at", "summary_line", "next_action", "links"]  # the added keys sit up front
    assert [k for k in dumped_on if k not in NEW_KEYS] == list(dumped_off)  # the other keys keep their order


def test_a_service_answer_has_the_pending_first_lookup_shape(monkeypatch):
    on = lookup(monkeypatch, "example.com", profile=None, due=True, official=None)
    assert on["recommendation"]["reasons"] == ["profile_pending"] or on["recommendation"]["reasons"] == ["robots_unknown"]
    assert on["next_action"]["do"] in ("retry_lookup_once", "no_clear_action")


def test_a_failing_guide_leaves_the_three_keys_out_and_the_lookup_record_still_runs(monkeypatch, caplog):
    recorded = []
    monkeypatch.setattr(service, "_record_signal", lambda *a, **k: recorded.append(a))

    def boom(_result):
        raise RuntimeError("simulated")
    from ahn.lookup_signals import LookupSignal
    signal = LookupSignal(client_kind="other", source_address=None)
    spec = dict(profile=robots("User-agent: *\nAllow: /\n"), official=OFFICIAL, signal=signal)
    good = lookup(monkeypatch, "example.com", on=True, **copy.deepcopy(spec))
    recorded.clear()
    monkeypatch.setattr(response_guide, "guide", boom)
    with caplog.at_level("WARNING", logger="ahn.service"):
        broken = lookup(monkeypatch, "example.com", on=True, **copy.deepcopy(spec))
    assert not set(NEW_KEYS) & set(broken) and "response_guide_unavailable" in caplog.text
    strip = lambda d: {k: v for k, v in d.items() if k not in NEW_KEYS and k != "generated_at"}  # noqa: E731
    assert strip(broken) == strip(good)
    assert len(recorded) == 1  # the D47 record ran
    CheckResponse(**broken)


def test_a_test_mode_host_gets_the_unknown_row_and_stays_synthetic(monkeypatch):
    monkeypatch.setattr(service, "connect", lambda _s: FakeConnection())
    monkeypatch.setattr(service, "cached_profile", lambda *_a, **_k: (None, False))
    result = service.check(Settings("postgresql://invalid/unused", "x" * 32, test_mode=True), "demo.test")
    assert result["synthetic"] is True and result["access_profile"] is None and result["official_paths"] is None
    assert result["summary_line"] == ROWS["U9"][1] and result["next_action"]["do"] == "no_clear_action"
    assert CheckResponse(**result).synthetic is True


def test_the_switch_is_read_from_the_environment(monkeypatch):
    monkeypatch.setenv("AHN_DATABASE_URL", "postgresql://invalid/unused")
    monkeypatch.setenv("AHN_KEY_PEPPER", "x" * 32)
    monkeypatch.delenv("AHN_RESPONSE_GUIDE", raising=False)
    assert Settings.from_env().response_guide is True
    for value, expected in (("0", False), (" 0 ", False), ("1", True), ("", True), ("off", True)):
        monkeypatch.setenv("AHN_RESPONSE_GUIDE", value)
        assert Settings.from_env().response_guide is expected


# --- unmapped combinations ----------------------------------------------------------------------------------------------

def test_an_unlisted_combination_falls_to_the_default_row_of_its_action_and_is_logged(caplog):
    result = answer(access_profile=robots("User-agent: *\nAllow: /\n"))
    result["recommendation"] = {"action": "proceed", "confidence": "low", "reasons": ["a_code_nobody_mapped"]}
    with caplog.at_level("WARNING", logger="ahn.response_guide"):
        extra = guide(result)
    assert extra["summary_line"] == ROWS["P1"][1] and extra["next_action"]["do"] == "proceed"
    assert "response_guide_unmapped" in caplog.text and "example.com" not in caplog.text


def test_every_triage_reason_code_is_known_to_the_table():
    # Reasons the table reads; a new reason in triage that is not here must be read (or named) before the table is complete.
    read = {"provider_scope", "provider_scope_partial", "provider_components_only", "provider_incident_stale", "provider_update_time_unknown",
            "provider_maintenance_only", "provider_history_incident",
            "human_handoff", "account_handoff",
            "profile_pending", "official_path_available", "robots_blocks_agents", "robots_blocks_some_agents", "robots_names_vendor_alias",
            "robots_fetch_refused", "robots_wildcard_disallow", "robots_blocks_not_your_agent", "robots_allows_agents", "robots_unknown",
            "profile_not_read"}
    seen = set()
    for case in sample():
        seen.update(answer(**case)["recommendation"]["reasons"])
    assert seen <= read, seen - read
    assert read <= seen, read - seen


def test_the_contract_documents_name_every_do_value_and_the_switch():
    for name in ("api-contract.en.md", "api-contract.md"):
        text = (ROOT / "docs" / name).read_text(encoding="utf-8")
        for do in DO_VALUES:
            assert f"`{do}`" in text, (name, do)
        assert "AHN_RESPONSE_GUIDE=0" in text and "`summary_line`" in text and "`links`" in text and "`stop_if_you_are`" in text
    assert "| D51 " in (ROOT / "docs" / "decisions.md").read_text(encoding="utf-8")


def test_w5_and_the_unknown_time_check_state_the_same_condition():
    # One rule in both texts: wait only if the issue is recent AND names a service you use; otherwise (old, or no
    # service you use) continue. The wait side must never read as "recent or names a service".
    from ahn.triage import CHECKS, CHECKS_KO
    w5 = ROWS["W5"][1]
    assert "dates it recently and names a service you use, wait; otherwise continue" in w5
    assert "recently or" not in w5 and "or names a service" not in w5
    assert "if it is old or names no service you use, continue" in CHECKS["provider_update_time_unknown"]
    assert "오래됐거나 사용하는 서비스가 없으면 진행" in CHECKS_KO["provider_update_time_unknown"]
