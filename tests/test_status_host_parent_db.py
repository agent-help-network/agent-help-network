"""D70: a lookup of a status page's own host shows the feed (or link) mapped for the site that page belongs to.

Database rows only, against the dedicated ahn_test database: no host is read. The fallback is read-only, applies only
when the host has no source of its own, and runs the parent's rows through the same feed_view path."""

from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

TEST_URL = os.environ.get("AHN_TEST_DATABASE_URL", "")
if not TEST_URL:
    pytest.skip("AHN_TEST_DATABASE_URL is required", allow_module_level=True)

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_home_example_db import counts, prepare, refuse_network, seed  # noqa: E402

from ahn import background_reads, feed_robots, service, status_refresh  # noqa: E402
from ahn.db import connect  # noqa: E402

PAGE = "https://status.acme-parent.com/"
API = "https://status.acme-parent.com/api/v2/status.json"
STATUS_HOST = "status.acme-parent.com"


@pytest.fixture(autouse=True)
def no_worker(monkeypatch):
    monkeypatch.setattr(background_reads, "worker_running", lambda: False)


def feed(settings, host, *, page=PAGE, api=API, indicator="major", name="Acme Status"):
    with connect(settings) as conn:
        conn.execute("""INSERT INTO status_sources (site_host,source_name,status_page_url,api_url,evidence)
                        VALUES (%s,%s,%s,%s,'curated_mapping')""", (host, name, page, api))
        conn.execute("INSERT INTO status_cache (site_host,indicator,fetched_at,active_incidents,max_incident_impact) "
                     "VALUES (%s,%s,now(),1,'major')", (host, indicator))


def look(settings, host):
    return service.check(settings, host, "site_access")


def test_the_status_host_of_a_mapped_feed_gets_the_parents_statement_and_its_recommendation(monkeypatch):
    settings = prepare(monkeypatch)
    feed(settings, "acme-parent.com")
    parent, status = look(settings, "acme-parent.com"), look(settings, STATUS_HOST)
    assert parent["provider_status"]["indicator"] == "major"
    assert status["provider_status"]["indicator"] == "major" and status["provider_status"]["source_url"] == PAGE
    assert status["provider_status"]["source_name"] == "Acme Status" and status["provider_source_state"] == "feed_connected"
    assert status["official_status_page"] is None
    assert status["recommendation"]["action"] == parent["recommendation"]["action"] == "wait_provider_incident"


def test_a_host_with_its_own_row_and_an_unrelated_host_are_unaffected(monkeypatch):
    settings = prepare(monkeypatch)
    feed(settings, "acme-parent.com")
    feed(settings, STATUS_HOST, page="https://own.example-status.com/", api="https://own.example-status.com/api/v2/status.json",
         indicator="none", name="Own")
    own = look(settings, STATUS_HOST)
    assert own["provider_status"]["indicator"] == "none" and own["provider_status"]["source_name"] == "Own"
    other = look(settings, "unrelated-site.com")
    assert not other.get("provider_status") and other["provider_source_state"] == "not_verified_by_current_methods"
    assert not look(settings, "status.acme-parent.com.other.com").get("provider_status")  # a host compare, not a prefix


def test_the_port_and_the_letter_case_of_the_page_url_do_not_matter(monkeypatch):
    settings = prepare(monkeypatch)
    feed(settings, "acme-parent.com", page="https://Status.Acme-Parent.com:8443/")
    assert look(settings, STATUS_HOST)["provider_status"]["indicator"] == "major"


def test_two_parents_with_different_feeds_are_ambiguous_and_show_nothing(monkeypatch):
    settings = prepare(monkeypatch)
    feed(settings, "acme-one.com")
    feed(settings, "acme-two.com", api="https://status.acme-parent.com/other/status.json")
    answer = look(settings, STATUS_HOST)
    assert not answer.get("provider_status") and answer["official_status_page"] is None


def test_alias_parents_with_one_feed_show_it(monkeypatch):
    settings = prepare(monkeypatch)
    feed(settings, "acme-one.com", indicator="minor")
    feed(settings, "acme-two.com", indicator="minor")
    assert look(settings, STATUS_HOST)["provider_status"]["indicator"] == "minor"
    with connect(settings) as conn:
        assert service._status_page_parent(conn, STATUS_HOST) == "acme-one.com"  # the smallest site_host stands for them


def test_a_disabled_source_is_not_a_parent(monkeypatch):
    settings = prepare(monkeypatch)
    feed(settings, "acme-parent.com")
    with connect(settings) as conn:
        conn.execute("UPDATE status_sources SET enabled=false")
    assert not look(settings, STATUS_HOST).get("provider_status")


def test_a_parent_feed_its_robots_txt_disallows_gives_the_same_link_and_no_reading(monkeypatch):
    settings = prepare(monkeypatch)
    feed(settings, "acme-parent.com")
    monkeypatch.setattr(feed_robots, "feed_state", lambda _conn, _api_url: "disallowed")
    parent, status = look(settings, "acme-parent.com"), look(settings, STATUS_HOST)
    assert not parent.get("provider_status") and not status.get("provider_status")
    assert status["official_status_page"] == parent["official_status_page"] == {"source_name": "Acme Status", "source_url": PAGE}
    assert status["provider_source_state"] == "official_page_only"


def test_the_fallback_writes_nothing_and_queues_no_feed_read(monkeypatch):
    settings = prepare(monkeypatch)
    feed(settings, "acme-parent.com")
    with connect(settings) as conn:
        before = [conn.execute(f"SELECT count(*) AS n FROM {t}").fetchone()["n"] for t in ("status_sources", "status_cache", "background_reads")]
        assert service.feed_view(conn, STATUS_HOST, datetime.now(timezone.utc)).provider_status["indicator"] == "major"
        after = [conn.execute(f"SELECT count(*) AS n FROM {t}").fetchone()["n"] for t in ("status_sources", "status_cache", "background_reads")]
    assert after == before
    look(settings, STATUS_HOST)
    with connect(settings) as conn:
        assert conn.execute("SELECT count(*) AS n FROM background_reads WHERE kind='status_feed' AND site_host=%s",
                            (STATUS_HOST,)).fetchone()["n"] == 0


def test_the_home_card_for_a_status_host_matches_the_lookup(monkeypatch):
    settings = prepare(monkeypatch)
    feed(settings, "acme-parent.com")
    seed(settings, STATUS_HOST)
    refuse_network(monkeypatch)
    before = counts(settings)
    card = service.home_example(settings, STATUS_HOST)
    answer = look(settings, STATUS_HOST)
    assert card["status"] == answer["provider_status"]["indicator"] == "major"
    assert card["provider_name"] == answer["provider_status"]["source_name"] == "Acme Status"
    assert counts(settings) == before


def test_the_link_only_table_is_read_in_reverse_and_an_ambiguous_page_host_is_left_out():
    pages = {
        "alpha.com": ("Alpha Status", "https://Status.Alpha.com/"),
        "alpha.net": ("Alpha Status", "https://Status.Alpha.com/"),            # same entry twice: not ambiguous
        "beta.com": ("Beta Status", "https://shared-status.example/beta"),
        "gamma.com": ("Gamma Status", "https://shared-status.example/gamma"),  # one host, two entries: ambiguous
    }
    assert status_refresh._page_host_map(pages) == {"status.alpha.com": ("Alpha Status", "https://Status.Alpha.com/")}


def test_a_link_only_page_host_shows_the_page_mapped_for_its_site(monkeypatch):
    settings = prepare(monkeypatch)
    monkeypatch.setitem(status_refresh.OFFICIAL_STATUS_PAGES, "link-only-site.com", ("Link Status", "https://status.link-only-site.com/"))
    monkeypatch.setattr(status_refresh, "_STATUS_PAGE_HOSTS", status_refresh._page_host_map(status_refresh.OFFICIAL_STATUS_PAGES))
    answer = look(settings, "status.link-only-site.com")
    assert answer["official_status_page"] == {"source_name": "Link Status", "source_url": "https://status.link-only-site.com/"}
    assert answer["provider_source_state"] == "official_page_only" and not answer.get("provider_status")
    assert look(settings, "link-only-site.com")["official_status_page"] == answer["official_status_page"]
    assert status_refresh.official_status_page_for("other.status.link-only-site.com") is None


@pytest.mark.parametrize("url, host", [
    ("https://status.example.com/", "status.example.com"), ("https://status.example.com/status/public", "status.example.com"),
    ("https://www.example-status.com/product/x", "www.example-status.com"), ("https://health.example.com/health/status", "health.example.com"),
    ("https://www.example.com/", "www.example.com"), ("https://www.example.com/support/systemstatus/", None),
    ("https://help.example.com/en/is-it-down", None), ("https://developer.example.com/status", None),
    ("not a url", None),
])
def test_only_a_host_that_serves_status_alone_is_a_status_page_host(url, host):
    assert status_refresh.dedicated_status_host(url) == host


def test_a_status_page_under_a_path_of_a_general_site_is_not_shown_for_that_site(monkeypatch):
    settings = prepare(monkeypatch)
    feed(settings, "mail-product.com", page="https://www.general-site.com/appsstatus/dashboard/",
         api="https://www.general-site.com/appsstatus/dashboard/incidents.json")
    answer = look(settings, "www.general-site.com")
    assert not answer.get("provider_status") and answer["provider_source_state"] != "feed_connected"
    pages = {"device.com": ("Device Status", "https://www.general-site.com/support/systemstatus/")}
    assert status_refresh._page_host_map(pages) == {}
