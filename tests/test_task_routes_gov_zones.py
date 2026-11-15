"""Government zones (D122): under a government suffix the exact host name is the task route site key, for a report and for a read.

The case table lists each host with the key a report is kept under (None: refused) and the key a read answers from (None: the
empty answer). The hosts are public government names used only as strings: nothing here reads a site. Only the last test needs
a database.
"""

from __future__ import annotations

import os
import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
os.environ.setdefault("AHN_DATABASE_URL", "postgresql://invalid/unused")
os.environ.setdefault("AHN_KEY_PEPPER", "x" * 32)

from ahn import task_routes, task_routes_data  # noqa: E402
from test_task_routes_write_db import (api, db, needs_db, post, report, rows, run, unlimited, write_settings)  # noqa: E402,F401

# host -> key a report is kept under (None: refused 422)
WRITE = {
    # Federal
    "irs.gov": "irs.gov", "www.irs.gov": "irs.gov", "sa.www4.irs.gov": "sa.www4.irs.gov",
    "secure.ssa.gov": "secure.ssa.gov", "my.uscis.gov": "my.uscis.gov", "myhealth.va.gov": "myhealth.va.gov",
    "travel.state.gov": "travel.state.gov", "army.mil": "army.mil", "www.army.mil": "army.mil", "x.fed.us": "x.fed.us",
    # States
    "nj.gov": "nj.gov", "mvc.nj.gov": "mvc.nj.gov", "dmv.ca.gov": "dmv.ca.gov", "www.dmv.ca.gov": "dmv.ca.gov",
    "tax.ny.gov": "tax.ny.gov", "help.nj.gov": "help.nj.gov", "support.ca.gov": "support.ca.gov",
    "unclaimedproperty.nj.gov": "unclaimedproperty.nj.gov", "www.unclaimedproperty.nj.gov": "unclaimedproperty.nj.gov",
    "state.nj.us": "state.nj.us", "www.state.nj.us": "state.nj.us", "dmv.state.nj.us": "dmv.state.nj.us",
    "www.dps.state.tx.us": "dps.state.tx.us",
    # Counties and cities
    "nyc.gov": "nyc.gov", "www1.nyc.gov": "www1.nyc.gov", "portal.311.nyc.gov": "portal.311.nyc.gov",
    "dpss.lacounty.gov": "dpss.lacounty.gov", "webapps1.chicago.gov": "webapps1.chicago.gov",
    # Abroad
    "hmrc.gov.uk": "hmrc.gov.uk", "tax.service.gov.uk": "tax.service.gov.uk", "www.tax.service.gov.uk": "tax.service.gov.uk",
    "www.gov.uk": "www.gov.uk", "cra-arc.gc.ca": "cra-arc.gc.ca", "ato.gov.au": "ato.gov.au",
    "service.nsw.gov.au": "service.nsw.gov.au", "ird.govt.nz": "ird.govt.nz", "minwon.go.kr": "minwon.go.kr",
    "etax.seoul.go.kr": "etax.seoul.go.kr",
    # Outside the zones, unchanged
    "usps.com": "usps.com", "tools.usps.com": None, "canada.ca": "canada.ca", "www.canada.ca": "canada.ca",
    "cityofchicago.org": "cityofchicago.org",
    # Left out of the zones on purpose
    "lausd.k12.ca.us": None, "ci.austin.tx.us": None, "co.travis.tx.us": None, "school.k12.ca.us": None,
    # Never a site: a bare suffix, a state label, too deep, punycode, a doubled www, a lookalike
    "gov.uk": None, "nj.us": None, "a.b.c.d.irs.gov": None, "a.b.c.d.state.nj.us": None, "xn--abc.gov": None,
    "www.www.irs.gov": None, "www.www.dmv.ca.gov": None,
    "irs.gov.example.com": None, "mil.example.com": None,
}
ZONE_FREE = {"usps.com", "tools.usps.com", "canada.ca", "www.canada.ca", "cityofchicago.org", "mil.example.com", "irs.gov.example.com"}
ZONE_NAMES = sorted(host for host, key in WRITE.items() if key and host not in ZONE_FREE)


@pytest.mark.parametrize("host", sorted(WRITE))
def test_a_report_is_kept_under_the_expected_key(host):
    assert task_routes.route_host(host) == WRITE[host], host


@pytest.mark.parametrize("host", ZONE_NAMES)
def test_a_zone_name_reads_its_own_name_only(host, monkeypatch):
    monkeypatch.setattr(task_routes_data, "TASK_ROUTES_CURATED", {})  # the case table is about the key rules, not the curated rows
    key = WRITE[host]
    assert task_routes.key_candidates(host) == [key]
    assert not task_routes.tenant_host(host) and task_routes.government_suffix(host)
    parent = key.split(".", 1)[1]
    siblings = {f"other.{parent}", parent}
    assert task_routes.table_key(host, frozenset({key} | siblings)) == key
    assert task_routes.table_key(host, frozenset(siblings)) is None  # a row of the parent or a sibling is never shown for it


@pytest.mark.parametrize("host", ["tools.usps.com", "lausd.k12.ca.us", "ci.austin.tx.us", "co.travis.tx.us", "a.b.c.d.irs.gov",
                                  "xn--abc.gov", "irs.gov.example.com", "mil.example.com", "www.support.help.shop.m.irs.gov"])
def test_a_refused_name_gets_the_empty_answer_and_climbs_to_no_parent(host):
    assert task_routes.tenant_host(host)
    assert task_routes.table_key(host, frozenset({"irs.gov", "usps.com", "example.com", "k12.ca.us", "austin.tx.us"})) is None


def test_outside_a_zone_nothing_changes_for_a_service_name():
    assert task_routes.key_candidates("www.canada.ca") == ["www.canada.ca", "canada.ca"]
    assert not task_routes.tenant_host("www.canada.ca") and not task_routes.government_suffix("canada.ca")
    assert task_routes.table_key("support.usps.com", frozenset({"usps.com"})) == "usps.com"


def test_the_zone_rules_in_one_place():
    assert task_routes.route_host("www.gov.uk") == "www.gov.uk" and task_routes.route_host("gov.uk") is None
    assert len(task_routes.GOVERNMENT_STATE_ZONES) == 56 and "state.nj.us" in task_routes.GOVERNMENT_STATE_ZONES
    assert task_routes.GOVERNMENT_ZONES == {"gov", "mil", "fed.us", "gov.uk", "go.kr", "gc.ca", "gov.au", "govt.nz"}
    assert task_routes.route_host("a.b.c.nj.gov") == "a.b.c.nj.gov" and task_routes.route_host("a.b.c.d.nj.gov") is None
    assert task_routes.route_host("a.b.c.state.nj.us") == "a.b.c.state.nj.us"
    for host in ("school.k12.ca.us", "dept.ci.austin.tx.us", "dept.co.travis.tx.us"):
        assert task_routes.route_host(host) is None and not task_routes.government_zone(host), host


def test_without_the_zones_main_behavior_comes_back(monkeypatch):
    zoned = {host: task_routes.route_host(host) for host in WRITE}
    monkeypatch.setattr(task_routes, "GOVERNMENT_ZONES", frozenset())
    monkeypatch.setattr(task_routes, "GOVERNMENT_STATE_ZONES", frozenset())
    monkeypatch.setattr(task_routes, "GOVERNMENT_SITE_HOSTS", frozenset({"unclaimedproperty.nj.gov"}))
    for host in ("mvc.nj.gov", "dmv.ca.gov", "my.uscis.gov", "army.mil", "state.nj.us", "cra-arc.gc.ca", "tax.service.gov.uk", "www.gov.uk"):
        assert zoned[host] is not None and task_routes.route_host(host) is None, host
    for host in ("irs.gov", "www.irs.gov", "nj.gov", "hmrc.gov.uk", "ato.gov.au", "minwon.go.kr", "usps.com", "unclaimedproperty.nj.gov"):
        assert task_routes.route_host(host) == zoned[host], host


def test_no_curated_answer_changes_because_of_the_zones(monkeypatch):
    # A curated key outside a zone is unchanged by the zones. A curated key inside a zone is kept under its own exact name.
    # (The byte-level comparison over every curated host is in test_task_routes_hardening, which empties the same constants.)
    def view(host):
        return (task_routes.route_host(host), task_routes.tenant_host(host),
                *(task_routes.key_candidates(name) for name in (host, f"www.{host}", f"support.{host}", f"springfield.{host}")))

    zoned_keys = sorted(host for host in task_routes_data.TASK_ROUTES_CURATED if task_routes.government_zone(host))
    assert all(task_routes.route_host(host) == host and not task_routes.tenant_host(host) for host in zoned_keys), zoned_keys
    hosts = sorted(host for host in task_routes_data.TASK_ROUTES_CURATED if host not in zoned_keys)
    assert hosts
    before = {host: view(host) for host in hosts}
    monkeypatch.setattr(task_routes, "GOVERNMENT_ZONES", frozenset())
    monkeypatch.setattr(task_routes, "GOVERNMENT_STATE_ZONES", frozenset())
    assert {host: view(host) for host in hosts} == before


def test_a_curated_government_key_answers_for_its_own_name_only():
    zoned = sorted(host for host in task_routes_data.TASK_ROUTES_CURATED if task_routes.government_zone(host))
    assert zoned
    for key in zoned:
        count = len(task_routes_data.TASK_ROUTES_CURATED[key])
        for name in (key, f"www.{key}"):
            answer = task_routes.lookup(name, today=date(2026, 11, 16))
            assert len(answer.routes) == count, name
            assert all(route.sensitive is True for route in answer.routes), name
            assert answer.notice == "case_record_not_advice", name
        for prefix in ("support.", "help.", "springfield.", "www.support.", "a.b."):
            name = prefix + key
            assert task_routes.lookup(name, today=date(2026, 11, 16)).routes == [], name
            assert task_routes.table_key(name) is None, name
            assert task_routes.table_key(name, frozenset({key})) is None, name


def test_an_apex_that_is_not_a_site_answers_like_its_www_name():
    # Names whose registrable domain is not an eligible site: on main the apex was empty and www.<apex> climbed to it.
    for host in ("austin.tx.us", "example.co.at", "co.travis.tx.us", "lausd.k12.ca.us"):
        assert task_routes.tenant_host(host) and task_routes.tenant_host(f"www.{host}"), host
        assert task_routes.table_key(f"www.{host}", frozenset({host})) is None, host


@needs_db
def test_a_report_for_a_department_host_is_kept_under_its_name_and_no_parent_or_sibling_reads_it(api):
    settings, client = api
    created = post(client, report(site_host="https://www.dmv.ca.gov/"))
    assert created.status_code == 201
    [row] = rows(settings)
    assert row["site_host"] == "dmv.ca.gov"
    run(settings, "UPDATE task_routes SET review_state='published',reviewed_at=now() WHERE id=%s", (created.json()["route_id"],))
    for host in ("dmv.ca.gov", "www.dmv.ca.gov"):
        answer = client.get("/v1/task-routes", params={"site_host": host}).json()
        assert [item["site_host"] for item in answer["routes"]] == ["dmv.ca.gov"], host
        assert answer["routes"][0]["sensitive"] is True  # a government name is shown as a case record
    for host in ("ca.gov", "edd.ca.gov", "x.dmv.ca.gov", "help.ca.gov", "dmv.ny.gov"):
        assert client.get("/v1/task-routes", params={"site_host": host}).json()["routes"] == [], host
    assert post(client, report(site_host="a.b.c.d.dmv.ca.gov")).status_code == 422
    assert len(rows(settings)) == 1
