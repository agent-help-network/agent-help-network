"""D114 (pure, no database): a status page host that is looked up with no failure signal gets the status answer, a failed period of AHN's own
checks older than 7 days that nobody else saw is told as likely AHN's own path, and a Korean /v1/why answer carries no English sentence
that is not marked as the lookup's own. The lookup's judgment (recommendation, next_action.do, branch, hint, confidence) is not changed by any
of it. Hosts are made up and are not on a reserved name (a reserved name has no status answer)."""

from __future__ import annotations

import copy
import itertools
import re
from datetime import datetime, timedelta, timezone

import pytest

from _why_fixtures import TODAY, fake_check, run, view
from why_ko_inputs import check_texts, cause_objects, english_sentences, ko_answers, text_leaves

from ahn import cause, explain as E, explain_text as X, response_guide, triage, why_models
from ahn.response_guide import STATUS_ROWS, guide
from ahn.why_models import WhyResponse

HANGUL = re.compile("[가-힣]")
STATUS_HOST = "status.fixture-vendor.com"
OFFICIAL = {"api_docs": "https://developer.fixture-vendor.com/apis"}
FEED = {"source_name": "Fixture Status", "source_url": f"https://{STATUS_HOST}/", "indicator": "none", "freshness": "fresh"}


def lookup(host, *, provider_status=FEED, official=OFFICIAL, reasons=("official_path_available",), action="use_official_path", error_class=None,
           failure_stage=None, status_check=False, official_status_page=None, **kw):
    result = {"site_host": host, "provider_status": provider_status, "official_status_page": official_status_page, "access_profile": None,
              "official_paths": official, "recommendation": {"action": action, "confidence": "medium", "reasons": list(reasons)}}
    return guide(result, True, False, status_check, True, error_class, failure_stage, True, True, d100=True, **kw)


# --- a status page host asked about with no failure signal ----------------------------------------------------------------
def test_the_status_page_host_answers_with_its_status_page():
    out = lookup(STATUS_HOST)
    assert out["summary_line"] == STATUS_ROWS["T1"]
    assert out["next_action"]["url"] == f"https://{STATUS_HOST}/" == out["next_action"]["status_url"]
    assert out["next_action"]["do"] == "use_official_path"


def test_nothing_but_the_sentence_and_the_url_changes():
    other = lookup("www.fixture-vendor.com")
    same = lookup(STATUS_HOST)
    assert other["summary_line"] != same["summary_line"] and other["next_action"]["url"] == OFFICIAL["api_docs"]
    keys = {"do", "url", "retry_after_seconds", "status_url"}
    assert set(same["next_action"]) == set(other["next_action"]) == keys
    for key in keys - {"url"}:
        assert same["next_action"][key] == other["next_action"][key]


def test_the_unread_incident_list_gets_its_own_sentence():
    page = dict(FEED, active_incidents=None)
    out = lookup(STATUS_HOST, provider_status=page, incidentio_unread=True)
    assert out["summary_line"] == STATUS_ROWS["T1-U"]
    assert lookup(STATUS_HOST, provider_status=dict(page, active_incidents=2))["summary_line"] == STATUS_ROWS["T6"]


def test_a_sign_in_status_page_is_not_made_the_url():
    page = {"source_url": f"https://{STATUS_HOST}/", "requires_sign_in": True, "covers": "all"}
    out = lookup(STATUS_HOST, provider_status=None, official_status_page=page)
    assert out["summary_line"] == STATUS_ROWS["T3-S"] and out["next_action"]["url"] == OFFICIAL["api_docs"]


def test_a_status_page_that_covers_only_the_api_says_so():
    page = {"source_url": f"https://{STATUS_HOST}/", "requires_sign_in": False, "covers": "api"}
    out = lookup(STATUS_HOST, provider_status=None, official_status_page=page)
    assert out["summary_line"] == STATUS_ROWS["T3-A"] and out["next_action"]["url"] == f"https://{STATUS_HOST}/"


@pytest.mark.parametrize("name, kw", [
    ("failure_code", dict(error_class="service_error")),
    ("failure_stage", dict(failure_stage="before_target_response")),
    ("login_failure", dict(error_class="login_auth")),
    ("another_subdomain_of_the_same_site", dict(host="www.fixture-vendor.com")),
    ("the_root_domain", dict(host="fixture-vendor.com")),
    ("a_status_page_under_a_path_of_a_general_site", dict(host="www.fixture-vendor.com", provider_status=dict(
        FEED, source_url="https://www.fixture-vendor.com/support/systemstatus/"))),
    ("a_robots_block", dict(reasons=("robots_blocks_agents", "official_path_available"))),
    ("a_wait_for_the_provider", dict(action="wait_provider_incident", reasons=("provider_incident",))),
    ("a_reserved_name", dict(host="status.fixture-vendor.test", provider_status=dict(FEED, source_url="https://status.fixture-vendor.test/"))),
    ("a_proceed", dict(action="proceed", reasons=("robots_allows_agents",), official=None)),
])
def test_every_other_case_is_answered_as_before(name, kw, monkeypatch):
    kw = dict(kw)
    host = kw.pop("host", STATUS_HOST)
    out = lookup(host, **kw)
    monkeypatch.setattr(response_guide, "STATUS_HOST_ROWS", ())  # status host rule off: the answer the code gave before
    assert out == lookup(host, **kw), name


def test_the_status_host_answer_is_the_only_one_that_the_switch_changes(monkeypatch):
    on = lookup(STATUS_HOST)
    monkeypatch.setattr(response_guide, "STATUS_HOST_ROWS", ())
    assert on != lookup(STATUS_HOST)


def test_a_status_check_lookup_is_answered_as_before():
    """The D76 answer of a status_check lookup of another host is the one the status host rule gives the status host without the step."""
    checked = lookup("www.fixture-vendor.com", status_check=True)
    self_asked = lookup(STATUS_HOST)
    assert (checked["summary_line"], checked["next_action"]["url"]) == (self_asked["summary_line"], self_asked["next_action"]["url"])
    assert lookup(STATUS_HOST, status_check=True) == self_asked


def test_a_status_page_under_a_path_of_a_general_site_keeps_the_developer_path():
    path_page = dict(FEED, source_url="https://www.fixture-vendor.com/support/systemstatus/")
    for host in ("www.fixture-vendor.com", "fixture-vendor.com"):
        out = lookup(host, provider_status=path_page)
        assert out["next_action"]["url"] == OFFICIAL["api_docs"] and out["summary_line"] != STATUS_ROWS["T1"]
        assert out["next_action"]["status_url"] == path_page["source_url"]
    assert lookup("www.fixture-vendor.com", provider_status=path_page, status_check=True)["next_action"]["url"] == path_page["source_url"]


def test_the_host_name_decides_not_the_case_of_it():
    assert lookup(STATUS_HOST.upper())["next_action"]["url"] == f"https://{STATUS_HOST}/"


def test_a_why_of_the_status_host_takes_the_lookup_of_its_root_and_only_the_status_keys_of_the_host_as_sent():
    """/v1/why normalizes status.x.com to x.com (D113): the judgment and the sentence are the root lookup's; the status-host answer is the
    lookup's, so only the three status keys of the host as sent are laid over the server block."""
    root = lookup("fixture-vendor.com")
    sent = lookup(STATUS_HOST)
    assert sent["next_action"]["url"] != root["next_action"]["url"]
    for lang in ("en", "ko"):
        i = E.parse_inputs(STATUS_HOST, today=TODAY, lang=lang)
        assert i.host_normalized == "apex" and i.site_host == "fixture-vendor.com" and i.lookup_host == STATUS_HOST
        root_check = fake_check("use_official_path", "use_official_path", summary_line=root["summary_line"], next_action=root["next_action"])
        sent_check = fake_check("use_official_path", "use_official_path", summary_line=sent["summary_line"], next_action=sent["next_action"])
        body = E.build(i, root_check, view(lang), [], [], TODAY, sent_check)
        action = body["server"]["next_action"]
        assert action["url"] == root["next_action"]["url"] == OFFICIAL["api_docs"] and action["do"] == "use_official_path"
        assert action["status_url"] == sent["next_action"]["status_url"]
        if lang == "en":
            assert body["server"]["summary_line"] == response_guide.noctx_summary(root["summary_line"])  # D115: no task_context
        else:
            assert HANGUL.search(body["server"]["summary_line"])
        WhyResponse.model_validate(body)


def _official_path_checks(body):
    return [c for c in body["do_now"]["checks"] if c["id"].startswith("CHK-OFFICIAL-PATH")]


def _before_d114(ctx):
    """The developer path as main (before D114) chose it: next_action.url when the action is use_official_path, status page or not."""
    na = ctx.next_action
    if na.get("do") == "use_official_path" and E._url(na.get("url")):
        return na["url"]
    paths = ctx.check.get("official_paths") or {}
    return E._url(paths.get("api_docs")) or E._url(paths.get("developer_portal"))


def _without_official_path(body):
    """The answer minus the developer-path check and the account warning layer row that go with it."""
    body = copy.deepcopy(body)
    body["do_now"]["checks"] = [c for c in body["do_now"]["checks"] if not c["id"].startswith("CHK-OFFICIAL-PATH")]
    kept = [r for r in body["what_happened"]["layers"] if r.get("template_id") != "S-L4-OFFICIAL-PATH-ACCOUNT"]
    if len(kept) != len(body["what_happened"]["layers"]):
        # the warning row was the only background row of the task layer; without it that layer is not seen (where_to_look.first is unchanged)
        body["where_to_look"]["layers"]["task"] = "not_seen"
    body["what_happened"]["layers"] = kept
    return body


CONTEXTS = (None, "public_read", "my_account_consumer", "my_account_work")


@pytest.mark.parametrize("status_check", [False, True])
def test_a_status_answer_lists_no_developer_path_check_and_nothing_else_changes(status_check, monkeypatch):
    """The status page as next_action.url is the one action (D96), not a developer path: no CHK-OFFICIAL-PATH beside it, and not a developer
    path from official_paths either. Compared with the answer main gave (the status page called a developer path), only that check and the
    account warning that goes with it are missing; every other field is byte-identical."""
    out = lookup(STATUS_HOST, status_check=status_check)
    assert out["next_action"]["url"] == f"https://{STATUS_HOST}/"
    removed = 0
    for official, lang, context in itertools.product((OFFICIAL, None), ("en", "ko"), CONTEXTS):
        check = fake_check("use_official_path", "use_official_path", summary_line=out["summary_line"], next_action=out["next_action"],
                           provider_status=FEED, **({"official_paths": official} if official else {}))
        new = run(STATUS_HOST, check=check, task_context=context, lang=lang)
        assert _official_path_checks(new) == [] and any(c["id"] == "CHK-STATUS" for c in new["do_now"]["checks"])
        assert not any(r.get("template_id") == "S-L4-OFFICIAL-PATH-ACCOUNT" for r in new["what_happened"]["layers"])
        with monkeypatch.context() as patch:
            patch.setattr(E, "_official_url", _before_d114)
            old = run(STATUS_HOST, check=check, task_context=context, lang=lang)
        removed += len(_official_path_checks(old))
        assert new == _without_official_path(old), (official, lang, context)
        for judged in ("recommendation", "next_action"):
            assert new["server"][judged] == old["server"][judged]
    assert removed > 0   # the grid has answers that did name the status page as a developer path


def test_a_developer_path_that_is_next_action_url_is_still_the_official_path_check():
    check = fake_check("use_official_path", "use_official_path", next_action={"do": "use_official_path", "url": OFFICIAL["api_docs"]},
                       official_paths=OFFICIAL, provider_status=FEED)
    body = run("fixture-vendor.com", check=check, task_context="public_read")
    assert [c.get("url") for c in _official_path_checks(body)] == [OFFICIAL["api_docs"]]


# --- a long failed period of AHN's own checks ----------------------------------------------------------------------------
NOW = datetime(2026, 11, 10, 12, 0, tzinfo=timezone.utc)


class Result:
    def __init__(self, row):
        self.row = row

    def fetchone(self):
        return self.row


class Conn:
    """Answers the two queries _site_errors makes: the open period, and whether anyone else saw the host failing."""

    def __init__(self, days, *, others=False, confirmed=True, ended=None, kind="no_response"):
        self.row = {"kind": kind, "first_fail_at": NOW - timedelta(days=days), "confirmed_at": NOW if confirmed else None,
                    "ended_at": ended, "ended_reason": "answered" if ended else None, "last_event_at": NOW - timedelta(minutes=5),
                    "held_overlap": 0}
        self.others = others

    def execute(self, sql, params=()):
        return Result(self.row if "FROM outage_episodes" in sql else ({"?": 1} if self.others else None))


def site_errors(conn):
    return cause._site_errors(conn, "fixture-vendor.com", cause.When(NOW, False, False), NOW)


def judgment(found):
    return {key: found[key] for key in ("branch", "hint", "confidence", "basis", "window")}


def test_the_long_failure_line_fits_and_is_plain_ascii():
    line = cause.line_for("site_errors_long", NOW - timedelta(days=8))
    assert len(line) <= cause.LINE_MAX and line.isascii() and line.startswith("AHN's own checks have had server errors (5xx) from the site for over 7 days (since ")
    # D115: this sentence is now only for a 5xx period, an answer from the site or its edge, so it never blames AHN's path
    assert "network path" not in line and "answered with an error" in line
    assert cause.LONG_FAIL == timedelta(days=7)


@pytest.mark.parametrize("days, long_line", [(8, True), (7.5, True), (6.9, False), (6, False), (1, False)])
def test_a_period_older_than_7_days_that_nobody_else_saw_gets_the_long_sentence(days, long_line):
    """A 5xx period (server_error) keeps the D114 shape: only the sentence changes (the unanswered kinds are D115, in test_why_d115)."""
    found = site_errors(Conn(days, kind="server_error"))
    assert found["line"].startswith("AHN's own checks have had server errors (5xx) from the site for over 7 days") == long_line
    assert ("network path" in found["line"]) != long_line   # the shorter period keeps the D82 sentence
    assert (found["branch"], found["hint"]) == ("transient", "site_errors")


def test_only_the_line_changes():
    long, short = site_errors(Conn(8, kind="server_error")), site_errors(Conn(6, kind="server_error"))
    assert long["line"] != short["line"]
    assert {k: v for k, v in long.items() if k not in ("line", "window")} == {k: v for k, v in short.items() if k not in ("line", "window")}
    assert judgment(site_errors(Conn(8, confirmed=False, kind="server_error")))["confidence"] == "medium"
    # the confidence is what it was before (D82): high with someone else's evidence, medium without it
    assert site_errors(Conn(8, others=True, kind="server_error"))["confidence"] == "high"
    assert site_errors(Conn(8, kind="server_error"))["confidence"] == "medium"


def test_another_source_that_saw_the_host_failing_keeps_the_old_sentence():
    found = site_errors(Conn(8, others=True))
    assert found["line"] == cause.line_for("site_errors", NOW - timedelta(days=8))
    assert site_errors(Conn(8, others=True, confirmed=False))["line"].startswith("AHN's own checks have not reached the site (since ")


def test_an_ended_period_is_never_told_with_the_long_sentence():
    found = site_errors(Conn(9, ended=NOW - timedelta(days=1)))
    assert found["hint"] == "recovered" and "7 days" not in found["line"]
    refused = Conn(9, ended=NOW - timedelta(days=1))
    refused.row["ended_reason"] = "answered_refused"
    assert "7 days" not in site_errors(refused)["line"]


def test_the_long_line_has_a_korean_version():
    line = cause.line_for("site_errors_long", NOW - timedelta(days=8))
    korean = X.cause_line_ko(line)
    assert korean and HANGUL.search(korean) and "7일 넘게" in korean and "(" in korean and "부터)" in korean
    assert "서버 오류" in korean and "네트워크 경로" not in korean


# --- the Korean tables and the scan -----------------------------------------------------------------------------------------
def test_every_cause_line_the_lookup_can_say_has_a_korean_version():
    lines = {found["line"] for found in cause_objects()}
    assert len(lines) > 90
    for line in lines:
        korean = X.cause_line_ko(line)
        assert korean and HANGUL.search(korean) and "{" not in korean, line
        assert english_sentences(korean) == [], (line, korean)
    assert {key for key in cause.LINES} >= {"site_errors_long"}
    # no Korean version is left over after its line changed or was removed
    for _, (pattern, _korean) in enumerate(X._CAUSE_COMPILED):
        assert any(pattern.match(line) for line in lines), pattern.pattern


def test_the_cause_table_is_korean_hae_yo_without_a_particle_after_a_blank():
    english_only = {m for en, _ in X._CAUSE_PAIRS for m in re.findall(r"\{(\w+)\}", en)}
    assert english_only <= {"win", "age", "art", "what"}
    for english, korean in X._CAUSE_PAIRS:
        assert HANGUL.search(korean) and english.isascii(), english
        assert set(re.findall(r"\{(\w+)\}", english)) - {"art"} == set(re.findall(r"\{(\w+)\}", korean)), english
        assert X.forbidden_in(korean, "ko") == [], english
        assert re.findall(r"\} (?:이|가|을|를|은|는) ", korean) == [], english
        assert korean.endswith("."), korean
    assert len({en for en, _ in X._CAUSE_PAIRS}) == len(X._CAUSE_PAIRS)
    assert X.forbidden_in(X.CAUSE_NOTE_KO, "ko") == [] and HANGUL.search(X.CAUSE_NOTE_KO)


def test_the_window_age_and_refusal_words_come_out_in_korean():
    start = datetime(2026, 11, 1, 3, 4, tzinfo=timezone.utc)
    assert "(11-01 03:04 UTC부터)" in X.cause_line_ko(cause.line_for("site_errors", start))
    assert "(11-01 03:04 ~ 11-03 03:04 UTC)" in X.cause_line_ko(cause.line_for("recovered_site", start, start + timedelta(days=2)))
    assert "(11-01 03:04-05:04 UTC)" in X.cause_line_ko(cause.line_for("recovered_site", start, start + timedelta(hours=2)))
    assert "닿지 못했어요." in X.cause_line_ko(cause.line_for("site_errors")) and "(" not in X.cause_line_ko(cause.line_for("site_errors"))
    ages = {60: "방금", 600: "10분 전", 7200: "2시간 전", 86400: "1일 전", 3 * 86400: "3일 전"}
    for seconds, words in ages.items():
        assert words in X.cause_line_ko(cause.side_line(timedelta(seconds=seconds)))
    gate = cause.gate_line("bot_challenge", True, timedelta(hours=2), "aws_waf")
    assert "AWS WAF 봇 챌린지 거절" in X.cause_line_ko(gate) and "2시간 전" in X.cause_line_ko(gate)
    assert "HTTP 거절" in X.cause_line_ko(cause.gate_line("refused", False, timedelta(minutes=10)))
    assert "로그인 벽 거절" in X.cause_line_ko(cause.gate_line("login_wall", True, timedelta(days=2)))
    assert X.cause_line_ko("A line cause.py does not say.") is None


def test_every_next_check_sentence_has_a_korean_version():
    for code, text in check_texts():
        assert text in triage.CHECK_TEXT_KO and HANGUL.search(triage.CHECK_TEXT_KO[text]), code
    assert set(triage.CHECKS.values()) <= set(triage.CHECK_TEXT_KO)


def test_the_cause_and_check_blocks_come_out_korean_and_validate():
    found = cause.line_for("site_errors_long", NOW - timedelta(days=8))
    obj = cause._cause("transient", "site_errors", "medium", ["outage_episodes"], "site_errors_long", start=NOW - timedelta(days=8))
    assert obj["line"] == found
    check = fake_check("use_official_path", "use_official_path", cause=obj, summary_line=STATUS_ROWS["T1"],
                       next_checks=[{"code": "provider_clear", "text": triage.CHECKS["provider_clear"]}])
    ko = run(check=check, lang="ko")
    WhyResponse.model_validate(ko)
    assert ko["server"]["cause"]["line"] == X.cause_line_ko(found) and ko["server"]["cause"]["note"] == X.CAUSE_NOTE_KO
    assert ko["server"]["next_checks"][0]["text"] == triage.CHECK_TEXT_KO[triage.CHECKS["provider_clear"]]
    assert "server_note" not in ko
    layer = next(r for r in ko["what_happened"]["layers"] if r["template_id"] == "S-L1-CAUSE")
    assert X.cause_line_ko(found) in layer["text"] and english_sentences(layer["text"]) == []
    # the judgment fields are the lookup's own, and an English answer carries the lookup's text byte for byte
    en = run(check=check, lang="en")
    for key in ("recommendation", "next_action"):
        assert ko["server"][key] == en["server"][key]
    assert en["server"]["cause"] == obj and en["server"]["next_checks"] == check["next_checks"]
    for key in ("branch", "hint", "confidence", "basis"):
        assert ko["server"]["cause"][key] == obj[key]


def test_a_sentence_without_a_korean_version_stays_whole_english_and_is_marked():
    obj = cause._cause("transient", "site_errors", "medium", ["outage_episodes"], "site_errors", line="A cause line that is not in the table.")
    check = fake_check("use_official_path", "use_official_path", cause=obj,
                       next_checks=[{"code": "provider_clear", "text": "A check sentence that is not in the table."}])
    ko = run(check=check, lang="ko")
    assert ko["server_note"]["template_id"] == "S-G-SERVER-EN-PARTS" and HANGUL.search(ko["server_note"]["text"])
    assert ko["server"]["cause"]["line"] == obj["line"] and ko["server"]["next_checks"][0]["text"] == check["next_checks"][0]["text"]
    # no English clause inside a Korean sentence: the layer row is left out instead
    assert not [r for r in ko["what_happened"]["layers"] if r["template_id"] == "S-L1-CAUSE"]
    WhyResponse.model_validate(ko)
    assert "server_note" not in run(check=check, lang="en")


def test_the_korean_server_model_takes_the_korean_cause_and_the_lookups_model_does_not_change():
    from ahn.models import Cause
    assert issubclass(why_models.WhyCause, Cause) and why_models.WhyServer.model_fields["cause"].annotation.__args__[0] is why_models.WhyCause
    base = Cause.model_fields["line"].metadata
    assert any(getattr(m, "pattern", None) == r"^[ -~]+$" for m in base)


KO = list(ko_answers())


def test_the_scan_has_something_to_scan():
    assert len(KO) > 5000
    assert all(body["lang"] == "ko" for body in KO)
    assert any("cause" in body["server"] for body in KO) and any(body["server"].get("next_checks") for body in KO)


def test_a_korean_answer_has_no_english_sentence_that_is_not_marked():
    """The fixed form of the scan: in lang=ko answers (every combination of the earlier helpers, and lookups carrying every next_checks
    sentence, every cause line and every summary sentence) a person-readable string has no sentence without Hangul. The lookup block's own
    strings may be English only when the answer says so (server_note)."""
    bad, marked = [], set()
    for body in KO:
        note = "server_note" in body
        for path, template_id, text in text_leaves(body):
            for sentence in english_sentences(text):
                if note and path.startswith(".server."):
                    marked.add(sentence)
                else:
                    bad.append((path, template_id, sentence[:80]))
    assert bad == [], bad[:10]
    # what is marked is only the hand-made English of the earlier helpers' fixtures, never a sentence the lookup can say
    assert marked <= {"Fixture summary.", "The provider's status page reports no active issue."}, sorted(marked)[:10]


def _korean_strings(node, template_id=None):
    """(template id or table name, string) for every string with Hangul in the tables of explain_text (templates, sentence pairs, phrases)."""
    if isinstance(node, str):
        if HANGUL.search(node):
            yield template_id, node
    elif isinstance(node, dict):
        for key, value in node.items():
            yield from _korean_strings(value, key if isinstance(key, str) and key.startswith(("S-", "CHK-", "TL-")) else template_id)
    elif isinstance(node, (tuple, list, frozenset, set)):
        for value in node:
            yield from _korean_strings(value, template_id)


QUOTED = re.compile(r'"([^"]*)"|“([^”]*)”')
ALLOWED_ENGLISH_QUOTE: set[str] = set()   # D115: S-G-NOCTX no longer quotes the lookup's English sentence start, so no table quotes English


def test_the_quoted_english_in_the_korean_tables_is_one_template_s_quotation_of_the_lookup():
    """The scan lets a quoted text pass inside a Korean sentence because it is the document's or the page's own words. A Korean table of ours
    must not use that for an English sentence of its own: no Korean table quotes an English sentence (D115)."""
    found = {}
    for name in dir(X):
        value = getattr(X, name)
        if name.startswith("__") or not isinstance(value, (dict, tuple, list, frozenset)):
            continue
        for template_id, text in _korean_strings(value):
            for match in QUOTED.finditer(re.sub(r"\{\w+\}", " ", text)):
                quoted = match.group(1) or match.group(2) or ""
                if len(re.findall(r"[A-Za-z][A-Za-z'-]+", quoted)) >= 2:
                    found.setdefault(template_id or name, quoted)
    assert set(found) == ALLOWED_ENGLISH_QUOTE, found


def test_a_sample_of_the_korean_answers_validates():
    for body in KO[::40]:
        WhyResponse.model_validate(body)
    for body in (b for b in KO if "cause" in b["server"]):
        WhyResponse.model_validate(body)
        break


def test_the_scan_finds_an_english_sentence_when_there_is_one():
    assert english_sentences("Information only; follow next_action.")
    assert english_sentences("AHN 이 말해요. Not an access block.") == ["Not an access block."]
    assert english_sentences("AHN 이 말해요.Not an access block.") == ["Not an access block."]   # no space after the full stop
    assert english_sentences("Retry later.") == ["Retry later."]                                 # two words are a sentence
    assert english_sentences("`next_action` 과 https://a.example.com/x 와 robots.txt 와 ai-input=no 예요.") == []
    assert english_sentences("문서가 \"Do not scrape this site.\" 라고 적어요.") == []              # the document's words in a Korean sentence
    assert english_sentences("\"Our own sentence here.\"") == ["\"Our own sentence here.\""]      # our sentence in quotes is still ours
    assert english_sentences("사이트가 not an access 예요.") and english_sentences("업체 Google Cloud 가 말해요.") == []
    assert english_sentences("사이트가 응답해요.") == [] and english_sentences("AHN 의 HTTP 확인이에요.") == []
    mixed = dict(KO[0], what_happened={"layers": [{"template_id": "T", "text": "한국어예요. And an English one."}], "unknowns": []})
    assert [s for p, _, t in text_leaves(mixed) if p.startswith(".what_happened") for s in english_sentences(t)] == ["And an English one."]
