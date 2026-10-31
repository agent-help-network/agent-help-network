"""D63: proceed when no failure is reported, and a missing robots.txt reads as no declared restriction.

Pure: no database, no network. The pre-D63 decision code is kept as tests/_pre_d63_triage.py, the reference the switch-off
and the unchanged rows are compared with. The service-level check is in test_d63_decision_order_db.py."""

from __future__ import annotations

import asyncio
import hashlib
import importlib.util
import itertools
import json
import logging
import random
import re
from datetime import datetime, timezone
from pathlib import Path

import pytest

from ahn import mcp_adapter, response_guide, triage
from ahn.access_profile import parse_robots
from ahn.models import CheckResponse
from ahn.response_guide import CONSUMER_ROWS, LINE_MAX, PROCEED_CONSUMER_ROWS, PROCEED_OFFICIAL_ROWS, ROWS, guide
from ahn.triage import (CHECKS, CHECKS_KO, HISTORY_QUIET_CHECK, HISTORY_QUIET_CHECK_KO, LLMS_TOO_LARGE_CHECK,
                        LLMS_TOO_LARGE_CHECK_KO, ROBOTS_ABSENT_CHECK, ROBOTS_ABSENT_CHECK_KO)

HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("pre_d63_triage", HERE / "_pre_d63_triage.py")
pre = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(pre)

NOW = datetime(2026, 10, 31, 12, 0, tzinfo=timezone.utc)
HELP = "https://www.example.com/help"
SRC = "https://developer.example.com/"
OFFICIAL = {"name": "Example", "api_docs": "https://developer.example.com/", "developer_portal": None, "note": None}
OFFICIAL_N = {**OFFICIAL, "access": {"auth": [{"method": "api_key", "source_url": SRC}, {"method": "none", "source_url": SRC}]}}
OFFICIAL_K = {**OFFICIAL, "access": {"auth": [{"method": "api_key", "source_url": SRC}]}}
CONSUMER = {"paths": [{"kind": "customer_help", "url": HELP, "source_url": "https://www.example.com/"}], "verified_at": "2026-10-31"}
WITH_CONSUMER = {**OFFICIAL, "consumer": CONSUMER}
CONSUMER_ONLY = {"name": "Example", "api_docs": None, "developer_portal": None, "note": None, "consumer": CONSUMER}
ERROR_CLASSES = (None, "site_access", "login_auth", "human_verification", "service_error")


def robots(text: str) -> dict:
    return {"robots_status": "ok", "robots": parse_robots(text), "llms_txt_status": "absent", "freshness": "fresh"}


def status(value: str) -> dict:
    return {"robots_status": value, "robots": None, "llms_txt_status": "absent", "freshness": "fresh"}


ALLOWS = robots("User-agent: *\nAllow: /\n")
BLOCKS = robots("User-agent: *\nDisallow: /\n")
OTHER_BLOCKED = robots("User-agent: Claude-SearchBot\nDisallow: /\n\nUser-agent: *\nAllow: /\n")
VENDOR_BLOCKED = robots("User-agent: Claude-User\nDisallow: /\n\nUser-agent: *\nAllow: /\n")
ABSENT = status("absent")
UNREADABLE = [status(s) for s in ("error", "redirected", "invalid", "too_large", "not_public")]
PENDING = {"robots_status": "pending", "robots": None, "llms_txt_status": "pending", "freshness": "pending", "retry_after_seconds": 120}
PROFILES = [None, PENDING, ABSENT, ALLOWS, BLOCKS, OTHER_BLOCKED, VENDOR_BLOCKED, status("refused"), *UNREADABLE,
            robots("User-agent: ChatGPT-User\nDisallow: /\n\nUser-agent: *\nAllow: /\n"),
            {**ALLOWS, "llms_txt_status": "present", "llms_detail": "too_large"}]
PROVIDERS = [None, {"freshness": "fresh", "indicator": "none"},
             {"source_kind": "history_atom", "freshness": "fresh", "indicator": None, "active_incidents": 0}]


def answer(official, profile, *, ec=None, fs=None, vendor=None, provider=None, switch=True, mod=triage) -> dict:
    kwargs = {} if mod is pre else {"proceed_without_failure": switch}
    rec_kwargs = kwargs if mod is pre else {**kwargs, "failure_stage": fs}
    rec = mod.recommendation(ec, provider, profile, official, profile_not_read=False, provider_regional=False, now=NOW,
                             agent_vendor=vendor, **rec_kwargs)
    checks = mod.next_checks(ec, provider, "no_reports", fs, profile, official, profile_not_read=False, provider_partial=False,
                             now=NOW, agent_vendor=vendor, **kwargs)
    return {"site_host": "example.com", "provider_status": provider, "official_status_page": None, "access_profile": profile,
            "official_paths": official, "recommendation": rec, "next_checks": checks}


def row_of(result: dict) -> str:
    return response_guide.select_row(result["recommendation"], result["access_profile"], result["provider_status"],
                                     result["site_host"], result["official_paths"])[0]


def codes(result: dict) -> list[str]:
    return [c["code"] for c in result["next_checks"]]


def rec(result: dict) -> tuple:
    r = result["recommendation"]
    return r["action"], r["confidence"], r["reasons"]


def is_clear(profile, vendor) -> bool:
    if not profile:
        return False
    if profile.get("robots_status") == "absent":
        return True
    if profile.get("robots_status") != "ok":
        return False
    return not profile.get("robots") or triage.robots_verdict(profile, vendor)[0] == "clear"


def envelope(result: dict) -> dict:
    return {"site_host": "example.com", "generated_at": NOW, "synthetic": False, "window_hours": 24, "data_horizon_hours": 168,
            "current_site_status": "unknown", "evidence_level": "no_reports", "summary_code": "X", "summary": "Y", "failures": 0,
            "successes": 0, "reporting_sources": 0, "observations": [], "provider_status": None, "access_profile": None,
            "official_paths": result["official_paths"], "recommendation": result["recommendation"],
            "next_checks": result["next_checks"], "failure_stage": None, "lookup_activity": None, "official_status_page": None,
            "provider_discovery_attempted_at": None, "provider_source_state": "not_verified_by_current_methods"}


# --- recommendation ---------------------------------------------------------------------------------------------------

def test_t1_t2_no_failure_and_clear_robots_proceeds_even_with_an_official_path():
    result = answer(OFFICIAL, ALLOWS)
    assert rec(result) == ("proceed", "low", ["robots_allows_agents", "official_path_available"])
    assert row_of(result) == "P1"
    out = guide(result)
    assert out["summary_line"] == PROCEED_OFFICIAL_ROWS["P1"] and out["next_action"]["url"] is None
    assert "customer_url" not in out["next_action"]
    vendor = answer(OFFICIAL, ALLOWS, vendor="anthropic")
    assert rec(vendor) == rec(result) and vendor["recommendation"]["agent_vendor"] == "anthropic"
    assert row_of(vendor) == "P1-V"


def test_t3_t4_t5_a_failure_signal_keeps_the_old_answer():
    for ec, fs in ((None, "unknown"), ("site_access", None), ("service_error", None)):
        result = answer(OFFICIAL, ALLOWS, ec=ec, fs=fs)
        assert rec(result) == ("use_official_path", "medium", ["official_path_available"]), (ec, fs)
        assert row_of(result) == "O7"


def test_t6_a_block_naming_other_agents_proceeds_with_the_official_tail():
    result = answer(OFFICIAL, OTHER_BLOCKED)
    assert rec(result) == ("proceed", "low", ["robots_blocks_not_your_agent", "official_path_available"])
    assert guide(result)["summary_line"] == PROCEED_OFFICIAL_ROWS["P2"]


def test_t7_to_t10_a_missing_robots_txt_is_clear():
    result = answer(None, ABSENT)
    assert rec(result) == ("proceed", "low", ["robots_allows_agents"]) and row_of(result) == "P3"
    by_code = {c["code"]: c["text"] for c in result["next_checks"]}
    assert by_code["robots_allows_agents"] == ROBOTS_ABSENT_CHECK and "robots_unknown" not in by_code
    both = answer(OFFICIAL, ABSENT)
    assert rec(both) == ("proceed", "low", ["robots_allows_agents", "official_path_available"]) and row_of(both) == "P3"
    assert guide(both)["summary_line"] == PROCEED_OFFICIAL_ROWS["P3"]
    assert rec(answer(None, ABSENT, ec="site_access")) == ("proceed", "low", ["robots_allows_agents"])
    assert rec(answer(OFFICIAL, ABSENT, ec="site_access")) == ("use_official_path", "medium", ["official_path_available"])


@pytest.mark.parametrize("profile", UNREADABLE, ids=lambda p: p["robots_status"])
def test_t11_t12_an_unreadable_robots_txt_stays_unknown(profile):
    result = answer(None, profile)
    assert rec(result) == ("unknown", "low", ["robots_unknown"])
    assert "permissive" not in guide(result)["summary_line"]
    assert rec(answer(OFFICIAL, profile)) == ("use_official_path", "medium", ["official_path_available"])


def test_t13_rows_that_are_not_clear_match_the_code_before_d63():
    checked = 0
    for profile, official, vendor, ec in itertools.product(PROFILES, (None, OFFICIAL), (None, "anthropic", "openai"), ERROR_CLASSES):
        if is_clear(profile, vendor):
            continue
        new = answer(official, profile, ec=ec, vendor=vendor)
        old = answer(official, profile, ec=ec, vendor=vendor, mod=pre)
        assert new["recommendation"] == old["recommendation"], (profile, official, vendor, ec)
        checked += 1
    assert checked > 100


def test_t14_a_handoff_answer_names_no_robots_check_and_keeps_its_action():
    for ec, profile in itertools.product(("login_auth", "human_verification"), PROFILES):
        new, old = answer(OFFICIAL, profile, ec=ec), answer(OFFICIAL, profile, ec=ec, mod=pre)
        assert new["recommendation"]["action"] == old["recommendation"]["action"], (ec, profile)
        assert not [c for c in codes(new) if c.startswith("robots_")], (ec, profile)
    present = {**ALLOWS, "llms_txt_status": "present"}
    assert "llms_txt_present" in codes(answer(OFFICIAL, present, ec="login_auth"))


def test_t15_t16_t17_locating_the_failure_stage_needs_a_failure_signal():
    for profile in PROFILES:
        assert "locate_failure_stage" not in codes(answer(OFFICIAL, profile)), profile
    assert codes(answer(OFFICIAL, ALLOWS, ec="site_access"))[0] == "locate_failure_stage"
    assert "locate_failure_stage" in codes(answer(OFFICIAL, ALLOWS, fs="unknown"))


def test_t18_t19_t20_new_sentences_keep_their_codes():
    big = {**ALLOWS, "llms_txt_status": "present", "llms_detail": "too_large"}
    checks = {c["code"]: c["text"] for c in answer(None, big)["next_checks"]}
    assert checks["llms_txt_present"] == LLMS_TOO_LARGE_CHECK and "Read it first" not in LLMS_TOO_LARGE_CHECK
    quiet = {"source_kind": "history_atom", "freshness": "fresh", "indicator": None, "active_incidents": 0}
    with_atom, without = answer(None, ALLOWS, provider=quiet), answer(None, ALLOWS, provider=None)
    texts = {c["code"]: c["text"] for c in with_atom["next_checks"]}
    assert texts["provider_unknown"] == HISTORY_QUIET_CHECK
    assert with_atom["recommendation"] == without["recommendation"]
    stale = {**quiet, "freshness": "stale"}
    stale_texts = {c["code"]: c["text"] for c in answer(None, ALLOWS, provider=stale)["next_checks"]}
    assert stale_texts["provider_unknown"] == CHECKS["provider_unknown"]


# --- summary rows -----------------------------------------------------------------------------------------------------

def test_t21_t22_t23_the_listed_api_row_follows_its_sign_up_option():
    n, k, plain = (answer(o, ALLOWS, ec="site_access") for o in (OFFICIAL_N, OFFICIAL_K, OFFICIAL))
    assert (row_of(n), row_of(k), row_of(plain)) == ("O7-N", "O7-K", "O7")
    assert guide(n)["summary_line"] == ROWS["O7-N"][1] and guide(n)["next_action"]["url"] == OFFICIAL["api_docs"]
    both = answer({**OFFICIAL_N, "consumer": CONSUMER}, ALLOWS, ec="site_access")
    out = guide(both)
    assert out["summary_line"] == CONSUMER_ROWS["O7-N"] and out["summary_line"].startswith("For a task in the user's own account")
    assert out["next_action"]["url"] == out["next_action"]["customer_url"] == HELP
    assert "developer API is also listed" in out["summary_line"]


def test_t24_t34_proceed_carries_the_customer_page_and_the_mcp_text_does_not_repeat_it():
    result = answer(WITH_CONSUMER, ALLOWS)
    out = guide(result)
    assert out["next_action"]["do"] == "proceed" and out["next_action"]["url"] == out["next_action"]["customer_url"] == HELP
    assert out["summary_line"] == PROCEED_CONSUMER_ROWS["P1"] and "developer API" not in out["summary_line"]
    response = CheckResponse.model_validate({**envelope(result), **out})
    lines = mcp_adapter.check_result(response).content[0].text.splitlines()
    assert lines == [out["summary_line"], "URL: " + HELP]
    only = guide(answer(CONSUMER_ONLY, ABSENT))
    assert only["summary_line"] == PROCEED_CONSUMER_ROWS["P3"]
    assert only["next_action"]["url"] == only["next_action"]["customer_url"] == HELP


def test_t35_blocked_rows_keep_the_developer_path_and_hand_the_customer_page_over():
    for profile in (BLOCKS, status("refused")):
        out = guide(answer(WITH_CONSUMER, profile))
        assert out["next_action"]["url"] == OFFICIAL["api_docs"]
        assert out["next_action"]["customer_url"] == HELP and out["summary_line"].endswith(response_guide.CONSUMER_HAND_TAIL)


def test_t36_an_unsafe_customer_page_is_dropped(caplog):
    bad = {**WITH_CONSUMER, "consumer": {**CONSUMER, "paths": [{**CONSUMER["paths"][0], "url": "http://127.0.0.1/help"}]}}
    with caplog.at_level(logging.WARNING):
        out = guide(answer(bad, ALLOWS))
    assert "customer_url" not in out["next_action"] and out["summary_line"] == PROCEED_OFFICIAL_ROWS["P1"]
    assert "response_guide_url_dropped" in caplog.text


def test_t37_no_proceed_answer_loses_its_customer_page():
    seen = 0
    for profile, ec, vendor, provider in itertools.product(PROFILES, ERROR_CLASSES, (None, "anthropic"), PROVIDERS):
        out = guide(answer(WITH_CONSUMER, profile, ec=ec, vendor=vendor, provider=provider))
        if out["next_action"]["do"] in ("proceed", "use_official_path"):
            assert out["next_action"].get("customer_url") == HELP, (profile, ec, vendor)
            seen += 1
    assert seen > 100


def test_t25_the_switch_off_matches_the_code_before_d63():
    rng = random.Random(62)
    pool = [*PROFILES]
    for _ in range(300):
        text = "".join(f"User-agent: {rng.choice(['*', 'Claude-User', 'ChatGPT-User', 'Claude-SearchBot', 'Googlebot'])}\n"
                       f"{rng.choice(['Allow', 'Disallow'])}: {rng.choice(['/', '/a', ''])}\n\n" for _ in range(rng.randint(1, 3)))
        pool.append(robots(text))
    for profile in pool:
        for official, ec, vendor, fs in itertools.product((None, OFFICIAL, WITH_CONSUMER), ERROR_CLASSES, (None, "anthropic", "openai", "google"),
                                                         (None, "unknown", "response_observed")):
            new = answer(official, profile, ec=ec, fs=fs, vendor=vendor, switch=False)
            old = answer(official, profile, ec=ec, fs=fs, vendor=vendor, mod=pre)
            assert new["recommendation"] == old["recommendation"], (profile, official, ec, fs, vendor)
            assert codes(new) == codes(old), (profile, official, ec, fs, vendor)


def test_t27_t27b_every_sentence_fits_and_names_no_host():
    tables = [{k: v[1] for k, v in ROWS.items()}, PROCEED_OFFICIAL_ROWS, PROCEED_CONSUMER_ROWS, CONSUMER_ROWS]
    assert len(ROWS) == 34
    for table in tables:
        for row, text in table.items():
            text = text.replace("{n}", "3600")
            assert len(text) <= LINE_MAX, (row, len(text))
            assert not re.search(r"\{[^}]*\}|https?://|\b[a-z0-9-]+\.(com|org|io|net)\b", text), row


def test_t28_open_sentences_do_not_say_not_permissive():
    open_texts = (ROWS["U7"][1], ROWS["U9"][1], *PROCEED_OFFICIAL_ROWS.values(), CHECKS["robots_unknown"], ROBOTS_ABSENT_CHECK)
    for text in open_texts:
        assert "not permissive" not in text and "not as permission" not in text, text
    for row in ("U4", "U5", "O4", "O6"):
        assert "permission" in ROWS[row][1], row
    for code in ("robots_wildcard_disallow", "robots_names_vendor_alias"):
        assert "permission" in CHECKS[code], code


def test_t29_both_languages_cover_the_same_codes():
    assert set(CHECKS) == set(CHECKS_KO)
    for en, ko in ((ROBOTS_ABSENT_CHECK, ROBOTS_ABSENT_CHECK_KO), (LLMS_TOO_LARGE_CHECK, LLMS_TOO_LARGE_CHECK_KO),
                   (HISTORY_QUIET_CHECK, HISTORY_QUIET_CHECK_KO)):
        assert en and ko and en != ko


def test_t31_rest_and_mcp_structured_content_are_the_same():
    result = answer(WITH_CONSUMER, ALLOWS)
    response = CheckResponse.model_validate({**envelope(result), **guide(result)})
    out = mcp_adapter.check_result(response)
    assert json.loads(out.content[-1].text) == out.structured_content


# --- no change to what an MCP client scans ------------------------------------------------------------------------------

def test_t33_the_mcp_tool_definitions_and_the_answer_schema_are_those_of_the_base():
    tools = asyncio.run(mcp_adapter.mcp.list_tools())
    blob = json.dumps({"schema": CheckResponse.model_json_schema(),
                       "tools": sorted([t.model_dump(mode="json") for t in tools], key=lambda x: x["name"])},
                      sort_keys=True, default=str)
    # Hash of the same blob taken from the tree before D63 (the three earlier changes merged).
    assert hashlib.sha256(blob.encode()).hexdigest() == "0fa71616f301ed1c2f4d644454088da7ad972199b736b2f234f638d4ebe53f9d"
