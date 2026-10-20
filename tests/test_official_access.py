"""Pure checks on the curated access bundles (official_paths.access); no network.

Live checks of the same bundles (every URL fetched, quotes and MCP endpoints found on their pages):
python scripts/verify_official_paths.py --access-only
"""

from __future__ import annotations

import copy
import importlib.util
import json
from datetime import date, timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError

from ahn import triage
from ahn.models import OfficialAccess, OfficialPaths
from ahn.official_paths import (ACCESS_AUTH_METHODS, ACCESS_MAX_AGE_DAYS, ACCESS_QUOTE_MAX, OFFICIAL_ACCESS, OFFICIAL_PATHS,
                                access_is_current, official_paths)

ROOT = Path(__file__).resolve().parents[1]


def _verifier():
    spec = importlib.util.spec_from_file_location("verify_official_paths", ROOT / "scripts" / "verify_official_paths.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


VERIFIER = _verifier()
VERIFIED = date(2026, 10, 20)
GOOD = {
    "auth": [{"method": "api_key", "source_url": "https://docs.stripe.com/api/authentication"}],
    "key_signup_url": "https://docs.stripe.com/keys",
    "free_tier": {"quote": "No setup fees.", "source_url": "https://stripe.com/pricing"},
    "openapi_url": "https://raw.githubusercontent.com/stripe/openapi/master/openapi/spec3.json",
    "official_mcp": [{"endpoint": "https://mcp.stripe.com", "doc_url": "https://docs.stripe.com/mcp"}],
    "terms": {"url": "https://stripe.com/legal/ssa", "quote": "A short verbatim sentence."},
    "verified_at": "2026-10-20",
}


def on(host: str) -> date:
    """The day a curated bundle was verified, so these tests survive re-verification."""
    return date.fromisoformat(OFFICIAL_ACCESS[host]["verified_at"])


def problems(access, host="stripe.com"):
    return VERIFIER.access_problems(host, access, today=VERIFIED)


def with_field(**changes):
    access = copy.deepcopy(GOOD)
    for key, value in changes.items():
        if value is None:
            access.pop(key)
        else:
            access[key] = value
    return access


# --- The curated table -----------------------------------------------------------------------------------------------

def test_first_bundles_cover_ten_publishers_and_their_aliases():
    assert len({id(value) for value in OFFICIAL_ACCESS.values()}) >= 10
    assert {"github.com", "stripe.com", "openai.com", "youtube.com", "slack.com", "notion.so", "reddit.com", "x.com",
            "wikipedia.org", "cloudflare.com"} <= set(OFFICIAL_ACCESS)
    # Aliases share the very same object, so they can never drift apart.
    assert OFFICIAL_ACCESS["twitter.com"] is OFFICIAL_ACCESS["x.com"] and OFFICIAL_ACCESS["notion.com"] is OFFICIAL_ACCESS["notion.so"]
    assert OFFICIAL_ACCESS["chatgpt.com"] is OFFICIAL_ACCESS["openai.com"]
    assert OFFICIAL_ACCESS["wikimedia.org"] is OFFICIAL_ACCESS["wikipedia.org"]


def test_every_bundle_belongs_to_a_listed_official_path():
    for host in OFFICIAL_ACCESS:
        entry = OFFICIAL_PATHS.get(host)
        assert entry is not None and (entry["api_docs"] or entry["developer_portal"]), host


@pytest.mark.parametrize("host", sorted(OFFICIAL_ACCESS))
def test_every_bundle_passes_the_pure_rules(host):
    assert VERIFIER.access_problems(host, OFFICIAL_ACCESS[host], ) == []  # real date: no bundle may be dated in the future


@pytest.mark.parametrize("host", sorted(OFFICIAL_ACCESS))
def test_every_bundle_validates_against_the_response_model(host):
    model = OfficialAccess(**OFFICIAL_ACCESS[host])
    # Round trip: the JSON is exactly the curated bundle (absent fields stay absent, nothing turns into null).
    assert model.model_dump(mode="json") == OFFICIAL_ACCESS[host]


def test_quotes_are_short_and_never_judge_the_terms():
    for host, access in OFFICIAL_ACCESS.items():
        for field in ("free_tier", "terms"):
            if field in access:
                assert 0 < len(access[field]["quote"]) <= ACCESS_QUOTE_MAX, (host, field)
        # Owner decision D-1020-3: a link and a verbatim quote only, no verdict or summary field.
        assert not {"stance", "allowed", "summary", "automation"} & set(access.get("terms", {})), host


def test_verify_script_lists_every_url_and_every_quote():
    access = OFFICIAL_ACCESS["github.com"]
    fields = [field for field, _ in VERIFIER.access_urls(access)]
    assert fields.count("access.auth.pat") == 1 and "access.openapi_url" in fields and "access.terms" in fields
    endpoints = [text for field, _, text in VERIFIER.access_needles(access) if field == "access.official_mcp"]
    assert endpoints == ["https://api.githubcopilot.com/mcp/"]
    # MCP endpoints are matched as text on their doc page and never requested themselves.
    assert "https://api.githubcopilot.com/mcp/" not in {url for _, url in VERIFIER.access_urls(access)}
    urls = VERIFIER.table_urls({"github.com"}, paths=False)
    assert set(urls) == {url for _, url in VERIFIER.access_urls(access)}


# --- Rules ------------------------------------------------------------------------------------------------------------

def test_a_complete_bundle_passes():
    assert problems(GOOD) == []


@pytest.mark.parametrize("field", ["free_tier", "terms"])
def test_quote_cap_is_200_characters(field):
    at_cap = with_field(**{field: {**GOOD[field], "quote": "a" * ACCESS_QUOTE_MAX}})
    over = with_field(**{field: {**GOOD[field], "quote": "a" * (ACCESS_QUOTE_MAX + 1)}})
    assert problems(at_cap) == []
    assert any("over 200" in line for line in problems(over))
    with pytest.raises(ValidationError):
        OfficialAccess(**over)
    assert problems(with_field(**{field: {**GOOD[field], "quote": " padded "}}))
    assert problems(with_field(**{field: {**GOOD[field], "quote": "two\nlines"}}))


@pytest.mark.parametrize("url", [
    "https://evil.example/keys",                      # unrelated domain
    "https://stripe.com.evil.example/keys",           # the key as a subdomain of another registrable domain
    "https://docs.stripe.co.uk/keys",                 # a lookalike under a public suffix
    "http://docs.stripe.com/keys",                    # not https
    "https://docs.stripe.com:8443/keys",              # explicit port
    "https://user@docs.stripe.com/keys",              # credentials in the URL
    "https://127.0.0.1/keys",                         # address literal
    "https://github.com/someone-else/openapi",        # shared code host, undeclared organisation
    "https://raw.githubusercontent.com/someone-else/openapi/main/spec.json",
])
def test_urls_must_be_publisher_owned_https(url):
    assert problems(with_field(key_signup_url=url))
    assert problems(with_field(openapi_url=url))


def test_publisher_domains_follow_registrable_domains_and_declarations():
    owned = VERIFIER.publisher_owned
    assert owned("stripe.com", "https://docs.stripe.com/keys")
    assert owned("github.com", "https://api.githubcopilot.com/mcp/")        # declared in PUBLISHER_DOMAINS
    assert not owned("stripe.com", "https://api.githubcopilot.com/mcp/")    # declarations are per key
    assert owned("github.com", "https://github.com/github/rest-api-description")
    assert not owned("github.com", "https://github.com/stripe/openapi")     # github.com itself needs its own organisation
    assert owned("stripe.com", "https://github.com/stripe/openapi")
    # Under a two-label public suffix the registrable domain has three labels.
    assert VERIFIER.registrable("developer.bbc.co.uk") == "bbc.co.uk"
    assert not owned("bbc.co.uk", "https://evil.co.uk/x")
    assert set(VERIFIER.PUBLISHER_GITHUB_ORGS) <= set(OFFICIAL_PATHS)


@pytest.mark.parametrize("auth", [
    [{"method": "password", "source_url": "https://docs.stripe.com/api/authentication"}],
    [{"method": "api_key"}],
    [{"method": "api_key", "source_url": "https://docs.stripe.com/a"}, {"method": "api_key", "source_url": "https://docs.stripe.com/b"}],
    "api_key",
])
def test_auth_methods_come_from_the_enum_with_a_source_each(auth):
    assert problems(with_field(auth=auth))


def test_every_enum_value_is_accepted_by_rules_and_model():
    auth = [{"method": method, "source_url": "https://docs.stripe.com/auth"} for method in ACCESS_AUTH_METHODS]
    assert problems(with_field(auth=auth)) == []
    assert len(OfficialAccess(**with_field(auth=auth)).auth) == len(ACCESS_AUTH_METHODS)


@pytest.mark.parametrize("change", [
    {"verified_at": None}, {"verified_at": "2026-13-01"}, {"verified_at": "20 Oct 2026"}, {"verified_at": "2026-10-21"},
    {"sandbox_url": ""}, {"official_mcp": []}, {"terms": {"url": "https://stripe.com/legal/ssa"}},
    {"terms": {"url": "https://stripe.com/legal/ssa", "quote": "q", "stance": "permitted"}},
    {"official_mcp": [{"endpoint": "https://mcp.stripe.com", "doc_url": "https://docs.stripe.com/mcp"}] * 2},
    {"official_mcp": [{"endpoint": "https://mcp.evil.example", "doc_url": "https://docs.stripe.com/mcp"}]},
    {"pricing": "free"},
])
def test_malformed_bundles_are_rejected(change):
    access = copy.deepcopy(GOOD)
    for key, value in change.items():
        if value is None:
            access.pop(key)
        else:
            access[key] = value
    assert problems(access)


def test_a_bundle_needs_more_than_a_date():
    assert problems({"verified_at": "2026-10-20"})
    assert problems({"verified_at": "2026-10-20", "auth": None})


# --- Quote and endpoint matching on fetched pages ----------------------------------------------------------------------

def test_quotes_match_through_markup_entities_and_typography():
    body = ("<html><head><script>var hidden = 'Only in a script.';</script></head><body>"
            "<p>The three paid usage tiers are <strong>Build</strong>, <strong>Launch</strong>,\n and"
            " <strong>Grow</strong>.</p><p>User&#8217;s access &amp; more</p>"
            "<pre><code><span>\"url\"</span>: <span>\"https://mcp.example.com/mcp\"</span></code></pre></body></html>").encode()
    text = VERIFIER.page_text(body)
    assert VERIFIER.text_contains(text, "The three paid usage tiers are Build, Launch, and Grow.")
    assert VERIFIER.text_contains(text, "User's access & more")
    assert VERIFIER.text_contains(text, "https://mcp.example.com/mcp")
    assert not VERIFIER.text_contains(text, "Only in a script.")
    assert not VERIFIER.text_contains(text, "The three paid usage tiers are Build, Launch, and Gold.")
    assert not VERIFIER.text_contains(text, "   ")


def test_openapi_marker_accepts_json_and_yaml_and_rejects_html():
    assert VERIFIER.OPENAPI_MARK.search(b'{"components": {}, "openapi": "3.0.0"}')
    assert VERIFIER.OPENAPI_MARK.search(b"openapi: 3.1.0\ninfo:\n")
    assert VERIFIER.OPENAPI_MARK.search(b'{"swagger":"2.0"}')
    assert not VERIFIER.OPENAPI_MARK.search(b"<html><title>OpenAPI</title></html>")


# --- Answers ------------------------------------------------------------------------------------------------------------

def test_access_rides_along_only_for_curated_and_current_bundles():
    hit = official_paths("api.stripe.com", today=on("stripe.com"))
    assert hit["access"] == OFFICIAL_ACCESS["stripe.com"] and hit["access"] is not OFFICIAL_ACCESS["stripe.com"]
    hit["access"]["auth"].append({"method": "none"})  # a caller mutating its copy never touches the table
    assert {"method": "none"} not in OFFICIAL_ACCESS["stripe.com"]["auth"]
    assert "access" not in official_paths("census.gov", today=on("stripe.com"))
    assert official_paths("twitter.com", today=on("x.com"))["access"] == OFFICIAL_ACCESS["x.com"]


def test_stale_bundles_are_left_out_of_answers():
    last_day = on("github.com") + timedelta(days=ACCESS_MAX_AGE_DAYS)
    assert access_is_current(OFFICIAL_ACCESS["github.com"], last_day)
    assert "access" in official_paths("github.com", today=last_day)
    assert not access_is_current(OFFICIAL_ACCESS["github.com"], last_day + timedelta(days=1))
    stale = official_paths("github.com", today=last_day + timedelta(days=1))
    assert "access" not in stale and stale["api_docs"] == OFFICIAL_PATHS["github.com"]["api_docs"]
    assert not access_is_current(OFFICIAL_ACCESS["github.com"], on("github.com") - timedelta(days=1))  # a future date is not current


def test_response_omits_access_when_absent_and_keeps_old_fields():
    plain = OfficialPaths(**OFFICIAL_PATHS["census.gov"]).model_dump(mode="json")
    assert set(plain) == {"name", "api_docs", "developer_portal", "note"}  # unchanged for hosts without a bundle
    rich = OfficialPaths(**official_paths("stripe.com", today=on("stripe.com"))).model_dump(mode="json")
    assert rich["access"] == OFFICIAL_ACCESS["stripe.com"]
    sparse = OfficialPaths(name="Example", api_docs="https://docs.stripe.com/api",
                           access={"terms": GOOD["terms"], "verified_at": "2026-10-20"}).model_dump(mode="json")
    assert sparse["access"] == {"terms": GOOD["terms"], "verified_at": "2026-10-20"}  # no nulls for the fields left out
    assert "null" not in json.dumps(sparse["access"])


def test_response_model_rejects_unknown_access_fields_and_bad_values():
    with pytest.raises(ValidationError):
        OfficialAccess(**{**GOOD, "stance": "permitted"})
    with pytest.raises(ValidationError):
        OfficialAccess(**{**GOOD, "auth": [{"method": "password", "source_url": "https://docs.stripe.com/a"}]})
    with pytest.raises(ValidationError):
        OfficialAccess(**{**GOOD, "key_signup_url": "http://docs.stripe.com/keys"})


def test_recommendation_and_next_checks_ignore_access():
    entry = OFFICIAL_PATHS["github.com"]
    with_access = official_paths("github.com", today=on("github.com"))
    profile = {"robots_status": "ok", "robots": {"agents_blocked": ["ChatGPT-User"], "rules": [
        {"agent": "ChatGPT-User", "kind": "user_agent", "explicit_group": True, "root_allowed": False}]},
        "freshness": "fresh"}
    for error_class in (None, "site_access", "login_auth", "human_verification", "service_error"):
        for access_profile in (None, profile):
            assert triage.recommendation(error_class, None, access_profile, dict(entry)) == \
                triage.recommendation(error_class, None, access_profile, with_access)
            assert triage.next_checks(error_class, None, "no_reports", None, access_profile, dict(entry)) == \
                triage.next_checks(error_class, None, "no_reports", None, access_profile, with_access)
