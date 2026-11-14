"""D116 (pure, no database): the Korean surface of /v1/why and the lookup (particles, polite style, timeout wording), a refused-and-reported lookup
that reaches a proceed row gets a resolved sentence, wait rows name the provider's affected components, a status host's /v1/why server block is
the lookup of that host, /v1/why without a task_context reads a customer page after its checks, and AHN's own long failing probe is not offered as
a reason in the hand-off. Hosts are made up."""

from __future__ import annotations

import re
from datetime import timedelta

import pytest

from _why_fixtures import TODAY, fake_check, run, view
from test_blocked_default import answer
from test_d63_decision_order import ABSENT, ALLOWS, CONSUMER_ONLY, OFFICIAL, PROFILES, WITH_CONSUMER
from test_why_d110 import STATUS, every_answer, home, user_line
from test_why_d114 import FEED, NOW, STATUS_HOST, lookup
from why_extra_inputs import extra_answers
from why_ko_inputs import server_text_answers, text_leaves

from ahn import cause, explain as E, explain_text as X, response_guide, triage
from ahn.response_guide import (AFFECTED_ROWS, LINE_MAX, REFUSED_PROCEED_CONSUMER_ROWS, REFUSED_PROCEED_ROWS, ROWS, guide, with_affected_names)
from ahn.why_models import WhyResponse

HANGUL = re.compile("[가-힣]")
# a particle set after a word that is not a placeholder; "AHN 에이전트" and similar are different words and are not hits
PARTICLE_GAP = re.compile(r"(에이전트|AHN) (에서|에는|에도|에게|으로|의|은|는|이|가|을|를|에)(?![가-힣])")
POLITE_END = re.compile(r"(합니다|습니다)\.?$")


def ko_texts():
    for body in list(every_answer()) + list(extra_answers()) + list(server_text_answers()):
        if body["lang"] == "ko":
            yield from (text for _path, _tid, text in text_leaves(body))


# --- Korean surface ----------------------------------------------------------------------------------------------------
def test_no_korean_text_has_a_space_between_agent_or_ahn_and_its_particle():
    seen = 0
    for text in ko_texts():
        seen += 1
        assert PARTICLE_GAP.search(text) is None, text
    assert seen > 10000


def test_a_particle_follows_a_filled_korean_word_and_keeps_its_space_after_an_english_name():
    ko = run(lang="ko", agent_platform=None, stop_point="site_login")
    assert "에이전트에서 하다가 끝내지 못했어요" in user_line(ko)
    chrome = run(lang="ko", agent_platform="claude_in_chrome", stop_point="site_login")
    name = next(p["display_name"] for p in view("ko")["platforms"] if p["platform"] == "claude_in_chrome")
    assert f"{name} 에서 하다가 끝내지 못했어요" in user_line(chrome)
    assert X.attach_particles("{host} 의 {platform} 에서", {"host": "x.com", "platform": "에이전트"}) == "{host} 의 {platform}에서"


def test_no_korean_check_sentence_ends_in_the_formal_style():
    assert triage.CHECK_TEXT_KO
    for text in triage.CHECK_TEXT_KO.values():
        assert POLITE_END.search(text) is None, text
    for text in triage.CHECKS_KO.values():
        assert POLITE_END.search(text) is None, text


def test_the_no_response_sentences_that_were_5xx_only_say_timeout_too():
    rows = [response_guide.SERVICE_ERROR_ROWS, response_guide.SERVICE_ERROR_CONSUMER_ROWS]
    for table in rows:
        for row, text in table.items():
            assert "timeout or 5xx" in text, row
            assert len(text) <= LINE_MAX
            korean = X.summary_line_ko(text)
            assert korean and "시간 초과" in korean, row
    assert "timeout or 5xx" in response_guide.FAILED_U3_SERVICE_ERROR and "timeout or 5xx" in response_guide.U7_O_SERVICE_ERROR
    assert "timeout or 5xx" in response_guide._NO_PATH_5XX
    # the before_target_response sentences keep their own wording
    for text in response_guide.NO_RESPONSE_ROWS.values():
        assert "No response came" in text


# --- A refused-and-reported lookup that reaches a proceed row ----------------------------------------------------------------
OFFICIALS = (None, OFFICIAL, WITH_CONSUMER, CONSUMER_ONLY)


def _guided(official, profile, *, ec=None, fs=None, assume=True, vendor=None):
    return guide(answer(official, profile, ec=ec, fs=fs, vendor=vendor, assume=assume), True, True, error_class=ec, failure_stage=fs, failure_kind_lines=True)


@pytest.mark.parametrize("report", [("site_access", None), (None, "access_denial_observed"), ("site_access", "access_denial_observed")])
def test_a_reported_refusal_on_a_proceed_row_is_told_as_a_resolved_sentence(report):
    ec, fs = report
    count = 0
    for profile in PROFILES:
        for official in OFFICIALS:
            out = _guided(official, profile, ec=ec, fs=fs, assume=False)
            if out["next_action"]["do"] != "proceed":
                continue
            count += 1
            text = out["summary_line"]
            assert "If a page refused you" not in text and "If refused" not in text, text
            assert text in REFUSED_PROCEED_ROWS.values() or text in REFUSED_PROCEED_CONSUMER_ROWS.values()
            assert len(text) <= LINE_MAX and "tell your user" in text
            korean = X.summary_line_ko(text)
            assert korean and "If" not in korean
    assert count > 4


def test_an_unreported_lookup_keeps_the_conditional_sentence_and_do_stays_proceed():
    for profile in PROFILES:
        for official in OFFICIALS:
            out = _guided(official, profile, assume=True)
            if out["next_action"]["do"] == "proceed":
                assert out["summary_line"] in response_guide.BLOCKED_PROCEED_ROWS.values() or out["summary_line"] in response_guide.BLOCKED_PROCEED_CONSUMER_ROWS.values()
    refused = _guided(None, ABSENT, ec="site_access", assume=False)
    unreported = _guided(None, ABSENT, assume=True)
    assert refused["next_action"] == unreported["next_action"] and refused["next_action"]["do"] == "proceed"
    assert refused["summary_line"] != unreported["summary_line"]


# --- The provider's affected components ---------------------------------------------------------------------------------------
def test_wait_rows_name_the_affected_components_only_when_the_status_lists_them():
    base = {"source_name": "Fixture Status", "source_url": "https://status.fixture-vendor.com/", "indicator": "minor", "freshness": "fresh",
            "active_incidents": 1, "max_incident_impact": "minor", "updated_at": NOW.isoformat()}
    rec = {"action": "wait_provider_incident", "confidence": "medium", "reasons": ["provider_incident"]}

    def sentence(provider):
        result = {"site_host": "fixture-vendor.com", "provider_status": provider, "official_status_page": None, "access_profile": None,
                  "official_paths": None, "recommendation": rec}
        return guide(result, True, False)["summary_line"]

    plain = sentence(base)
    named = sentence(dict(base, affected=["Login", "API"]))
    assert named == f"{plain} Affected: Login, API." and len(named) <= LINE_MAX
    assert sentence(dict(base, affected=None)) == plain and sentence(dict(base, affected=[])) == plain
    assert sentence(dict(base, affected=["bad\nname", "<b>x</b>", "x" * 80])) == plain
    assert X.summary_line_ko(named).endswith("영향받는 구성요소: Login, API.")


def test_the_names_are_dropped_one_by_one_rather_than_cut():
    for row in sorted(AFFECTED_ROWS):
        sentence = ROWS[row][1]
        out = with_affected_names(sentence, ["Alpha service", "Beta service", "Gamma service"])
        assert len(out) <= LINE_MAX - 1 and out.startswith(sentence)
        assert out.endswith(".")
    huge = with_affected_names(ROWS["W6"][1], ["N" * 40, "M" * 40, "L" * 40])
    assert len(huge) <= LINE_MAX - 1


# --- A status host's /v1/why says what the lookup of that host says ------------------------------------------------------------
def _status_host_pair(lang="en", error_class="site_access", task_step="status_check", **kw):
    root = lookup("fixture-vendor.com", error_class=error_class, status_check=True)
    sent = lookup(STATUS_HOST, error_class=error_class, status_check=True)
    i = E.parse_inputs(STATUS_HOST, today=TODAY, lang=lang, error_class=error_class, task_step=task_step, **kw)
    root_check = fake_check(root["next_action"]["do"], root["next_action"]["do"], summary_line=root["summary_line"], next_action=root["next_action"])
    sent_check = fake_check(sent["next_action"]["do"], sent["next_action"]["do"], summary_line=sent["summary_line"], next_action=sent["next_action"],
                            provider_status=FEED)
    return i, root, sent, root_check, sent_check


@pytest.mark.parametrize("lang", ["en", "ko"])
def test_why_server_do_is_the_lookup_do_for_the_status_host(lang):
    i, root, sent, root_check, sent_check = _status_host_pair(lang)
    assert i.lookup_host == STATUS_HOST and i.site_host == "fixture-vendor.com"
    body = E.build(i, root_check, view(lang), [], [], TODAY, sent_check)
    assert body["server"]["next_action"]["do"] == sent["next_action"]["do"]
    assert body["server"]["recommendation"] == sent_check["recommendation"]
    if body["server"]["next_action"].get("url") is not None or sent["next_action"].get("url") is not None:
        assert body["server"]["next_action"]["url"] == sent["next_action"]["url"]
    WhyResponse.model_validate(body)


def test_the_handoff_names_the_host_as_sent_when_it_is_a_status_host_and_the_layers_stay_on_the_root():
    i, root, sent, root_check, sent_check = _status_host_pair()
    assert sent["next_action"]["do"] == "hand_off_to_human" != root["next_action"]["do"]
    body = E.build(i, root_check, view(), [], [], TODAY, sent_check)
    assert STATUS_HOST in user_line(body)
    assert body["site_host"] == "fixture-vendor.com"
    plain = E.build(i, root_check, view(), [], [], TODAY)
    assert STATUS_HOST not in user_line(plain)


def test_a_host_that_is_not_a_status_host_keeps_the_root_judgment():
    i = E.parse_inputs("api.fixture-vendor.com", today=TODAY, error_class="site_access")
    root = fake_check("use_official_path", "use_official_path", next_action={"do": "use_official_path", "url": None})
    sent = fake_check("hand_off_to_human", "hand_off_to_human", next_action={"do": "hand_off_to_human", "url": None,
                      "status_url": f"https://{STATUS_HOST}/"})
    body = E.build(i, root, view(), [], [], TODAY, sent)
    assert body["server"]["next_action"]["do"] == "use_official_path" and body["server"]["next_action"]["status_url"] == f"https://{STATUS_HOST}/"


# --- No task_context and a customer page ----------------------------------------------------------------------------------------
CUSTOMER = "https://help.fixture-host.test/contact"


def _customer_check():
    return fake_check("use_official_path", "use_official_path", provider_status=STATUS,
                      next_action={"do": "use_official_path", "url": CUSTOMER, "customer_url": CUSTOMER, "retry_after_seconds": None},
                      official_paths={"consumer": {"paths": [{"url": CUSTOMER}]}})


def test_without_a_task_context_a_customer_page_block_comes_after_the_checks_and_the_check_is_conditional():
    body = run(check=_customer_check(), error_class="site_access", lang="en")
    assert body["server"]["position"] == "after_checks" and body["render_order"][-1] == "server"
    assert body["server"]["next_action"] == {"do": "use_official_path"}
    check = next(c for c in body["do_now"]["checks"] if c["id"] == "CHK-OFFICIAL-PATH")
    assert check["text"].startswith("If the task is a user-account matter") and CUSTOMER in check["text"]
    korean = run(check=_customer_check(), error_class="site_access", lang="ko")
    assert next(c for c in korean["do_now"]["checks"] if c["id"] == "CHK-OFFICIAL-PATH")["text"].startswith("작업이 사용자 계정 일이면")
    WhyResponse.model_validate(body)


@pytest.mark.parametrize("context", ["public_read", "my_account_consumer", "my_account_work"])
def test_with_a_task_context_the_customer_page_answer_is_as_before(context):
    body = run(check=_customer_check(), error_class="site_access", task_context=context)
    check = [c for c in body["do_now"]["checks"] if c["id"] == "CHK-OFFICIAL-PATH"]
    assert all(not c["text"].startswith("If the task") for c in check)


def test_an_account_stop_without_a_context_keeps_the_customer_page_as_before():
    body = run(check=_customer_check(), stop_point="extra_verification")
    check = [c for c in body["do_now"]["checks"] if c["id"] == "CHK-OFFICIAL-PATH"]
    assert all(not c["text"].startswith("If the task") for c in check)


# --- AHN's own long failing probe is not offered as a reason ---------------------------------------------------------------------------
def _long_cause(days):
    return cause._cause("undetermined", "no_measurement", "low", ["outage_episodes"], "site_errors_unreached_long", start=NOW - timedelta(days=days))


@pytest.mark.parametrize("lang", ["en", "ko"])
def test_the_open_it_yourself_tail_drops_ahns_own_reads_when_that_probe_has_failed_for_long(lang):
    check = fake_check(ahn_check=home("unreachable"), cause=_long_cause(8))
    body = run("www.air-fixture.test", check=check, stop_point="timeout_or_5xx", task_context="public_read", lang=lang)
    assert body["server"]["next_action"]["do"] == "hand_off_to_human"
    line = user_line(body)
    assert X.TEMPLATES["H-USER-OPEN-LONG"][lang] in line and X.TEMPLATES["H-USER-OPEN"][lang] not in line
    reason = next(r for r in body["person"]["reasons"] if r["code"] == "server_hand_off")
    assert reason["text"] == X.TEMPLATES["S-P-SERVER-HAND-OFF-OPEN-LONG"][lang]
    if lang == "en":
        assert "could not reach" not in line and "not evidence about this site" in line


def test_without_that_long_cause_the_open_it_yourself_tail_is_as_before():
    body = run("www.air-fixture.test", check=fake_check(ahn_check=home("unreachable")), stop_point="timeout_or_5xx", task_context="public_read")
    assert user_line(body).endswith(X.TEMPLATES["H-USER-OPEN"]["en"])
