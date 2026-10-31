"""D61: the site's own customer path (official_paths.consumer). Pure: no database, no network.

Live checks of the same entries (robots.txt first, every page read, store listings linked from their source page):
python scripts/verify_official_paths.py --consumer-only
"""

from __future__ import annotations

import asyncio
import copy
import importlib.util
import itertools
import json
import re
import typing
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

from ahn import mcp_adapter, response_guide
from ahn.access_profile import parse_robots
from ahn.models import CheckResponse, ConsumerPath, ConsumerPaths, NextAction, OfficialPaths
from ahn.official_paths import (CONSUMER_KINDS, CONSUMER_MAX_AGE_DAYS, CONSUMER_MAX_PATHS, CONSUMER_PATHS, CONSUMER_STORE_HOSTS,
                                OFFICIAL_PATHS, consumer_is_current, customer_url, official_paths)
from ahn.response_guide import CONSUMER_HAND_TAIL, CONSUMER_ROWS, CONSUMER_SEE_TAIL, LINE_MAX, ROWS, guide
from ahn.triage import next_checks, recommendation

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 10, 31, 12, 0, tzinfo=timezone.utc)
TODAY = date(2026, 10, 31)


def _verifier():
    spec = importlib.util.spec_from_file_location("verify_official_paths", ROOT / "scripts" / "verify_official_paths.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


VERIFIER = _verifier()
HELP = "https://www.example.com/help"
CONSUMER = {"paths": [{"kind": "customer_help", "url": HELP, "source_url": "https://www.example.com/"},
                      {"kind": "official_app", "url": "https://apps.apple.com/us/app/example/id123456",
                       "source_url": "https://www.example.com/app"}],
            "verified_at": "2026-10-31"}
OFFICIAL = {"name": "Example", "api_docs": None, "developer_portal": "https://developer.example.com/", "note": None}
WITH_CONSUMER = {**OFFICIAL, "consumer": CONSUMER}
TAILS = (CONSUMER_SEE_TAIL, CONSUMER_HAND_TAIL)
# Rows where robots.txt or a refused read blocks pages hand the customer page to the user; the others only point to it.
HAND_ROWS = frozenset({"O2", "O3", "O4", "O5", "O6"})
PHONE = re.compile(r"(?:tel:|\+?1?[ (.-]*\d{3}[ ).-]*\d{3}[ .-]*\d{4})")


def on(host: str) -> date:
    """The day a curated entry was verified, so these tests survive re-verification."""
    return date.fromisoformat(CONSUMER_PATHS[host]["verified_at"])


def robots(text: str) -> dict:
    return {"robots_status": "ok", "robots": parse_robots(text), "llms_txt_status": "absent", "freshness": "fresh"}


def pending(retry=120) -> dict:
    return {"robots_status": "pending", "robots": None, "llms_txt_status": "pending", "freshness": "pending", "retry_after_seconds": retry}


PROFILES = [None, pending(120), pending(None),
            {"robots_status": "refused", "robots": None, "freshness": "fresh", "llms_txt_status": "absent"},
            {"robots_status": "error", "robots": None, "freshness": "stale", "llms_txt_status": "absent"},
            robots("User-agent: *\nAllow: /\n"), robots("User-agent: *\nDisallow: /\n"),
            robots("User-agent: Claude-User\nDisallow: /\n\nUser-agent: *\nAllow: /\n"),
            robots("User-agent: ChatGPT-User\nDisallow: /\n\nUser-agent: *\nAllow: /\n"),
            robots("User-agent: claude-web\nDisallow: /\n\nUser-agent: *\nAllow: /\n"),
            robots("User-agent: ChatGPT-User\nDisallow: /\n\nUser-agent: Claude-User\nDisallow: /\n\nUser-agent: *\nAllow: /\n")]
PROVIDERS = [None, {"source_name": "S", "source_url": "https://status.example.com/", "indicator": "major", "freshness": "fresh",
                    "basis": "incident", "active_incidents": 1, "max_incident_impact": "major",
                    "incident_updated_at": NOW - timedelta(hours=1), "active_maintenances": 0}]
ERROR_CLASSES = (None, "site_access", "login_auth", "human_verification", "service_error")


def answer(official, *, error_class=None, profile=None, provider=None, vendor=None, site_host="example.com") -> dict:
    rec = recommendation(error_class, provider, profile, official, profile_not_read=False, provider_regional=False, now=NOW,
                         agent_vendor=vendor)
    checks = next_checks(error_class, provider, "no_reports", None, profile, official, profile_not_read=False, provider_partial=False,
                         now=NOW, agent_vendor=vendor)
    return {"site_host": site_host, "provider_status": provider, "official_status_page": None, "access_profile": profile,
            "official_paths": official, "recommendation": rec, "next_checks": checks}


def space():
    for error_class, profile, provider, vendor in itertools.product(ERROR_CLASSES, PROFILES, PROVIDERS, (None, "anthropic", "openai")):
        yield dict(error_class=error_class, profile=profile, provider=provider, vendor=vendor)


# --- The seed table ---------------------------------------------------------------------------------------------------

def test_the_seed_covers_consumer_hosts_and_a_developer_path_must_be_verified_when_present():
    assert len(CONSUMER_PATHS) >= 10
    only = [host for host in CONSUMER_PATHS if host not in OFFICIAL_PATHS]
    assert only, "D64: consumer-only hosts exist"
    for host in CONSUMER_PATHS:
        entry = OFFICIAL_PATHS.get(host)
        if entry is not None and (entry["api_docs"] or entry["developer_portal"]):
            assert VERIFIED_DEV(entry), host  # a developer path, when listed, is a real https address
    assert {"walgreens.com", "walmart.com"} <= set(CONSUMER_PATHS)
    # Its pages answered with a captcha or 403 to the curation agent, so it is listed as left out, not curated.
    assert "amazon.com" not in CONSUMER_PATHS


def VERIFIED_DEV(entry) -> bool:
    return all(entry[k] is None or entry[k].startswith("https://") for k in ("api_docs", "developer_portal"))


@pytest.mark.parametrize("host", sorted(CONSUMER_PATHS))
def test_every_entry_passes_the_rules_and_the_model(host):
    entry = CONSUMER_PATHS[host]
    assert VERIFIER.consumer_problems(host, entry, today=on(host)) == []
    ConsumerPaths(**entry)
    kinds = [item["kind"] for item in entry["paths"]]
    assert kinds[0] == "customer_help"  # every host has its help page, listed first
    assert kinds == sorted(kinds, key=("customer_help", "entry_link", "official_app").index)


def test_no_phone_number_or_tel_link_is_copied_into_the_table():
    text = json.dumps(CONSUMER_PATHS)
    assert not PHONE.search(re.sub(r"/(?:id)?\d+", "", text)), PHONE.search(text)  # store listing identifiers are not phone numbers
    assert "tel:" not in text and "sms:" not in text


def test_store_listings_keep_only_the_identifier_and_sit_on_store_hosts():
    for host, entry in CONSUMER_PATHS.items():
        for item in entry["paths"]:
            name = item["url"].split("/")[2]
            if item["kind"] == "official_app":
                assert name in CONSUMER_STORE_HOSTS and VERIFIER.store_link_key(item["url"]) == item["url"], item
            else:
                assert VERIFIER.registrable(name) == VERIFIER.registrable(host), item
            assert VERIFIER.registrable(item["source_url"].split("/")[2]) == VERIFIER.registrable(host), item


# --- Pure rules -------------------------------------------------------------------------------------------------------

def problems(consumer, host="example.com"):
    return VERIFIER.consumer_problems(host, consumer, today=TODAY)


def changed(path_index=0, **changes):
    consumer = copy.deepcopy(CONSUMER)
    consumer["paths"][path_index].update(changes)
    return consumer


def test_a_good_entry_passes():
    assert problems(CONSUMER) == []


@pytest.mark.parametrize("consumer, fragment", [
    (changed(kind="phone"), "unknown kind"),
    (changed(url="http://www.example.com/help"), "plain https"),
    (changed(url="https://www.example.com/help page"), "plain https"),
    (changed(url="https://www.example.com/" + "a" * 200), "plain https"),
    (changed(url="https://help.other-site.com/"), "site's own domain"),
    (changed(source_url="https://search.example.net/?q=example"), "site's own domain"),
    (changed(url="https://www.example.com/login"), "sign-in page"),
    (changed(url="https://www.example.com/account/sign-in/"), "sign-in page"),
    (changed(url="https://192.168.0.1/help"), "public hostname"),
    (changed(1, url="https://apps.apple.com/us/app/example/id123456?ppid=abc"), "listing identifier"),
    (changed(1, url="https://play.google.com/store/apps/details?id=com.example&hl=en_US"), "listing identifier"),
    (changed(1, url="https://www.example.com/app"), "App Store or Google Play"),
    (changed(1, url="https://apps.example.com/example"), "App Store or Google Play"),
    (changed(1, url=HELP, kind="customer_help"), "listed twice"),
    ({**CONSUMER, "verified_at": "2026-11-30"}, "in the future"),
    ({**CONSUMER, "verified_at": "31/10/2026"}, "ISO date"),
    ({**CONSUMER, "paths": []}, "non-empty"),
    ({**CONSUMER, "paths": [dict(CONSUMER["paths"][0], url=f"{HELP}/{n}") for n in range(CONSUMER_MAX_PATHS + 1)]}, "at most"),
    ({**CONSUMER, "phone": "x"}, "exactly"),
    (changed(label="Help"), "exactly"),
])
def test_bad_entries_are_refused(consumer, fragment):
    found = problems(consumer)
    assert any(fragment in line for line in found), found


def test_the_store_key_reduces_listings_and_ignores_other_urls():
    key = VERIFIER.store_link_key
    assert key("https://apps.apple.com/us/app/x/id42?pt=1&ct=2") == "https://apps.apple.com/us/app/x/id42"
    assert key("https://itunes.apple.com/app/apple-store/id42?mt=8") == "https://apps.apple.com/app/apple-store/id42"
    assert key("https://play.google.com/store/apps/details?hl=en&id=com.x.y&gl=US") == "https://play.google.com/store/apps/details?id=com.x.y"
    for other in ("https://www.example.com/app", "https://apps.apple.com/us/app/x", "https://play.google.com/store/search?q=x",
                  "https://play.google.com/store/apps/details?hl=en"):
        assert key(other) is None


def test_store_links_are_read_from_hrefs_only():
    body = (b'<a href="https://apps.apple.com/us/app/x/id42?ppid=1">iPhone</a> <a href=\'https://play.google.com/store/apps/'
            b'details?id=com.x&amp;hl=en\'>Android</a> <p>https://apps.apple.com/us/app/other/id7</p>')
    assert VERIFIER.store_links(body, "https://www.example.com/") == {
        "https://apps.apple.com/us/app/x/id42", "https://play.google.com/store/apps/details?id=com.x"}


def test_kinds_agree_between_table_model_and_docs():
    model_kinds = typing.get_args(ConsumerPath.model_fields["kind"].annotation)
    assert tuple(model_kinds) == CONSUMER_KINDS
    item = CONSUMER["paths"][0]
    ConsumerPaths(paths=[dict(item, url=f"{HELP}/{n}") for n in range(CONSUMER_MAX_PATHS)], verified_at="2026-10-31")
    with pytest.raises(ValidationError):
        ConsumerPaths(paths=[dict(item, url=f"{HELP}/{n}") for n in range(CONSUMER_MAX_PATHS + 1)], verified_at="2026-10-31")
    assert CONSUMER_MAX_AGE_DAYS == 90


# --- Lookup: rides along only when curated and current ------------------------------------------------------------------

def test_consumer_rides_along_only_for_curated_and_current_entries():
    found = official_paths("walgreens.com", today=on("walgreens.com"))
    assert found["consumer"] == CONSUMER_PATHS["walgreens.com"] and found["consumer"] is not CONSUMER_PATHS["walgreens.com"]
    assert official_paths("www.walgreens.com", today=on("walgreens.com"))["consumer"] == CONSUMER_PATHS["walgreens.com"]
    assert "consumer" not in official_paths("github.com", today=TODAY)  # a developer host without a consumer entry: unchanged
    assert official_paths("not-curated.example") is None
    stale = on("walgreens.com") + timedelta(days=CONSUMER_MAX_AGE_DAYS + 1)
    assert "consumer" not in official_paths("walgreens.com", today=stale)
    assert official_paths("walgreens.com", today=stale)["developer_portal"] == OFFICIAL_PATHS["walgreens.com"]["developer_portal"]
    assert not consumer_is_current({"verified_at": "2026-11-01"}, TODAY)  # a future date is not current either


CONSUMER_ONLY = {"name": "example.com", "api_docs": None, "developer_portal": None, "note": None, "consumer": CONSUMER}
NO_DEV = {"name": "example.com", "api_docs": None, "developer_portal": None, "note": None}


def test_a_consumer_only_host_answers_with_the_consumer_block_alone():
    host = next(h for h in sorted(CONSUMER_PATHS) if h not in OFFICIAL_PATHS)
    found = official_paths(host, today=on(host))
    assert found["name"] == host and found["api_docs"] is None and found["developer_portal"] is None and found["note"] is None
    assert found["consumer"] == CONSUMER_PATHS[host] and found["consumer"] is not CONSUMER_PATHS[host]
    assert official_paths("www." + host, today=on(host))["consumer"] == CONSUMER_PATHS[host]
    stale = on(host) + timedelta(days=CONSUMER_MAX_AGE_DAYS + 1)
    assert official_paths(host, today=stale) is None  # nothing curated and current is left to say


def test_the_response_model_accepts_a_consumer_only_answer_without_developer_fields():
    model = OfficialPaths(**{"name": "example.com", "consumer": CONSUMER})
    dumped = json.loads(model.model_dump_json())
    assert dumped["api_docs"] is None and dumped["developer_portal"] is None and dumped["consumer"] == CONSUMER
    assert "access" not in dumped


def test_a_consumer_only_host_never_becomes_use_official_path():
    guides = set()
    for case in space():
        with_only = answer(CONSUMER_ONLY, **case)
        bare = answer(NO_DEV, **case)
        assert with_only["recommendation"]["action"] != "use_official_path", case
        assert with_only["recommendation"] == bare["recommendation"] and with_only["next_checks"] == bare["next_checks"], case
        result, plain = guide(with_only), guide(bare)
        assert "customer_url" not in result["next_action"], case
        assert result == plain, case  # same row and tail as a host with no official path at all: no developer tail
        guides.add(result["summary_line"])
    assert not any(line.endswith(tail) for line in guides for tail in TAILS)


def test_customer_url_prefers_help_then_entry_link_then_app():
    app = {"kind": "official_app", "url": "https://apps.apple.com/us/app/x/id1", "source_url": "https://www.example.com/"}
    entry = {"kind": "entry_link", "url": "https://www.example.com/start", "source_url": "https://www.example.com/"}
    helpful = {"kind": "customer_help", "url": HELP, "source_url": "https://www.example.com/"}
    assert customer_url({"consumer": {"paths": [app, entry, helpful]}}) == HELP
    assert customer_url({"consumer": {"paths": [app, entry]}}) == entry["url"]
    assert customer_url({"consumer": {"paths": [app]}}) == app["url"]
    assert customer_url(OFFICIAL) is None and customer_url(None) is None


# --- The verdict does not change; the guide names both paths ---------------------------------------------------------

def test_recommendation_and_next_checks_ignore_consumer_over_the_whole_space():
    for case in space():
        plain, with_consumer = answer(OFFICIAL, **case), answer(WITH_CONSUMER, **case)
        assert plain["recommendation"] == with_consumer["recommendation"], case
        assert plain["next_checks"] == with_consumer["next_checks"], case


def test_only_use_official_path_answers_gain_the_customer_branch_and_do_and_url_stay():
    seen_rows = set()
    for case in space():
        before, after = guide(answer(OFFICIAL, **case)), guide(answer(WITH_CONSUMER, **case))
        assert {k: v for k, v in after["next_action"].items() if k != "customer_url"} == before["next_action"], case
        if before["next_action"]["do"] == "use_official_path":
            row, _ = response_guide.select_row(answer(OFFICIAL, **case)["recommendation"], case["profile"], case["provider"], "example.com")
            seen_rows.add(row)
            assert after["next_action"]["customer_url"] == HELP
            n = after["next_action"]["retry_after_seconds"]
            assert after["summary_line"] == CONSUMER_ROWS[row].replace("{n}", str(n if n is not None else 0))
            tail = CONSUMER_HAND_TAIL if row in HAND_ROWS else CONSUMER_SEE_TAIL
            assert after["summary_line"].endswith(tail) and len(after["summary_line"]) <= LINE_MAX
        else:
            assert after == before, case
    assert {"O1", "O2", "O3", "O5", "O6", "O7"} <= seen_rows


def test_without_a_consumer_entry_the_guide_is_the_table_as_before():
    for case in space():
        result = guide(answer(OFFICIAL, **case))
        assert "customer_url" not in result["next_action"]
        do, sentence = ROWS[response_guide.select_row(answer(OFFICIAL, **case)["recommendation"], case["profile"], case["provider"],
                                                       "example.com")[0]]
        assert re.fullmatch(re.escape(sentence).replace(re.escape("{n}"), r"[0-9]{1,4}"), result["summary_line"])
        assert not result["summary_line"].endswith(TAILS)


@pytest.mark.parametrize("n", [0, 5, 120, 3600])
def test_every_consumer_sentence_is_short_printable_and_names_both_paths(n):
    assert set(CONSUMER_ROWS) == {row for row, (do, _s) in ROWS.items() if do == "use_official_path"}
    for row, sentence in CONSUMER_ROWS.items():
        text = sentence.replace("{n}", str(n))
        assert len(text) <= LINE_MAX and re.fullmatch(r"[\x20-\x7E]+", text), (row, len(text))
        assert "official path" in text.lower() or "developer" in text  # the developer path is still named
        assert text.endswith(TAILS) and "next_action.customer_url" in text
        assert "may not reach" in text  # never asserts that the API cannot reach the account (some use OAuth)
        for absolute in ("no substitute", "not a substitute", "cannot", "can't", "is not a way"):
            assert absolute not in text.lower(), (row, absolute)
        assert ("{n}" in sentence) == ("{n}" in ROWS[row][1])  # the same retry placeholder as the plain row
        for banned in ("bypass", "solve", "user agent string", "proxy", "password"):
            assert banned not in text.lower()


def test_each_consumer_row_is_pinned_and_blocked_rows_hand_off():
    see = " The API may not reach the user's own account; the site's page is next_action.customer_url."
    hand = " The API may not reach the user's own account; hand the user next_action.customer_url."
    assert (CONSUMER_SEE_TAIL, CONSUMER_HAND_TAIL) == (see, hand)
    assert CONSUMER_ROWS == {
        "O1": "Official path listed; use it instead of page automation. robots.txt is pending: look up once more in {n} s." + see,
        "O2": "The site's robots.txt blocks agent user agents at the root. Stop retrying pages; use the listed official path." + hand,
        "O3": "robots.txt blocks some agent tokens (next_action.stop_if_you_are). Use the listed official path, not pages." + hand,
        "O4": "robots.txt blocks your vendor's agent under another name; not permission. Use the listed official path." + hand,
        "O5": "The site refused even a plain robots.txt read; expect pages to refuse too. Use the listed official path." + hand,
        "O6": "robots.txt disallows the root for agents it does not name; not permission. Use the listed official path." + hand,
        "O7": "An official developer API or portal is listed; prefer it over page automation." + see,
    }
    for row, sentence in CONSUMER_ROWS.items():
        # A blocked row never sends the agent to the same site's pages; only the user is handed the customer page.
        assert (" see " not in sentence and "hand the user" in sentence) if row in HAND_ROWS else "hand the user" not in sentence
    assert "use it instead of page automation" in CONSUMER_ROWS["O1"] and "use it instead of page automation" in ROWS["O1"][1]


def test_an_unsafe_consumer_url_is_dropped_and_the_plain_row_stays(caplog):
    bad = {**OFFICIAL, "consumer": {"paths": [{"kind": "customer_help", "url": "https://www.example.com/a b",
                                               "source_url": "https://www.example.com/"}], "verified_at": "2026-10-31"}}
    result = guide(answer(bad))
    assert result["next_action"]["do"] == "use_official_path" and "customer_url" not in result["next_action"]
    assert result["summary_line"] == ROWS["O7"][1]
    assert "response_guide_url_dropped" in caplog.text


def test_the_live_wrong_answer_now_names_the_sites_own_path():
    # A lookup of a pharmacy chain that allows agents in robots.txt answered use_official_path with a partner developer
    # portal only. With the curated consumer entry the answer stays use_official_path and now names the site's help page.
    official = official_paths("walgreens.com", today=on("walgreens.com"))
    result = answer(official, error_class="site_access", profile=robots("User-agent: *\nAllow: /\n"))
    assert result["recommendation"]["action"] == "use_official_path"
    out = guide(result)
    assert out["next_action"]["url"] == OFFICIAL_PATHS["walgreens.com"]["developer_portal"]
    assert out["next_action"]["customer_url"] == CONSUMER_PATHS["walgreens.com"]["paths"][0]["url"]
    assert out["summary_line"] == CONSUMER_ROWS["O7"]


# --- Response model and MCP text --------------------------------------------------------------------------------------

def _payload(official) -> dict:
    result = answer(official, profile=robots("User-agent: *\nAllow: /\n"))
    payload = {"site_host": "example.com", "generated_at": NOW.isoformat().replace("+00:00", "Z"), "synthetic": False, "window_hours": 24,
               "data_horizon_hours": 168, "current_site_status": "unknown", "evidence_level": "no_reports", "summary_code": "X",
               "summary": "Y", "failures": 0, "successes": 0, "reporting_sources": 0, "observations": [], "provider_status": None, "official_paths": official, "access_profile": None, "recommendation": result["recommendation"],
               "next_checks": result["next_checks"], "failure_stage": None, "lookup_activity": None, "official_status_page": None,
               "provider_discovery_attempted_at": None, "provider_source_state": "not_verified_by_current_methods"}
    payload.update(guide({**payload, "access_profile": result["access_profile"]}))
    return payload


def test_the_response_model_carries_consumer_and_leaves_it_out_when_absent():
    with_it = json.loads(CheckResponse(**_payload(WITH_CONSUMER)).model_dump_json())
    assert with_it["official_paths"]["consumer"] == CONSUMER and with_it["next_action"]["customer_url"] == HELP
    without = json.loads(CheckResponse(**_payload(OFFICIAL)).model_dump_json())
    assert "consumer" not in without["official_paths"] and "customer_url" not in without["next_action"]
    with pytest.raises(ValidationError):
        OfficialPaths(**{**OFFICIAL, "consumer": {**CONSUMER, "paths": [{**CONSUMER["paths"][0], "kind": "phone"}]}})
    with pytest.raises(ValidationError):
        OfficialPaths(**{**OFFICIAL, "consumer": {"paths": [], "verified_at": "2026-10-31"}})
    with pytest.raises(ValidationError):
        NextAction(do="use_official_path", customer_url="https://www.example.com/" + "a" * 200)


def test_mcp_first_block_adds_a_customer_path_line(monkeypatch):
    payload = _payload(WITH_CONSUMER)

    async def fake(method, path, *, params=None, extra_headers=None, **_):
        return payload
    monkeypatch.setattr(mcp_adapter, "call_api", fake)
    result = asyncio.run(mcp_adapter.mcp.call_tool("check_incident", {"site_host": "example.com"}))
    assert result.content[0].text.splitlines() == [payload["summary_line"], "URL: " + OFFICIAL["developer_portal"], "Customer path: " + HELP]
    assert json.loads(result.content[-1].text) == result.structured_content
    tool = next(t for t in asyncio.run(mcp_adapter.mcp.list_tools()) if t.name == "check_incident")
    assert "consumer" not in tool.description and "customer" not in tool.description  # no tool-metadata change (no rescan)


# --- Documentation parity ---------------------------------------------------------------------------------------------

@pytest.mark.parametrize("name", ["docs/api-contract.en.md", "docs/api-contract.md", "ahn/llms.txt"])
def test_contracts_and_llms_txt_name_every_new_key(name):
    text = (ROOT / name).read_text(encoding="utf-8")
    for key in ("official_paths.consumer", "customer_url", *CONSUMER_KINDS, "source_url"):
        assert key in text, (name, key)


def test_the_contracts_state_the_tail_sentence_and_the_decision():
    for name in ("docs/api-contract.en.md", "docs/api-contract.md"):
        text = (ROOT / name).read_text(encoding="utf-8")
        assert CONSUMER_SEE_TAIL.strip() in text and CONSUMER_HAND_TAIL.strip() in text
        assert "D61" in text and "D64" in text and "--consumer-only" in text
        assert f"{len(CONSUMER_PATHS)}" in text and f"{sum(len(v['paths']) for v in CONSUMER_PATHS.values())}" in text
    decisions = (ROOT / "docs/decisions.md").read_text(encoding="utf-8")
    assert "| D61 " in decisions and "| D64 " in decisions


@pytest.mark.parametrize("name", ["docs/api-contract.en.md", "docs/api-contract.md", "docs/decisions.md", "ahn/llms.txt", "README.md",
                                  "README.ko.md", "ahn/templates/search.html", "ahn/templates/human.html"])
def test_no_page_asserts_that_a_developer_api_cannot_reach_the_users_account(name):
    # Some developer APIs reach the user's own account through OAuth (see the access bundles), so D61 never says otherwise.
    text = (ROOT / name).read_text(encoding="utf-8")
    for absolute in ("no substitute", "not a substitute", "not reachable through them", "is not a way to do",
                     "대신할 수 없", "일을 할 수 없기 때문", "대체 경로가 아니다"):
        assert absolute not in text, (name, absolute)
