"""Hardening before the task-route write switch is turned on (D118): public and shared-hosting suffixes, the per-client limit by
IPv6 /64, the list of government sites kept under their own name, and the policy line on operator withdrawal.

Tests that need a database use the throwaway ahn_test of the other task-route write tests; the rest run without one. The reports
are invented and use example names only.
"""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent))
os.environ.setdefault("AHN_DATABASE_URL", "postgresql://invalid/unused")
os.environ.setdefault("AHN_KEY_PEPPER", "x" * 32)

from ahn import lookup_signals, task_routes, task_routes_data  # noqa: E402
from ahn.main import create_app  # noqa: E402
from ahn.settings import Settings  # noqa: E402
from ahn.task_routes import lookup  # noqa: E402
from test_task_routes_write_db import (PEPPER, SINCE, api, db, needs_db, policy_page, post, report, rows, run,  # noqa: E402,F401
                                       unlimited, write_settings)

# --- Suffixes ---------------------------------------------------------------------------------------------------------------

REFUSED_NAMES = [
    "herokuapp.com", "blogspot.com", "web.app", "firebaseapp.com", "workers.dev", "pages.dev", "fly.dev", "onrender.com",
    "cloudfront.net", "netlify.app", "vercel.app", "azurewebsites.net", "appspot.com", "github.io", "gitlab.io",
    "sch.uk", "ltd.uk", "plc.uk", "me.uk", "net.uk", "org.uk", "co.uk", "gov.uk", "ac.uk",
    "ca.us", "ny.us", "tx.us", "dc.us", "gov.sg", "com.sg", "edu.sg", "com.de", "co.com", "on.ca", "bc.ca",
    "www.herokuapp.com", "www.sch.uk", "www.gov.sg", "www.ca.us",
]
# A site under each of them: an account, a school or a tenant, never the suffix's own site.
SUBSITES = ["myapp.herokuapp.com", "school.sch.uk", "agency.gov.sg", "someone.blogspot.com", "app.web.app", "shop.workers.dev",
            "team.ca.us", "firm.ltd.uk", "x.fly.dev", "my.onrender.com", "d111.cloudfront.net", "site.netlify.app", "me.gitlab.io"]


@needs_db
@pytest.mark.parametrize("host", REFUSED_NAMES)
def test_a_report_under_a_public_or_shared_hosting_suffix_is_refused_and_takes_no_slot(api, host):
    settings, client = api
    response = post(client, report(site_host=host))
    assert response.status_code == 422 and response.json() == {"error": "site_host_not_accepted"}
    assert rows(settings) == [] and rows(settings, "SELECT * FROM task_route_intake_daily") == []


@needs_db
@pytest.mark.parametrize("host", SUBSITES)
def test_a_report_for_a_site_under_such_a_suffix_is_refused(api, host):
    settings, client = api
    response = post(client, report(site_host=host))
    assert response.status_code == 422 and response.json() == {"error": "site_host_not_accepted"}
    assert rows(settings) == []


def test_every_state_and_province_second_level_is_a_suffix_name():
    states = "ak al ar az ca co ct dc de fl ga hi ia id il in ks ky la ma md me mi mn mo ms mt nc nd ne nh nj nm nv ny oh ok or pa " \
             "ri sc sd tn tx ut va vt wa wi wv wy as gu mp pr vi".split()
    assert len(states) == 56
    for label in states:
        assert task_routes.suffix_name(f"{label}.us") and task_routes.route_host(f"{label}.us") is None, label
        assert task_routes.route_host(f"school.{label}.us") is None, label
    for label in "ab bc mb nb nl ns nt nu on pe qc sk yk".split():
        assert task_routes.suffix_name(f"{label}.ca") and task_routes.route_host(f"{label}.ca") is None, label


def test_the_new_suffixes_have_two_lowercase_labels_and_leave_every_curated_host_alone():
    extra = task_routes.EXTRA_PUBLIC_SUFFIXES_2 | task_routes.PRIVATE_SUFFIXES
    for name in extra:
        assert name == name.lower() and len(name.split(".")) == 2 and all(name.split(".")), name
    for host in task_routes_data.TASK_ROUTES_CURATED:
        parts = host.split(".")
        for index in range(len(parts) - 1):  # the host and each parent down to two labels
            assert ".".join(parts[index:]) not in extra, host
        assert task_routes.route_host(host) == host and task_routes.registrable(host) == host
        assert not task_routes.suffix_name(host) and not task_routes.suffix_name(f"www.{host}")


@pytest.mark.parametrize("host, expected", [
    ("myapp.herokuapp.com", ["myapp.herokuapp.com"]),
    ("www.myapp.herokuapp.com", ["www.myapp.herokuapp.com", "myapp.herokuapp.com"]),
    ("help.myapp.herokuapp.com", ["help.myapp.herokuapp.com", "myapp.herokuapp.com"]),
    ("school.sch.uk", ["school.sch.uk"]),
    ("www.school.sch.uk", ["www.school.sch.uk", "school.sch.uk"]),
    ("a.b.web.app", ["a.b.web.app", "b.web.app"]),
    ("agency.gov.sg", ["agency.gov.sg"]),
    ("team.ca.us", ["team.ca.us"]),
])
def test_the_site_walk_stops_below_the_new_suffixes(host, expected):
    assert task_routes.key_candidates(host) == expected


@needs_db
def test_a_stored_row_under_a_new_suffix_name_is_never_read_into_a_sites_answer(api):
    settings, client = api
    names = ["herokuapp.com", "sch.uk", "gov.sg", "ca.us", "web.app", "blogspot.com", "ltd.uk", "com.de"]
    for name in names:  # rows that slipped in before the rule or by hand, all published
        run(settings, """INSERT INTO task_routes (id,site_host,task_kind,obstacle,routes_tried,basis,outcome,period,source_kind,sensitive,
                             review_state,reviewed_at) VALUES (%s,%s,'refund_request','wrong_channel','{phone}','own_attempt','resolved',
                             '2026-Q4','external_agent',false,'published',now())""", (uuid4(), name))
    probes = ["myapp.herokuapp.com", "www.myapp.herokuapp.com", "help.myapp.herokuapp.com", "school.sch.uk", "www.school.sch.uk",
              "agency.gov.sg", "team.ca.us", "app.web.app", "x.blogspot.com", "firm.ltd.uk", "shop.com.de", *names, *(f"www.{n}" for n in names)]
    for host in probes:
        answer = client.get("/v1/task-routes", params={"site_host": host}).json()
        assert answer["routes"] == [] and answer["counts"]["records"] == 0, host
        assert lookup(host, settings=settings).counts.records == 0, host
        assert client.get(f"/en/routes/{host}", follow_redirects=False).status_code == 404, host


def test_every_curated_answer_is_byte_identical_with_and_without_the_new_suffixes_and_government_list(unlimited, monkeypatch):
    # registrable, key_candidates and tenant_host are shared by the read path: the larger sets must change no curated answer.
    # (The per-client request limiter is off here: hundreds of requests from one client would otherwise be refused unevenly.)
    # Curated government hosts are kept under their exact name (D122), so their sub-names answer differently by design.
    curated = [host for host in task_routes_data.TASK_ROUTES_CURATED if not task_routes.government_zone(host)]
    hosts = set()
    for host in curated:
        hosts |= {host, f"www.{host}", f"support.{host}", f"help.www.{host}", f"springfield.{host}"}
        labels = host.split(".")
        hosts |= {".".join(labels[index:]) for index in range(len(labels) - 1)}
    read_only = Settings("postgresql://invalid/unused", PEPPER, public_mode=True, task_routes=True, task_routes_since=SINCE)
    today = datetime.now(timezone.utc).date()

    def answers():
        out = {}
        with TestClient(create_app(read_only)) as client:
            for host in sorted(hosts):
                out[host] = (lookup(host, today=today).model_dump_json(),
                             client.get("/v1/task-routes", params={"site_host": host}).content,
                             client.get(f"/en/routes/{host}", follow_redirects=False).content,
                             client.get(f"/ko/routes/{host}", follow_redirects=False).content)
        return out

    new = answers()
    with monkeypatch.context() as patch:  # the sets as main had them before this change
        patch.setattr(task_routes, "EXTRA_PUBLIC_SUFFIXES_2", frozenset())
        patch.setattr(task_routes, "PRIVATE_SUFFIXES",
                      frozenset({"github.io", "gitbook.io", "readthedocs.io", "netlify.app", "vercel.app", "pages.dev"}))
        patch.setattr(task_routes, "GOVERNMENT_SITE_HOSTS", frozenset())
        patch.setattr(task_routes, "GOVERNMENT_ZONES", frozenset())
        patch.setattr(task_routes, "GOVERNMENT_STATE_ZONES", frozenset())
        old = answers()
    assert len(hosts) >= 5 * len(curated)
    assert [host for host in sorted(hosts) if new[host] != old[host]] == []


# --- The per-client limit counts an IPv6 client by its /64 ------------------------------------------------------------------

def test_limiter_network_groups_ipv6_by_64_and_leaves_ipv4_alone():
    network = lookup_signals.limiter_network
    assert network("2001:db8:1:2::1") == network("2001:db8:1:2:ffff:ffff:ffff:ffff") == "2001:db8:1:2::"
    assert network("2001:db8:1:2::1") == network("2001:0db8:0001:0002:0000:0000:0000:0002")
    assert network("2001:db8:1:2::1") != network("2001:db8:1:3::1")
    assert network("203.0.113.7") == "203.0.113.7" and network("203.0.113.7") != network("203.0.113.8")
    assert network("::ffff:203.0.113.7") == "203.0.113.7"  # an IPv4 client seen through a mapped address is still one IPv4 client
    assert network("testclient") == "testclient" and network("unknown") == "unknown"


def test_the_limiter_shares_one_grouping_with_the_source_digest():
    assert lookup_signals.source_network("2001:db8:1:2::9") == "2001:db8:1:2::"
    assert lookup_signals.source_network("203.0.113.7") == "203.0.113.7"

    def digest_before(pepper, day, host, address):  # source_digest as main had it, written out
        ip = ipaddress.ip_address(address)
        if ip.version == 6:
            address = ipaddress.ip_network((ip, 64), strict=False).network_address.compressed
        return hmac.new(pepper.encode(), f"src-v1|{day.isoformat()}|{host}|{address}".encode(), hashlib.sha256).hexdigest()

    day = datetime(2026, 11, 14, tzinfo=timezone.utc).date()
    for address in ("203.0.113.7", "2001:db8:1:2::1", "2001:db8:1:2:aaaa:bbbb:cccc:dddd", "::1", "::ffff:203.0.113.7", "fe80::1"):
        assert lookup_signals.source_digest("p" * 32, day, "example.com", address) == digest_before("p" * 32, day, "example.com", address)
    assert (lookup_signals.source_digest("p" * 32, day, "example.com", "2001:db8:1:2::1")
            == lookup_signals.source_digest("p" * 32, day, "example.com", "2001:db8:1:2::2"))


KINDS = ["refund_request", "cancel_subscription", "account_recovery", "portal_login"]


def codes(client, site, headers):
    return [client.post("/v1/task-routes", json=report(site_host=site, task_kind=kind), headers=headers).status_code for kind in KINDS]


@needs_db
def test_four_addresses_of_one_ipv6_64_share_one_limit_and_another_64_has_its_own(db, monkeypatch):
    monkeypatch.setattr(task_routes_data, "TASK_ROUTES_CURATED", {})
    with TestClient(create_app(db)) as client:
        same = [client.post("/v1/task-routes", json=report(task_kind=kind), headers={"CF-Connecting-IP": f"2001:db8:1:2::{index}"}).status_code
                for index, kind in enumerate(KINDS, start=1)]
        other = codes(client, "example.org", {"CF-Connecting-IP": "2001:db8:1:3::1"})
    assert same == [201, 201, 201, 429]  # the fourth address is the same /64
    assert other == [201, 201, 201, 429]  # a different /64 started its own count


@needs_db
def test_ipv4_clients_still_count_by_address(db, monkeypatch):
    monkeypatch.setattr(task_routes_data, "TASK_ROUTES_CURATED", {})
    with TestClient(create_app(db)) as client:
        first = codes(client, "example.com", {"CF-Connecting-IP": "203.0.113.7"})
        next_address = codes(client, "example.org", {"CF-Connecting-IP": "203.0.113.8"})
    assert first == [201, 201, 201, 429] and next_address == [201, 201, 201, 429]


@needs_db
def test_the_limit_over_mcp_counts_the_passed_on_address_by_its_64(db, monkeypatch):
    from ahn.lookup_signals import ORIGIN_HEADER
    from ahn.tool_metrics import mcp_metric_token

    monkeypatch.setattr(task_routes_data, "TASK_ROUTES_CURATED", {})
    hop = {"X-AHN-MCP-Call": mcp_metric_token(db.key_pepper)}
    with TestClient(create_app(db), client=("127.0.0.1", 50000)) as client:
        same = [client.post("/v1/task-routes", json=report(task_kind=kind), headers={**hop, ORIGIN_HEADER: f"2001:db8:9:9::{index}"}).status_code
                for index, kind in enumerate(KINDS, start=1)]
        other = client.post("/v1/task-routes", json=report(site_host="example.org"), headers={**hop, ORIGIN_HEADER: "2001:db8:9:a::1"})
        v4 = [client.post("/v1/task-routes", json=report(site_host="example.net", task_kind=kind), headers={**hop, ORIGIN_HEADER: f"203.0.113.{index}"}).status_code
              for index, kind in enumerate(KINDS, start=20)]
    assert same == [201, 201, 201, 429] and other.status_code == 201
    assert v4 == [201, 201, 201, 201]  # four IPv4 addresses are four clients


# --- Government sites kept under their own name -----------------------------------------------------------------------------

def test_the_government_site_list_holds_valid_names_and_only_one_www_exception():
    assert {"unclaimedproperty.nj.gov", "www.gov.uk"} == set(task_routes.GOVERNMENT_SITE_HOSTS)
    for name in task_routes.GOVERNMENT_SITE_HOSTS:
        labels = name.split(".")
        assert name == name.lower() and len(labels) >= 3 and all(labels), name
        assert task_routes.valid_host(name) == name
        assert not name.startswith("www.") or name == "www.gov.uk"


def test_a_listed_government_site_is_kept_under_its_exact_name_and_nothing_else_gains_from_it():
    listed = "unclaimedproperty.nj.gov"
    assert task_routes.route_host(listed) == listed
    assert task_routes.route_host(f"www.{listed}") == listed
    # Other names under a government zone are sites of their own now (D122), each under its exact name and never the listed one.
    for other in ("treasury.nj.gov", "x.unclaimedproperty.nj.gov", "a.b.nj.gov", "unclaimed.nj.gov", "xunclaimedproperty.nj.gov",
                  "unclaimedproperty.ny.gov"):
        assert task_routes.route_host(other) == other != listed, other
    assert task_routes.route_host("www.x.unclaimedproperty.nj.gov") == "x.unclaimedproperty.nj.gov"
    assert task_routes.route_host("unclaimedproperty.nj.gov.example.com") is None
    assert task_routes.route_host("nj.gov") == "nj.gov"  # the parent keeps its own key and does not become the listed name
    assert task_routes.key_candidates(listed) == [listed] == task_routes.key_candidates(f"www.{listed}")
    assert not task_routes.tenant_host(listed) and not task_routes.tenant_host(f"www.{listed}")
    for other in ("treasury.nj.gov", "x.unclaimedproperty.nj.gov"):
        assert not task_routes.tenant_host(other), other
        assert task_routes.key_candidates(other) == [other]
        assert task_routes.table_key(other, frozenset({listed, "nj.gov"})) is None  # neither the listed name nor the parent answers


def test_the_government_list_is_read_from_the_constant(monkeypatch):
    monkeypatch.setattr(task_routes, "GOVERNMENT_ZONES", frozenset())  # no zone: only the list decides, as before D122
    monkeypatch.setattr(task_routes, "GOVERNMENT_STATE_ZONES", frozenset())
    monkeypatch.setattr(task_routes, "GOVERNMENT_SITE_HOSTS", frozenset())
    assert task_routes.route_host("unclaimedproperty.nj.gov") is None
    monkeypatch.setattr(task_routes, "GOVERNMENT_SITE_HOSTS", frozenset({"records.example.gov"}))
    assert task_routes.route_host("records.example.gov") == "records.example.gov"
    assert task_routes.route_host("unclaimedproperty.nj.gov") is None


@needs_db
def test_a_report_for_a_listed_government_site_is_stored_under_its_name_and_answers_only_there(api):
    settings, client = api
    listed = "unclaimedproperty.nj.gov"
    created = post(client, report(site_host=f"https://www.{listed}/", task_kind="claim_unclaimed_property"))
    assert created.status_code == 201
    [row] = rows(settings)
    assert row["site_host"] == listed
    for refused in ("a.b.c.d.unclaimedproperty.nj.gov", "unclaimedproperty.nj.gov.example.com"):  # too deep, or no zone at all
        assert post(client, report(site_host=refused)).status_code == 422, refused
    assert len(rows(settings)) == 1
    run(settings, "UPDATE task_routes SET review_state='published',reviewed_at=now() WHERE id=%s", (created.json()["route_id"],))
    # A published row under the parent, to see that it never answers for the listed name or the other way round.
    run(settings, """INSERT INTO task_routes (id,site_host,task_kind,obstacle,routes_tried,basis,outcome,period,source_kind,sensitive,
                         review_state,reviewed_at) VALUES (%s,'nj.gov','refund_request','wrong_channel','{phone}','own_attempt','resolved',
                         '2026-Q4','external_agent',false,'published',now())""", (uuid4(),))
    for host in (listed, f"www.{listed}"):
        answer = client.get("/v1/task-routes", params={"site_host": host}).json()
        assert [route["task_kind"] for route in answer["routes"]] == ["claim_unclaimed_property"], host
        assert answer["routes"][0]["site_host"] == listed
    for host in ("nj.gov", "www.nj.gov"):
        answer = client.get("/v1/task-routes", params={"site_host": host}).json()
        assert [route["task_kind"] for route in answer["routes"]] == ["refund_request"], host
    for host in ("treasury.nj.gov", "x.unclaimedproperty.nj.gov", "www.x.unclaimedproperty.nj.gov", "unclaimedproperty.ny.gov",
                 "a.b.c.d.unclaimedproperty.nj.gov"):
        answer = client.get("/v1/task-routes", params={"site_host": host}).json()
        assert answer["routes"] == [], host
        assert lookup(host, settings=settings).counts.records == 0, host
    assert client.get(f"/en/routes/{listed}").status_code == 200
    assert client.get(f"/en/routes/www.{listed}", follow_redirects=False).status_code == 301


# --- Policy ------------------------------------------------------------------------------------------------------------------

def test_the_policy_says_an_operator_can_withdraw_a_public_report():
    en = policy_page("en")
    assert "An operator can also withdraw a public task-route report at any time" in en
    assert "운영자는 공개된 태스크 경로 제보도 언제든 철회할 수 있으며" in policy_page("ko")
    for lang in ("en", "ko"):  # with the write switch off the page does not mention task-route reports at all
        off = policy_page(lang, task_route_reports=False, task_route_reports_since=None)
        assert "withdraw a public task-route report" not in off and "공개된 태스크 경로 제보도" not in off


# --- Follow-up: more suffixes, and a listed government site is read as government ---------------------------------------------

MORE_REFUSED = [
    "myshopify.com", "wixsite.com", "run.app", "cloudfunctions.net", "azureedge.net", "cloudapp.net", "r2.dev", "deno.dev",
    "replit.app", "webflow.io", "notion.site", "ngrok.io", "ngrok.app", "surge.sh", "ddns.net",
    "co.at", "or.at", "gv.at", "gov.it", "gov.co", "org.co", "edu.co", "br.com", "cn.com", "de.com", "ru.com", "sa.com",
    "za.com", "jpn.com", "gb.net", "uk.net", "eu.org", "us.org",
]


@pytest.mark.parametrize("host", MORE_REFUSED)
def test_the_further_suffixes_are_no_site_and_nothing_below_them_climbs_to_them(host):
    assert task_routes.suffix_name(host) and task_routes.suffix_name(f"www.{host}")
    assert task_routes.route_host(host) is None and task_routes.route_host(f"shop.{host}") is None
    assert task_routes.key_candidates(f"shop.{host}") == [f"shop.{host}"]


def test_gc_ca_is_no_site_itself_but_a_government_zone_below_it():  # D122: the one suffix above that is also a zone
    assert task_routes.suffix_name("gc.ca") and task_routes.route_host("gc.ca") is None
    assert task_routes.route_host("shop.gc.ca") == "shop.gc.ca" and task_routes.key_candidates("shop.gc.ca") == ["shop.gc.ca"]


@needs_db
@pytest.mark.parametrize("host", MORE_REFUSED)
def test_a_report_under_a_further_suffix_is_refused(api, host):
    settings, client = api
    response = post(client, report(site_host=host))
    assert response.status_code == 422 and response.json() == {"error": "site_host_not_accepted"}
    assert rows(settings) == []


def test_a_listed_government_site_is_read_as_government_and_its_claim_is_a_case_record():
    from ahn.task_routes import government_suffix, is_sensitive

    listed = "unclaimedproperty.nj.gov"
    assert government_suffix(listed) and government_suffix(f"www.{listed}")
    assert is_sensitive("claim_unclaimed_property", government_host=government_suffix(listed)) is True
    assert not government_suffix("example.com")
