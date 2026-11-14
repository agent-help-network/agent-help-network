"""Task routes human pages (PR-F): GET /{lang}/routes and /{lang}/routes/{host}, behind AHN_TASK_ROUTES (default off).

No database and no network. The curated table is replaced with monkeypatch, so the tests do not depend on what the real table holds.
"""

from __future__ import annotations

import html
import os
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("AHN_DATABASE_URL", "postgresql://invalid/unused")
os.environ.setdefault("AHN_KEY_PEPPER", "x" * 32)

from ahn import main as ahn_main
from ahn import task_routes_api, task_routes_data
from ahn.main import LANG, create_app
from ahn.settings import Settings
from ahn.task_routes import RouteSourceKind, TaskKind
from ahn.task_routes_data import _route

ROOT = Path(__file__).resolve().parents[1]
TODAY = datetime.now(timezone.utc).date()
SINCE = date(2026, 11, 20)
HOST = "example-retailer.com"
OTHER = "example-bank.com"
PAGE = "https://www.example-retailer.com/help/cancel"
HELP = "https://www.example-retailer.com/help"


def days_ago(days: int) -> str:
    return (TODAY - timedelta(days=days)).isoformat()


def entry(task_kind="cancel_subscription", *, obstacle="wrong_channel", verified=10, routes=("web_form", "phone"), worked="phone",
          host=HOST, **more):
    return _route(task_kind, obstacle=obstacle, routes=routes, worked=worked, official_url=f"https://www.{host}/help/cancel",
                  source_url=f"https://www.{host}/help", verified_at=days_ago(verified), **more)


def client(task_routes_on: bool) -> TestClient:
    settings = Settings("postgresql://invalid/unused", "x" * 32, public_mode=True, reporting_enabled=True, task_routes=task_routes_on,
                        task_routes_since=SINCE if task_routes_on else None)
    return TestClient(create_app(settings))  # no `with`: the lifespan (database) never runs


@pytest.fixture
def on():
    return client(True)


def table(monkeypatch, data):
    monkeypatch.setattr(task_routes_data, "TASK_ROUTES_CURATED", data)


def three(monkeypatch):
    table(monkeypatch, {
        HOST: [entry(), entry("refund_request", obstacle="eligibility_unclear", routes=("web_form",), worked=None, verified=20)],
        OTHER: [entry("billing_dispute", obstacle="request_denied", routes=("regulator",), worked="regulator", verified=5, host=OTHER)],
    })


def text_of(response) -> str:
    return html.unescape(response.text)


def visible(response) -> str:
    """The page without the base template's own address echoes (canonical and alternate links, the other-language link), which
    carry the request path and query, escaped, on every page of the site."""
    body = re.sub(r"<link [^>]*>", "", response.text)
    return re.sub(r'<a class="language"[^>]*>', "", body)


# --- Switch off -------------------------------------------------------------------------------------------------------------

def test_switch_off_the_pages_do_not_exist(monkeypatch):
    three(monkeypatch)
    off = client(False)
    for lang in ("en", "ko"):
        assert off.get(f"/{lang}/routes").status_code == 404
        assert off.get(f"/{lang}/routes/{HOST}").status_code == 404


def test_switch_off_the_sitemap_does_not_list_the_pages():
    body = client(False).get("/sitemap.xml").text
    assert "/routes" not in body and "/en/platforms" in body


def test_switch_on_the_sitemap_lists_the_list_page_in_both_languages(on):
    body = on.get("/sitemap.xml").text
    assert "https://agenthelpnetwork.com/en/routes<" in body and "https://agenthelpnetwork.com/ko/routes<" in body
    assert "/routes/" not in body  # one page per host is not listed


def test_unknown_language_is_not_found(on):
    assert on.get("/fr/routes").status_code == 404
    assert on.get(f"/fr/routes/{HOST}").status_code == 404


# --- The list page ----------------------------------------------------------------------------------------------------------

def test_en_and_ko_pages_say_the_same_things(on, monkeypatch):
    three(monkeypatch)
    en, ko = on.get("/en/routes"), on.get("/ko/routes")
    assert en.status_code == ko.status_code == 200
    assert '<html lang="en">' in en.text and '<html lang="ko">' in ko.text
    assert "Of 3 records, 0 from AHN operator-side agents, 3 from the sites' own documents." in text_of(en)
    assert "기록 3건 중 운영 측 에이전트 0건, 공식 문서 3건." in text_of(ko)
    for body in (en.text, ko.text):
        assert body.count("<tr>") == 4  # head plus three records
        assert HOST in body and OTHER in body
    assert "cancel subscription" in en.text and "구독 해지" in ko.text and "구독 해지" not in en.text
    assert "wrong channel" in en.text and "잘못된 창구" in ko.text
    assert "Route that worked" in en.text and "통한 경로" in ko.text


def test_the_headline_is_computed_from_the_counts():
    text = LANG
    counts = {RouteSourceKind.operator_panel: 4, RouteSourceKind.curated_official: 2}
    assert task_routes_api.headline("en", text, 6, counts) == "Of 6 records, 4 from AHN operator-side agents, 2 from the sites' own documents."
    assert task_routes_api.headline("ko", text, 6, counts) == "기록 6건 중 운영 측 에이전트 4건, 공식 문서 2건."
    more = {**counts, RouteSourceKind.external_agent: 1}
    assert task_routes_api.headline("ko", text, 7, more).endswith("공식 문서 2건, 외부 에이전트 1건.")
    assert "agents reporting in" in task_routes_api.headline("en", text, 7, more)
    assert "community" not in task_routes_api.headline("en", text, 7, more)  # an absent source is not named
    assert "0 from AHN operator-side agents" in task_routes_api.headline("en", text, 2, {RouteSourceKind.curated_official: 2})
    for lang in ("en", "ko"):
        assert "No task route records yet" in task_routes_api.headline("en", text, 0, {}) and text[lang]["routes_head_empty"]


def test_the_page_headline_follows_the_table(on, monkeypatch):
    table(monkeypatch, {HOST: [entry()]})
    assert "Of 1 record," in text_of(on.get("/en/routes"))
    three(monkeypatch)
    assert "Of 3 records" in text_of(on.get("/en/routes"))


def test_rows_are_newest_first_with_no_hit_count_order(on, monkeypatch):
    table(monkeypatch, {
        HOST: [entry("cancel_subscription", verified=80), entry("refund_request", obstacle="eligibility_unclear", routes=(), worked=None, verified=2)],
        OTHER: [entry("return_or_exchange", obstacle="deadline_or_window", routes=("web_form",), worked=None, verified=40, host=OTHER)],
    })
    body = on.get("/en/routes").text
    order = [body.index(word) for word in ("refund request", "return or exchange", "cancel subscription")]
    assert order == sorted(order)
    for forbidden in ("most viewed", "views", "lookups"):
        assert forbidden not in body.lower()


def test_empty_table_shows_the_empty_state_and_no_table(on, monkeypatch):
    table(monkeypatch, {})
    for lang, words in (("en", "A missing record does not mean there is no route."), ("ko", "기록이 없다고 경로가 없는 것은 아니에요.")):
        response = on.get(f"/{lang}/routes")
        assert response.status_code == 200
        assert "<table" not in response.text and words in response.text
    assert "No task route records yet" in on.get("/en/routes").text


def test_old_records_are_left_out(on, monkeypatch):
    table(monkeypatch, {HOST: [entry(verified=task_routes_data.TASK_ROUTE_MAX_AGE_DAYS + 30)]})
    assert "<table" not in on.get("/en/routes").text


def test_sensitive_line_says_case_record_not_advice(on, monkeypatch):
    table(monkeypatch, {HOST: [entry("cancel_subscription"), entry("billing_dispute", obstacle="request_denied", routes=("regulator",), worked="regulator")]})
    en, ko = on.get("/en/routes"), on.get("/ko/routes")
    assert en.text.count("Case record, not advice.") == 1  # only the billing dispute line
    assert ko.text.count("사례 기록이며 조언이 아님</span>") == 1
    table(monkeypatch, {HOST: [entry("cancel_subscription")]})
    assert "Case record, not advice" not in on.get("/en/routes").text
    assert "조언이 아님" not in on.get("/ko/routes").text


def test_server_values_come_from_the_server_not_the_entry(on, monkeypatch):
    # A table entry that claims otherwise is ignored: sensitive, source_kind and period are decided by server_route.
    item = entry("cancel_subscription")
    item.update({"sensitive": True, "source_kind": "operator_panel", "period": "1999-Q1"})
    table(monkeypatch, {HOST: [item]})
    body = text_of(on.get("/en/routes"))
    assert "Case record, not advice" not in body and "1999-Q1" not in body
    assert "<td>AHN operator-side agent" not in body and "AHN operator-side agent<" not in body.split("<tbody>")[1]
    assert "site's own documents" in body.split("<tbody>")[1]


def test_external_links_are_nofollow_noopener(on, monkeypatch):
    three(monkeypatch)
    for path in ("/en/routes", "/ko/routes", f"/en/routes/{HOST}"):
        body = on.get(path).text
        external = re.findall(r"<a [^>]*href=\"https://[^\"]*\"[^>]*>", body)
        assert external
        assert all('rel="nofollow noopener"' in link for link in external if "agenthelpnetwork.com" not in link)
        assert HELP in body and PAGE in body
    # Internal links carry no rel and point inside the site.
    assert f'href="/en/routes/{HOST}"' in on.get("/en/routes").text


def test_autoescape(on, monkeypatch):
    three(monkeypatch)
    real = task_routes_api.row_for

    def hostile(record, lang, per_site):
        row = real(record, lang, per_site)
        row["task"] = "<b>x</b>"
        row["source_url"] = 'https://a.example/"><script>alert(1)</script>'
        return row

    monkeypatch.setattr(task_routes_api, "row_for", hostile)
    body = on.get("/en/routes").text
    assert "<b>x</b>" not in body and "&lt;b&gt;x&lt;/b&gt;" in body
    assert "<script>alert(1)" not in body
    assert ahn_main.templates.env.autoescape("routes.html") and ahn_main.templates.env.autoescape("routes_host.html")


def test_page_is_a_reading_of_the_table_only(on, monkeypatch):
    # No verdict, no database, no site: the app here has an unusable database URL and check() must not run.
    from ahn import service

    def refuse(*args, **kwargs):
        raise AssertionError("check() was called by a task routes page")

    monkeypatch.setattr(service, "check", refuse)
    three(monkeypatch)
    for path in ("/en/routes", "/ko/routes", f"/en/routes/{HOST}", "/en/routes?task_kind=refund_request"):
        assert on.get(path).status_code == 200
    source = (ROOT / "ahn" / "task_routes_api.py").read_text(encoding="utf-8")
    assert not re.search(r"import[^\n]*\bcheck\b|service\.check|check_incident", source)


# --- Filters ----------------------------------------------------------------------------------------------------------------

def rows(response) -> int:
    return max(response.text.count("<tr>") - 1, 0)


def test_filters_narrow_by_closed_values(on, monkeypatch):
    three(monkeypatch)
    assert rows(on.get("/en/routes", params={"task_kind": "refund_request"})) == 1
    assert rows(on.get("/en/routes", params={"site": OTHER})) == 1
    assert rows(on.get("/en/routes", params={"source_kind": "curated_official"})) == 3
    assert rows(on.get("/en/routes", params={"source_kind": "operator_panel"})) == 0
    assert rows(on.get("/en/routes", params={"outcome": "not_applicable"})) == 3
    assert rows(on.get("/en/routes", params={"outcome": "resolved"})) == 0
    assert "No task route record matches" in on.get("/en/routes", params={"outcome": "resolved"}).text
    selected = on.get("/en/routes", params={"task_kind": "refund_request"}).text
    assert '<option value="refund_request" selected>' in selected
    assert "Of 3 records" in selected  # the headline counts all records, not the filtered ones


@pytest.mark.parametrize("query", [
    "task_kind=nonsense", "task_kind=", "task_kind=REFUND_REQUEST", "task_kind=refund_request%00", "outcome=%3Cscript%3Ealert(1)%3C/script%3E",
    "site=evil.example", "site=example-retailer.com.evil.example", "source_kind=1;DROP", "unknown=1", "task_kind[]=refund_request",
    "task_kind=%E2%80%AE", "site=" + "a" * 5000,
])
def test_values_outside_the_list_are_ignored_and_not_echoed(on, monkeypatch, query):
    three(monkeypatch)
    plain = on.get("/en/routes")
    response = on.get(f"/en/routes?{query}")
    assert response.status_code == 200
    assert rows(response) == rows(plain) == 3
    shown = visible(response)
    assert "<script>alert" not in response.text and "alert(1)" not in shown
    assert "evil.example" not in shown and "DROP" not in shown and "aaaaaaaaaa" not in shown


def test_the_first_value_of_a_repeated_filter_is_read(on, monkeypatch):
    three(monkeypatch)
    assert rows(on.get("/en/routes?task_kind=refund_request&task_kind=cancel_subscription")) == 1
    assert rows(on.get("/en/routes?task_kind=nonsense&task_kind=refund_request")) == 3  # the first one is not on the list


def test_every_task_kind_is_a_filter_choice(on, monkeypatch):
    three(monkeypatch)
    body = on.get("/en/routes").text
    for kind in TaskKind:
        assert f'<option value="{kind.value}"' in body


# --- The host page ----------------------------------------------------------------------------------------------------------

def test_host_page_shows_the_site_records_in_both_languages(on, monkeypatch):
    three(monkeypatch)
    en, ko = on.get(f"/en/routes/{HOST}"), on.get(f"/ko/routes/{HOST}")
    assert en.status_code == ko.status_code == 200
    assert rows(en) == rows(ko) == 2
    assert OTHER not in en.text and OTHER not in ko.text
    assert "Of 2 records, 0 from AHN operator-side agents, 2 from the sites' own documents." in text_of(en)
    assert "기록 2건 중 운영 측 에이전트 0건, 공식 문서 2건." in text_of(ko)
    assert f'<h1 class="host-title">{HOST}</h1>' in en.text
    assert f"/v1/task-routes?site_host={HOST}" in en.text
    assert f'href="/en/routes"' in en.text and f'href="/ko/routes"' in ko.text


def test_host_page_follows_the_same_host_rules_as_the_api(on, monkeypatch):
    three(monkeypatch)
    assert rows(on.get(f"/en/routes/www.{HOST}")) == 2  # a parent in the table answers
    assert rows(on.get(f"/en/routes/Support.{HOST.upper()}")) == 2  # case and a generic label
    empty = on.get("/en/routes/unrecorded-site.com")
    assert empty.status_code == 200 and "<table" not in empty.text and "does not mean there is no route" in empty.text
    assert rows(on.get(f"/en/routes/springfield.{HOST}")) == 0  # a tenant subdomain gets the empty answer, as in the API


def test_host_page_tenant_rule_matches_the_api(on, monkeypatch):
    three(monkeypatch)
    for host in (f"www.{HOST}", "classlink-tenant.classlink.com", "x.github.io"):
        api = on.get("/v1/task-routes", params={"site_host": host}).json()["counts"]["records"]
        page = on.get(f"/en/routes/{host}")
        assert page.status_code == 200
        assert rows(page) == api


@pytest.mark.parametrize("bad", ["%3Cscript%3Ealert(1)%3C/script%3E", "localhost", "10.0.0.1", "a..b", "x_y.example", "bad%20host.example", "a" * 300 + ".com"])
def test_host_page_refuses_a_bad_host_and_never_echoes_it(on, bad):
    response = on.get(f"/en/routes/{bad}")
    assert response.status_code == 404
    shown = visible(response)
    assert "<script>" not in response.text and "alert" not in shown
    assert "10.0.0.1" not in shown and "aaaaaaaaaa" not in shown


def test_host_page_filters_ignore_values_outside_the_list(on, monkeypatch):
    three(monkeypatch)
    assert rows(on.get(f"/en/routes/{HOST}?task_kind=refund_request")) == 1
    assert rows(on.get(f"/en/routes/{HOST}?task_kind=nonsense&site={OTHER}")) == 2  # the site filter does not exist here
    assert "nonsense" not in visible(on.get(f"/en/routes/{HOST}?task_kind=nonsense"))


def test_host_page_with_more_than_the_answer_limit_says_so(on, monkeypatch):
    kinds = [kind.value for kind in TaskKind if kind.value not in {"other", "claim_unclaimed_property"}]
    many = [entry(kind, obstacle="wrong_channel", routes=(), worked=None, verified=3 + index) for index, kind in enumerate(kinds)]
    assert len(many) > task_routes_api.lookup.__globals__["TASK_ROUTES_MAX"]
    table(monkeypatch, {HOST: many})
    response = on.get(f"/en/routes/{HOST}")
    assert rows(response) == 20
    assert f"Showing the 20 newest of {len(many)}." in response.text
    assert f"Of {len(many)} records" in response.text


# --- Words ------------------------------------------------------------------------------------------------------------------

def test_every_closed_value_has_a_korean_word_and_a_distinct_group():
    for group, enum in task_routes_api.LABEL_ENUMS.items():
        for item in enum:
            assert task_routes_api.label(group, item, "ko") != item.value.replace("_", " "), (group, item.value)
            assert task_routes_api.label(group, item, "en")
        assert set(task_routes_api.KO_LABELS[group]) == {item.value for item in enum}, group
    assert set(task_routes_api.KO_LABELS) == set(task_routes_api.LABEL_ENUMS)


def test_the_page_words_exist_in_both_languages():
    en = {key for key in LANG["en"] if key.startswith("routes_")}
    ko = {key for key in LANG["ko"] if key.startswith("routes_")}
    assert en == ko and len(en) >= 30
    template_keys = set()
    for name in ("routes.html", "routes_host.html"):
        template_keys |= set(re.findall(r"\bt\.(routes_\w+)", (ROOT / "ahn" / "templates" / name).read_text(encoding="utf-8")))
    template_keys |= {"routes_head", "routes_part", "routes_head_empty"}
    assert template_keys <= en
    for key in ("routes_head", "routes_part", "routes_showing", "routes_newest"):
        for lang in ("en", "ko"):
            LANG[lang][key].format(total=1, s="", parts="x", n=1, label="y", shown=1)


def test_the_pages_carry_no_judgement_words(on, monkeypatch):
    three(monkeypatch)
    body = on.get("/en/routes").text.split("<main", 1)[1].split("</main>", 1)[0].lower()
    for word in ("recommended", "guarantee", "trusted", "safe to", "you should", "approved", "bypass"):
        assert word not in body.replace("not guarantees", "")
