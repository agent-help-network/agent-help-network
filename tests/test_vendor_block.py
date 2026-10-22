"""D48: robots.txt blocks are judged for the calling agent's vendor (agent_vendor), and without a vendor by the agreement
of every vendor's answer. No database: the end-to-end REST check is in test_public_intake.py."""

from __future__ import annotations

import asyncio
import random
from datetime import datetime
from pathlib import Path

import pytest

from ahn import mcp_adapter, triage
from ahn.access_profile import (AGENT_USER_AGENTS, AGENT_VENDOR, AGENT_VENDORS, GOVERNING_TOKENS, VENDOR_ALIASES,
                                ai_input_declared_no, parse_robots)
from ahn.lookup_signals import LookupSignal
from ahn.models import AgentFamily, AgentVendor, CheckResponse, NextCheck, Recommendation
from ahn.triage import (ACTIVE_INDICATORS, CHECKS, MAINTENANCE_CHECK, WAIT_DESPITE_COMPONENTS_ONLY, _wait, accepted_vendor,
                        host_fact_codes, incident_stale, next_checks, page_above_incidents, profile_pending,
                        recommendation, robots_verdict, vendor_state)

ROOT = Path(__file__).resolve().parents[1]
OFFICIAL = {"name": "Example", "api_docs": "https://developer.example.com/", "developer_portal": None, "note": None}
RANK = {"clear": 0, "caution": 1, "block": 2}
KINDS = dict(AGENT_USER_AGENTS)


def profile(text: str) -> dict:
    return {"robots_status": "ok", "robots": parse_robots(text), "llms_txt_status": "absent", "freshness": "fresh"}


def codes(*args, **kwargs) -> list[str]:
    return [item["code"] for item in next_checks(*args, **kwargs)]


def legacy(prof: dict) -> str:
    return robots_verdict(prof, vendor_aware=False)[0]


# --- 1. the token-to-vendor table ---------------------------------------------------------------------------------

def test_every_token_has_a_vendor_and_every_vendor_has_governing_tokens():
    assert set(AGENT_VENDOR) == {token for token, _ in AGENT_USER_AGENTS}
    for token, vendor in AGENT_VENDOR.items():
        if vendor not in AGENT_VENDORS:  # commoncrawl, bytedance, cohere: training crawlers only, never a caller
            assert KINDS[token] == "training_crawler", token
    assert [item.value for item in AgentVendor] == [*AGENT_VENDORS, "other"]
    for vendor in AGENT_VENDORS:
        own = [token for token, owner in AGENT_VENDOR.items() if owner == vendor]
        user = [token for token in own if KINDS[token] == "user_agent"]
        governing = GOVERNING_TOKENS[vendor]
        assert governing, vendor
        # The user-directed fetchers govern; the search crawler only when the vendor documents no user_agent token.
        assert list(governing) == (user or [token for token in own if KINDS[token] == "search_crawler"]), vendor
        assert all(KINDS[token] != "training_crawler" for token in governing)
    assert GOVERNING_TOKENS["microsoft"] == ("Bingbot",) and GOVERNING_TOKENS["apple"] == ("Applebot",)
    assert GOVERNING_TOKENS["exa"] == ("ExaSearchBot",) and GOVERNING_TOKENS["anthropic"] == ("Claude-User",)
    assert GOVERNING_TOKENS["google"] == ("Google-Agent", "Google-GeminiNotebook")
    # The report field agent_family (dots/muse/other) is a different thing and keeps its own values.
    assert [item.value for item in AgentFamily] == ["dots", "muse", "other"]


def test_aliases_are_not_tokens_and_leave_ad_and_enterprise_crawlers_out():
    tokens = {token.lower() for token, _ in AGENT_USER_AGENTS}
    names = [name for group in VENDOR_ALIASES.values() for name in group]
    assert set(VENDOR_ALIASES) <= set(AGENT_VENDORS) and len(names) == len(set(names))
    for name in names:
        assert name == name.lower() and name not in tokens
        # Not a prefix of a token either: an alias group must never name a documented token by prefix matching.
        assert not any(token.startswith(name) for token in tokens), name
    for crawler in ("mediapartners-google", "oai-adsbot", "amazon-kendra", "amazon-qbusiness", "google-cloudvertexbot",
                    "googleother", "facebookbot", "facebookexternalhit"):
        assert crawler not in names


# --- 2. a known vendor: every state, with and without an official path ---------------------------------------------

STATE_FILES = {
    "explicit": "User-agent: Claude-User\nDisallow: /\n\nUser-agent: *\nAllow: /\n",
    "alias": "User-agent: claude-web\nDisallow: /\n\nUser-agent: *\nAllow: /\n",
    "wildcard": "User-agent: *\nDisallow: /\n",
    "other_vendor_blocked": "User-agent: ChatGPT-User\nDisallow: /\n\nUser-agent: *\nAllow: /\n",
    "own_search_crawler_blocked": "User-agent: Claude-SearchBot\nDisallow: /\n\nUser-agent: *\nAllow: /\n",
    "allowed": "User-agent: GPTBot\nDisallow: /\n\nUser-agent: *\nAllow: /\n",
}
KNOWN_VENDOR_TABLE = {
    # case: (with official path, without official path)
    "explicit": (("use_official_path", "high", ["robots_blocks_agents", "official_path_available"]),
                 ("stop_declared_block", "medium", ["robots_blocks_agents"])),
    "alias": (("use_official_path", "medium", ["robots_names_vendor_alias", "official_path_available"]),
              ("unknown", "low", ["robots_names_vendor_alias"])),
    "wildcard": (("use_official_path", "medium", ["robots_wildcard_disallow", "official_path_available"]),
                 ("unknown", "low", ["robots_wildcard_disallow"])),
    "other_vendor_blocked": (("use_official_path", "medium", ["robots_blocks_not_your_agent", "official_path_available"]),
                             ("proceed", "low", ["robots_blocks_not_your_agent"])),
    "own_search_crawler_blocked": (("use_official_path", "medium", ["robots_blocks_not_your_agent", "official_path_available"]),
                                   ("proceed", "low", ["robots_blocks_not_your_agent"])),
    "allowed": (("use_official_path", "medium", ["official_path_available"]), ("proceed", "low", ["robots_allows_agents"])),
}


@pytest.mark.parametrize("case", list(KNOWN_VENDOR_TABLE))
@pytest.mark.parametrize("official", [True, False])
def test_known_vendor_table(case, official):
    prof = profile(STATE_FILES[case])
    action, confidence, reasons = KNOWN_VENDOR_TABLE[case][0 if official else 1]
    answer = recommendation("site_access", None, prof, OFFICIAL if official else None, agent_vendor="anthropic")
    assert answer == {"action": action, "confidence": confidence, "reasons": reasons, "agent_vendor": "anthropic"}
    Recommendation(**answer)
    robots_code = [code for code in reasons if code != "official_path_available"] or ["robots_allows_agents"]
    assert codes("site_access", None, "no_reports", None, prof, OFFICIAL if official else None,
                 agent_vendor="anthropic")[1:1 + len(robots_code)] == robots_code


def test_the_rows_above_the_robots_rows_still_win_for_a_known_vendor():
    blocked = profile(STATE_FILES["explicit"])
    assert recommendation("human_verification", None, blocked, OFFICIAL, agent_vendor="anthropic") == {
        "action": "hand_off_to_human", "confidence": "high", "reasons": ["human_handoff"], "agent_vendor": "anthropic"}
    assert recommendation("login_auth", None, blocked, None, agent_vendor="anthropic")["action"] == "hand_off_to_human"
    incident = {"freshness": "fresh", "indicator": "major"}
    assert recommendation("site_access", incident, blocked, None, agent_vendor="anthropic")["action"] == "wait_provider_incident"
    pending = {"robots_status": "pending", "freshness": "pending", "retry_after_seconds": 5}
    assert recommendation("site_access", None, pending, None, agent_vendor="anthropic")["reasons"] == ["profile_pending"]
    refused = {"robots_status": "refused", "robots": None, "freshness": "fresh"}
    assert recommendation("site_access", None, refused, None, agent_vendor="anthropic")["reasons"] == ["robots_fetch_refused"]
    assert codes("site_access", None, "no_reports", None, refused, None, agent_vendor="anthropic")[1] == "robots_fetch_refused"


def test_other_reads_only_the_wildcard_group():
    assert recommendation("site_access", None, profile(STATE_FILES["explicit"]), None, agent_vendor="other") == {
        "action": "proceed", "confidence": "low", "reasons": ["robots_blocks_not_your_agent"], "agent_vendor": "other"}
    assert recommendation("site_access", None, profile(STATE_FILES["wildcard"]), None, agent_vendor="other")["reasons"] == ["robots_wildcard_disallow"]
    # Even when every listed token is named and allowed, a * block still covers a token the table does not list.
    named = "".join(f"User-agent: {token}\n" for token, _ in AGENT_USER_AGENTS) + "Allow: /\n\nUser-agent: *\nDisallow: /\n"
    assert recommendation("site_access", None, profile(named), None, agent_vendor="other")["action"] == "unknown"
    assert recommendation("site_access", None, profile(named), None)["action"] == "proceed"


# --- 3. no vendor: the agreement of every vendor's answer ----------------------------------------------------------

def every_governing_token_blocked() -> str:
    return "".join(f"User-agent: {token}\n" for vendor in AGENT_VENDORS for token in GOVERNING_TOKENS[vendor]) + "Disallow: /\n"


def test_unknown_vendor_stops_only_when_every_vendor_is_blocked():
    everyone = profile(every_governing_token_blocked())
    assert recommendation("site_access", None, everyone, None) == {"action": "stop_declared_block", "confidence": "medium",
                                                                   "reasons": ["robots_blocks_agents"]}
    assert recommendation("site_access", None, everyone, OFFICIAL)["confidence"] == "high"
    # Same file with one vendor left out: some vendors only.
    some = profile(every_governing_token_blocked().replace("User-agent: ExaSearchBot\n", ""))
    assert recommendation("site_access", None, some, None) == {"action": "unknown", "confidence": "low", "reasons": ["robots_blocks_some_agents"]}
    assert recommendation("site_access", None, some, OFFICIAL) == {"action": "use_official_path", "confidence": "medium",
                                                                   "reasons": ["robots_blocks_some_agents", "official_path_available"]}
    assert recommendation("site_access", None, some, None, agent_vendor="exa")["action"] == "proceed"
    assert recommendation("site_access", None, some, None, agent_vendor="anthropic")["action"] == "stop_declared_block"


def test_unknown_vendor_rows():
    mixed = profile("User-agent: *\nDisallow: /\n\nUser-agent: ChatGPT-User\nDisallow: /\n")
    assert recommendation("site_access", None, mixed, None)["reasons"] == ["robots_blocks_some_agents", "robots_wildcard_disallow"]
    assert codes("site_access", None, "no_reports", None, mixed, None)[1:3] == ["robots_blocks_some_agents", "robots_wildcard_disallow"]
    # Only a search crawler (zillow.com in the sample: Amazonbot): nobody's governing token, so proceed.
    crawler = profile("User-agent: Amazonbot\nDisallow: /\n\nUser-agent: *\nAllow: /\n")
    assert recommendation("site_access", None, crawler, None) == {"action": "proceed", "confidence": "low", "reasons": ["robots_blocks_not_your_agent"]}
    assert recommendation("site_access", None, crawler, OFFICIAL) == {"action": "use_official_path", "confidence": "medium",
                                                                      "reasons": ["robots_blocks_not_your_agent", "official_path_available"]}
    assert recommendation("site_access", None, crawler, None, agent_vendor="amazon")["action"] == "proceed"
    alias = profile(STATE_FILES["alias"])
    assert recommendation("site_access", None, alias, None) == {"action": "unknown", "confidence": "low", "reasons": ["robots_names_vendor_alias"]}


@pytest.mark.parametrize("text", [
    "User-agent: *\nDisallow: /\n",                                                       # wildcard only (D28)
    "User-agent: *\nDisallow: /\n\nUser-agent: GPTBot\nDisallow: /\n",                    # plus a training block
    "User-agent: *\nAllow: /\n",                                                          # nothing blocked
    "User-agent: GPTBot\nDisallow: /\n\nUser-agent: *\nAllow: /\n",                       # training only
    every_governing_token_blocked(),                                                      # every vendor blocked
])
@pytest.mark.parametrize("official", [OFFICIAL, None])
def test_cells_that_do_not_change_without_a_vendor(text, official):
    prof = profile(text)
    for error_class in (None, "site_access", "service_error"):
        assert recommendation(error_class, None, prof, official) == recommendation(error_class, None, prof, official, vendor_aware=False)
        assert next_checks(error_class, None, "no_reports", None, prof, official)[1]["code"] == \
            next_checks(error_class, None, "no_reports", None, prof, official, vendor_aware=False)[1]["code"]


# --- 4. monotonicity, on random files ------------------------------------------------------------------------------

NAMES = [token for token, _ in AGENT_USER_AGENTS] + ["*", "*", "*", "google", "diffbot", "claude", "meta", "amzn", "mistralai",
                                                      "perplexity‑user", "claude-web", "copilot", "googleagent-mariner",
                                                      "mediapartners-google", "SomeBot", "claude-user-beta"]
ALIAS_NAMES = {name for group in VENDOR_ALIASES.values() for name in group}


def random_file(rng: random.Random) -> str:
    # Now and then every vendor's governing tokens first, so the "every vendor blocked" row is reached too.
    lines = [every_governing_token_blocked()] if rng.random() < 0.1 else []
    for _ in range(rng.randint(1, 6)):
        lines += [f"User-agent: {name}" for name in rng.sample(NAMES, rng.randint(1, 3))]
        lines += [f"{rng.choice(['Allow', 'Disallow'])}: {rng.choice(['/', '/', '', '/x', '/*'])}" for _ in range(rng.randint(0, 2))]
        lines.append("")
    return "\n".join(lines)


def test_monotonicity_on_random_files():
    rng = random.Random(20261022)
    seen = set()
    for _ in range(4000):
        text = random_file(rng)
        prof = profile(text)
        robots = prof["robots"]
        per_vendor = {vendor: robots_verdict(prof, vendor)[0] for vendor in AGENT_VENDORS}
        unknown = robots_verdict(prof)[0]
        seen.add(unknown)
        # (i) without a vendor, "proceed" only when every vendor's answer is proceed (or the official path) ...
        assert (unknown == "clear") == all(v == "clear" for v in per_vendor.values()), text
        # (ii) ... and "stop" only when every vendor's answer is stop.
        assert (unknown == "block") == all(v == "block" for v in per_vendor.values()), text
        # Never stricter than the strictest vendor answer.
        assert RANK[unknown] <= max(RANK[v] for v in per_vendor.values()), text
        # (iii) a vendor whose governing token is explicitly disallowed always gets the declared-block answer.
        rules = {rule["agent"]: rule for rule in robots["rules"]}
        for vendor in AGENT_VENDORS:
            if any(rules[t]["explicit_group"] and not rules[t]["root_allowed"] for t in GOVERNING_TOKENS[vendor]):
                assert per_vendor[vendor] == "block", (vendor, text)
                assert recommendation("site_access", None, prof, None, agent_vendor=vendor)["action"] == "stop_declared_block"
        # (iv) without alias names in the file, no answer is stricter than the rule before D48.
        if not {line.split(": ", 1)[1].lower() for line in text.splitlines() if line.startswith("User-agent: ")} & ALIAS_NAMES:
            before = RANK[legacy(prof)]
            assert RANK[unknown] <= before, text
            assert all(RANK[v] <= before for v in per_vendor.values()), text
            assert RANK[robots_verdict(prof, "other")[0]] <= before, text
    assert seen == {"clear", "caution", "block"}  # the generator reaches every row


# --- 5. profiles that cannot be read per vendor ---------------------------------------------------------------------

def test_profile_cached_before_d48_keeps_the_previous_rule():
    for text in STATE_FILES.values():
        prof = profile(text)
        del prof["robots"]["vendor_aliases_blocked"]
        for vendor in (None, *AGENT_VENDORS, "other"):
            assert robots_verdict(prof, vendor) == robots_verdict(prof, vendor_aware=False), (text, vendor)
            answer = recommendation("site_access", None, prof, None, agent_vendor=vendor)
            answer.pop("agent_vendor", None)
            assert answer == recommendation("site_access", None, prof, None, vendor_aware=False)


def test_a_missing_governing_token_is_not_read_as_allowed():
    prof = profile(STATE_FILES["other_vendor_blocked"])
    prof["robots"]["rules"] = [rule for rule in prof["robots"]["rules"] if rule["agent"] != "Google-Agent"]
    assert vendor_state(prof, "google") is None
    # google itself and an unknown vendor fall back to the previous rule (stop); a vendor whose tokens are all there does not.
    assert recommendation("site_access", None, prof, None, agent_vendor="google")["action"] == "stop_declared_block"
    assert recommendation("site_access", None, prof, None)["action"] == "stop_declared_block"
    assert recommendation("site_access", None, prof, None, agent_vendor="anthropic")["action"] == "proceed"


# --- 6. lenient parameter -----------------------------------------------------------------------------------------

def test_accepted_vendor_is_an_exact_match_and_never_raises():
    assert accepted_vendor("anthropic") == "anthropic" and accepted_vendor("other") == "other"
    for wrong in ("Anthropic", "openai ", "", "7", 7, None, True, ["openai"], {"v": "openai"}, "dots", "commoncrawl"):
        assert accepted_vendor(wrong) is None, wrong


def test_the_echo_appears_only_for_an_accepted_vendor_while_the_rule_is_on():
    prof = profile(STATE_FILES["allowed"])
    assert "agent_vendor" not in recommendation("site_access", None, prof, None)
    assert recommendation("site_access", None, prof, None, agent_vendor="meta")["agent_vendor"] == "meta"
    assert "agent_vendor" not in recommendation("site_access", None, prof, None, agent_vendor="meta", vendor_aware=False)
    dumped = Recommendation(action="proceed", confidence="low").model_dump(mode="json")
    assert "agent_vendor" not in dumped  # left out, never null


# --- 7. switch off: the answers served before D48, byte for byte ---------------------------------------------------

def test_switch_off_matches_the_previous_code_on_random_profiles():
    rng = random.Random(48)
    officials = (None, OFFICIAL, {"name": "N", "api_docs": None, "developer_portal": None, "note": "No public API"})
    extra = [{"robots_status": "refused", "robots": None, "freshness": "fresh"}, {"robots_status": "absent", "robots": None, "freshness": "fresh"},
             {"robots_status": "pending", "freshness": "pending", "retry_after_seconds": 5}, None]
    profiles = [profile(random_file(rng)) for _ in range(300)] + [profile(text) for text in STATE_FILES.values()] + extra
    for prof in profiles:
        for official in officials:
            for error_class in (None, "site_access", "service_error", "human_verification", "login_auth"):
                for vendor in (None, "anthropic", "google", "other"):
                    assert recommendation(error_class, None, prof, official, agent_vendor=vendor, vendor_aware=False) == \
                        _previous_recommendation(error_class, None, prof, official)
                    assert next_checks(error_class, None, "no_reports", None, prof, official, agent_vendor=vendor, vendor_aware=False) == \
                        _previous_next_checks(error_class, None, "no_reports", None, prof, official)


# --- 9. aliases and 10. prefix matching ------------------------------------------------------------------------------

def test_alias_names_only_their_own_vendor():
    prof = profile(STATE_FILES["alias"])
    assert prof["robots"]["vendor_aliases_blocked"] == ["claude-web"] and prof["robots"]["agents_blocked"] == []
    assert vendor_state(prof, "anthropic") == "alias"
    assert recommendation("site_access", None, prof, None, agent_vendor="openai")["reasons"] == ["robots_allows_agents"]
    # Exact names only (after lowercasing and dash folding): a longer name is not the alias, a dash variant is.
    assert parse_robots("User-agent: claude-webx\nDisallow: /\n")["vendor_aliases_blocked"] == []
    assert parse_robots("User-agent: CLAUDE‐WEB\nDisallow: /\n")["vendor_aliases_blocked"] == ["claude-web"]
    # An alias group that allows the root, or an ad crawler, says nothing.
    assert parse_robots("User-agent: claude-web\nAllow: /\n\nUser-agent: *\nDisallow: /\n")["vendor_aliases_blocked"] == []
    ads = profile("User-agent: mediapartners-google\nDisallow: /\n\nUser-agent: *\nAllow: /\n")
    assert ads["robots"]["vendor_aliases_blocked"] == []
    assert recommendation("site_access", None, ads, None, agent_vendor="google")["action"] == "proceed"
    # An explicit block of the vendor's own token wins over its alias.
    both = profile("User-agent: claude-web\nUser-agent: Claude-User\nDisallow: /\n")
    assert recommendation("site_access", None, both, None, agent_vendor="anthropic")["reasons"] == ["robots_blocks_agents"]


def test_prefix_matching_still_names_a_vendor_family():
    diffbot = profile("User-agent: Diffbot\nDisallow: /\n\nUser-agent: *\nAllow: /\n")
    assert vendor_state(diffbot, "diffbot") == "explicit"
    assert recommendation("site_access", None, diffbot, None, agent_vendor="diffbot")["action"] == "stop_declared_block"
    google = profile("User-agent: Google\nDisallow: /\n\nUser-agent: *\nAllow: /\n")
    assert vendor_state(google, "google") == "explicit"
    assert recommendation("site_access", None, google, None)["reasons"] == ["robots_blocks_some_agents"]
    assert recommendation("site_access", None, google, None, agent_vendor="openai")["reasons"] == ["robots_blocks_not_your_agent"]


def test_unicode_dash_now_names_the_vendor():
    prof = profile("User-agent: perplexity‑user\nDisallow: /\n\nUser-agent: *\nAllow: /\n")
    assert recommendation("site_access", None, prof, None, agent_vendor="perplexity")["action"] == "stop_declared_block"


# --- 11. MCP tool -------------------------------------------------------------------------------------------------

def test_mcp_tool_advertises_the_vendor_list_and_drops_a_wrong_value(monkeypatch):
    tool = next(t for t in asyncio.run(mcp_adapter.mcp.list_tools()) if t.name == "check_incident")
    assert tool.input_schema["properties"]["agent_vendor"]["enum"] == [item.value for item in AgentVendor]
    assert "agent_vendor" not in tool.input_schema.get("required", [])
    assert "Send agent_vendor only when you know" in tool.description
    seen = {}

    async def fake(method, path, *, params=None, extra_headers=None, **_):
        seen.update(params=params)
        raise mcp_adapter.ToolError("stop")
    monkeypatch.setattr(mcp_adapter, "call_api", fake)
    for value, expected in (("anthropic", {"agent_vendor": "anthropic"}), ("Anthropic", {}), (3, {}), (None, {})):
        with pytest.raises(mcp_adapter.ToolError, match="stop"):  # reached the API: never refused
            asyncio.run(mcp_adapter.check_incident("demo-site.io", agent_vendor=value))
        assert seen["params"] == {"site_host": "demo-site.io", **expected}


# --- models, contract and D47 ---------------------------------------------------------------------------------------

def test_new_codes_have_texts_and_are_in_the_model_and_the_contracts():
    model_codes = set(NextCheck.model_fields["code"].annotation.__args__)
    new = {"robots_blocks_some_agents", "robots_blocks_not_your_agent", "robots_names_vendor_alias"}
    assert new <= model_codes and len(model_codes) == 32
    assert new <= set(triage.CHECKS) and new <= set(triage.CHECKS_KO)
    en = (ROOT / "docs" / "api-contract.en.md").read_text(encoding="utf-8")
    ko = (ROOT / "docs" / "api-contract.md").read_text(encoding="utf-8")
    for code in new | {"agent_vendor", "vendor_aliases_blocked", "AHN_VENDOR_AWARE_BLOCK=0"}:
        assert f"`{code}`" in en and f"`{code}`" in ko, code
    assert "모두 32개 코드" in ko
    for vendor in AgentVendor:
        assert f"`{vendor.value}`" in en
    decisions = (ROOT / "docs" / "decisions.md").read_text(encoding="utf-8")
    assert "| D48 (2026-10-22" in decisions


def test_agent_vendor_is_never_stored():
    assert not any("vendor" in name for name in LookupSignal.__dataclass_fields__) if hasattr(LookupSignal, "__dataclass_fields__") \
        else not any("vendor" in name for name in LookupSignal.model_fields)
    for migration in sorted((ROOT / "migrations").glob("*.sql")):
        assert "agent_vendor" not in migration.read_text(encoding="utf-8"), migration.name
    assert "agent_vendor" not in CheckResponse.model_fields  # only inside recommendation, as an echo


# --- The code before D48, frozen (ahn/triage.py at 1cad565). AHN_VENDOR_AWARE_BLOCK=0 must answer exactly like it. ----

_PREVIOUS_KINDS_FOR_BLOCK = {"user_agent", "search_crawler"}
_PREVIOUS_CHECKS = {**CHECKS,
                    "robots_blocks_agents": "This host's robots.txt explicitly disallows one or more agent user agents at the site root (see access_profile.robots.agents_blocked). Treat it as the host's declared policy: stop automated retries against pages, prefer an official path, and hand the step to the user where their participation is needed.",
                    "robots_allows_agents": "This host's robots.txt does not disallow the listed agent user agents at the site root. Declared crawler policy is not an access guarantee; CDN challenges and rate limits can still apply, so keep requests modest and honor any Crawl-delay."}


def _previous_declared_block(access_profile: dict | None) -> list[str]:
    """Agent tokens whose own robots group disallows the site root (declared policy, not a measurement)."""
    if not access_profile or access_profile.get("robots_status") != "ok" or not access_profile.get("robots"):
        return []
    return [rule["agent"] for rule in access_profile["robots"].get("rules", [])
            if rule.get("explicit_group") and not rule.get("root_allowed") and rule.get("kind") in _PREVIOUS_KINDS_FOR_BLOCK]


def _previous_wildcard_block(access_profile: dict | None) -> list[str]:
    """Agent tokens that no group names and that the wildcard group disallows at the root: not a declared agent block, not permission either."""
    if not access_profile or access_profile.get("robots_status") != "ok" or not access_profile.get("robots"):
        return []
    return [rule["agent"] for rule in access_profile["robots"].get("rules", [])
            if not rule.get("explicit_group") and not rule.get("root_allowed") and rule.get("kind") in _PREVIOUS_KINDS_FOR_BLOCK]


def _previous_recommendation(error_class: str | None, provider_status: dict | None, access_profile: dict | None,
                   official: dict | None, *, profile_not_read: bool = False, provider_regional: bool = False,
                   now: datetime | None = None) -> dict:
    """One action for the calling agent, derived only from declared and official sources.

    provider_regional: the stored scope's affected_regional (every affected component is in a region or
    point-of-presence group, status_scope.REGIONAL_GROUPS); now: the lookup time (D45)."""
    reasons: list[str] = []
    if provider_status and provider_status.get("freshness") == "fresh" and provider_status.get("indicator") in ACTIVE_INDICATORS:
        if incident_stale(provider_status, now):
            # Only incidents nobody has updated for over a week (D45): not a reason to wait by themselves. A major or
            # critical page above them still waits, for the same reason as a components_only page (D43).
            if page_above_incidents(provider_status):
                return _wait("provider_incident_stale", provider_regional)
        elif provider_status.get("basis") != "components_only":
            return _wait("provider_scope", provider_regional)
        # Nothing announced (basis components_only, D43). Only a minor or maintenance page stops being a reason to
        # wait: a major or critical page may be a monitoring integration flipping components before anyone posts an
        # incident, or a list read from a cache that lags the indicator, so the wait stays and the check explains it.
        elif provider_status.get("indicator") in WAIT_DESPITE_COMPONENTS_ONLY:
            return {"action": "wait_provider_incident", "confidence": "medium", "reasons": ["provider_components_only"]}
    if error_class == "human_verification":
        return {"action": "hand_off_to_human", "confidence": "high", "reasons": ["human_handoff"]}
    if error_class == "login_auth":
        return {"action": "hand_off_to_human", "confidence": "medium", "reasons": ["account_handoff"]}
    official_listed = bool(official and (official.get("api_docs") or official.get("developer_portal")))
    if profile_pending(access_profile):
        # No declared-policy evidence yet: decide as for an unreadable robots.txt, and say a read is under way.
        if official_listed:
            return {"action": "use_official_path", "confidence": "medium", "reasons": ["profile_pending", "official_path_available"]}
        return {"action": "unknown", "confidence": "low", "reasons": ["profile_pending"]}
    blocked = _previous_declared_block(access_profile)
    if blocked:
        reasons.append("robots_blocks_agents")
        if official and (official.get("api_docs") or official.get("developer_portal")):
            return {"action": "use_official_path", "confidence": "high", "reasons": reasons + ["official_path_available"]}
        return {"action": "stop_declared_block", "confidence": "medium", "reasons": reasons}
    if access_profile and access_profile.get("robots_status") == "refused":
        # The edge would not even serve robots.txt to an automated reader: a strong hint, not a declaration.
        if official and (official.get("api_docs") or official.get("developer_portal")):
            return {"action": "use_official_path", "confidence": "medium", "reasons": ["robots_fetch_refused", "official_path_available"]}
        return {"action": "unknown", "confidence": "medium", "reasons": ["robots_fetch_refused"]}
    if _previous_wildcard_block(access_profile):
        # "Disallow: /" for everyone it does not name: the host did not invite automation, but it named no agent either.
        if official and (official.get("api_docs") or official.get("developer_portal")):
            return {"action": "use_official_path", "confidence": "medium", "reasons": ["robots_wildcard_disallow", "official_path_available"]}
        return {"action": "unknown", "confidence": "low", "reasons": ["robots_wildcard_disallow"]}
    if official and (official.get("api_docs") or official.get("developer_portal")) and error_class in (None, "site_access", "service_error"):
        return {"action": "use_official_path", "confidence": "medium", "reasons": ["official_path_available"]}
    if access_profile and access_profile.get("robots_status") == "ok":
        return {"action": "proceed", "confidence": "low", "reasons": ["robots_allows_agents"]}
    if access_profile:
        return {"action": "unknown", "confidence": "low", "reasons": ["robots_unknown"]}
    return {"action": "unknown", "confidence": "low", "reasons": ["profile_not_read"] if profile_not_read else []}


def _previous_next_checks(error_class: str | None, provider_status: dict | None, evidence_level: str,
                failure_stage: str | None = None, access_profile: dict | None = None,
                official: dict | None = None, *, profile_not_read: bool = False,
                host_facts: dict | None = None, provider_partial: bool = False,
                now: datetime | None = None) -> list[dict[str, str]]:
    """provider_partial: status_scope.scope_partial for the provider's stored scope; now: the lookup time (D45)."""
    codes: list[str] = []
    if failure_stage and failure_stage != "unknown":
        codes.append(failure_stage)
    elif error_class not in {"human_verification", "login_auth"}:
        codes.append("locate_failure_stage")
    if error_class == "human_verification":
        codes.append("human_handoff")
    elif error_class == "login_auth":
        codes.append("account_handoff")
    if access_profile is None and profile_not_read:
        codes.append("profile_not_read")
    if profile_pending(access_profile):
        codes.append("profile_pending")
    elif access_profile:
        if access_profile.get("robots_status") == "refused":
            codes.append("robots_fetch_refused")
        elif access_profile.get("robots_status") != "ok":
            codes.append("robots_unknown")
        elif _previous_declared_block(access_profile):
            codes.append("robots_blocks_agents")
        elif _previous_wildcard_block(access_profile):
            codes.append("robots_wildcard_disallow")
        else:
            codes.append("robots_allows_agents")
        if access_profile.get("robots_status") == "ok" and (access_profile.get("robots") or {}).get("grouping_differs_by_parser"):
            # Information only: _previous_recommendation() reads the default grouping and never this list.
            codes.append("robots_grouping_differs_by_parser")
        if access_profile.get("llms_txt_status") == "present":
            codes.append("llms_txt_present")
        if ai_input_declared_no(access_profile.get("content_use")):
            # Information only: the recommendation above never reads content_use.
            codes.append("content_use_ai_input_declared_no")
    if official and (official.get("api_docs") or official.get("developer_portal")):
        codes.append("official_path_available")
    codes.extend(host_fact_codes(host_facts))
    if provider_status and provider_status.get("freshness") == "fresh":
        indicator = provider_status.get("indicator")
        if indicator in ACTIVE_INDICATORS:
            if provider_status.get("basis") == "components_only":
                codes.append("provider_components_only")
            else:
                codes.append("provider_incident_stale" if incident_stale(provider_status, now) else "provider_scope")
            if provider_partial:
                codes.append("provider_scope_partial")
        elif indicator == "none":
            codes.append("provider_clear")
        else:
            codes.append("provider_unknown")
    else:
        codes.append("provider_unknown")
    codes.append("reports_stale" if evidence_level == "stale_reports" else "reports_unknown" if evidence_level == "no_reports" else "reports_unverified")
    maintenance = bool(provider_status and provider_status.get("freshness") == "fresh" and provider_status.get("indicator") == "maintenance")
    return [{"code": code, "text": MAINTENANCE_CHECK if maintenance and code == "provider_scope" else _PREVIOUS_CHECKS[code]} for code in codes]
