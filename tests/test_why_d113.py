"""D113 (pure, no database): in /v1/why the lookup block of an answer that looks elsewhere first is short and points to the full lookup
answer (links.check); a customer page is not called a developer path; an answer with no task_context does not assume an account; a
subdomain's lookup block is the lookup of the host as sent; the provider_clear sentence does not say "no active issue" when the incident
list was not read; and two timeline items at the same time are one. The lookup's own judgments are not changed by any of it."""

from __future__ import annotations

import itertools
import json
from datetime import date
from urllib.parse import parse_qs, urlparse

import pytest

from _why_fixtures import TODAY, at, fake_check, ids, inputs, run, view
from test_why_d110 import STATUS, every_answer, server_is_subordinate

from ahn import explain as E, explain_text as X, response_guide, triage
from ahn.why_models import WhyResponse

OFFICIAL = {"api_docs": "https://developer.fixture-host.test/apis"}
CUSTOMER = "https://help.fixture-host.test/contact"
CONSUMER = {"consumer": {"paths": [{"kind": "customer_help", "url": CUSTOMER}]}}
D112_SERVER_KEYS = {"summary_line", "recommendation", "next_action", "cause", "failure_stage", "next_checks", "position"}


def dumped(body):
    """The answer as the route serializes it (response_model, aliases)."""
    return WhyResponse.model_validate(body).model_dump(mode="json", by_alias=True)


def nytimes_like(**kw):
    check = fake_check("use_official_path", "use_official_path", official_paths=OFFICIAL, cause={"branch": "transient", "hint": "recovered",
                       "confidence": "low", "basis": [], "line": "Fixture line.", "note": "n"}, failure_stage="unknown",
                       next_checks=[{"code": "reports_unknown", "text": "t"}],
                       next_action={"do": "use_official_path", "url": "https://developer.fixture-host.test/apis", "retry_after_seconds": None,
                                    "stop_if_you_are": ["ClaudeBot"], "customer_url": CUSTOMER})
    kw.setdefault("stop_point", "tools_off_page_visible")
    kw.setdefault("task_context", "public_read")
    return run(check=check, **kw), check


# --- F1. the lookup block of a subordinate answer is short ----------------------------------------------------------------------
@pytest.mark.parametrize("lang", ["en", "ko"])
def test_a_subordinate_server_block_keeps_position_recommendation_and_do_only(lang):
    body, check = nytimes_like(lang=lang)
    assert body["server_context_note"]["template_id"] == "S-G-SERVER-CONTEXT"
    server = body["server"]
    assert list(server) == ["recommendation", "next_action", "position"]
    assert server["position"] == "after_checks" and server["recommendation"] == check["recommendation"]
    assert server["next_action"] == {"do": "use_official_path"}
    assert "server_note" not in body  # no summary_line in the block, so no sentence about its language
    # as the route serializes it: no null url, no developer address, no stop list anywhere in the block
    out = dumped(body)["server"]
    assert out["next_action"] == {"do": "use_official_path"} and set(out) == {"recommendation", "next_action", "position"}
    text = json.dumps(out)
    assert "developer.fixture-host.test" not in text and "stop_if_you_are" not in text and "ClaudeBot" not in text


def test_the_context_sentence_points_to_links_check_in_both_languages():
    assert "links.check" in X.TEMPLATES["S-G-SERVER-CONTEXT"]["en"] and "links.check" in X.TEMPLATES["S-G-SERVER-CONTEXT"]["ko"]


def test_links_check_is_the_lookup_with_the_codes_the_lookup_was_given():
    body, _ = nytimes_like(http_status=403, error_class="site_access", ahn_test=True, agent_vendor="anthropic", via="browser")
    link = body["links"]["check"]
    assert link.startswith("/v1/incidents/check?") and list(body["links"]) == ["self", "check"]
    query = {k: v[0] for k, v in parse_qs(urlparse(link).query).items()}
    assert query == {"site_host": "login-fixture-host.com", "error_class": "site_access", "task_step": "other", "window_hours": "168",
                     "http_status": "403", "via": "browser", "agent_vendor": "anthropic", "ahn_test": "1"}
    assert dumped(body)["links"]["check"] == link


def test_links_check_uses_the_codes_the_stop_maps_to_and_no_test_mark_without_one():
    body, _ = nytimes_like(stop_point="tools_off_page_visible")
    query = {k: v[0] for k, v in parse_qs(urlparse(body["links"]["check"]).query).items()}
    assert query == {"site_host": "login-fixture-host.com", "task_step": "other", "window_hours": "168"}


def test_only_a_subordinate_answer_has_links_check_and_a_short_block():
    short = full = 0
    for body in every_answer():
        if server_is_subordinate(body):
            short += 1
            assert "check" in body["links"] and list(body["server"]) == ["recommendation", "next_action", "position"], body["input_echo"]
            assert body["server"]["next_action"] == {"do": "use_official_path"}
        else:
            full += 1
            # the D112 block, key for key: not shortened, no links.check
            assert "check" not in body["links"] and list(body["links"]) == ["self"], body["input_echo"]
            keys = list(body["server"])
            assert keys[:3] == ["recommendation", "next_checks", "position"] and set(keys) <= D112_SERVER_KEYS, keys
            assert "server_context_note" not in body
    assert short > 100 and full > 100


def test_a_non_subordinate_block_is_the_lookups_own_bytes():
    next_action = {"do": "hand_off_to_human", "url": None, "retry_after_seconds": None}
    check = fake_check("hand_off_to_human", "hand_off_to_human", next_action=next_action, failure_stage="unknown",
                       next_checks=[{"code": "reports_unknown", "text": "t"}])
    body = run(check=check, stop_point="page_blank_or_blocked")
    assert "server_context_note" not in body
    out = dumped(body)["server"]
    assert out["next_action"] == next_action and out["next_checks"] == check["next_checks"] and out["summary_line"] == check["summary_line"]


def test_a_short_block_still_validates_without_cause_failure_stage_or_next_checks():
    body, _ = nytimes_like()
    server = WhyResponse.model_validate(body).server
    assert server.cause is None and server.failure_stage is None and server.next_checks is None


# --- F2. a customer page is not called a developer path ------------------------------------------------------------------------
def customer_check(**extra):
    return fake_check("use_official_path", "use_official_path", official_paths={**OFFICIAL, **CONSUMER},
                      next_action={"do": "use_official_path", "url": CUSTOMER, "customer_url": CUSTOMER}, **extra)


@pytest.mark.parametrize("lang", ["en", "ko"])
def test_a_customer_page_address_is_worded_as_a_customer_page(lang):
    body = run(check=customer_check(), task_context="public_read", stop_point="timeout_or_5xx", external_browser="no", lang=lang)
    path = [c for c in body["do_now"]["checks"] if c["id"] == "CHK-OFFICIAL-PATH"]
    assert len(path) == 1 and path[0]["url"] == CUSTOMER
    assert path[0]["text"] == X.TEMPLATES["CHK-OFFICIAL-PATH-CUSTOMER"][lang].format(url=CUSTOMER)
    assert "developer" not in path[0]["text"].lower() and "개발자" not in path[0]["text"]


def test_the_customer_wording_is_the_sites_own_customer_page_sentence():
    assert X.TEMPLATES["CHK-OFFICIAL-PATH-CUSTOMER"]["en"] == "The site's own customer page is {url}."


@pytest.mark.parametrize("lang", ["en", "ko"])
def test_a_developer_path_keeps_the_developer_wording(lang):
    check = fake_check("use_official_path", "use_official_path", official_paths={**OFFICIAL, **CONSUMER},
                       next_action={"do": "use_official_path", "url": OFFICIAL["api_docs"], "customer_url": CUSTOMER})
    body = run(check=check, task_context="public_read", stop_point="timeout_or_5xx", external_browser="no", lang=lang)
    path = [c for c in body["do_now"]["checks"] if c["id"] == "CHK-OFFICIAL-PATH"]
    assert len(path) == 1 and path[0]["text"] == X.TEMPLATES["CHK-OFFICIAL-PATH"][lang].format(url=OFFICIAL["api_docs"])


def test_the_account_step_does_not_warn_that_a_customer_page_is_a_developer_path():
    body = run(check=customer_check(), task_context="my_account_consumer", stop_point="site_login")
    path = [c for c in body["do_now"]["checks"] if c["id"] == "CHK-OFFICIAL-PATH"]
    assert path and all(c["text"] == X.TEMPLATES["CHK-OFFICIAL-PATH-CUSTOMER"]["en"].format(url=CUSTOMER) for c in path)
    assert not [r for r in body["what_happened"]["layers"] if r["template_id"] == "S-L4-OFFICIAL-PATH-ACCOUNT"]
    dev = run(check=fake_check("use_official_path", "use_official_path", official_paths=OFFICIAL), task_context="my_account_consumer",
              stop_point="site_login")
    assert [r for r in dev["what_happened"]["layers"] if r["template_id"] == "S-L4-OFFICIAL-PATH-ACCOUNT"]


def test_no_check_that_names_a_customer_page_says_developer():
    count = 0
    for lang, context, stop, browser in itertools.product(["en", "ko"], ["public_read", "my_account_consumer", "other", None],
                                                           list(E.STOP_POINTS) + [None], ["yes_same_account", "no", None]):
        body = run(check=customer_check(), task_context=context, stop_point=stop, external_browser=browser, lang=lang)
        for item in body["do_now"]["checks"]:
            if item["id"] == "CHK-OFFICIAL-PATH" and item.get("url") == CUSTOMER:
                count += 1
                assert "developer" not in item["text"].lower() and "개발자" not in item["text"], (context, stop, browser)
    assert count > 20


# --- F3. no task_context: the account is not assumed ---------------------------------------------------------------------------
def compare(body):
    return next(c["text"] for c in body["do_now"]["checks"] if c["id"] == "CHK-BROWSER-COMPARE")


@pytest.mark.parametrize("lang", ["en", "ko"])
def test_without_a_task_context_the_comparison_does_not_assume_an_account(lang):
    body = run(stop_point="page_blank_or_blocked", lang=lang)
    assert compare(body) == X.TEMPLATES["CHK-BROWSER-COMPARE-NOCTX"][lang]
    assert ("signed in to the same account" not in compare(body)) if lang == "en" else ("같은 계정" not in compare(body))
    assert "S-G-NOCTX" in [u["template_id"] for u in body["what_happened"]["unknowns"]]
    unknown = next(u for u in body["what_happened"]["unknowns"] if u["template_id"] == "S-G-NOCTX")
    assert unknown["text"] == X.TEMPLATES["S-G-NOCTX"][lang]
    assert len(body["what_happened"]["unknowns"]) <= 6


def test_the_noctx_unknown_is_given_only_where_the_noctx_check_is():
    for stop in ("site_login", "company_login"):
        body = run(stop_point=stop)
        assert "S-G-NOCTX" not in [u["template_id"] for u in body["what_happened"]["unknowns"]], stop
        assert compare(body) != X.TEMPLATES["CHK-BROWSER-COMPARE-NOCTX"]["en"]
    for stop in ("page_blank_or_blocked", "tools_off_page_visible", None):
        body = run(stop_point=stop) if stop else run()
        has_check = any(c["id"] == "CHK-BROWSER-COMPARE" and c["text"] in (X.TEMPLATES["CHK-BROWSER-COMPARE-NOCTX"]["en"],
                                                                            X.TEMPLATES["CHK-BROWSER-COMPARE-OPEN-NOCTX"]["en"]) for c in body["do_now"]["checks"])
        assert has_check == ("S-G-NOCTX" in [u["template_id"] for u in body["what_happened"]["unknowns"]]), stop


def test_the_korean_noctx_sentence_quotes_the_phrase_the_korean_lookup_sentences_have():
    phrase = "사용자 본인 계정의 일에는"
    assert phrase in X.TEMPLATES["S-G-NOCTX"]["ko"] and "사용자 계정 작업이면" not in X.TEMPLATES["S-G-NOCTX"]["ko"]
    assert any(ko.startswith(phrase) or f", {phrase}" in ko or phrase in ko for ko in X.SUMMARY_SENTENCES_KO.values())
    assert any("For a task in the user's own account" in en for en in X.SUMMARY_SENTENCES_KO)


def test_the_noctx_sentences():
    assert X.TEMPLATES["CHK-BROWSER-COMPARE-NOCTX"]["en"].startswith("A person can open the same page once in their usual browser; sign in only if the task needs the user's account.")
    assert X.TEMPLATES["S-G-NOCTX"]["en"] == ("AHN was not told what the task is; the lookup's sentences that start with 'For a task in the user's own account' "
                                              "apply only if it is.")


def test_a_public_read_wins_over_no_context_and_a_given_context_keeps_the_default():
    assert compare(run(stop_point="page_blank_or_blocked", task_context="public_read")) == X.TEMPLATES["CHK-BROWSER-COMPARE-PUBLIC"]["en"]
    assert compare(run(stop_point="page_blank_or_blocked", task_context="other")) == X.TEMPLATES["CHK-BROWSER-COMPARE"]["en"]
    body = run(stop_point="page_blank_or_blocked", task_context="other")
    assert "S-G-NOCTX" not in [u["template_id"] for u in body["what_happened"]["unknowns"]]


def test_the_platform_stop_without_a_context_has_its_own_wording():
    assert compare(run(stop_point="tools_off_page_visible")) == X.TEMPLATES["CHK-BROWSER-COMPARE-OPEN-NOCTX"]["en"]


def test_the_lookup_summary_line_is_not_changed_by_the_missing_context():
    check = fake_check()
    assert run(check=check, stop_point="page_blank_or_blocked")["server"]["summary_line"] == check["summary_line"]
    assert run(check=check, stop_point="page_blank_or_blocked", task_context="other")["server"]["summary_line"] == check["summary_line"]


def test_a_stop_that_implies_an_account_keeps_the_account_wording_without_a_context():
    body = run(stop_point="site_login", external_browser="no")
    if "CHK-BROWSER-COMPARE" in ids(body):
        assert compare(body) == X.TEMPLATES["CHK-BROWSER-COMPARE"]["en"]


# --- F4. a subdomain's lookup block is the lookup of the host as sent --------------------------------------------------------
SENT = "status.replit.com"


def sent_check():
    return fake_check("wait_provider_incident", "wait_for_provider", next_action={
        "do": "wait_for_provider", "url": None, "retry_after_seconds": 60, "status_url": "https://status.replit.com/api/v2/status.json",
        "status_url_kind": "feed_json", "status_url_note": "The page may show a challenge, but this feed address usually answers; fetch it once."})


def test_the_input_remembers_the_host_as_sent_only_when_it_became_its_apex():
    i = E.parse_inputs(SENT, today=TODAY)
    assert i.host_normalized == "apex" and i.site_host == "replit.com" and i.lookup_host == SENT
    assert E.parse_inputs("replit.com", today=TODAY).lookup_host is None
    assert E.parse_inputs("www.replit.com", today=TODAY).lookup_host is None
    assert E.parse_inputs("https://replit.com/path?q=1", today=TODAY).lookup_host is None


def test_the_judgment_is_the_apex_lookups_and_only_the_status_address_comes_from_the_host_as_sent():
    i = E.parse_inputs(SENT, today=TODAY, error_class="site_access", http_status=403)
    apex = fake_check("hand_off_to_human", "hand_off_to_human", provider_status=dict(STATUS, indicator="minor"),
                      next_action={"do": "hand_off_to_human", "url": None, "retry_after_seconds": 300})
    plain = E.build(i, apex, view(), [], [], TODAY)
    body = E.build(i, apex, view(), [], [], TODAY, sent_check())
    assert body["host_normalized"] == "apex" and body["site_host"] == "replit.com"
    sent_action = sent_check()["next_action"]
    action = body["server"]["next_action"]
    assert body["server"]["recommendation"] == apex["recommendation"] and action["do"] == "hand_off_to_human"
    assert action["retry_after_seconds"] == 300 and action["url"] is None  # every other key is the apex lookup's
    assert all(action[k] == sent_action[k] for k in ("status_url", "status_url_kind", "status_url_note"))
    # everything but those three keys is what the answer without the host as sent says
    strip = lambda a: {k: v for k, v in a.items() if not k.startswith("status_url")}
    assert strip(action) == strip(plain["server"]["next_action"])
    assert {k: v for k, v in body.items() if k != "server"} == {k: v for k, v in plain.items() if k != "server"}
    assert {k: v for k, v in body["server"].items() if k != "next_action"} == {k: v for k, v in plain["server"].items() if k != "next_action"}
    WhyResponse.model_validate(body)


def test_the_apex_stop_automation_judgment_stays_and_a_status_address_is_replaced_not_added_twice():
    i = E.parse_inputs(SENT, today=TODAY, error_class="site_access")
    apex = fake_check("stop_automation", "stop_automation", next_action={"do": "stop_automation", "url": None, "retry_after_seconds": None,
                      "status_url": "https://replit.com/status", "status_url_kind": "page", "status_url_note": "old"})
    body = E.build(i, apex, view(), [], [], TODAY, sent_check())
    assert body["server"]["recommendation"]["action"] == "stop_automation" and body["server"]["next_action"]["do"] == "stop_automation"
    assert body["server"]["next_action"]["status_url"] == "https://status.replit.com/api/v2/status.json"
    assert body["server"]["next_action"]["status_url_kind"] == "feed_json"
    nothing = E.build(i, apex, view(), [], [], TODAY, fake_check("hand_off_to_human", "hand_off_to_human"))  # the host as sent has no status address
    assert nothing["server"]["next_action"]["status_url"] == "https://replit.com/status"


def test_a_subordinate_block_takes_nothing_from_the_host_as_sent():
    i = E.parse_inputs(SENT, today=TODAY, stop_point="tools_off_page_visible", task_context="public_read")
    check = fake_check("use_official_path", "use_official_path", official_paths=OFFICIAL)
    plain = E.build(i, check, view(), [], [], TODAY)
    assert E.build(i, check, view(), [], [], TODAY, sent_check()) == plain
    assert plain["server"]["next_action"] == {"do": "use_official_path"}


def test_korean_translates_the_note_of_the_status_address_in_the_block_from_the_host_as_sent():
    i = E.parse_inputs(SENT, today=TODAY, error_class="site_access", lang="ko")
    body = E.build(i, fake_check("hand_off_to_human", "hand_off_to_human"), view("ko"), [], [], TODAY, sent_check())
    note = body["server"]["next_action"]["status_url_note"]
    assert note in X.STATUS_URL_NOTE_KO.values() or note == sent_check()["next_action"]["status_url_note"]


def test_explain_asks_the_lookup_for_the_apex_and_for_the_host_as_sent(monkeypatch):
    from ahn import models, service

    calls = []

    def fake(settings, host, *args, **kw):
        calls.append(host)
        return sent_check() if host == SENT else fake_check("hand_off_to_human", "hand_off_to_human")

    class Stub:
        def __init__(self, data):
            self.data = data

        @classmethod
        def model_validate(cls, data):
            return cls(data)

        def model_dump(self, mode="python"):
            return self.data

    monkeypatch.setattr(service, "check", fake)
    monkeypatch.setattr(models, "CheckResponse", Stub)
    body = E.explain(None, E.parse_inputs(SENT, today=TODAY, error_class="site_access", http_status=403), None, today=TODAY, platforms=[])
    assert calls == ["replit.com", SENT]
    assert body["server"]["next_action"]["do"] == "hand_off_to_human"
    assert body["server"]["next_action"]["status_url"] == "https://status.replit.com/api/v2/status.json"
    assert body["server"]["next_action"]["status_url_kind"] == "feed_json" and body["server"]["next_action"]["status_url_note"]
    calls.clear()
    E.explain(None, E.parse_inputs("replit.com", today=TODAY), None, today=TODAY, platforms=[])
    assert calls == ["replit.com"]  # one lookup, as before


def test_links_check_of_a_subdomain_answer_names_the_root_domain_whose_answer_the_block_is():
    i = E.parse_inputs(SENT, today=TODAY, stop_point="tools_off_page_visible", task_context="public_read")
    check = fake_check("use_official_path", "use_official_path", official_paths=OFFICIAL)
    body = E.build(i, check, view(), [], [], TODAY, check)
    assert parse_qs(urlparse(body["links"]["check"]).query)["site_host"] == ["replit.com"]
    assert body["links"]["self"].startswith("/v1/why?site_host=replit.com")


# --- F5. provider_clear does not say "no active issue" for an incident list that was not read ----------------------------------
UNREAD = {"freshness": "fresh", "indicator": "none", "active_incidents": None}
READ_ZERO = {"freshness": "fresh", "indicator": "none", "active_incidents": 0}


def clear_text(provider, **kw):
    checks = triage.next_checks("site_access", provider, "no_reports", **kw)
    return next(c["text"] for c in checks if c["code"] == "provider_clear")


def test_an_unread_incident_list_is_not_said_to_be_no_active_issue():
    text = clear_text(UNREAD, list_unread=True)
    assert text == triage.CLEAR_UNREAD_CHECK
    assert "no active issue" not in text and "did not read its incident list" in text
    assert text == ("The provider's overall indicator reads operational; AHN did not read its incident list, so this does not say that no issue is "
                    "open. Continue diagnosing this specific attempt; it does not prove access from your environment.")
    assert triage.CHECK_TEXT_KO[triage.CLEAR_UNREAD_CHECK] == triage.CLEAR_UNREAD_CHECK_KO


def test_a_list_that_was_read_keeps_the_sentence_and_the_code_is_the_same():
    assert clear_text(READ_ZERO, list_unread=False) == triage.CHECKS["provider_clear"]
    assert clear_text(UNREAD, list_unread=False) == triage.CHECKS["provider_clear"]
    for flag in (True, False):
        assert [c["code"] for c in triage.next_checks("site_access", UNREAD, "no_reports", list_unread=flag)] == \
            [c["code"] for c in triage.next_checks("site_access", UNREAD, "no_reports")]


@pytest.mark.parametrize("freshness", ["fresh", "stale"])
@pytest.mark.parametrize("indicator", ["none", "minor", "major"])
@pytest.mark.parametrize("count", [None, 0, 2])
@pytest.mark.parametrize("d100", [True, False])
@pytest.mark.parametrize("incidentio", [True, False])
def test_the_wording_is_chosen_by_the_condition_of_status_row_t1_u(freshness, indicator, count, d100, incidentio):
    provider = {"freshness": freshness, "indicator": indicator, "active_incidents": count, "source_url": "https://status.fixture-host.test/"}
    unread = response_guide.incident_list_unread(provider, d100, incidentio)
    row = response_guide.status_row(provider, None, "https://status.fixture-host.test/", d100, incidentio)
    assert unread == (row == "T1-U")
    codes = triage.next_checks("site_access", provider, "no_reports", list_unread=unread, unresolved_listed=d100)
    clear = [c for c in codes if c["code"] == "provider_clear"]
    if unread:
        assert clear and clear[0]["text"] == triage.CLEAR_UNREAD_CHECK
    assert all("no active issue" not in c["text"] for c in codes if unread)


def test_the_judgment_is_the_same_with_either_wording():
    base = triage.next_checks("site_access", UNREAD, "no_reports")
    flagged = triage.next_checks("site_access", UNREAD, "no_reports", list_unread=True)
    assert [c["code"] for c in base] == [c["code"] for c in flagged]
    assert [c for c in base if c["code"] != "provider_clear"] == [c for c in flagged if c["code"] != "provider_clear"]


# --- F6. the same time and layer is one timeline item -------------------------------------------------------------------------
def vendor_change(day, before, after, hour=3):
    return {"at": at(day, hour), "field": "bot_vendor", "from_code": before, "to_code": after, "source": "home_check"}


def outcome_change(day, before, after, hour=3):
    return {"at": at(day, hour), "field": "home_outcome", "from_code": before, "to_code": after, "source": "home_check"}


def doc_event(day, url, hour=3):
    return {"url": url, "changed_at": at(day, hour)}


DOC_A, DOC_B = "https://docs.fixture-vendor.test/safely", "https://docs.fixture-vendor.test/trouble"  # the two documents of claude_in_chrome


def timeline_items(body):
    return body["timeline"]["items"]


@pytest.mark.parametrize("lang", ["en", "ko"])
def test_a_challenge_that_started_and_a_result_that_changed_at_one_moment_are_one_item(lang):
    changes = [vendor_change("2026-11-05", "none", "datadome"), outcome_change("2026-11-05", "reachable", "login_wall")]
    items = timeline_items(run(changes=changes, lang=lang))
    assert len(items) == 1 and items[0]["layer"] == "L1e" and items[0]["kind"] == "home_check_changed"
    assert len(items[0]["text"]) <= 300
    assert X.CODE_WORDS["home_outcome"]["login_wall"][lang] in items[0]["text"] and ("DataDome" in items[0]["text"])
    assert items[0]["text"].count("2026-11-05") == 1  # the date it repeats is said once


def test_a_second_sentence_that_the_first_already_says_is_not_repeated():
    changes = [vendor_change("2026-11-05", "none", "datadome"), outcome_change("2026-11-05", "reachable", "login_wall")]
    text = timeline_items(run(changes=changes))[0]["text"]
    assert text.count("This is what AHN's own request got") == 1


def test_items_at_different_times_or_layers_stay_apart():
    url = "https://docs.fixture-vendor.test/agent"
    body = run(changes=[vendor_change("2026-11-05", "none", "datadome", 3), vendor_change("2026-11-05", "datadome", "none", 4)],
               events=[doc_event("2026-11-05", url, 3)], agent_platform="chatgpt_agent")
    items = timeline_items(body)
    assert len(items) == 3 and len({(i["at"], i["layer"]) for i in items}) == 3


def test_no_combination_has_two_home_check_items_at_one_time_or_loses_a_document_address_or_has_a_text_over_the_limit():
    url = "https://docs.fixture-vendor.test/agent"
    pool = [vendor_change("2026-11-05", "none", "datadome"), vendor_change("2026-11-05", "datadome", "none"),
            outcome_change("2026-11-05", "reachable", "login_wall"), outcome_change("2026-11-05", "login_wall", "reachable"),
            vendor_change("2026-11-04", "none", "cloudflare"), outcome_change("2026-11-04", "reachable", "blocked")]
    count = merged = 0
    for size in (1, 2, 3, 4, 5, 6):
        for chosen in itertools.combinations(pool, size):
            for events in ([], [doc_event("2026-11-05", DOC_A), doc_event("2026-11-05", DOC_B)]):
                for lang in ("en", "ko"):
                    body = run(changes=list(chosen), events=events, agent_platform="claude_in_chrome", lang=lang)
                    items = timeline_items(body)
                    keys = [(i["at"], i["layer"]) for i in items if i["layer"] == "L1e"]
                    assert len(keys) == len(set(keys)), (chosen, events)
                    docs = [i["source_url"] for i in items if i["layer"] == "L2"]
                    assert len(docs) == len(set(docs)) == len(events) and "Same time" not in " ".join(i["text"] for i in items if i["layer"] == "L2")
                    assert all(1 <= len(i["text"]) <= 300 for i in items)
                    WhyResponse.model_validate(body)
                    count += 1
                    merged += any("같은 때: " in i["text"] or "Same time: " in i["text"] for i in items)
    assert count > 100 and merged > 10


def test_a_merge_that_would_pass_the_limit_leaves_the_later_sentence_out_and_never_cuts():
    items = [{"at": at("2026-11-05"), "layer": "L1e", "kind": "home_check_changed", "template_id": "A", "text": "On 2026-11-05, " + "a" * 200 + "."},
             {"at": at("2026-11-05"), "layer": "L1e", "kind": "home_check_changed", "template_id": "B", "text": "On 2026-11-05, " + "b" * 200 + "."}]
    out = E._merge_same_time(items, "en", 300)
    assert len(out) == 1 and out[0]["template_id"] == "A" and out[0]["text"].endswith("a" * 200 + ".")


def test_two_platform_document_changes_at_one_moment_stay_two_items_with_their_own_address():
    body = run(events=[doc_event("2026-11-05", DOC_A), doc_event("2026-11-05", DOC_B)], agent_platform="claude_in_chrome")
    items = [i for i in timeline_items(body) if i["layer"] == "L2"]
    assert len(items) == 2 and {i["source_url"] for i in items} == {DOC_A, DOC_B}
    assert all(i["kind"] == "platform_doc_changed" and "Same time" not in i["text"] for i in items)


def test_the_extra_input_helper_yields_valid_answers_for_the_three_new_kinds_of_input():
    from why_extra_inputs import extra_answers

    count = sub = customer = unread = 0
    for body in extra_answers():
        WhyResponse.model_validate(body)
        count += 1
        sub += body.get("host_normalized") == "apex"
        customer += "customer_url" in body["server"].get("next_action", {})
    assert count > 300 and sub > 100 and customer > 20
