"""D110: GET /v1/why follows the caller's stop, keeps a person check and `person` in step, names the stop from measured codes,
keeps a status check a status check, echoes every accepted input, reads an unresolved incident as one, says AHN's home-page
check changes as what AHN's request got, and keeps Korean blanks free of hard-coded particles. Fixtures are made up."""

from __future__ import annotations

import itertools
import pathlib
import re
from urllib.parse import parse_qs, urlparse

import pytest

from _why_fixtures import at, fake_check, ids, run
from ahn import explain as E, explain_text as X, lookup_signals
from ahn.why_models import WhyResponse

STATUS = {"source_name": "Fixture Status", "source_url": "https://status.fixture-host.test", "indicator": "none",
          "fetched_at": "2026-11-07T00:30:00Z", "freshness": "fresh"}


def home(outcome, vendor=None):
    data = {"status": "measured", "outcome": outcome, "measured_at": "2026-11-07T00:10:00Z"}
    if vendor:
        data["bot_defense"] = {"vendor": vendor}
    return data


def user_line(body):
    return next(h for h in body["handoff"] if h["to"] == "user")["text"]


# --- 1. L1d: an unresolved incident under an operational indicator --------------------------------------------------------
@pytest.mark.parametrize("lang", ["en", "ko"])
def test_an_unresolved_incident_is_never_read_as_no_announced_incident(lang):
    body = run(check=fake_check(provider_status=dict(STATUS, active_incidents=1)), external_browser="no", lang=lang)
    rows = {r["template_id"]: r for r in body["what_happened"]["layers"] if r["layer"] == "L1d"}
    assert "S-L1D-CLEAR" not in rows and rows["S-L1D-UNRESOLVED"]["state"] == "evidence"
    assert "no announced incident" not in str(body) and "발표된 장애가 없었" not in str(body)
    assert body["where_to_look"]["first"] == "site"
    assert X.REASON_WORDS[("site", "provider_incident")][lang] in body["where_to_look"]["text"]


@pytest.mark.parametrize("count", [0, None])
def test_no_unresolved_incident_keeps_the_clear_sentence(count):
    status = dict(STATUS, active_incidents=count) if count is not None else STATUS
    body = run(check=fake_check(provider_status=status), external_browser="no")
    assert [r["template_id"] for r in body["what_happened"]["layers"] if r["layer"] == "L1d"] == ["S-L1D-CLEAR"]


# --- 2. the four panel scenarios (made-up hosts, the shape of each case) -----------------------------------------------------
def scenario_account():  # extra verification after the password at a work sign-in; AHN's home-page check got a refusal
    return run("hr-fixture.test", check=fake_check(ahn_check=home("refused")), stop_point="extra_verification",
               task_context="my_account_work")


def scenario_platform():  # the page shows, the agent's tools do not act, no product named, a bot challenge at the home page
    return run("news-fixture.test", check=fake_check("unknown", "no_clear_action", ahn_check=home("bot_challenge", "datadome")),
               stop_point="tools_off_page_visible", task_context="public_read")


def scenario_site_access():  # a 403 at a page that worked before, sent as codes only (the links.why shape)
    return run("shop-fixture.test", check=fake_check("unknown", "no_clear_action", ahn_check=home("bot_challenge", "datadome")),
               error_class="site_access", http_status="403", since_kind="worked_before", since_date="2026-11-01")


def scenario_timeout():  # a public read that timed out; the server hands it over; AHN's own read got no response
    return run("www.air-fixture.test", check=fake_check(ahn_check=home("unreachable")), stop_point="timeout_or_5xx",
               task_context="public_read")


SCENARIOS = {"account": scenario_account, "platform": scenario_platform, "site_access": scenario_site_access, "timeout": scenario_timeout}


def test_scenario_extra_verification_looks_at_the_account_first():
    body = scenario_account()
    assert body["where_to_look"]["layers"]["site"] == "evidence"  # the home-page check alone ...
    assert body["where_to_look"]["first"] == "account"             # ... does not go ahead of the caller's stop
    assert body["person"]["needed"] == "yes" and body["person"]["who"] == ["user"]


def test_scenario_tools_off_looks_at_the_platform_first_and_the_person_can_open_the_page():
    body = scenario_platform()
    assert body["where_to_look"]["first"] == "platform" and body["where_to_look"]["layers"]["platform"] == "your_answer"
    assert X.REASON_WORDS[("platform", "tools_off_unnamed")]["en"] in body["where_to_look"]["text"]
    assert body["person"]["needed"] == "yes" and body["person"]["who"] == ["user"]
    assert body["person"]["reasons"][0]["code"] == "page_visible_tools_off"


def test_scenario_site_access_keeps_the_site_first_and_names_the_stop():
    body = scenario_site_access()
    assert body["where_to_look"]["first"] == "site"
    assert X.STOP_WORDS["page_blank_or_blocked"]["en"] in user_line(body) and "an unclear step" not in user_line(body)


def test_scenario_public_read_timeout_keeps_the_site_first_and_says_the_person_can_open_it():
    body = scenario_timeout()
    assert body["where_to_look"]["first"] == "site"
    assert body["person"]["needed"] == "yes" and body["server"]["next_action"]["do"] == "hand_off_to_human"
    reason = next(r for r in body["person"]["reasons"] if r["code"] == "server_hand_off")
    assert reason["text"] == X.TEMPLATES["S-P-SERVER-HAND-OFF-OPEN"]["en"]
    assert user_line(body).endswith(X.TEMPLATES["H-USER-OPEN"]["en"]) and "You need to do this step yourself" not in user_line(body)


def test_a_public_read_timeout_with_a_provider_incident_keeps_the_plain_hand_off_sentence():
    check = fake_check(provider_status=dict(STATUS, indicator="major"))
    body = run(check=check, stop_point="timeout_or_5xx", task_context="public_read")
    reason = next(r for r in body["person"]["reasons"] if r["code"] == "server_hand_off")
    assert reason["text"] == X.TEMPLATES["S-P-SERVER-HAND-OFF"]["en"]


@pytest.mark.parametrize("stop", ["extra_verification", "site_login", "company_login", "after_login_action_fails"])
def test_an_account_stop_goes_ahead_of_a_home_page_check_but_not_of_a_stored_cause(stop):
    plain = run(check=fake_check(ahn_check=home("bot_challenge", "cloudflare")), stop_point=stop)
    assert plain["where_to_look"]["first"] == "account"
    cause = {"branch": "policy_closed", "hint": "persistent_refusal", "confidence": "high", "basis": ["site_probe"],
             "line": "Refused on every check.", "note": "n"}
    direct = run(check=fake_check(cause=cause, ahn_check=home("bot_challenge", "cloudflare")), stop_point=stop)
    expected = "account" if stop == "company_login" else "site"  # a company sign-in already puts the account layer first (R2)
    assert direct["where_to_look"]["first"] == expected


def test_a_platform_stop_goes_ahead_of_a_home_page_check():
    body = run(check=fake_check(ahn_check=home("refused")), stop_point="before_visit")
    assert body["where_to_look"]["first"] == "platform"
    assert X.REASON_WORDS[("platform", "stopped_unnamed")]["en"] in body["where_to_look"]["text"]


# --- 3. person and do_now: one place keeps them in step --------------------------------------------------------------------
DO_VALUES = ["hand_off_to_human", "proceed", "use_official_path", "wait_for_provider", "no_clear_action", "stop_automation", "retry_lookup_once"]
ACTION_OF = {"hand_off_to_human": "hand_off_to_human", "proceed": "proceed", "use_official_path": "use_official_path",
             "wait_for_provider": "wait_provider_incident", "no_clear_action": "unknown", "stop_automation": "stop_declared_block",
             "retry_lookup_once": "unknown"}


def every_answer():
    stops = list(E.STOP_POINTS) + [None]
    for stop, do, context, browser, platform, lang in itertools.product(
            stops, DO_VALUES, ["public_read", "my_account_consumer", None], ["yes_same_account", "no", None],
            ["claude_in_chrome", "meta_muse", None], ["en", "ko"]):
        check = fake_check(ACTION_OF[do], do, provider_status=STATUS, official_paths={"api_docs": "https://api.fixture-host.test/docs"})
        yield run(check=check, stop_point=stop, task_context=context, external_browser=browser, agent_platform=platform, lang=lang)
    for name, make in SCENARIOS.items():
        yield make()


def server_is_subordinate(body):
    """D112: the lookup says use_official_path, and the answer's first layer is not the site or the step is a public read (not a status check)."""
    echo = body["input_echo"]
    return (body["server"]["next_action"]["do"] == "use_official_path" and echo.get("task_step") != "status_check"
            and (body["where_to_look"]["first"] not in ("site", "unknown") or echo.get("task_context") == "public_read"))


def test_every_answer_keeps_person_do_now_handoff_and_the_server_action_in_step():
    count = 0
    for body in every_answer():
        count += 1
        WhyResponse.model_validate(body)
        checks, person, do = body["do_now"]["checks"], body["person"], body["server"]["next_action"]["do"]
        lang = body["lang"]
        # the invariant: a check only a person can do means a person is needed
        if any(c["who"] == "person" for c in checks):
            assert person["needed"] == "yes", body["input_echo"]
        # the server handing the step over is never answered with "not indicated"
        if do == "hand_off_to_human":
            assert person["needed"] == "yes"
        # "wait and look up once more" never sits next to an action that hands over or stops
        if do in ("hand_off_to_human", "stop_automation"):
            assert "CHK-WAIT" not in ids(body)
        # the user handoff line says what person says
        line = user_line(body)
        assert (X.TEMPLATES["H-USER-NO"][lang] in line) == (person["needed"] == "not_indicated")
        # D112: "follow server.next_action" is a check only where the lookup's developer path is not subordinate to the answer's own first layer
        assert ("CHK-SERVER-ACTION" in ids(body)) == (not server_is_subordinate(body))
        if person["needed"] == "not_indicated":
            assert "who" not in person and person["reasons"] == []
    assert count > 1000


def test_tools_off_always_needs_the_user():
    for platform in ("claude_in_chrome", "meta_muse", "other", None):
        body = run(stop_point="tools_off_page_visible", agent_platform=platform, check=fake_check("proceed", "proceed"))
        assert body["person"]["needed"] == "yes" and "user" in body["person"]["who"]
        assert "page_visible_tools_off" in [r["code"] for r in body["person"]["reasons"]]


# --- 4. the handoff names the stop from the measured codes ------------------------------------------------------------------
@pytest.mark.parametrize("lang", ["en", "ko"])
@pytest.mark.parametrize("codes,stop", [
    ({"error_class": "site_access"}, "page_blank_or_blocked"),
    ({"error_class": "human_verification"}, "human_verification"),
    ({"error_class": "service_error"}, "timeout_or_5xx"),
    ({"failure_stage": "before_target_response"}, "timeout_or_5xx"),
    ({"error_class": "login_auth"}, "site_login"),
    ({"failure_stage": "response_observed"}, "not_sure"),
    ({}, "not_sure"),
])
def test_without_a_stop_point_the_handoff_names_the_stop_from_the_codes(codes, stop, lang):
    body = run(lang=lang, **codes)
    assert X.STOP_WORDS[stop][lang] in user_line(body)
    assert body["where_to_look"]["layers"] == run(lang=lang, **codes)["where_to_look"]["layers"]  # nothing else reads it


def test_a_chosen_stop_wins_over_the_codes_in_the_handoff():
    body = run(stop_point="extra_verification", error_class="site_access")
    assert X.STOP_WORDS["extra_verification"]["en"] in user_line(body)


# --- 5. a status check stays one -------------------------------------------------------------------------------------------
def test_links_why_carries_task_step():
    link = lookup_signals.why_url({"site_host": "x-fixture.test", "error_class": "service_error", "task_step": "status_check"})
    assert parse_qs(urlparse(link).query)["task_step"] == ["status_check"]
    assert lookup_signals.why_url({"site_host": "x-fixture.test", "task_step": "status_check"}) is None  # still only with a failure code


def test_a_status_check_puts_the_status_page_first_and_passes_the_step_to_the_lookup():
    inputs = E.parse_inputs("x-fixture.test", today=at().date(), error_class="service_error", task_step="status_check")
    assert E.map_symptoms(inputs) == ("service_error", None, "status_check")
    chosen = E.parse_inputs("x-fixture.test", today=at().date(), stop_point="timeout_or_5xx", task_step="status_check")
    assert E.map_symptoms(chosen)[2] == "status_check"  # the sent step wins over the one chosen from stop_point
    body = run(check=fake_check(provider_status=STATUS), error_class="service_error", task_step="status_check")
    assert ids(body)[0] == "CHK-STATUS" and body["do_now"]["checks"][0]["who"] == "agent"
    plain = run(check=fake_check(), error_class="service_error", task_step="status_check")
    assert ids(plain)[0] == "CHK-SERVER-ACTION"
    other = run(check=fake_check(provider_status=STATUS), error_class="service_error")
    assert ids(other)[0] != "CHK-STATUS"


# --- 6. input_echo ---------------------------------------------------------------------------------------------------------
def test_every_accepted_input_is_echoed_and_in_the_self_link():
    body = run(error_class="site_access", failure_stage="access_denial_observed", task_step="status_check", http_status="403",
               challenge="header", retry_after="lt_60", via="unknown", agent_vendor="openai", ahn_test=True, stop_point="not_sure")
    assert body["input_echo"] == {"error_class": "site_access", "failure_stage": "access_denial_observed", "task_step": "status_check",
                                  "http_status": 403, "challenge": "header", "retry_after": "lt_60", "via": "unknown",
                                  "agent_vendor": "openai", "stop_point": "not_sure", "ahn_test": True}
    query = parse_qs(urlparse(body["links"]["self"]).query)
    assert query["error_class"] == ["site_access"] and query["http_status"] == ["403"] and query["ahn_test"] == ["1"]
    assert query["task_step"] == ["status_check"] and query["via"] == ["unknown"]


def test_values_outside_their_lists_are_not_echoed():
    body = run(task_step="Status Check!", http_status="999", challenge="<b>", retry_after="soon", via="carrier pigeon", ahn_test="1")
    assert body["input_echo"] == {}
    assert "pigeon" not in body["links"]["self"] and "ahn_test" not in body["links"]["self"]


# --- 7. Korean ---------------------------------------------------------------------------------------------------------------
def test_no_template_hard_codes_a_particle_after_a_blank():
    source = (pathlib.Path(E.__file__).parent / "explain_text.py").read_text(encoding="utf-8")
    assert re.findall(r"\} (?:이|가|을|를|은|는) ", source) == []
    for tid, pair in X.TEMPLATES.items():
        assert re.findall(r"\} (?:이|가|을|를|은|는) ", pair["ko"]) == [], tid


@pytest.mark.parametrize("platform", [None, "other", "claude_in_chrome", "claude_code"])
def test_the_korean_user_handoff_has_no_particle_after_the_tool_name(platform):
    body = run(lang="ko", agent_platform=platform, stop_point="site_login")
    line = user_line(body)
    assert "에이전트 이" not in line and "도구: " not in line and " 에서 하다가 끝내지 못했어요" in line and "—" not in line


def test_a_korean_answer_marks_the_english_server_sentence():
    ko = run(lang="ko")
    assert ko["server_note"] == {"template_id": "S-G-SERVER-EN", "text": X.TEMPLATES["S-G-SERVER-EN"]["ko"]}
    assert "영어 그대로" in ko["server_note"]["text"] and "서버 원문, 영어" not in ko["server_note"]["text"]
    assert "server_note" not in run(lang="en")
    no_line = {k: v for k, v in fake_check().items() if k != "summary_line"}
    assert "server_note" not in run(lang="ko", check=no_line)


# --- 9. AHN's home-page check changes are what AHN's request got -------------------------------------------------------------
def vendor_change(day, before, after, hour=3):
    return {"at": at(day, hour), "field": "bot_vendor", "from_code": before, "to_code": after, "source": "home_check"}


FLAP = [vendor_change("2026-11-02", "none", "datadome"), vendor_change("2026-11-04", "datadome", "none", 1),
        vendor_change("2026-11-04", "none", "datadome", 6)]


@pytest.mark.parametrize("lang", ["en", "ko"])
def test_a_marker_change_is_said_as_a_challenge_ahn_got_not_a_site_setting(lang):
    body = run(changes=[vendor_change("2026-11-05", "none", "datadome")], lang=lang)
    item = body["timeline"]["items"][0]
    assert item["template_id"] == "S-L1E-HOME-CHALLENGE-ON" and item["kind"] == "home_check_changed"
    assert ("started getting a DataDome challenge" if lang == "en" else "DataDome 챌린지를 받기 시작했어요") in item["text"]
    assert X.TEMPLATES["S-L1E-FLAP"][lang] not in item["text"]
    off = run(changes=[vendor_change("2026-11-05", "cloudflare", "none")], lang=lang)["timeline"]["items"][0]
    assert off["template_id"] == "S-L1E-HOME-CHALLENGE-OFF" and "Cloudflare" in off["text"]
    row = next(r for r in body["what_happened"]["layers"] if r["layer"] == "L1e")
    assert row["state"] == "evidence" and row["template_id"] == "S-L1E-HOME-CHALLENGE-ON"


@pytest.mark.parametrize("lang", ["en", "ko"])
def test_a_marker_that_came_went_and_came_back_says_ahns_results_wavered(lang):
    body = run(changes=FLAP, external_browser="no", since_kind="worked_before", since_date="2026-11-01", lang=lang)
    texts = [i["text"] for i in body["timeline"]["items"]]
    assert len(texts) == 3 and all(X.TEMPLATES["S-L1E-FLAP"][lang] in t for t in texts)
    rows = [r for r in body["what_happened"]["layers"] if r["layer"] == "L1e"]
    assert rows and all(r["state"] == "background" and X.TEMPLATES["S-L1E-FLAP"][lang] in r["text"] for r in rows)
    assert body["where_to_look"]["first"] != "site"  # a wavering change alone does not put the site first


def test_a_home_outcome_change_reads_as_what_ahns_request_got():
    change = {"at": at("2026-11-05"), "field": "home_outcome", "from_code": "redirected_same_host", "to_code": "bot_challenge", "source": "home_check"}
    item = run(changes=[change])["timeline"]["items"][0]
    assert item["template_id"] == "S-L1E-HOME-OUTCOME"
    assert "home-page check of login-fixture-host.com got a human-verification page where it had got a redirect within the site" in item["text"]


def test_a_declared_file_change_keeps_its_sentence():
    change = {"at": at("2026-11-05"), "field": "robots_status", "from_code": "ok", "to_code": "refused", "source": "read"}
    item = run(changes=[change])["timeline"]["items"][0]
    assert item["template_id"] == "S-L1E-CHANGED"


def test_the_change_sentences_never_say_the_site_set_or_removed_a_defense():
    for lang in ("en", "ko"):
        for tid in ("S-L1E-HOME-CHALLENGE-ON", "S-L1E-HOME-CHALLENGE-OFF", "S-L1E-HOME-CHALLENGE-SWAP", "S-L1E-HOME-OUTCOME", "S-L1E-FLAP"):
            text = X.TEMPLATES[tid][lang].lower()
            assert "site added" not in text and "site removed" not in text and "turned on" not in text and X.forbidden_in(text, lang) == []
