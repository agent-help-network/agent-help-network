"""D112 (pure, no database): in /v1/why the lookup's developer path does not outrank the answer's own first layer, the browser comparison
is worded for the step, a status page that is a machine-readable feed is marked as one, and a Korean answer carries the Korean versions
that exist. The lookup's own fields (do, recommendation, person.needed reasons) are not changed by any of it."""

from __future__ import annotations

import itertools
import json
import re

import pytest

from _why_fixtures import fake_check, ids, run
from test_why_d110 import every_answer, server_is_subordinate

from ahn import explain as E, explain_text as X, response_guide, why_models
from ahn.models import NextAction
from ahn.response_guide import FEED_JSON_NOTE, FEED_JSON_PATH, guide
from ahn.why_models import WhyResponse

OFFICIAL = {"api_docs": "https://developer.fixture-host.test/apis"}
HANGUL = re.compile("[가-힣]")


def nytimes_like(**kw):
    """A public read that the tool stopped while the page shows, and a lookup that answers use_official_path."""
    check = fake_check("use_official_path", "use_official_path", official_paths=OFFICIAL,
                       next_action={"do": "use_official_path", "url": "https://developer.fixture-host.test/apis", "stop_if_you_are": ["ClaudeBot"]})
    kw.setdefault("stop_point", "tools_off_page_visible")
    kw.setdefault("task_context", "public_read")
    return run(check=check, **kw), check


# --- 1. the server block is subordinate -------------------------------------------------------------------------------------
@pytest.mark.parametrize("lang", ["en", "ko"])
def test_a_tools_off_public_read_has_no_developer_path_and_no_follow_the_server_check(lang):
    body, check = nytimes_like(lang=lang)
    WhyResponse.model_validate(body)
    assert body["where_to_look"]["first"] == "platform"
    assert "CHK-SERVER-ACTION" not in ids(body) and "CHK-OFFICIAL-PATH" not in ids(body)
    assert "CHK-PLATFORM-LOOK" in ids(body)
    text = json.dumps(body["do_now"], ensure_ascii=False).lower()
    assert "server.next_action" not in text and "developer" not in text and "개발자" not in text
    # the block is the short one (D113): the recommendation as given and the lookup's action only; it comes last and says what it is
    assert body["server"]["next_action"] == {"do": check["next_action"]["do"]} and body["server"]["recommendation"] == check["recommendation"]
    assert body["render_order"][-1] == "server" and len(body["render_order"]) == 8
    assert body["server_context_note"] == {"template_id": "S-G-SERVER-CONTEXT", "text": X.TEMPLATES["S-G-SERVER-CONTEXT"][lang]}


def test_the_context_sentence_in_both_languages():
    assert X.TEMPLATES["S-G-SERVER-CONTEXT"]["en"] == ("This block is a summary of the plain lookup answer, given without your step's context; "
                                                       "its full text is at links.check.")
    assert HANGUL.search(X.TEMPLATES["S-G-SERVER-CONTEXT"]["ko"])


def test_a_site_first_answer_for_a_page_that_is_not_a_public_read_keeps_the_server_action():
    body = run(check=fake_check("use_official_path", "use_official_path", official_paths=OFFICIAL), stop_point="page_blank_or_blocked")
    assert body["where_to_look"]["first"] in ("site", "unknown")
    if body["where_to_look"]["first"] == "site":
        assert "CHK-SERVER-ACTION" in ids(body) and "server_context_note" not in body and "server" not in body["render_order"]


def test_a_public_read_with_the_site_first_swaps_the_server_action_for_the_site_checks():
    check = fake_check("use_official_path", "use_official_path", official_paths=OFFICIAL,
                       provider_status={"source_name": "P", "source_url": "https://status.fixture-host.test/", "indicator": "none",
                                        "freshness": "fresh", "basis": "incident", "active_incidents": 0})
    body = run(check=check, task_context="public_read", stop_point="timeout_or_5xx", external_browser="no")
    assert "CHK-SERVER-ACTION" not in ids(body)
    assert "CHK-STATUS" in ids(body) and "CHK-BROWSER-COMPARE" in ids(body)
    assert body["render_order"][-1] == "server" and "server_context_note" in body


def test_a_status_check_keeps_the_server_action():
    check = fake_check("use_official_path", "use_official_path", official_paths=OFFICIAL)
    body = run(check=check, task_step="status_check", task_context="public_read", stop_point="tools_off_page_visible")
    assert "CHK-SERVER-ACTION" in ids(body) and "server_context_note" not in body and "server" not in body["render_order"]


def test_the_account_step_keeps_its_warned_developer_path_sentence():
    body = run(check=fake_check("use_official_path", "use_official_path", official_paths=OFFICIAL), task_context="my_account_consumer",
               stop_point="site_login")
    path = [c for c in body["do_now"]["checks"] if c["id"] == "CHK-OFFICIAL-PATH"]
    assert body["where_to_look"]["first"] == "account" and "CHK-SERVER-ACTION" not in ids(body)
    assert all(c["who"] == "person" for c in path)


def test_in_every_combination_the_server_action_follows_the_first_layer():
    count = subordinate = 0
    for body in every_answer():
        count += 1
        WhyResponse.model_validate(body)
        first, echo = body["where_to_look"]["first"], body["input_echo"]
        if server_is_subordinate(body):
            subordinate += 1
            assert "CHK-SERVER-ACTION" not in ids(body), echo
            assert body["render_order"][-1] == "server" and "server_context_note" in body, echo
            # the developer path is not offered to the agent where the first layer is not the site
            if first not in ("site", "unknown"):
                assert not [c for c in body["do_now"]["checks"] if c["id"] == "CHK-OFFICIAL-PATH" and c["who"] == "agent"], echo
            # no do_now text tells the agent to follow the server's action
            # (the wait check may still point at server.next_action.retry_after_seconds: that is a timing value)
            assert "Follow the server's next action" not in " ".join(c["text"] for c in body["do_now"]["checks"]), echo
            # the same direction in the handoff: it never names the developer path
            assert "developer" not in " ".join(h["text"] for h in body["handoff"]).lower(), echo
            # the person's reasons are those of the stop and the checks, never the developer path
            assert body["person"]["needed"] in ("yes", "not_indicated")
        else:
            assert "server_context_note" not in body and "server" not in body["render_order"], echo
        assert len(body["render_order"]) <= 8
    assert count > 1000 and subordinate > 100


def test_the_lookup_block_keeps_its_own_fields_and_the_lookups_do():
    for body in itertools.islice(every_answer(), 0, None, 7):
        # nothing in the block is computed by explain: the lookup's fields and the position
        assert set(body["server"]) <= {"summary_line", "recommendation", "next_action", "cause", "failure_stage", "next_checks", "position"}
        assert body["server"]["next_action"]["do"] in {"hand_off_to_human", "proceed", "use_official_path", "wait_for_provider", "no_clear_action",
                                                       "stop_automation", "retry_lookup_once"}


# --- 2. the browser comparison is worded for the step -----------------------------------------------------------------------
def compare_text(body):
    return next(c["text"] for c in body["do_now"]["checks"] if c["id"] == "CHK-BROWSER-COMPARE")


@pytest.mark.parametrize("lang", ["en", "ko"])
@pytest.mark.parametrize("stop", list(E.STOP_POINTS) + [None])
@pytest.mark.parametrize("context", ["public_read", "my_account_consumer", "other", None])
def test_the_comparison_is_worded_for_the_context_and_the_stop(lang, stop, context):
    body = run(stop_point=stop, task_context=context, lang=lang)
    if "CHK-BROWSER-COMPARE" not in ids(body):
        return
    text = compare_text(body)
    platform = stop in ("tools_off_page_visible", "before_visit")
    # D113: with no task_context (and no stop that implies an account) the account is not assumed
    no_context = context is None and "site_login" not in (stop,) and body["rules_applied"].count("R3") == 0
    account = context not in ("public_read",) and not no_context
    if lang == "en":
        assert ("signed in to the same account" in text) == account
        assert ("without signing in" in text) == (context == "public_read")
        assert ("sign in only if the task needs the user's account" in text or "signing in only if the task needs their account" in text) == no_context
        assert text.startswith("The user opens the") == platform
    else:
        assert ("같은 계정" in text) == account and ("로그인 없이" in text) == (context == "public_read")
        assert ("작업이 사용자 계정을 쓸 때만" in text) == no_context
        assert text.startswith("사용자가 직접") == platform
    assert body["do_now"]["checks"][[c["id"] for c in body["do_now"]["checks"]].index("CHK-BROWSER-COMPARE")]["who"] == "person"


def test_the_platform_stop_comparison_names_the_person_opening_the_page():
    body = run(stop_point="tools_off_page_visible", task_context="public_read")
    assert compare_text(body).startswith("The user opens the same public page themselves or with another tool")
    body = run(stop_point="before_visit", task_context="my_account_work")
    assert compare_text(body).startswith("The user opens the page themselves or with another tool")


def test_the_unchanged_default_text_is_kept_for_a_site_step():
    assert compare_text(run(stop_point="page_blank_or_blocked", task_context="my_account_consumer")) == X.TEMPLATES["CHK-BROWSER-COMPARE"]["en"]


# --- 3. a feed address is marked ---------------------------------------------------------------------------------------------
def status_result(site_host, status_url):
    rec = {"action": "wait_for_provider", "confidence": "high", "reasons": ["provider_incident"]}
    return {"site_host": site_host, "provider_status": {"source_name": "P", "source_url": status_url, "indicator": "major", "freshness": "fresh",
                                                         "basis": "incident", "active_incidents": 1, "max_incident_impact": "major",
                                                         "incident_updated_at": "2026-11-08T11:00:00Z", "active_maintenances": 0},
            "official_status_page": None, "access_profile": None, "official_paths": None, "recommendation": rec}


def action_of(site_host, status_url, error_class=None, feed_url=None):
    return guide(status_result(site_host, status_url), True, False, False, True, error_class, None, True, True, feed_url=feed_url)["next_action"]


def test_the_note_sentence_and_the_paths():
    assert FEED_JSON_NOTE == "The page may show a challenge, but this feed address usually answers; fetch it once."
    for path in ("/api/v2/status.json", "/api/v2/summary.json", "/summary.json", "/index.json"):
        assert FEED_JSON_PATH.match(path), path
    for path in ("/", "/status", "/api/v2/", "/api/v2/a/b.json", "/history.atom", "/api/v2/status.json/x", "/other.json"):
        assert not FEED_JSON_PATH.match(path), path


def test_a_feed_json_on_the_looked_up_site_is_marked_and_nothing_else_changes():
    feed = "https://status.provider-example.org/api/v2/status.json"
    marked = action_of("status.provider-example.org", feed)
    assert marked["status_url"] == feed and marked["status_url_kind"] == "feed_json" and marked["status_url_note"] == FEED_JSON_NOTE
    unmarked = {k: v for k, v in marked.items() if k not in ("status_url_kind", "status_url_note")}
    assert unmarked["do"] in {do for do, _ in response_guide.ROWS.values()}
    NextAction.model_validate(marked)
    # the root domain and its subdomains count as the looked-up site, as in D111
    assert action_of("provider-example.org", feed)["status_url_kind"] == "feed_json"
    assert action_of("www.provider-example.org", feed)["status_url_kind"] == "feed_json"


def test_the_feed_chosen_after_a_failure_is_marked():
    page = "https://status.provider-example.org/"
    feed = "https://status.provider-example.org/api/v2/summary.json"
    out = action_of("status.provider-example.org", page, "service_error", feed)
    assert out["status_url"] == feed and out["status_url_kind"] == "feed_json"
    assert "status_url_kind" not in action_of("status.provider-example.org", page, "service_error", None)


def test_a_page_another_site_or_a_non_feed_address_is_not_marked():
    feed = "https://status.provider-example.org/api/v2/status.json"
    assert "status_url_kind" not in action_of("shop-example.net", feed)  # a feed on another site
    assert "status_url_kind" not in action_of("status.provider-example.org", "https://status.provider-example.org/")  # a page
    assert "status_url_note" not in action_of("status.provider-example.org", "https://status.provider-example.org/")


def test_a_lookup_without_a_status_url_has_neither_field():
    out = guide({"site_host": "x-example.org", "recommendation": {"action": "proceed", "confidence": "low", "reasons": []},
                 "access_profile": None, "provider_status": None, "official_paths": None}, True, False, False, True, None, None, True, True)
    assert "status_url_kind" not in out["next_action"] and "status_url_note" not in out["next_action"]


# --- 4. a Korean answer carries the Korean versions that exist --------------------------------------------------------------
NOT_SENTENCES = {"FEED_JSON_NOTE"}


def template_sentences():
    """Every sentence the lookup's summary_line can be made of, split the way summary_line_ko splits a line (placeholders filled)."""
    texts = []
    for name, value in vars(response_guide).items():
        if name.startswith("_") or name in NOT_SENTENCES or name in ("LOG", "SAFE_URL"):
            continue
        if isinstance(value, str) and " " in value.strip() and value.strip().endswith("."):
            texts.append(value)
        elif isinstance(value, dict):
            for item in value.values():
                item = item[1] if isinstance(item, tuple) else item
                if isinstance(item, str) and " " in item:
                    texts.append(item)
    texts.append(response_guide._HANDOFF_API)
    found = []
    for text in texts:
        found.extend(s for s in X._SENTENCE_SPLIT.split(text.replace("{n}", "7").strip()) if s)
    return sorted(set(found))


def test_the_scan_finds_the_sentences_it_is_meant_to():
    found = template_sentences()
    assert len(found) > 150 and response_guide.ROWS["H1"][1].split(". ")[0] + "." in found


def test_every_summary_sentence_in_the_response_guide_has_a_korean_version():
    missing = [s for s in template_sentences() if X.summary_line_ko(s) is None]
    assert missing == [], "add a Korean version to explain_text._SUMMARY_PAIRS for: " + " | ".join(missing)


def test_no_korean_version_is_left_over_after_its_sentence_changed():
    sentences = template_sentences()
    for english, _ in X._SUMMARY_PAIRS:
        probe = re.compile("^" + re.escape(english).replace(r"\{n\}", r"\d+").replace(r"\{m\}", r"\d+") + "$")
        assert any(probe.match(s) for s in sentences), english


def pairs_filled():
    for english, korean in X._SUMMARY_PAIRS:
        yield english.replace("{n}", "7").replace("{m}", "15"), korean.replace("{n}", "7").replace("{m}", "15"), english, korean


def test_every_korean_version_is_korean_hae_yo_without_a_particle_after_a_blank():
    for filled_en, filled_ko, english, korean in pairs_filled():
        assert HANGUL.search(korean) and english.isascii() and english.count("{") == korean.count("{"), english
        assert X.forbidden_in(korean, "ko") == [], english
        assert re.findall(r"\} (?:이|가|을|를|은|는) ", korean) == [], english
        assert korean.endswith("."), korean
        assert X.summary_line_ko(filled_en) == filled_ko, english
    assert len({en for en, _ in X._SUMMARY_PAIRS}) == len(X._SUMMARY_PAIRS)


def test_the_whole_table_passes_the_server_summary_pattern_and_keeps_control_characters_out():
    field = why_models.WhyServer.model_fields["summary_line"]
    pattern = re.compile(next(m.pattern for m in field.metadata if getattr(m, "pattern", None)))
    for _, filled_ko, _, _ in pairs_filled():
        assert pattern.fullmatch(filled_ko), filled_ko
    assert X.STATUS_URL_NOTE_KO  # the note is a separate field with its own limit
    for bad in ("a\nb", "a\tb", "a\x00b", "가\r"):
        assert not pattern.fullmatch(bad)
    # the characters the table actually uses are Hangul syllables and printable ASCII only
    used = {ch for _, ko in X._SUMMARY_PAIRS for ch in ko if not (" " <= ch <= "~")}
    assert used and all("가" <= ch <= "힣" for ch in used)


def with_summary(summary, lang, **extra):
    check = fake_check("hand_off_to_human", "hand_off_to_human", summary_line=summary, **extra)
    extra_run = {"task_context": "other"}  # D115: with no task_context the why answer neutralizes account sentences
    return run(check=check, lang=lang, **extra_run)


def test_every_table_sentence_comes_out_korean_in_a_korean_answer_and_unchanged_in_an_english_one():
    for filled_en, filled_ko, _, _ in pairs_filled():
        ko = with_summary(filled_en, "ko")
        WhyResponse.model_validate(ko)
        assert ko["server"]["summary_line"] == filled_ko and HANGUL.search(filled_ko) and "server_note" not in ko, filled_en
        en = with_summary(filled_en, "en")
        assert en["server"]["summary_line"] == filled_en and "server_note" not in en


def test_a_line_with_one_sentence_missing_stays_whole_english_and_is_marked():
    known = response_guide.ROWS["H1"][1]
    mixed = known + " A sentence that is not in the table."
    ko = with_summary(mixed, "ko")
    assert ko["server"]["summary_line"] == mixed and ko["server_note"]["template_id"] == "S-G-SERVER-EN"
    assert X.summary_line_ko(mixed) is None and X.summary_line_ko("Fixture summary.") is None
    assert with_summary("Fixture summary.", "ko")["server_note"]["template_id"] == "S-G-SERVER-EN"


def lookup_line(recommendation, official=None, **kw):
    status = kw.get("status_check", False)
    result = {"site_host": "x-example.org", "recommendation": recommendation, "access_profile": kw.pop("access_profile", None),
              "provider_status": kw.pop("provider_status", None), "official_paths": official, "official_status_page": None}
    return guide(result, True, False, status_guide=status, failure_kind_lines=True, one_action=True, **kw)


def rec(action, *reasons):
    return {"action": action, "confidence": "medium", "reasons": list(reasons)}


SCENARIOS = {
    "timeout_hand_off": lambda: lookup_line(rec("hand_off_to_human", "no_path_after_failure", "robots_unknown"), error_class="service_error",
                                            failure_stage="before_target_response"),
    "5xx_hand_off": lambda: lookup_line(rec("hand_off_to_human", "no_path_after_failure", "profile_not_read", "official_path_available"),
                                        error_class="service_error", failure_stage="after_target_response"),
    "human_verification_hand_off": lambda: lookup_line(rec("hand_off_to_human", "human_handoff"), error_class="human_verification"),
    "robots_use_official_path": lambda: lookup_line(rec("use_official_path", "robots_blocks_agents", "official_path_available")),
    "pending_robots_with_seconds": lambda: lookup_line(rec("use_official_path", "profile_pending", "official_path_available"),
                                                       access_profile={"retry_after_seconds": 45}),
    "wait_for_provider": lambda: lookup_line(rec("wait_provider_incident", "provider_scope")),
    "t6_unresolved_incident": lambda: lookup_line(
        rec("proceed", "robots_allows_agents"), status_check=True, d100=True,
        provider_status={"source_name": "P", "source_url": "https://status.x-example.org/", "indicator": "none", "freshness": "fresh",
                         "basis": "incident", "active_incidents": 2}),
    "status_check_no_page": lambda: lookup_line(rec("proceed", "robots_allows_agents"), status_check=True),
    "proceed": lambda: lookup_line(rec("proceed", "robots_allows_agents")),
}
ALLOWED_ENGLISH = {"AHN", "API", "user", "agent", "agents", "edge", "robots", "txt", "RFC", "xx", "url", "do"}


@pytest.mark.parametrize("name", sorted(SCENARIOS))
def test_a_korean_why_of_a_real_lookup_line_is_all_korean(name):
    out = SCENARIOS[name]()
    line = out["summary_line"]
    ko = with_summary(line, "ko")
    WhyResponse.model_validate(ko)
    shown = ko["server"]["summary_line"]
    assert HANGUL.search(shown) and "server_note" not in ko, (name, line)
    # no English sentence is left: what is not Hangul is a field name, a number or a short fixed term
    words = re.findall(r"[A-Za-z]+", re.sub(r"[\w.]*[_.][\w.]*", " ", shown))
    assert set(words) <= ALLOWED_ENGLISH, (name, shown, sorted(set(words) - ALLOWED_ENGLISH))
    # an English answer carries the lookup's line byte for byte
    assert with_summary(line, "en")["server"]["summary_line"] == line
    assert ko["server"]["next_action"] == with_summary(line, "en")["server"]["next_action"]


def test_the_scenarios_land_on_the_rows_they_name():
    assert "robots.txt" in SCENARIOS["robots_use_official_path"]()["summary_line"]
    assert "5xx" in SCENARIOS["5xx_hand_off"]()["summary_line"]
    assert "No response came" in SCENARIOS["timeout_hand_off"]()["summary_line"]
    assert "human verification" in SCENARIOS["human_verification_hand_off"]()["summary_line"]
    assert "45 s" in SCENARIOS["pending_robots_with_seconds"]()["summary_line"]
    assert "unresolved incident" in SCENARIOS["t6_unresolved_incident"]()["summary_line"]
    assert "about 15 minutes" in SCENARIOS["wait_for_provider"]()["summary_line"]
    assert "45초" in with_summary(SCENARIOS["pending_robots_with_seconds"]()["summary_line"], "ko")["server"]["summary_line"]
    assert "15분" in with_summary(SCENARIOS["wait_for_provider"]()["summary_line"], "ko")["server"]["summary_line"]


def test_the_feed_note_is_korean_in_a_korean_answer_only():
    action = {"do": "use_official_path", "status_url": "https://status.fixture-host.test/api/v2/status.json",
              "status_url_kind": "feed_json", "status_url_note": FEED_JSON_NOTE}
    check = fake_check("use_official_path", "use_official_path", next_action=action)
    ko = run(check=check, lang="ko", stop_point="timeout_or_5xx")
    en = run(check=check, lang="en", stop_point="timeout_or_5xx")
    WhyResponse.model_validate(ko)
    assert HANGUL.search(ko["server"]["next_action"]["status_url_note"]) and ko["server"]["next_action"]["status_url"] == action["status_url"]
    assert en["server"]["next_action"] == action
    assert check["next_action"] == action  # the lookup's own answer is not modified


def test_an_unknown_first_layer_keeps_the_server_action_and_the_server_block_first():
    check = fake_check("use_official_path", "use_official_path", official_paths=OFFICIAL)
    body = run(check=check, stop_point="human_verification")
    WhyResponse.model_validate(body)
    assert body["where_to_look"]["first"] == "unknown"
    assert "CHK-SERVER-ACTION" in ids(body)
    assert "server_context_note" not in body and "server" not in body["render_order"]


def test_the_server_position_agrees_with_the_render_order_and_the_context_note():
    seen = set()
    for body in every_answer():
        in_order = "server" in body["render_order"]
        assert ("server_context_note" in body) == in_order, body["input_echo"]
        if in_order:
            assert body["server"]["position"] == "after_checks", body["input_echo"]
        seen.add(in_order)
    assert seen == {True, False}
