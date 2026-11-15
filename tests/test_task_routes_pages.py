"""Task routes human pages (PR-F): GET /{lang}/routes and /{lang}/routes/{host}, behind AHN_TASK_ROUTES (default off).

No database and no network. The curated table is replaced with monkeypatch, so the tests do not depend on what the real table holds.
"""

from __future__ import annotations

import html
import logging
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
NO_ROUTE = {"en": "A missing record does not mean there is no route.", "ko": "기록이 없다고 경로가 없는 것은 아니에요."}


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
    assert task_routes_api.headline("en", text, 0, {}) == "No task route records yet. A missing record does not mean there is no route."
    assert task_routes_api.headline("ko", text, 0, {}) == "아직 태스크 경로 기록이 없어요. 기록이 없다고 경로가 없는 것은 아니에요."


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
    for path in ("/en/routes", "/ko/routes", f"/en/routes/{HOST}", "/en/routes?task_kind=refund_request", f"/en/routes?site={HOST}",
                 "/en/routes?site=unrecorded-site.com", "/en/routes?site=example", "/en/routes?site=%3Cb%3E", "/en/routes?site=https://www.unrecorded-site.com/a"):
        assert on.get(path).status_code == 200
    source = (ROOT / "ahn" / "task_routes_api.py").read_text(encoding="utf-8")
    assert not re.search(r"import[^\n]*\bcheck\b|service\.check|check_incident", source)


# --- Filters ----------------------------------------------------------------------------------------------------------------

def rows(response) -> int:
    return max(response.text.count("<tr>") - 1, 0)


def test_filters_narrow_by_closed_values(on, monkeypatch):
    three(monkeypatch)
    assert rows(on.get("/en/routes", params={"task_kind": "refund_request"})) == 1
    moved = on.get("/en/routes", params={"site": OTHER, "task_kind": "billing_dispute"}, follow_redirects=False)
    assert moved.status_code == 302 and moved.headers["location"] == f"/en/routes/{OTHER}?task_kind=billing_dispute"
    assert rows(on.get("/en/routes", params={"site": OTHER})) == 1  # followed to the site's own page
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
    "source_kind=1;DROP", "unknown=1", "task_kind[]=refund_request", "task_kind=%E2%80%AE", "site=",
])
def test_values_outside_the_list_are_ignored_and_not_echoed(on, monkeypatch, query):
    three(monkeypatch)
    plain = on.get("/en/routes")
    response = on.get(f"/en/routes?{query}")
    assert response.status_code == 200
    assert rows(response) == rows(plain) == 3
    assert "<script>alert" not in response.text and "alert(1)" not in response.text
    assert "DROP" not in response.text and "aaaaaaaaaa" not in response.text


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
    for page in (en, ko):  # the other site's records are not on the page; its name is only one suggestion of the site box
        assert OTHER not in re.sub(r"<datalist.*?</datalist>", "", page.text, flags=re.S)
    assert "Of 2 records, 0 from AHN operator-side agents, 2 from the sites' own documents." in text_of(en)
    assert "기록 2건 중 운영 측 에이전트 0건, 공식 문서 2건." in text_of(ko)
    assert f'<h1 class="host-title">{HOST}</h1>' in en.text
    assert f"/v1/task-routes?site_host={HOST}" in en.text
    assert f'href="/en/routes"' in en.text and f'href="/ko/routes"' in ko.text


@pytest.mark.parametrize("variant", [f"www.{HOST}", HOST.upper(), f"Support.{HOST.upper()}", f"{HOST}.", f"shop.{HOST}"])
def test_host_page_sends_other_spellings_to_the_apex_key(on, monkeypatch, variant):
    three(monkeypatch)
    moved = on.get(f"/en/routes/{variant}", follow_redirects=False)
    assert moved.status_code == 301
    assert moved.headers["location"] == f"/en/routes/{HOST}"
    final = on.get(f"/ko/routes/{variant}", follow_redirects=True)
    assert final.status_code == 200 and str(final.url).endswith(f"/ko/routes/{HOST}") and rows(final) == 2
    # The apex is the only name on the page: title, heading, canonical and alternate links, form action.
    body = final.text
    assert variant.lower().rstrip(".") not in body.replace(HOST, "")
    assert f"<title>{HOST} · " in body and f'<h1 class="host-title">{HOST}</h1>' in body
    assert f'rel="canonical" href="https://agenthelpnetwork.com/ko/routes/{HOST}"' in body
    assert f'hreflang="en" href="https://agenthelpnetwork.com/en/routes/{HOST}"' in body


def test_host_page_redirect_keeps_only_valid_filters(on, monkeypatch):
    three(monkeypatch)
    moved = on.get(f"/en/routes/www.{HOST}?task_kind=refund_request&outcome=zzz&evil=1&source_kind=curated_official", follow_redirects=False)
    assert moved.status_code == 301
    assert moved.headers["location"] == f"/en/routes/{HOST}?task_kind=refund_request&source_kind=curated_official"
    assert on.get(f"/en/routes/www.{HOST}?outcome=zzz", follow_redirects=False).headers["location"] == f"/en/routes/{HOST}"


@pytest.mark.parametrize("name", ["call-now-555-0100-refund-desk.com", "unrecorded-site.com", f"springfield.{HOST}", f"{HOST}.evil.example",
                                  "x.github.io", "com"])
def test_host_page_is_not_found_for_a_site_without_an_entry_and_echoes_nothing(on, monkeypatch, name):
    three(monkeypatch)
    for lang in ("en", "ko"):
        response = on.get(f"/{lang}/routes/{name}", follow_redirects=False)
        assert response.status_code == 404
        assert name not in response.text and name not in " ".join(response.headers.values())
        assert name.split(".")[0] not in response.text


def test_host_page_follows_the_same_walk_as_the_api(on, monkeypatch):
    three(monkeypatch)
    for host in (HOST, f"www.{HOST}", f"support.{HOST}", f"www.support.{HOST}", f"springfield.{HOST}", "classlink-tenant.classlink.com", "x.github.io",
                 "unrecorded-site.com", OTHER, f"www.{OTHER}"):
        api = on.get("/v1/task-routes", params={"site_host": host}).json()["counts"]["records"]
        page = on.get(f"/en/routes/{host}", follow_redirects=True)
        assert (page.status_code == 200) == (api > 0), host
        if api:
            assert rows(page) == api


def test_table_key_is_the_one_walk_behind_lookup(monkeypatch):
    from ahn.task_routes import lookup, table_key

    three(monkeypatch)
    assert table_key(HOST) == table_key(f"www.{HOST}") == table_key(f"www.support.{HOST}") == HOST
    assert table_key("unrecorded-site.com") is None and table_key(f"springfield.{HOST}") is None and table_key("com") is None
    assert lookup(f"www.{HOST}").site_host == f"www.{HOST}" and lookup(f"www.{HOST}").counts.records == 2


@pytest.mark.parametrize("bad", ["%3Cscript%3Ealert(1)%3C/script%3E", "localhost", "10.0.0.1", "a..b", "x_y.example", "bad%20host.example", "a" * 300 + ".com"])
def test_host_page_refuses_a_bad_host_and_never_echoes_it(on, bad):
    response = on.get(f"/en/routes/{bad}", follow_redirects=False)
    assert response.status_code == 404
    assert "<script>" not in response.text and "alert" not in response.text
    assert "10.0.0.1" not in response.text and "aaaaaaaaaa" not in response.text and "localhost" not in response.text


def test_host_page_filters_ignore_values_outside_the_list(on, monkeypatch):
    three(monkeypatch)
    assert rows(on.get(f"/en/routes/{HOST}?task_kind=refund_request")) == 1
    assert rows(on.get(f"/en/routes/{HOST}?task_kind=nonsense&site={OTHER}")) == 2  # the site filter does not exist here
    assert "nonsense" not in on.get(f"/en/routes/{HOST}?task_kind=nonsense").text


# --- The other-language link and the request metrics ---------------------------------------------------------------------

def language_link(response, other: str) -> str:
    return html.unescape(re.search(rf'<a class="language" href="([^"]*)" lang="{other}"', response.text).group(1))


def test_the_language_link_carries_only_validated_filters(on, monkeypatch):
    three(monkeypatch)
    assert language_link(on.get("/en/routes"), "ko") == "/ko/routes"
    assert language_link(on.get("/en/routes?evil=1&task_kind=%3Cb%3E&outcome=zzz"), "ko") == "/ko/routes"
    assert language_link(on.get(f"/en/routes?task_kind=refund_request&evil=1&site={HOST}"), "ko") == f"/ko/routes/{HOST}?task_kind=refund_request"  # followed to the site page
    assert language_link(on.get("/en/routes?task_kind=refund_request&evil=1&site=unrecorded-site.com"), "ko") == "/ko/routes?site=unrecorded-site.com&task_kind=refund_request"
    assert language_link(on.get("/en/routes?task_kind=refund_request&site=%3Cb%3Ex"), "ko") == "/ko/routes?task_kind=refund_request"  # a typed value that is not a hostname is not carried
    assert language_link(on.get(f"/ko/routes/{HOST}?source_kind=curated_official&evil=1&site={OTHER}"), "en") == f"/en/routes/{HOST}?source_kind=curated_official"
    assert language_link(on.get(f"/en/routes/{HOST}?q=<x>"), "ko") == f"/ko/routes/{HOST}"
    body = on.get("/en/routes?evil=zzqq&task_kind=%3Cb%3Ex").text
    assert "zzqq" not in body and "%3Cb%3Ex" not in body and "<b>x" not in body


def test_other_pages_keep_echoing_their_own_query_in_the_language_link(on):
    assert language_link(on.get("/en/platforms?x=1"), "ko") == "/ko/platforms?x=1"
    assert language_link(on.get("/en/platforms"), "ko") == "/ko/platforms"


def test_request_metrics_count_the_route_template_and_never_the_host(monkeypatch):
    three(monkeypatch)
    saved = []

    class Conn:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def execute(self, sql, params=()):
            saved.append(tuple(params))

    monkeypatch.setattr(ahn_main, "connect", lambda settings: Conn())
    settings = Settings("postgresql://invalid/unused", "x" * 32, public_mode=True, reporting_enabled=True, task_routes=True,
                        task_routes_since=SINCE, collect_request_metrics=True)
    metrics = TestClient(create_app(settings))
    assert metrics.get("/en/routes").status_code == 200
    assert metrics.get(f"/ko/routes/{HOST}").status_code == 200
    assert metrics.get("/en/routes/unrecorded-site.com").status_code == 404
    codes = [params[1] for params in saved if len(params) > 8]  # traffic_attribution_daily rows: (day, route_code, ...)
    assert codes.count("/{lang}/routes") == 1
    assert codes.count("/{lang}/routes/{host}") == 2  # the 200 and the 404 are both counted under the template
    assert "other" not in codes
    flat = " ".join(str(value) for params in saved for value in params)
    assert HOST not in flat and "unrecorded-site" not in flat


def test_host_page_with_more_than_the_answer_limit_says_so(on, monkeypatch):
    kinds = [kind.value for kind in TaskKind if kind.value not in {"other", "claim_unclaimed_property"}]
    many = [entry(kind, obstacle="wrong_channel", routes=(), worked=None, verified=3 + index) for index, kind in enumerate(kinds)]
    assert len(many) > task_routes_api.lookup.__globals__["TASK_ROUTES_MAX"]
    table(monkeypatch, {HOST: many})
    response = on.get(f"/en/routes/{HOST}")
    assert rows(response) == 20
    assert f"Showing the 20 newest of {len(many)}." in response.text
    assert f"Of {len(many)} records" in response.text


# --- The site box ------------------------------------------------------------------------------------------------------------

NONE_SENTENCE = "No task route record for unrecorded-site.com yet."


def site_get(client, value, lang="en", follow=False, **more):
    return client.get(f"/{lang}/routes", params={"site": value, **more}, follow_redirects=follow)


@pytest.mark.parametrize("typed", ["IKEA.com", " ikea.com ", "https://www.ikea.com/us/en/returns/?x=1#y", "ikea.com:443", "ikea.com.",
                                   "http://ikea.com", "HTTPS://WWW.IKEA.COM", "ikea.com/", "ikea.com?x=1"])
def test_a_typed_or_pasted_site_goes_to_its_page(on, monkeypatch, typed):
    table(monkeypatch, {"ikea.com": [entry(host="ikea.com")]})
    moved = site_get(on, typed)
    assert moved.status_code == 302
    assert moved.headers["location"] == "/en/routes/ikea.com"
    assert moved.headers["x-robots-tag"] == "noindex" and moved.headers["cache-control"] == "no-store"
    final = site_get(on, typed, lang="ko", follow=True)
    assert final.status_code == 200 and str(final.url).endswith("/ko/routes/ikea.com") and rows(final) == 1


@pytest.mark.parametrize("typed", ["user:pw@ikea.com", "mailto:a@b.com", "javascript:alert(1)", "ikea com", "127.0.0.1", "localhost",
                                   "ftp://ikea.com/x", "https://", "x_y.example", "a" * 2001, "%3Cb%3E", "<b>x</b>", "ikea.com\u0007"])
def test_a_value_that_is_not_a_hostname_gets_a_fixed_sentence_and_is_never_repeated(on, monkeypatch, typed):
    table(monkeypatch, {"ikea.com": [entry(host="ikea.com")]})
    for lang, sentence in (("en", "That is not a public hostname."), ("ko", "공개 호스트명이 아니에요.")):
        response = site_get(on, typed, lang=lang)
        assert response.status_code == 200 and sentence in text_of(response)
        assert rows(response) == 0 and "<table" not in response.text
        assert response.headers["x-robots-tag"] == "noindex" and response.headers["cache-control"] == "no-store"
        assert "location" not in response.headers
        shown = text_of(response)
        for piece in {typed, typed.strip(), typed[:12], "alert", "pw@", "a" * 12}:
            if piece.strip() and piece != "https://":  # (every page links to https://agenthelpnetwork.com)
                assert piece not in shown, piece
        # the box stays empty: it holds no value attribute
        assert 'name="site"' in response.text and not re.search(r'name="site"[^>]*\svalue=', response.text)


def test_a_pasted_address_leaves_no_path_or_query_in_the_answer_or_the_log(on, monkeypatch):
    from ahn import request_log

    table(monkeypatch, {"ikea.com": [entry(host="ikea.com")]})
    lines = []

    class Capture(logging.Handler):
        def emit(self, record):
            lines.append(record.getMessage())

    handler = Capture(logging.INFO)
    request_log.logger.addHandler(handler)
    try:
        hit = site_get(on, "https://www.ikea.com/us/en/orders/9f3a-secret/?token=abc#frag")
        unrecorded_url = "https://www.unrecorded-site.com/us/en/orders/9f3a-secret/?token=abc#frag"
        unrecorded = site_get(on, unrecorded_url)
        final = site_get(on, unrecorded_url, follow=True)
    finally:
        request_log.logger.removeHandler(handler)
    assert hit.status_code == 302 and unrecorded.status_code == 302 and final.status_code == 200
    for response in (hit, unrecorded, final):
        assert not re.search(r"secret|token|frag|orders|/us/en", text_of(response) + str(response.headers.get("location")))
    assert unrecorded.headers["location"] == "/en/routes?site=unrecorded-site.com"  # the hostname alone: nothing else stays in the address bar
    assert lines and all(re.search(r"path=/en/routes(/\{host\})? ", line) for line in lines), lines
    assert not any(re.search(r"secret|token|frag|ikea|unrecorded|orders", line) for line in lines)


def test_the_redirect_keeps_only_valid_filters(on, monkeypatch):
    three(monkeypatch)
    moved = site_get(on, HOST, task_kind="refund_request", outcome="<script>", source_kind="curated_official", evil="1")
    assert moved.status_code == 302
    assert moved.headers["location"] == f"/en/routes/{HOST}?task_kind=refund_request&source_kind=curated_official"
    assert site_get(on, HOST, outcome="zzz").headers["location"] == f"/en/routes/{HOST}"
    assert site_get(on, f"www.{HOST}").headers["location"] == f"/en/routes/{HOST}"  # the key, whatever spelling was typed
    assert site_get(on, f"shop.{HOST}").headers["location"] == f"/en/routes/{HOST}"


def test_a_valid_site_without_records_says_so_and_links_to_its_profile(on, monkeypatch):
    three(monkeypatch)
    for lang, none, profile in (("en", NONE_SENTENCE, "Host profile for unrecorded-site.com"),
                                ("ko", "unrecorded-site.com 의 태스크 경로 기록은 아직 없어요.", "unrecorded-site.com 호스트 프로필")):
        response = site_get(on, "UNRECORDED-site.com", lang=lang, follow=True)
        text = text_of(response)
        assert response.status_code == 200 and none in text and NO_ROUTE[lang] in text and profile in text
        assert rows(response) == 0 and "<table" not in response.text
        assert f'rel="nofollow" href="/{lang}/search?site_host=unrecorded-site.com"' in response.text
        assert 'value="unrecorded-site.com"' in response.text  # the box shows the hostname that was read
        assert response.headers["x-robots-tag"] == "noindex" and response.headers["cache-control"] == "no-store"
    assert "Records cover 2 sites so far." in text_of(site_get(on, "unrecorded-site.com"))
    table(monkeypatch, {HOST: [entry()]})
    assert "Records cover 1 site so far." in text_of(site_get(on, "unrecorded-site.com"))


@pytest.mark.parametrize("typed", ["springfield.classlink.com", f"springfield.{HOST}", "x.github.io", "www.co.uk", "co.uk"])
def test_a_tenant_or_suffix_name_is_not_named_and_gets_no_profile_link(on, monkeypatch, typed):
    three(monkeypatch)
    response = site_get(on, typed)
    assert response.status_code == 200
    text = text_of(response)
    assert "No task route record for that site yet." in text and NO_ROUTE["en"] in text
    assert typed not in text and typed not in response.text
    assert "springfield" not in text and "classlink" not in text and "github" not in text
    assert "/search?site_host=" not in response.text and 'name="site"' in response.text and not re.search(r'name="site"[^>]*\svalue=', response.text)
    assert rows(response) == 0


def test_a_one_word_name_finds_the_recorded_site_by_the_first_label_of_its_domain(on, monkeypatch):
    table(monkeypatch, {"ikea.com": [entry(host="ikea.com")], "acme.co.uk": [entry(host="acme.co.uk")],
                        "acme-bank.com": [entry(host="acme-bank.com")], "acme.org": [entry(host="acme.org")]})
    one = site_get(on, "IKEA")
    assert one.status_code == 302 and one.headers["location"] == "/en/routes/ikea.com"
    several = site_get(on, "ACME")
    assert several.status_code == 200 and "More than one site matches." in text_of(several)
    assert '<a href="/en/routes/acme.co.uk">' in several.text and '<a href="/en/routes/acme.org">' in several.text
    assert "acme-bank.com</a>" not in several.text and "<table" not in several.text
    nothing = site_get(on, "nothing")
    assert nothing.status_code == 200 and "No recorded site matches that name." in text_of(nothing) and NO_ROUTE["en"] in text_of(nothing)
    assert "nothing" not in text_of(nothing) and "<table" not in nothing.text
    assert site_get(on, "nothing", lang="ko").status_code == 200


def test_a_site_box_with_no_value_is_the_plain_list(on, monkeypatch):
    three(monkeypatch)
    for query in ("site=", "site=%20%20", ""):
        response = on.get(f"/en/routes?{query}")
        assert response.status_code == 200 and rows(response) == 3 and "x-robots-tag" not in response.headers
    assert on.get(f"/en/routes?site={HOST}&site=nothing", follow_redirects=False).status_code == 302  # the first value is read


def test_the_site_box_is_a_text_input_with_a_list_of_the_recorded_hosts(on, monkeypatch):
    three(monkeypatch)
    body = on.get("/en/routes").text
    assert '<select id="f-site"' not in body and 'id="f-site" name="site" type="text"' in body and 'list="route-sites"' in body
    assert re.findall(r'<datalist id="route-sites">(.*?)</datalist>', body)[0].count("<option") == 2
    assert f'<option value="{HOST}">' in body and f'<option value="{OTHER}">' in body
    assert "Hostname only, such as ikea.com." in body and 'maxlength="2000"' in body
    assert "/static/routes.js?v=1" in body
    monkeypatch.setattr(task_routes_api, "ROUTES_DATALIST_MAX", 1)  # more sites than the list may hold: no list, same behaviour
    big = on.get("/en/routes").text
    assert "<datalist" not in big and "list=" not in big and 'name="site" type="text"' in big
    assert on.get(f"/en/routes/{HOST}").text.count("<datalist") == 0


def test_the_site_page_has_a_box_to_find_another_site(on, monkeypatch):
    three(monkeypatch)
    en, ko = on.get(f"/en/routes/{HOST}"), on.get(f"/ko/routes/{HOST}")
    assert 'action="/en/routes"' in en.text and "Find another site" in en.text and "다른 사이트 찾기" in ko.text
    assert 'name="site"' in en.text and '<select id="f-site"' not in en.text
    assert '<datalist id="route-sites">' in en.text


def test_a_task_past_the_newest_twenty_is_found_by_choosing_it(on, monkeypatch):
    kinds = [kind.value for kind in TaskKind if kind.value not in {"other", "claim_unclaimed_property"}]
    many = [entry(kind, obstacle="wrong_channel", routes=(), worked=None, verified=3 + index) for index, kind in enumerate(kinds)]
    table(monkeypatch, {HOST: many})
    oldest = kinds[-1]
    assert rows(on.get(f"/en/routes/{HOST}")) == 20
    picked = on.get(f"/en/routes/{HOST}?task_kind={oldest}")
    assert rows(picked) == 1 and f'<option value="{oldest}" selected>' in picked.text
    assert f"Of {len(many)} records" in picked.text  # the heading counts the whole site
    assert "Showing the" not in picked.text  # the filtered answer is complete


def test_a_task_without_records_on_a_site_with_records_says_so(on, monkeypatch):
    three(monkeypatch)
    for lang, sentence, link in (("en", "No record for this task on this site yet; 2 records for other tasks.", "Show every record for this site"),
                                 ("ko", "이 사이트에는 이 업무의 기록이 아직 없고, 다른 업무 기록이 2건 있어요.", "이 사이트의 기록 전부 보기")):
        response = on.get(f"/{lang}/routes/{HOST}?task_kind=account_recovery")
        text = text_of(response)
        assert response.status_code == 200 and sentence in text and link in text and NO_ROUTE[lang] in text
        assert f'<a href="/{lang}/routes/{HOST}">' in response.text and rows(response) == 0
    assert "No task route record matches" in on.get(f"/en/routes/{HOST}?task_kind=refund_request&outcome=resolved").text


def test_a_page_shows_at_most_a_hundred_rows_and_says_how_to_narrow(on, monkeypatch):
    assert task_routes_api.PAGE_ROWS_MAX == 100
    hosts = {f"site-{index:03d}.example.com": [entry(host=f"site-{index:03d}.example.com")] for index in range(120)}
    table(monkeypatch, hosts)
    response = on.get("/en/routes")
    assert rows(response) == 100
    assert "Showing 100 of 120. Narrow by site or task to see the rest." in text_of(response)
    assert "<datalist" in response.text and response.text.count('<option value="site-') == 120
    assert "사이트나 업무로 좁히면 나머지를 볼 수 있어요." in text_of(on.get("/ko/routes"))
    assert "Narrow by" not in on.get("/en/routes?task_kind=refund_request").text
    three(monkeypatch)
    assert "Narrow by" not in on.get("/en/routes").text


def test_the_curated_records_are_kept_for_a_minute_and_a_swapped_table_is_read_at_once(on, monkeypatch):
    calls = []
    real = task_routes_api.server_route

    def counting(host, item):
        calls.append(host)
        return real(host, item)

    monkeypatch.setattr(task_routes_api, "server_route", counting)
    three(monkeypatch)
    on.get("/en/routes")
    first = len(calls)
    assert first == 3
    on.get("/en/routes")
    on.get(f"/en/routes?site={HOST}", follow_redirects=True)
    assert len(calls) == first  # the same table: no second validation pass
    table(monkeypatch, {OTHER: [entry(host=OTHER)]})
    assert rows(on.get("/en/routes")) == 1 and len(calls) == first + 1
    monkeypatch.setattr(task_routes_api, "monotonic", lambda: 10 ** 9)  # a minute later
    on.get("/en/routes")
    assert len(calls) == first + 2


def test_the_site_box_is_read_on_the_screen_only():
    # The REST and MCP reading of a host is unchanged: a pasted path is still an invalid_site_host there.
    from ahn.task_routes import TaskRouteInputError, parse_query, site_from_input

    assert site_from_input("https://www.ikea.com/us/en/returns/") == ("host", "www.ikea.com")
    assert site_from_input(None) == ("empty", "") and site_from_input("   ") == ("empty", "")
    assert site_from_input("ikea") == ("label", "ikea") and site_from_input("-ikea") == ("invalid", "")
    with pytest.raises(TaskRouteInputError):
        parse_query("https://www.ikea.com/us/en/returns/")
    assert parse_query("HTTPS://IKEA.com/") == ("ikea.com", None)


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
    for name in ("routes.html", "routes_host.html", "routes_site_field.html"):
        template_keys |= set(re.findall(r"\bt\.(routes_\w+)", (ROOT / "ahn" / "templates" / name).read_text(encoding="utf-8")))
    template_keys |= {"routes_head", "routes_part", "routes_head_empty"}
    assert template_keys <= en
    for key in ("routes_head", "routes_part", "routes_showing", "routes_newest", "routes_site_none", "routes_site_profile_link",
                "routes_site_coverage", "routes_empty_kind"):
        for lang in ("en", "ko"):
            LANG[lang][key].format(total=1, s="", parts="x", n=1, label="y", shown=1, host="x.example", m=1)
    for lang in ("en", "ko"):  # a missing record is never read as a missing route, in any of the "nothing found" sentences
        for key in ("routes_site_none", "routes_site_none_plain", "routes_site_label_none", "routes_empty_kind", "routes_empty"):
            assert NO_ROUTE[lang] in LANG[lang][key], (lang, key)


def test_the_pages_carry_no_judgement_words(on, monkeypatch):
    three(monkeypatch)
    for path in ("/en/routes", "/en/routes?site=unrecorded-site.com", "/en/routes?site=%3Cb%3E", "/en/routes?site=nothing-like-it",
                 f"/en/routes/{HOST}?task_kind=account_recovery"):
        body = on.get(path).text.split("<main", 1)[1].split("</main>", 1)[0].lower()
        for word in ("recommended", "guarantee", "trusted", "safe to", "you should", "approved", "bypass"):
            assert word not in body.replace("not guarantees", ""), (path, word)
