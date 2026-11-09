"""D95: a 5xx or no-response lookup on a host that lists only a developer path no longer gets use_official_path, unless robots.txt blocks pages.

The decision code and the guide from before D95 are kept as tests/_pre_d95_triage.py and tests/_pre_d95_response_guide.py. The comparison
walks the grid of test_response_guide.space() (with and without a curated customer path) and asserts both directions: no pair outside the D95
condition differs, and every pair inside it changes to exactly the row the contract names. The switch off gives the old answer for every pair."""

from __future__ import annotations

import copy
import importlib.util
import itertools
import os
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from test_d85_edge_refusal import ABSENT, ALLOWS, CONSUMER, FULL, GUIDE_MODES, NOW, OFFICIAL, STAGES, checks_of, result_of, variants  # noqa: E402
from test_response_guide import block, edge_cases, pending, robots  # noqa: E402
import _d97_scope  # noqa: E402

from ahn import response_guide, settings as settings_module, triage  # noqa: E402
from ahn.response_guide import LINE_MAX, ROWS, U7_O_NO_RESPONSE, U7_O_SERVICE_ERROR, guide  # noqa: E402
from ahn.settings import Settings, parse_d95_switch  # noqa: E402
from ahn.triage import failure_observed  # noqa: E402

_spec = importlib.util.spec_from_file_location("pre_d95_triage", HERE / "_pre_d95_triage.py")
pre = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(pre)
_spec_rg = importlib.util.spec_from_file_location("pre_d95_response_guide", HERE / "_pre_d95_response_guide.py")
pre_rg = importlib.util.module_from_spec(_spec_rg)
_spec_rg.loader.exec_module(pre_rg)

PORTAL = {"name": "Example", "api_docs": None, "developer_portal": "https://developer.example.com/", "note": None}
ERROR_PROFILE = {"robots_status": "error", "robots": None, "freshness": "pending", "llms_txt_status": "absent"}
OLD_ALLOWED = {"official_path_available", "robots_blocks_not_your_agent", "profile_pending"}


@pytest.fixture(autouse=True)
def _stand_in_hosts_are_not_reserved(monkeypatch):
    monkeypatch.setattr(response_guide, "is_reserved_example", lambda host: False)
    monkeypatch.setattr(pre_rg, "is_reserved_example", lambda host: False)


def rec(mod, case, stage, pwf, *, on=None):
    kwargs = dict(profile_not_read=case.get("not_read", False), provider_regional=case.get("regional", False), now=NOW,
                  agent_vendor=case.get("vendor"), failure_stage=stage, proceed_without_failure=pwf)
    if on is not None:
        kwargs["service_error_reference_only"] = on
    return mod.recommendation(case.get("error_class"), case.get("provider_status"), case.get("access_profile"), case.get("official"), **kwargs)


def listed(official):
    return bool(official and (official.get("api_docs") or official.get("developer_portal")))


def inside(case, stage, pwf, old):
    """The D95 condition, from the inputs and the answer before D95."""
    official = case.get("official")
    return bool(case.get("error_class") == "service_error" and pwf and listed(official) and not (official.get("consumer"))
                and (case.get("access_profile") is not None or case.get("not_read"))
                and old["action"] == "use_official_path" and set(old["reasons"]) <= OLD_ALLOWED)


def expected(case, old):
    profile = case.get("access_profile")
    if profile is None:
        return {"action": "unknown", "confidence": "low", "reasons": ["profile_not_read", "official_path_available"]}
    if triage.profile_pending(profile):
        return {"action": "unknown", "confidence": "low", "reasons": ["profile_pending", "official_path_available"]}
    verdict, codes = None, []
    if profile.get("robots_status") == "ok" and profile.get("robots"):
        verdict, codes = triage.robots_verdict(profile, case.get("vendor"), vendor_aware=True)
    if triage._robots_clear(profile, verdict):
        return {"action": "proceed", "confidence": "low", "reasons": (codes or ["robots_allows_agents"]) + ["official_path_available"]}
    return {"action": "unknown", "confidence": "low", "reasons": ["robots_unknown", "official_path_available"]}


def test_the_pairs_that_change_are_exactly_the_d95_condition_in_the_whole_grid():
    recs = changed = guides = guides_changed = 0
    rows_seen = set()
    for case in itertools.chain(variants(1 if FULL else 29), edge_cases()):
        for stage, pwf in itertools.product(STAGES, (True, False)):
            old = rec(pre, case, stage, pwf)
            assert rec(triage, case, stage, pwf) == old, (case, stage, pwf)       # the default keeps the answer before D95
            assert rec(triage, case, stage, pwf, on=False) == old, (case, stage)  # switch 0: same
            new = rec(triage, case, stage, pwf, on=True)
            recs += 1
            assert checks_of(triage, case, stage, pwf) == checks_of(pre, case, stage, pwf)  # next_checks never read the recommendation
            if not inside(case, stage, pwf, old):
                assert new == old, (case, stage, pwf)
            else:
                changed += 1
                assert {k: v for k, v in new.items() if k != "agent_vendor"} == expected(case, old), (case, stage)
                assert new.get("agent_vendor") == old.get("agent_vendor")  # the vendor echo stays
                assert new["action"] != "use_official_path" and old["action"] == "use_official_path"
            for mode in GUIDE_MODES:
                if mode[2] and failure_observed(case.get("error_class"), stage):
                    continue
                for kind in (True, False):
                    old_out = pre_rg.guide(result_of(case, old), *mode, case.get("error_class"), stage, kind)
                    old_row, _ = response_guide.select_row(old, case.get("access_profile"), case.get("provider_status"),
                                                           case.get("site_host", "example.com"), case.get("official"), variants=mode[0])
                    # D97 later changed U6/U7/U9 after a service_error; D95's own rows are not among them
                    old_out = _d97_scope.adjust(old_out, old_row, mode[0], mode[2], case.get("error_class"), stage, kind, case.get("official"))
                    # the guide alone: an answer that is not a D95 one is rendered as before
                    assert guide(result_of(case, old), *mode, case.get("error_class"), stage, kind) == old_out, (case, stage, mode)
                    new_out = guide(result_of(case, new), *mode, case.get("error_class"), stage, kind)
                    guides += 1
                    if new == old:
                        assert new_out == old_out, (case, stage, mode)
                        continue
                    guides_changed += 1
                    row, _ = response_guide.select_row(new, case.get("access_profile"), case.get("provider_status"), case.get("site_host", "example.com"),
                                                       case.get("official"), variants=mode[0])
                    rows_seen.add(row)
                    assert row in ("P1", "P1-V", "P2", "P2-V", "P3", "U1", "U2", "U7-O", "U8"), row
                    assert new_out["next_action"]["do"] in ("proceed", "retry_lookup_once", "no_clear_action", "lookup_apex_host")
                    # the developer portal is never the url, and the lookup does not lose its official_paths (the guide never touches them)
                    assert new_out["next_action"]["url"] is None
                    assert "customer_url" not in new_out["next_action"]
                    assert len(new_out["summary_line"]) <= LINE_MAX
    print("D95 comparison", recs, changed, guides, guides_changed, sorted(rows_seen))
    assert recs > 20000 and changed > 40 and guides_changed > 100
    assert {"U7-O", "U1", "P1", "U8"} <= rows_seen


def test_the_switch_off_gives_the_answer_before_d95_for_every_pair():
    for case in variants(11):
        for stage in STAGES:
            for pwf in (True, False):
                assert rec(triage, case, stage, pwf, on=False) == rec(pre, case, stage, pwf)


# --- the shapes of the failing lookups ----------------------------------------------------------------------------------

def lookup(profile, official, *, stage, error_class="service_error", vendor=None, on=True):
    case = {"error_class": error_class, "access_profile": profile, "official": official, "vendor": vendor}
    result = result_of(case, rec(triage, case, stage, True, on=on))
    return result["recommendation"], guide(copy.deepcopy(result), True, False, False, True, error_class, stage, True)


def test_no_response_with_a_portal_only_gives_the_tool_check_line_and_no_portal_url():
    recommendation, out = lookup(ERROR_PROFILE, PORTAL, stage="before_target_response")
    assert recommendation == {"action": "unknown", "confidence": "low", "reasons": ["robots_unknown", "official_path_available"]}
    assert out["summary_line"] == U7_O_NO_RESPONSE and out["summary_line"].startswith("No response came")
    assert out["next_action"] == {"do": "no_clear_action", "url": None, "retry_after_seconds": None}
    before = lookup(ERROR_PROFILE, PORTAL, stage="before_target_response", on=False)
    assert before[0]["action"] == "use_official_path" and before[1]["next_action"]["url"] == "https://developer.example.com/"


def test_a_503_with_the_robots_read_failing_keeps_the_5xx_line_and_points_at_official_paths_for_api_work():
    recommendation, out = lookup(ERROR_PROFILE, PORTAL, stage="response_observed")
    assert recommendation["action"] == "unknown" and out["summary_line"] == U7_O_SERVICE_ERROR
    assert out["summary_line"].endswith("API work: official_paths.") and out["next_action"]["url"] is None
    assert lookup(ERROR_PROFILE, PORTAL, stage=None)[1]["summary_line"] == U7_O_SERVICE_ERROR


def test_a_pending_read_waits_for_one_lookup_and_the_developer_path_stays_in_the_reasons():
    recommendation, out = lookup(pending(5), OFFICIAL, stage="before_target_response")
    assert recommendation == {"action": "unknown", "confidence": "low", "reasons": ["profile_pending", "official_path_available"]}
    assert out["next_action"] == {"do": "retry_lookup_once", "url": None, "retry_after_seconds": 5}
    assert "robots.txt is being read now" in out["summary_line"]
    failed_read = lookup(pending(60, failed=True), OFFICIAL, stage="before_target_response")[1]
    assert failed_read["next_action"]["do"] == "no_clear_action" and failed_read["next_action"]["retry_after_seconds"] == 60


def not_read_lookup(site_host, stage, *, official=PORTAL, on=True):
    case = {"error_class": "service_error", "access_profile": None, "official": official, "not_read": True, "site_host": site_host}
    old = rec(pre, case, stage, True)
    new = rec(triage, case, stage, True, on=on)
    return old, new, guide(result_of(case, new), True, False, False, True, "service_error", stage, True)


def test_a_subdomain_with_no_profile_is_sent_to_its_root_domain_not_to_the_inherited_developer_path():
    for stage in ("before_target_response", "response_observed", None):
        old, new, out = not_read_lookup("app.example.com", stage)
        assert old["action"] == "use_official_path"
        assert new == {"action": "unknown", "confidence": "low", "reasons": ["profile_not_read", "official_path_available"]}
        assert out["next_action"] == {"do": "lookup_apex_host", "url": None, "retry_after_seconds": None, "lookup_host": "example.com"}
        assert "once, with the same fields you sent" in out["summary_line"]
        assert not_read_lookup("app.example.com", stage, on=False)[1] == old


def test_a_tenant_host_with_no_root_domain_gets_a_sentence_that_does_not_say_no_path_is_listed():
    for stage, line in (("before_target_response", U7_O_NO_RESPONSE), ("response_observed", U7_O_SERVICE_ERROR)):
        old, new, out = not_read_lookup("example.com", stage)
        assert new["reasons"] == ["profile_not_read", "official_path_available"] and out["next_action"] == \
            {"do": "no_clear_action", "url": None, "retry_after_seconds": None}
        assert out["summary_line"] == line
    plain = not_read_lookup("example.com", None)[2]["summary_line"]
    assert plain == U7_O_SERVICE_ERROR
    nothing = guide(result_of({"access_profile": None, "official": PORTAL}, {"action": "unknown", "confidence": "low",
                                                                                "reasons": ["profile_not_read", "official_path_available"]}))
    assert nothing["summary_line"] == ROWS["U9-O"][1] and "No official path" not in nothing["summary_line"]


def test_other_cases_without_a_profile_keep_the_earlier_answer():
    case = {"error_class": "service_error", "access_profile": None, "official": PORTAL}  # no profile and not a not-read host
    assert rec(triage, case, "before_target_response", True, on=True) == rec(pre, case, "before_target_response", True)
    curated = not_read_lookup("app.example.com", "before_target_response", official={**PORTAL, "consumer": CONSUMER})
    assert curated[0] == curated[1]


def test_a_clear_robots_txt_gives_proceed_and_the_d92_and_d87_sentences():
    for profile, row in ((ALLOWS, "P1"), (ABSENT, "P3")):
        recommendation, out = lookup(profile, OFFICIAL, stage="before_target_response")
        assert recommendation["action"] == "proceed" and recommendation["reasons"][-1] == "official_path_available"
        assert out["summary_line"] == response_guide.NO_RESPONSE_ROWS[row] and out["next_action"]["url"] is None
        assert lookup(profile, OFFICIAL, stage="response_observed")[1]["summary_line"] == response_guide.SERVICE_ERROR_ROWS[row]


def test_a_curated_customer_path_keeps_the_answer_before_d95():
    official = {**OFFICIAL, "consumer": CONSUMER}
    for profile in (ALLOWS, ERROR_PROFILE, pending(5)):
        for stage in ("before_target_response", "response_observed"):
            on = lookup(profile, official, stage=stage)
            assert on[0]["action"] == "use_official_path" and on == lookup(profile, official, stage=stage, on=False)
            assert on[1]["next_action"]["customer_url"] == "https://www.example.com/help"


@pytest.mark.parametrize("profile,vendor,reason", [
    (robots("User-agent: *\nDisallow: /\n"), None, "robots_wildcard_disallow"),                        # root closed to unnamed agents
    (robots(block("Claude-User")), "anthropic", "robots_blocks_agents"),
    (robots(block("Claude-User")), None, "robots_blocks_some_agents"),
    ({"robots_status": "refused", "robots": None, "freshness": "fresh", "llms_txt_status": "absent"}, None, "robots_fetch_refused"),
])
def test_a_robots_block_keeps_use_official_path(profile, vendor, reason):
    for stage in ("before_target_response", "response_observed"):
        recommendation, out = lookup(profile, PORTAL, stage=stage, vendor=vendor)
        old = rec(pre, {"error_class": "service_error", "access_profile": profile, "official": PORTAL, "vendor": vendor}, stage, True)
        assert recommendation == old and recommendation["action"] == "use_official_path", (reason, stage)
        assert out["next_action"]["url"] == "https://developer.example.com/"


def test_other_failures_and_no_profile_are_unchanged():
    for error_class in (None, "site_access", "login_auth", "human_verification"):
        for profile in (ERROR_PROFILE, ALLOWS, pending(5)):
            case = {"error_class": error_class, "access_profile": profile, "official": PORTAL}
            assert rec(triage, case, None, True, on=True) == rec(pre, case, None, True), (error_class,)
    case = {"error_class": "service_error", "access_profile": None, "official": PORTAL}
    assert rec(triage, case, "before_target_response", True, on=True) == rec(pre, case, "before_target_response", True)
    with_d63_off = {"error_class": "service_error", "access_profile": ALLOWS, "official": PORTAL}
    assert rec(triage, with_d63_off, None, False, on=True) == rec(pre, with_d63_off, None, False)


# --- sentences, switch, documents --------------------------------------------------------------------------------------

def test_the_new_sentences_are_short_plain_and_carry_no_host_or_url():
    for text in (ROWS["U7-O"][1], ROWS["U9-O"][1], U7_O_NO_RESPONSE, U7_O_SERVICE_ERROR):
        assert len(text) <= LINE_MAX and re.fullmatch(r"[\x20-\x7E]+", text) and "{" not in text and "http" not in text, text
        assert "official_paths" in text or "No response" in text
    assert ROWS["U7-O"][0] == ROWS["U9-O"][0] == "no_clear_action"
    assert "No official path is listed" not in ROWS["U7-O"][1] + ROWS["U9-O"][1] and "no official path is listed" not in ROWS["U9-O"][1]
    assert "5xx" not in U7_O_NO_RESPONSE and U7_O_NO_RESPONSE.startswith("No response came")
    assert {row: value for row, value in ROWS.items() if row not in ("U7-O", "U9-O", "H3", "H3-R", *response_guide.HANDOFF_ROWS)} == {k: v for k, v in pre_rg.ROWS.items() if k != "H3"}  # every other row is as before


def test_the_switch_parses_like_its_siblings(monkeypatch):
    monkeypatch.setattr(settings_module, "_D95_WARNED", False)
    assert parse_d95_switch("") and parse_d95_switch("1") and not parse_d95_switch("0")
    assert not parse_d95_switch("yes")  # an unknown value is off, never on
    monkeypatch.delenv("AHN_D95_NO_DEV_PATH_ON_FAILURE", raising=False)
    monkeypatch.setenv("AHN_DATABASE_URL", "postgresql://x/y")
    monkeypatch.setenv("AHN_KEY_PEPPER", "p" * 40)
    assert Settings.from_env().d95_no_dev_path_on_failure is True
    monkeypatch.setenv("AHN_D95_NO_DEV_PATH_ON_FAILURE", "0")
    assert Settings.from_env().d95_no_dev_path_on_failure is False


def test_documents_name_the_rule():
    for name in ("api-contract.en.md", "api-contract.md"):
        text = (ROOT / "docs" / name).read_text(encoding="utf-8")
        assert "D95" in text and "AHN_D95_NO_DEV_PATH_ON_FAILURE" in text, name
    assert "| D95 " in (ROOT / "docs" / "decisions.md").read_text(encoding="utf-8")
