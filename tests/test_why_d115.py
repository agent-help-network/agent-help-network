"""D115 (pure, no database): AHN's own probe failing for over 7 days with nobody else seeing it is not site evidence (the one judgment change: the
cause is undetermined, and /v1/why does not send the caller to the site first because of it); /v1/why without a task_context does not assume a task
in the user's own account in its server summary; a status page host answered with its status page gets the feed address when one is known; a hand-off
that carries a status feed says the step stays with the user. Hosts are made up and are not on a reserved name."""

from __future__ import annotations

import re
from datetime import timedelta

import pytest

from _why_fixtures import fake_check, run
from test_why_d110 import home
from test_why_d114 import FEED, NOW, OFFICIAL, STATUS_HOST, Conn, lookup, site_errors
from why_ko_inputs import english_sentences, text_leaves

from ahn import cause, explain as E, explain_text as X, response_guide
from ahn.response_guide import FEED_HANDOFF_TAIL, NOCTX_SENTENCES, STATUS_LINE_MAX, noctx_summary
from ahn.why_models import WhyResponse

HANGUL = re.compile("[가-힣]")
ACCOUNT = re.compile(r"own account|their own", re.I)
FEED_URL = f"https://{STATUS_HOST}/api/v2/status.json"


# --- A long failure of AHN's own probe is not site evidence ---------------------------------------------------------------
def long_cause(days=8):
    return cause._cause("undetermined", "no_measurement", "low", ["outage_episodes"], "site_errors_unreached_long", start=NOW - timedelta(days=days))


def short_cause(days=2):
    return cause._cause("transient", "site_errors", "medium", ["outage_episodes"], "site_errors", start=NOW - timedelta(days=days))


def united(found, **kw):
    """The shape of a public read that timed out before any response, while AHN's own home-page check got no response either."""
    check = fake_check("use_official_path", "use_official_path", ahn_check=home("unreachable"), cause=found, official_paths=OFFICIAL)
    return run("united-fixture.test", check=check, error_class="service_error", failure_stage="before_target_response",
               task_context="public_read", **kw)


@pytest.mark.parametrize("lang", ["en", "ko"])
def test_a_long_failure_of_ahns_own_probe_does_not_send_the_caller_to_the_site(lang):
    long, short = united(long_cause(), lang=lang), united(short_cause(), lang=lang)
    assert short["where_to_look"]["first"] == "site"
    assert long["where_to_look"]["first"] != "site"
    rows = {r["layer"]: r["state"] for r in long["what_happened"]["layers"] if r["layer"] in ("L1", "L1b")}
    assert rows == {"L1": "background", "L1b": "background"}
    assert {r["layer"]: r["state"] for r in short["what_happened"]["layers"] if r["layer"] in ("L1", "L1b")} == {"L1": "evidence", "L1b": "evidence"}
    WhyResponse.model_validate(long)


def test_the_why_answer_carries_the_lookups_action_and_recommendation_whatever_the_cause():
    """/v1/why shows the lookup's own next_action and recommendation; a different cause does not alter them in the answer. (That the lookup itself
    does not change with the cause is the next test, which goes through response_guide.guide.)"""
    long, short = united(long_cause()), united(short_cause())
    assert long["server"]["next_action"] == short["server"]["next_action"]
    assert long["server"]["recommendation"] == short["server"]["recommendation"]


def test_the_lookup_guide_takes_no_cause_so_its_action_and_recommendation_cannot_depend_on_it():
    """D51: the cause is not an input of response_guide.guide, so the new cause shape cannot change the lookup's do, url or summary_line."""
    import inspect
    assert "cause" not in inspect.signature(response_guide.guide).parameters


def test_the_home_check_stays_background_only_for_the_long_failure_and_only_for_no_response_outcomes():
    check = fake_check(ahn_check=home("unreachable"), cause=long_cause())
    body = run(check=check, error_class="service_error", failure_stage="before_target_response")
    assert [r["state"] for r in body["what_happened"]["layers"] if r["layer"] == "L1b"] == ["background"]
    for outcome in ("server_error", "refused", "bot_challenge"):   # a 5xx answer of the home page is the site's, so it stays evidence   # a refusal of the home page is still evidence
        check = fake_check(ahn_check=home(outcome), cause=long_cause())
        body = run(check=check, error_class="service_error", failure_stage="before_target_response")
        assert [r["state"] for r in body["what_happened"]["layers"] if r["layer"] == "L1b"] == ["evidence"]


def test_a_period_of_7_days_or_less_or_with_another_source_keeps_the_site_evidence():
    for found in (site_errors(Conn(6)), site_errors(Conn(8, others=True)), site_errors(Conn(1))):
        assert not cause.long_ahn_path(found) and found["hint"] == "site_errors"
        assert united(found)["where_to_look"]["first"] == "site"
    for kind in ("no_response", "tls_error"):
        assert cause.long_ahn_path(site_errors(Conn(8, kind=kind))) and cause.long_ahn_path(site_errors(Conn(7.5, kind=kind)))


def test_a_long_period_of_5xx_answers_keeps_the_d114_shape():
    """A 5xx is an answer from the site or its edge: the cause stays transient / site_errors (only the D114 sentence), and the site stays first."""
    found = site_errors(Conn(8, kind="server_error"))
    assert (found["branch"], found["hint"], found["window"].keys() >= {"from"}) == ("transient", "site_errors", True)
    assert found["line"] == cause.line_for("site_errors_long", NOW - timedelta(days=8)) and not cause.long_ahn_path(found)
    assert united(found)["where_to_look"]["first"] == "site"


@pytest.mark.parametrize("lang", ["en", "ko"])
def test_a_long_period_of_5xx_answers_never_blames_ahns_path_and_keeps_the_action(lang):
    """A 5xx is the site or its edge answering with an error: the long sentence says so instead of pointing at AHN's network path, in
    English and in Korean, while first, do, recommendation and next_action stay those of the shorter 5xx period."""
    long, short = site_errors(Conn(8, kind="server_error")), site_errors(Conn(6, kind="server_error"))
    assert "network path" not in long["line"] and "answered with an error" in long["line"] and "network path" in short["line"]
    a, b = united(long, lang=lang), united(short, lang=lang)
    assert a["where_to_look"]["first"] == b["where_to_look"]["first"] == "site"
    for key in ("recommendation", "next_action"):
        assert a["server"][key] == b["server"][key]
    if lang == "ko":
        korean = X.cause_line_ko(long["line"])   # (this public read gets the short subordinate server block, so the line is read directly)
        assert korean and "네트워크 경로" not in korean and "서버 오류" in korean
    for kind in ("no_response", "tls_error"):   # the unanswered kinds keep their own sentence
        found = site_errors(Conn(8, kind=kind))
        assert "AHN's network path" in found["line"] and "blocks automated clients" in found["line"]

def test_the_long_sentence_leaves_the_possibility_of_a_site_that_blocks_automated_clients_open():
    line = cause.line_for("site_errors_unreached_long", NOW - timedelta(days=8))
    assert "blocks automated clients" in line and "AHN's network path" in line and "nothing about the site" not in line
    ko = X.cause_line_ko(line)
    assert HANGUL.search(ko) and "자동 클라이언트를 막는 사이트" in ko and "사이트의 상태나" not in ko
    assert len(line) <= cause.LINE_MAX and len(cause.line_for("site_errors_unreached_long", NOW - timedelta(days=30))) <= cause.LINE_MAX


def test_the_sites_hint_set_is_the_one_before():
    assert "no_measurement" not in E.SITE_EVIDENCE_HINTS and "site_errors" in E.SITE_EVIDENCE_HINTS


# --- No task_context, no assumed account ------------------------------------------------------------------------------------
def summary_lines():
    """Every whole summary line of the lookup that mentions a task in the user's own account (the {n} filled)."""
    tables = [response_guide.CONSUMER_ROWS, response_guide.CONSUMER_ROWS_BEFORE, response_guide.BLOCKED_PROCEED_CONSUMER_ROWS,
              response_guide.PROCEED_CONSUMER_ROWS, {k: v[1] for k, v in response_guide.ROWS.items()},
              response_guide.ROWS_BEFORE]
    lines = {text.replace("{n}", "7") for table in tables for text in table.values()}
    lines |= set(NOCTX_SENTENCES)
    return sorted(line for line in lines if ACCOUNT.search(line))


def test_the_scan_finds_the_lines_it_is_meant_to():
    lines = summary_lines()
    assert len(lines) >= 10 and response_guide.ROWS["O7"][1] in lines and response_guide.CONSUMER_ROWS["O2"] in lines


def test_every_account_sentence_of_the_lookup_has_a_neutral_replacement_and_no_replacement_is_left_over():
    from test_why_d112 import template_sentences
    found = {s for s in template_sentences() if ACCOUNT.search(s)}
    assert found == set(NOCTX_SENTENCES), sorted(found ^ set(NOCTX_SENTENCES))
    for before, after in NOCTX_SENTENCES.items():
        assert not ACCOUNT.search(after) and "hand the step" not in after and after != before, before


def test_the_replacement_is_fixed_sentence_by_sentence_and_a_line_without_one_is_untouched():
    assert noctx_summary("An official developer path is listed for this site. " + "Prefer that documented API or portal over page automation for API work.") \
        == "An official developer path is listed for this site. Prefer that documented API or portal over page automation for API work."
    assert noctx_summary("Fixture summary.") == "Fixture summary."
    tail = response_guide.CONSUMER_HAND_TAIL
    assert noctx_summary("Use the official path." + tail) == "Use the official path. " + response_guide._NO_PATH_CUSTOMER
    # a sentence already in the line is not said twice
    both = response_guide._NO_PATH_CUSTOMER + tail
    assert noctx_summary(both) == response_guide._NO_PATH_CUSTOMER


@pytest.mark.parametrize("lang", ["en", "ko"])
def test_without_a_task_context_the_server_summary_assumes_no_account(lang):
    count = 0
    for line in summary_lines():
        for stop in (None, "page_blank_or_blocked", "tools_off_page_visible"):   # a sign-in stop implies an account: see the test below
            check = fake_check("hand_off_to_human", "hand_off_to_human", summary_line=line)
            body = run(check=check, lang=lang, stop_point=stop)
            summary = body["server"]["summary_line"]
            count += 1
            assert not ACCOUNT.search(summary) and "사용자 본인 계정" not in summary and "본인 계정" not in summary, (line, summary)
            assert summary == noctx_summary(line) if lang == "en" else HANGUL.search(summary)
            if lang == "ko":
                assert "server_note" not in body and english_sentences(summary) == [], (line, summary)
            WhyResponse.model_validate(body)
    assert count > 30


def test_with_a_task_context_the_summary_is_the_lookups_own_and_nothing_else_changes():
    for line in summary_lines():
        check = fake_check("hand_off_to_human", "hand_off_to_human", summary_line=line)
        for context in ("public_read", "my_account_consumer", "other"):
            assert run(check=check, task_context=context)["server"]["summary_line"] == line
        plain, none = run(check=check, task_context="public_read"), run(check=check)
        for key in ("recommendation", "next_action", "next_checks"):
            assert plain["server"][key] == none["server"][key], key


def test_the_noctx_unknown_names_no_english_and_still_points_to_the_customer_page():
    en, ko = X.TEMPLATES["S-G-NOCTX"]["en"], X.TEMPLATES["S-G-NOCTX"]["ko"]
    assert "hand that step to your user" in en and "next_action.customer_url" in en
    assert ko == "AHN 은 무슨 작업인지 듣지 못했어요. 사용자 본인 계정의 일이면 그 단계는 사용자에게 넘기세요(고객 페이지가 있으면 next_action.customer_url)."
    for body in (run(stop_point="page_blank_or_blocked", lang="ko"), run(lang="ko")):
        unknown = next(u for u in body["what_happened"]["unknowns"] if u["template_id"] == "S-G-NOCTX")
        assert unknown["text"] == ko and english_sentences(unknown["text"]) == []
        assert not any("For a task" in text for _, _, text in text_leaves(body))


def test_the_lookup_answer_itself_is_not_changed_by_the_missing_context():
    """The lookup (guide) has no task_context: the sentences are the same as before, the neutral ones are only /v1/why's."""
    out = lookup("www.fixture-vendor.com")
    assert ACCOUNT.search(out["summary_line"])


# --- A status host looked up on its own gets its feed when one is known --------------------------------------------------------
def test_the_status_host_answer_names_the_feed_when_the_feed_address_is_known():
    plain, with_feed = lookup(STATUS_HOST), lookup(STATUS_HOST, feed_url=FEED_URL)
    action = with_feed["next_action"]
    assert action["url"] == action["status_url"] == FEED_URL
    assert action["status_url_kind"] == "feed_json" and action["status_url_note"] == response_guide.FEED_JSON_NOTE
    assert action["do"] == plain["next_action"]["do"] and with_feed["summary_line"] == plain["summary_line"]
    assert {k: v for k, v in action.items() if k not in ("url", "status_url", "status_url_kind", "status_url_note")} == \
        {k: v for k, v in plain["next_action"].items() if k not in ("url", "status_url")}


def test_a_status_host_without_a_known_feed_has_no_feed_keys_and_an_empty_feed_url_changes_nothing():
    plain = lookup(STATUS_HOST)
    assert plain["next_action"]["url"] == plain["next_action"]["status_url"] == f"https://{STATUS_HOST}/"
    assert "status_url_kind" not in plain["next_action"] and "status_url_note" not in plain["next_action"]
    assert lookup(STATUS_HOST, feed_url=None) == plain


@pytest.mark.parametrize("feed", [
    "https://other.fixture-vendor.com/api/v2/status.json",        # another host
    f"https://{STATUS_HOST}/status",                                # not a feed path
    f"https://{STATUS_HOST}/guessed/feed.json",                     # a guessed path
    "ftp://status.fixture-vendor.com/api/v2/status.json",           # not an address that is sent
])
def test_only_a_feed_on_the_same_host_with_a_feed_path_is_used(feed):
    assert lookup(STATUS_HOST, feed_url=feed) == lookup(STATUS_HOST)


def test_a_sign_in_status_page_and_a_failure_keep_their_answers_with_a_feed_known():
    page = {"source_url": f"https://{STATUS_HOST}/", "requires_sign_in": True, "covers": "all"}
    assert lookup(STATUS_HOST, provider_status=None, official_status_page=page, feed_url=FEED_URL) == lookup(STATUS_HOST, provider_status=None,
                                                                                                         official_status_page=page)
    failed = dict(error_class="service_error", failure_stage="before_target_response")
    assert lookup(STATUS_HOST, **failed, feed_url=FEED_URL)["summary_line"] == lookup(STATUS_HOST, **failed)["summary_line"]
    assert lookup("www.fixture-vendor.com", feed_url=FEED_URL) == lookup("www.fixture-vendor.com")


def test_the_why_answer_does_not_list_the_status_feed_as_a_developer_path():
    action = {"do": "use_official_path", "url": FEED_URL, "status_url": FEED_URL, "status_url_kind": "feed_json",
              "status_url_note": response_guide.FEED_JSON_NOTE}
    check = fake_check("use_official_path", "use_official_path", provider_status=FEED, official_paths=OFFICIAL, next_action=action)
    body = run("fixture-vendor.com", check=check)
    assert FEED_URL not in [c.get("url") for c in body["do_now"]["checks"] if c["id"] == "CHK-OFFICIAL-PATH"]
    assert body["server"]["next_action"]["url"] == FEED_URL


# --- A hand-off that carries a status feed says the step stays with the user --------------------------------------------------
FAILED = dict(status_check=True, error_class="service_error", failure_stage="before_target_response")


def test_a_hand_off_with_a_status_feed_says_the_feed_is_for_the_status_only():
    out = lookup(STATUS_HOST, feed_url=FEED_URL, **FAILED)
    assert out["next_action"]["do"] == "hand_off_to_human" and out["next_action"]["status_url_kind"] == "feed_json"
    assert out["summary_line"].endswith(FEED_HANDOFF_TAIL) and "status_url" in out["summary_line"]
    assert len(out["summary_line"]) <= STATUS_LINE_MAX and out["summary_line"].isascii()
    before = lookup(STATUS_HOST, **FAILED)   # without the feed: no feed, no tail
    assert before["next_action"]["do"] == "hand_off_to_human" and "status_url_kind" not in before["next_action"]
    assert FEED_HANDOFF_TAIL not in before["summary_line"] and out["summary_line"].startswith(before["summary_line"])
    assert {k: v for k, v in out["next_action"].items() if not k.startswith("status_url")} == \
        {k: v for k, v in before["next_action"].items() if not k.startswith("status_url")}


def test_other_answers_with_a_feed_do_not_get_the_tail():
    for kw in (dict(feed_url=FEED_URL), dict(feed_url=FEED_URL, error_class="service_error", failure_stage="before_target_response")):
        assert FEED_HANDOFF_TAIL not in lookup(STATUS_HOST, **kw)["summary_line"]


@pytest.mark.parametrize("lang", ["en", "ko"])
def test_the_hand_off_tail_comes_out_in_korean_in_a_korean_answer(lang):
    out = lookup(STATUS_HOST, feed_url=FEED_URL, **FAILED)
    body = run(check=fake_check("hand_off_to_human", "hand_off_to_human", summary_line=out["summary_line"], next_action=out["next_action"]), lang=lang)
    summary = body["server"]["summary_line"]
    if lang == "en":
        assert summary == out["summary_line"]
    else:
        assert HANGUL.search(summary) and "status_url 피드" in summary and "server_note" not in body and english_sentences(summary) == []
        WhyResponse.model_validate(body)


# --- Edges of the three rules above --------------------------------------------------------------------------------------------
def test_a_sign_in_step_without_a_task_context_keeps_the_account_clause():
    """S-G-NOCTX is not said when a stop implies an account (r3); the summary is then the lookup's own, with its account clause."""
    line = response_guide.ROWS["O7"][1]
    assert ACCOUNT.search(line)
    body = run(check=fake_check("hand_off_to_human", "hand_off_to_human", summary_line=line), stop_point="site_login")
    assert body["server"]["summary_line"] == line
    assert not any(u["template_id"] == "S-G-NOCTX" for u in body["what_happened"]["unknowns"])
    plain = run(check=fake_check("hand_off_to_human", "hand_off_to_human", summary_line=line))
    assert plain["server"]["summary_line"] == noctx_summary(line) != line


def test_a_stale_status_feed_is_not_replaced_by_the_feed_address_and_a_fresh_one_drops_the_covers_key():
    out = lookup(STATUS_HOST, feed_url=FEED_URL, provider_status=dict(FEED, freshness="stale"))
    assert out["summary_line"] in (response_guide.STATUS_ROWS["T5"], response_guide.STATUS_ROWS["T5-N"])
    assert "status_url_kind" not in out["next_action"] and out["next_action"]["url"] != FEED_URL
    page = {"source_url": f"https://{STATUS_HOST}/", "requires_sign_in": False, "covers": "api"}
    covered = lookup(STATUS_HOST, official_status_page=page, feed_url=None)
    assert covered["next_action"].get("status_url_covers") == "api"
    swapped = lookup(STATUS_HOST, official_status_page=page, feed_url=FEED_URL)
    assert swapped["next_action"]["status_url"] == FEED_URL and "status_url_covers" not in swapped["next_action"]


def test_a_neutral_replacement_that_would_pass_the_summary_cap_leaves_the_original():
    one = max(NOCTX_SENTENCES, key=lambda s: len(NOCTX_SENTENCES[s]) - len(s))
    grow = len(NOCTX_SENTENCES[one]) - len(one)
    assert grow > 0
    fits = "Fill." + " " + one
    assert noctx_summary(fits) != fits                                    # within the cap: replaced
    pad = "B" * (STATUS_LINE_MAX - len(one) - 2 - grow + 1) + "."   # the original just fits, the replacement is one over
    big = pad + " " + one
    assert len(big) <= STATUS_LINE_MAX
    assert noctx_summary(big) == big
