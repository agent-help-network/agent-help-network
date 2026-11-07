"""D108: explain.build() on fixed inputs. The fixtures are made up (a made-up platform table, made-up hosts); the expected
layer orders, check orders, person rules and positions are the rules of ahn/explain.py written out."""

from __future__ import annotations

import json
import pathlib

import pytest

from _why_fixtures import PLATFORMS, TODAY, at, fake_check, ids, layers, run
from ahn import explain as E, explain_text as X
from ahn.why_models import WhyResponse

OFFICIAL = {"api_docs": "https://api.fixture-host.test/docs"}
STATUS = {"source_name": "Fixture Status", "source_url": "https://status.fixture-host.test", "indicator": "none",
          "fetched_at": "2026-11-07T00:30:00Z", "freshness": "fresh"}

CASES = {
    "T1": dict(agent_platform="claude_desktop_browser", external_browser="yes_same_account", stop_point="company_login",
               screen_message="device_not_compliant", task_context="my_account_work"),
    "T2": dict(check=fake_check("proceed", "proceed"), agent_platform="claude_desktop_browser", external_browser="yes_same_account",
               stop_point="tools_off_page_visible", screen_message="claude_org_policy_banner", task_context="my_account_work"),
    "T3": dict(agent_platform="meta_muse", external_browser="not_tried", stop_point="site_login", screen_message="none_other",
               task_context="my_account_consumer"),
    "T4": dict(check=fake_check("use_official_path", "use_official_path", official_paths=OFFICIAL), agent_platform="meta_muse",
               external_browser="not_tried", stop_point="before_visit", screen_message="none_other", task_context="my_account_consumer"),
    "T5": dict(check=fake_check("unknown", "no_clear_action", official_paths=OFFICIAL, provider_status=STATUS), external_browser="no",
               stop_point="page_blank_or_blocked", screen_message="access_denied", task_context="public_read"),
    "T6": dict(external_browser="no", stop_point="timeout_or_5xx", screen_message="page_not_loading", task_context="other"),
    "T7": dict(check=fake_check("hand_off_to_human", "hand_off_to_human", official_paths=OFFICIAL, provider_status=STATUS), external_browser="no",
               stop_point="human_verification", screen_message="human_check", task_context="my_account_consumer"),
    "T8": dict(check=fake_check("proceed", "proceed"), agent_platform="claude_desktop_browser", external_browser="yes_same_account",
               stop_point="not_sure", screen_message="claude_asks_each_action", task_context="other"),
    "T11": dict(agent_platform="other", agent_env="cloud_browser", external_browser="not_tried", stop_point="extra_verification",
                screen_message="approval_on_other_device", since_kind="today_first", task_context="my_account_consumer"),
    "T12": dict(host="mybank-example.com",
                check=fake_check("use_official_path", "use_official_path", official_paths={"api_docs": "https://api.mybank-example.com/docs"}),
                agent_platform="claude_in_chrome", external_browser="yes_same_account", stop_point="before_visit",
                screen_message="none_other", task_context="my_account_consumer"),
    "T13": dict(check=fake_check("use_official_path", "use_official_path", official_paths=OFFICIAL), external_browser="not_tried",
                stop_point="extra_verification", task_context="my_account_consumer"),
    "T14": dict(agent_platform="meta_muse", stop_point="before_visit"),
    "T15": dict(agent_platform="perplexity_comet", stop_point="before_visit"),
    "T16": dict(agent_platform="claude_code", stop_point="before_visit"),
}


def case(name, **override):
    return run(**{**CASES[name], **override})


@pytest.mark.parametrize("lang", ["en", "ko"])
@pytest.mark.parametrize("name", sorted(CASES))
def test_every_fixture_makes_a_valid_answer_in_both_languages(name, lang):
    body = case(name, lang=lang)
    parsed = WhyResponse.model_validate(body)
    assert parsed.lang == lang
    texts = "".join(r["text"] for r in body["what_happened"]["layers"]) + body["where_to_look"]["text"]
    assert "{" not in texts and "}" not in texts and "None" not in texts


def test_t1_company_sign_in_goes_to_it():
    body = case("T1")
    assert body["rules_applied"] == ["R1", "R2", "R3"]
    assert layers(body) == ["L3a", "L2", "L1d", "L1a"]
    assert ids(body)[:3] == ["CHK-IT-SSO", "CHK-MSG-MATCH", "CHK-ORG-ADMIN"] and "CHK-SIGNED-IN" in ids(body) and ids(body)[-1] == "CHK-SERVER-ACTION"
    assert body["where_to_look"]["first"] == "account"
    assert [r["code"] for r in body["person"]["reasons"]] == ["company_it_decides", "sign_in_is_the_account_holders", "server_hand_off"]
    assert body["person"]["who"] == ["user", "it_admin"]
    assert [h["template_id"] for h in body["handoff"]] == ["H-IT", "H-USER"] and body["server"]["position"] == "first"


def test_t2_platform_banner_puts_the_platform_first_and_the_server_action_after_the_checks():
    body = case("T2")
    assert body["rules_applied"] == ["R1", "R7", "R3"]
    assert layers(body)[0] == "L2" and ids(body)[:2] == ["CHK-MSG-MATCH", "CHK-ORG-ADMIN"]
    assert [r["code"] for r in body["person"]["reasons"]] == ["platform_admin_decides"] and body["person"]["who"] == ["platform_admin"]
    assert body["server"]["position"] == "after_checks" and body["handoff"][0]["template_id"] == "H-ORG-ADMIN"
    found = {r["template_id"] for r in body["what_happened"]["layers"]}
    assert found >= {"S-L2-DOC", "S-L2-NOCLASS", "S-L1A-BACKGROUND-BROWSER", "S-L1A-BACKGROUND-ACCOUNT"}


def test_t3_unknown_browser_comparison_leaves_every_row_undetermined():
    body = case("T3")
    assert body["rules_applied"] == ["R5", "R3"] and layers(body)[:2] == ["L1d", "L1a"]
    assert ids(body) == ["CHK-BROWSER-COMPARE", "CHK-SIGNED-IN", "CHK-SERVER-ACTION"]
    assert all(r.get("undetermined") for r in body["what_happened"]["layers"])
    assert body["server"]["position"] == "first"


def test_t4_site_never_opened_with_an_official_path_moves_the_server_action_after_the_checks():
    body = case("T4")
    assert body["rules_applied"] == ["R5", "R7", "R3"] and layers(body)[0] == "L2"
    assert ids(body) == ["CHK-BROWSER-COMPARE", "CHK-SERVER-ACTION", "CHK-OFFICIAL-PATH"] and body["server"]["position"] == "after_checks"
    assert "S-L4-OFFICIAL-PATH-ACCOUNT" in {r["template_id"] for r in body["what_happened"]["layers"]}
    last = body["do_now"]["checks"][-1]
    assert last["who"] == "person" and last["text"].endswith(X.TEMPLATES["S-L4-OFFICIAL-PATH-ACCOUNT"]["en"])


def test_t5_public_read_site_first_puts_the_official_path_after_the_status_page():
    body = case("T5")
    assert body["rules_applied"] == ["R4"] and layers(body) == ["L1d", "L1a"]
    assert ids(body) == ["CHK-STATUS", "CHK-OFFICIAL-PATH", "CHK-SERVER-ACTION", "CHK-SITE-HELP"]
    assert body["where_to_look"]["first"] == "unknown" and body["person"]["needed"] == "not_indicated"


def test_t6_timeout_waits_instead_of_retrying():
    body = case("T6")
    assert ids(body) == ["CHK-WAIT", "CHK-SERVER-ACTION", "CHK-SITE-HELP"] and body["handoff"][0]["template_id"] == "H-SITE"


def test_t7_human_verification_in_an_account_task_keeps_the_official_path_last():
    body = case("T7")
    assert body["rules_applied"] == ["R4", "R3"]
    assert ids(body) == ["CHK-STATUS", "CHK-SERVER-ACTION", "CHK-SITE-HELP", "CHK-OFFICIAL-PATH"]


def test_t8_asking_before_each_action_is_the_persons_decision():
    body = case("T8")
    assert body["rules_applied"] == ["R1", "R7"]
    assert ids(body)[:3] == ["CHK-MSG-MATCH", "CHK-SAFETY-CONFIRM", "CHK-ORG-ADMIN"]
    assert body["where_to_look"]["layers"]["task"] == "your_answer"
    assert [r["code"] for r in body["person"]["reasons"]] == ["approval_is_the_persons_decision"]


def test_t9_a_message_of_another_product_is_not_used():
    body = run(agent_platform="meta_muse", stop_point="not_sure", screen_message="claude_org_policy_banner")
    assert "R7" not in body["rules_applied"]
    assert "S-L2-MSG-OTHER-PLATFORM" in {r["template_id"] for r in body["what_happened"]["layers"]}
    assert "CHK-MSG-MATCH" not in ids(body)
    other = run(agent_platform="other", stop_point="not_sure", screen_message="claude_org_policy_banner")
    assert "R7" not in other["rules_applied"] and "S-L2-MSG-OTHER-PLATFORM" not in {r["template_id"] for r in other["what_happened"]["layers"]}


def test_t10_a_recorded_change_moves_the_declared_files_one_step_up_and_the_timeline_after_what_happened():
    change = {"at": at("2026-11-05"), "field": "robots_status", "from_code": "ok", "to_code": "refused", "source": "read"}
    plain = run(external_browser="no", since_kind="worked_before", since_date="2026-10-30")
    moved = run(external_browser="no", since_kind="worked_before", since_date="2026-10-30", changes=[change])
    assert "R6" in moved["rules_applied"] and layers(plain).index("L1a") > layers(plain).index("L1d")
    assert layers(moved).index("L1a") < layers(moved).index("L1d") and "L1e" in layers(moved)
    assert moved["render_order"][3] == "timeline" and plain["render_order"][3] == "timeline"
    assert moved["timeline"]["items"][0]["kind"] == "declared_policy_changed"
    assert moved["timeline"]["limits_text"] == X.TEMPLATES["S-G-WINDOW-LIMITS"]["en"]
    assert plain["timeline"]["items"] == [] and plain["timeline"]["limits_text"] == X.TEMPLATES["S-G-WINDOW-EMPTY"]["en"]
    assert run(external_browser="no")["render_order"][3] == "do_now"


def test_t11_extra_verification_on_a_cloud_browser_is_for_the_account_holder():
    body = case("T11")
    assert body["rules_applied"] == ["R5", "R3"]
    assert body["where_to_look"]["first"] == "account"
    assert body["where_to_look"]["layers"] == {"site": "background", "platform": "not_reviewed", "account": "your_answer",
                                               "agent_env": "your_answer", "task": "not_seen"}
    assert [r["code"] for r in body["person"]["reasons"]] == ["verification_on_person_device", "sign_in_is_the_account_holders", "server_hand_off"]
    assert body["person"]["who"] == ["user"] and body["person"]["needed"] == "yes"
    assert ids(body)[:2] == ["CHK-PERSON-VERIFY", "CHK-BROWSER-COMPARE"] and ids(body)[-1] == "CHK-SERVER-ACTION"
    assert {r["template_id"] for r in body["what_happened"]["layers"]} >= {"S-L3A-VERIFY", "S-L3B-CLOUD", "S-L1A-BACKGROUND-ACCOUNT"}
    text = body["handoff"][0]["text"]
    assert body["handoff"][0]["to"] == "user" and "an extra verification after the password" in text
    assert text.startswith("The agent could not finish this step on login-fixture-host.com")


def test_t12_a_platform_that_stopped_before_the_site_is_the_first_place_and_the_person_decides():
    body = case("T12")
    assert body["rules_applied"] == ["R1", "R7", "R3"]
    assert body["where_to_look"]["first"] == "platform" and body["where_to_look"]["layers"]["platform"] == "evidence"
    assert body["where_to_look"]["text"].startswith("Look first at the agent product layer:")
    assert body["where_to_look"]["text"].endswith("is the cause.")
    assert [r["code"] for r in body["person"]["reasons"]] == ["platform_refused_step"] and body["person"]["who"] == ["user", "platform_admin"]
    rows = {r["template_id"]: r for r in body["what_happened"]["layers"]}
    assert {"S-L2-CONFLICT", "S-L2-NOCLASS", "S-L2-NOLIST"} <= set(rows) and rows["S-L2-CONFLICT"]["source"]["kind"] == "vendor_doc"
    assert ids(body) == ["CHK-MSG-MATCH", "CHK-ORG-ADMIN", "CHK-PLATFORM-REMEDY", "CHK-SERVER-ACTION", "CHK-OFFICIAL-PATH"]
    assert [h["template_id"] for h in body["handoff"]] == ["H-ORG-ADMIN", "H-USER"] and body["server"]["position"] == "after_checks"
    assert X.forbidden_in(body["person"]["reasons"][0]["text"], "en") == [] and "is a financial" not in json.dumps(body, default=str).lower()


def test_t13_a_sign_in_step_keeps_the_servers_action_first_even_when_it_is_not_a_hand_off():
    body = case("T13")
    assert body["person"]["needed"] == "yes" and body["server"]["position"] == "first"
    assert "server_hand_off" not in [r["code"] for r in body["person"]["reasons"]]
    assert body["server"]["next_action"] == {"do": "use_official_path"}


def test_t14_a_platform_with_one_page_read_and_one_unread_is_not_called_undocumented():
    body = case("T14")
    rows = {r["template_id"]: r for r in body["what_happened"]["layers"]}
    assert "S-L2-NODOC" not in rows and rows["S-L2-UNREAD"]["state"] == "unread"
    assert "script" in rows["S-L2-UNREAD"]["text"] and body["where_to_look"]["layers"]["platform"] == "your_answer"
    assert "describes sites it does not open" not in body["where_to_look"]["text"]


def test_t15_a_platform_whose_pages_were_all_unreadable_is_unread_and_not_the_first_place():
    body = case("T15")
    assert body["where_to_look"]["layers"]["platform"] == "unread" and body["where_to_look"]["first"] == "unknown"
    assert [r["template_id"] for r in body["what_happened"]["layers"]].count("S-L2-UNREAD") == 2


def test_t16_a_platform_the_table_has_no_row_for_is_not_reviewed():
    body = case("T16")
    assert body["where_to_look"]["layers"]["platform"] == "not_reviewed"
    assert "Claude Code" in next(r["text"] for r in body["what_happened"]["layers"] if r["template_id"] == "S-L2-NOT-REVIEWED")


def test_other_platform_and_no_platform_have_no_platform_row():
    for platform in ("other", None):
        body = run(agent_platform=platform, stop_point="before_visit")
        assert "L2" not in layers(body) and body["where_to_look"]["layers"]["platform"] == "not_reviewed"


def test_server_is_a_copy_of_the_lookup_answer():
    cause = {"branch": "transient", "hint": "recovered", "confidence": "low", "basis": [], "line": "Fixture line.", "note": "n"}
    check = fake_check("use_official_path", "use_official_path", cause=cause, failure_stage="unknown",
                       next_checks=[{"code": "reports_unknown", "text": "t"}])
    check["next_action"]["url"] = "https://api.fixture-host.test/docs"
    server = run(check=check, stop_point="before_visit")["server"]
    for key in ("summary_line", "recommendation", "next_action", "cause", "failure_stage", "next_checks"):
        assert server[key] == check[key]
    assert set(server) == {"summary_line", "recommendation", "next_action", "cause", "failure_stage", "next_checks", "position"}
    assert "summary_line" not in run(check={k: v for k, v in fake_check().items() if k != "summary_line"})["server"]


def test_person_is_not_indicated_without_a_rule_and_never_says_no_person_is_needed():
    body = run(check=fake_check("proceed", "proceed"))
    assert body["person"] == {"needed": "not_indicated", "reasons": [], "text": X.TEMPLATES["S-P-NOT-INDICATED"]["en"]}
    assert "who" not in body["person"] and "no person" not in body["person"]["text"].lower()


def test_site_evidence_comes_only_from_a_stored_cause_a_robots_block_a_provider_incident_or_an_odd_home_check():
    base = fake_check("proceed", "proceed")
    assert run(check=base)["where_to_look"]["first"] == "unknown"
    cause = dict(base, cause={"branch": "policy_closed", "hint": "persistent_refusal", "confidence": "high", "basis": ["site_probe"],
                              "line": "Refused on every check.", "note": "n"})
    body = run(check=cause, external_browser="no")
    assert body["where_to_look"]["first"] == "site" and "S-L1-CAUSE" in {r["template_id"] for r in body["what_happened"]["layers"]}
    blocked = dict(base, access_profile={"robots_status": "ok", "fetched_at": "2026-11-06T10:00:00Z", "freshness": "fresh",
                                         "robots": {"wildcard_root_allowed": False}})
    assert run(check=blocked, external_browser="no")["where_to_look"]["first"] == "site"
    assert run(check=blocked, external_browser="no", task_context="my_account_consumer")["where_to_look"]["layers"]["site"] == "background"
    incident = dict(base, provider_status=dict(STATUS, indicator="major"))
    assert run(check=incident, external_browser="no")["where_to_look"]["first"] == "site"
    home = dict(base, ahn_check={"status": "measured", "outcome": "bot_challenge", "measured_at": "2026-11-06T09:00:00Z",
                                 "bot_defense": {"vendor": "cloudflare"}})
    row = next(r for r in run(check=home, external_browser="no")["what_happened"]["layers"] if r["template_id"] == "S-L1B-AHN-HOME")
    assert "Cloudflare" in row["text"] and row["state"] == "evidence"


def test_others_saw_lists_at_most_five_unverified_reports_and_never_claims_success():
    obs = [{"observed_at": f"2026-11-0{n}T10:00:00Z", "attempt_actor": "agent", "attempt_env": "agent_cloud_browser", "outcome": "blocked",
            "error_class": "login_auth", "task_step": "login", "synthetic": False} for n in range(1, 8)]
    obs.insert(0, dict(obs[0], synthetic=True))
    activity = {"period_days": 7, "volume_band": "5-9", "unit": "requests_not_agents"}
    saw = run(check=fake_check(observations=obs, lookup_activity=activity))["others_saw"]
    assert len(saw["reports"]) == 5 and all(r["verified"] is False for r in saw["reports"]) and saw["lookup_band"] == "5-9"
    assert "not verified by AHN" in saw["text"] and "(agent cloud browser)" in saw["reports"][0]["text"]
    empty = run()["others_saw"]
    assert empty["reports"] == [] and "does not show the site works for them" in empty["text"] and "lookup_band" not in empty
    assert "cannot tell whether other agents succeed" in empty["text"]


def test_platform_page_changes_count_only_for_the_pages_in_the_table_and_not_the_baseline():
    own = PLATFORMS[0]["docs"][0]["url"]
    events = [{"url": own, "changed_at": at("2026-11-04")}, {"url": "https://elsewhere.test/page", "changed_at": at("2026-11-04")},
              {"url": own, "changed_at": at("2026-10-01")}]
    body = run(agent_platform="claude_desktop_browser", stop_point="before_visit", events=events)
    assert [i["kind"] for i in body["timeline"]["items"]] == ["platform_doc_changed"] and body["timeline"]["items"][0]["source_url"] == own
    assert "CHK-PLATFORM-CHANGES" in ids(body)
    assert "CHK-PLATFORM-CHANGES" not in ids(run(agent_platform="claude_desktop_browser", stop_point="before_visit"))


def test_unknown_change_codes_are_left_out_never_rendered_as_stored():
    odd = {"at": at("2026-11-05"), "field": "robots_status", "from_code": "ok", "to_code": "<script>x", "source": "read"}
    other = {"at": at("2026-11-05"), "field": "made_up", "from_code": "a", "to_code": "b", "source": "read"}
    body = run(changes=[odd, other])
    assert body["timeline"]["items"] == [] and "<script>" not in json.dumps(body, default=str)
    token = {"at": at("2026-11-05"), "field": "robots_verdict:AgentHelpNetwork", "from_code": "allow", "to_code": "block", "source": "read"}
    item = run(changes=[token])["timeline"]["items"][0]
    assert "AHN's own reader" in item["text"] and "allowed to blocked" in item["text"]
    assert run(changes=[dict(token, field="robots_verdict:NotARealToken")])["timeline"]["items"] == []


def test_host_notes_and_links_carry_only_accepted_codes():
    body = run("acme.fixture-tenant.test", agent_platform="not_a_platform", stop_point="before_visit", screen_message="free text here")
    assert body["input_echo"] == {"stop_point": "before_visit"}
    assert body["links"]["self"] == "/v1/why?site_host=acme.fixture-tenant.test&stop_point=before_visit&lang=en"
    assert "free text" not in json.dumps(body, default=str) and "not_a_platform" not in json.dumps(body, default=str)


def test_the_handoff_names_the_platform_and_at_most_two_items():
    body = run(agent_platform="claude_in_chrome", stop_point="before_visit", since_kind="worked_before", since_date="2026-11-01",
               external_browser="yes_same_account")
    assert len(body["handoff"]) <= 2 and "Since 2026-11-01" in body["handoff"][0]["text"] and "Fixture Chrome" in body["handoff"][0]["text"]
    assert len(case("T11")["handoff"]) == 1 and len(case("T1")["handoff"]) == 2


def test_checks_never_exceed_six_and_never_drop_the_server_action():
    check = fake_check(official_paths=OFFICIAL, provider_status=STATUS,
                       next_action={"do": "hand_off_to_human", "customer_url": "https://help.fixture-host.test/"})
    body = run(check=check, agent_platform="claude_desktop_browser", external_browser="yes_same_account", stop_point="company_login",
               screen_message="device_not_compliant", task_context="my_account_work", since_kind="worked_before",
               events=[{"url": PLATFORMS[0]["docs"][0]["url"], "changed_at": at("2026-11-04")}])
    assert len(ids(body)) == 6 and "CHK-SERVER-ACTION" in ids(body)


def test_the_answer_is_the_same_every_time_it_is_built():
    assert case("T12") == case("T12") and case("T12", lang="ko") == case("T12", lang="ko")


def test_korean_answer_uses_korean_fixed_sentences():
    body = case("T11", lang="ko")
    assert body["basis_text"] == X.TEMPLATES["S-G-BASIS"]["ko"] and "먼저 계정" in body["where_to_look"]["text"]
    assert body["person"]["reasons"][0]["text"] == X.TEMPLATES["S-P-VERIFY-DEVICE"]["ko"]


def test_no_public_fixture_uses_a_real_personal_host():
    text = (pathlib.Path(__file__).parent / "test_explain_build.py").read_text(encoding="utf-8")
    assert "equate" + "plus" not in text and E.SINCE_MAX_DAYS == 90 and TODAY.year == 2026


# --- the home-page check never turns a sign-in step into a site problem -----------------------------------------------------
def _home(outcome, vendor=None):
    home = {"status": "measured", "outcome": outcome, "measured_at": "2026-11-07T00:10:00Z"}
    if vendor:
        home["bot_defense"] = {"vendor": vendor}
    return home


@pytest.mark.parametrize("lang", ["en", "ko"])
@pytest.mark.parametrize("outcome,vendor", [("login_wall", None), ("redirected_offsite", None), ("reachable", "cloudflare")])
def test_a_home_page_answer_does_not_move_an_extra_verification_step_to_the_site(outcome, vendor, lang):
    body = run(check=fake_check(ahn_check=_home(outcome, vendor)), stop_point="extra_verification",
               screen_message="approval_on_other_device", agent_env="cloud_browser", lang=lang)
    assert body["where_to_look"]["first"] == "account"
    home = [r for r in body["what_happened"]["layers"] if r["template_id"] == "S-L1B-AHN-HOME"]
    assert home and all(r["state"] == "background" for r in home)
    text = body["where_to_look"]["text"].lower()
    assert "home-page" not in text and "홈 페이지" not in text


def test_a_home_page_that_is_not_normal_still_counts_when_the_step_is_not_a_sign_in():
    body = run(check=fake_check(ahn_check=_home("refused")), stop_point="page_blank_or_blocked", screen_message="access_denied")
    assert body["where_to_look"]["first"] == "site"
    assert "other than a normal page" in body["where_to_look"]["text"]


def test_a_normal_home_page_with_only_a_marker_is_never_site_evidence():
    body = run(check=fake_check(ahn_check=_home("reachable", "cloudflare")), stop_point="page_blank_or_blocked", screen_message="access_denied")
    assert body["where_to_look"]["first"] != "site"
    assert all(r["state"] == "background" for r in body["what_happened"]["layers"] if r["layer"] == "L1b")


# --- a host at the length limit ----------------------------------------------------------------------------------------------
LONG_HOST = ".".join(["a" * 63] * 3 + ["a" * 57, "com"])


@pytest.mark.parametrize("lang", ["en", "ko"])
@pytest.mark.parametrize("stop", [None, "company_login", "before_visit"])
def test_a_253_character_host_with_every_code_still_makes_a_valid_answer(lang, stop):
    assert len(LONG_HOST) == 253
    body = run(host=LONG_HOST, lang=lang, agent_platform="claude_desktop_browser", agent_env="cloud_browser", external_browser="no",
               stop_point=stop, screen_message="none_other", since_kind="worked_before", since_date="2026-11-01", task_context="my_account_work",
               error_class="login_auth", failure_stage="before_target_response")
    parsed = WhyResponse.model_validate(body)
    assert parsed.handoff and any(h.to == "user" for h in parsed.handoff)
    assert len(body["links"]["self"]) <= 1000


@pytest.mark.parametrize("lang,word", [("en", "yourself"), ("ko", "직접")])
def test_a_253_character_host_keeps_the_person_line_in_the_fallback_handoff(lang, word, monkeypatch):
    # The host is reduced to its registered domain, so force the sentence with the host over the cap to reach the fallback.
    monkeypatch.setitem(X.TEMPLATES["H-USER"], lang, "x" * 400 + "{host}{stop_words}{person_line}{platform_cap}")
    body = run(host=LONG_HOST, lang=lang, agent_env="cloud_browser", external_browser="not_tried", stop_point="extra_verification",
               screen_message="approval_on_other_device", since_kind="today_first", task_context="my_account_consumer")
    assert body["person"]["needed"] == "yes"
    user = [h for h in body["handoff"] if h["to"] == "user"]
    assert user and user[0]["template_id"] == "H-USER-NOHOST" and word in user[0]["text"]


@pytest.mark.parametrize("stop", sorted(E.STOP_POINTS))
def test_korean_handoffs_never_carry_the_english_stop_phrase(stop):
    body = run(lang="ko", stop_point=stop, task_context="my_account_consumer", agent_platform="claude_in_chrome")
    texts = [h["text"] for h in body["handoff"]]
    assert texts
    for text in texts:
        assert X.STOP_WORDS[stop]["en"] not in text
    assert any(X.STOP_WORDS[stop]["ko"] in t for t in texts)
