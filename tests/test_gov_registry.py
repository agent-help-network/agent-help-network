"""Government domain lists (U.S. .gov, UK .gov.uk) and the registry facts a lookup joins from them.

Parsers and rules are pure and run everywhere. The load, protection and answer tests need the dedicated
ahn_test database. No test contacts a publisher: every download goes through a stub, and conftest.py makes
the real downloader fail loudly.
"""

from __future__ import annotations

import dataclasses
import json
import logging
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from ahn import gov_registry
from ahn.gov_registry import (
    ALLOWED_HOSTS, REGISTRIES, UK, US, Fetched, ListRejected, http_get, newest_uk_csv, parse_uk, parse_us,
)
from ahn.models import CheckResponse
from ahn.service import aggregate_eligible_host, policy_host
from ahn.triage import CHECKS, CHECKS_KO, next_checks, recommendation

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures" / "gov_registry"
US_CSV = (FIXTURES / "us_current_full_sample.csv").read_bytes()
UK_CSV = (FIXTURES / "uk_domain_names_sample.csv").read_bytes()
UK_API = (FIXTURES / "uk_content_api_sample.json").read_bytes()
UK_CSV_URL = "https://assets.publishing.service.gov.uk/media/69cbd582024cdf09254f3f7d/List_of_.gov.uk_domain_names_as_of_31_March_2026_1.csv"
NOW = datetime(2026, 10, 20, 12, 0, tzinfo=timezone.utc)
# Words that would turn a registry fact into a verdict about a site; none may appear in the texts of this layer.
VERDICT_WORDS = re.compile(r"trusted|trustworthy|\bsafe\b|unsafe|legitimate|suspicious|fake|genuine|authentic|scam|phish|malicious"
                           r"|신뢰|안전|진짜|가짜|의심|사칭", re.I)


# --- Parsers (pure) ----------------------------------------------------------------------------------

def test_us_sample_parses_the_real_file_shape_and_keeps_no_contact_data():
    domains = parse_us_sample()
    assert len(domains) == 9
    assert domains["irs.gov"] == ("Federal - Executive", "Department of the Treasury", "Internal Revenue Service", "DC")
    assert domains["acton-ma.gov"][1] == "Town of Acton, Massachusetts"          # quoted field with a comma
    assert domains["ca.gov"] == ("State or territory", "State of California", None, "CA")  # empty cell -> None
    # The security contact email column and the city are never copied out of the file.
    flattened = json.dumps(domains)
    assert "@" not in flattened and "example.test" not in flattened and "jane.sample" not in flattened
    assert "Rancho Cordova" not in flattened and "Washington" not in flattened and "Huntsville" not in flattened


def parse_us_sample():
    return parse_us_small(US_CSV)


def parse_us_small(data: bytes):
    """Parse a small sample: the real minimum-row guard is for the full file and is tested separately."""
    original = REGISTRIES[US]
    REGISTRIES[US] = dataclasses.replace(original, min_rows=1)
    try:
        return parse_us(data)
    finally:
        REGISTRIES[US] = original


def test_uk_sample_reads_the_domain_column_only():
    original = REGISTRIES[UK]
    REGISTRIES[UK] = dataclasses.replace(original, min_rows=1)
    try:
        domains = parse_uk(UK_CSV)
    finally:
        REGISTRIES[UK] = original
    assert set(domains) == {"gsx.gov.uk", "hmrc.gov.uk", "ons.gov.uk", "service.gov.uk", "surreycc.gov.uk", "tfl.gov.uk", "www.gov.uk"}
    assert all(value == () for value in domains.values())      # no organisation or contact column is kept
    assert not any(name.startswith("_") for name in domains)   # DNS-record names such as _dmarc.gov.uk


def test_uk_parser_accepts_bom_and_extra_columns_and_skips_the_header():
    original = REGISTRIES[UK]
    REGISTRIES[UK] = dataclasses.replace(original, min_rows=1)
    try:
        data = "﻿Domain Name,Owner,Representing\r\nExample.GOV.UK,Some Council,Someone Named\r\nother.gov.uk,,\r\n".encode()
        assert set(parse_uk(data)) == {"example.gov.uk", "other.gov.uk"}
    finally:
        REGISTRIES[UK] = original


@pytest.mark.parametrize("data, label", [
    (b"", "format_changed"),
    (b"<!doctype html><html><body>Rate limited</body></html>", "format_changed"),
    (b"Domain name,Organization name\nirs.gov,Treasury\n", "format_changed"),                      # required columns missing
    (b"\xff\xfe\x00bad", "invalid_encoding"),
    (b"Domain name,Domain type,Organization name,Suborganization name,State\nnot a domain,City,X,,TX\n", "format_changed"),
])
def test_us_parser_rejects_a_changed_format(data, label):
    with pytest.raises(ListRejected) as raised:
        parse_us(data)
    assert raised.value.label == label


def test_us_parser_rejects_a_file_whose_rows_mostly_do_not_parse():
    header = "Domain name,Domain type,Organization name,Suborganization name,State\n"
    rows = "".join(f"d{i}.gov,City,X,,TX\n" for i in range(60)) + "".join(f"junk {i},City,X,,TX\n" for i in range(5))
    original = REGISTRIES[US]
    REGISTRIES[US] = dataclasses.replace(original, min_rows=1)
    try:
        with pytest.raises(ListRejected) as raised:
            parse_us((header + rows).encode())
        assert raised.value.label == "format_changed"
        assert len(parse_us((header + rows[: rows.index("junk")]).encode())) == 60
    finally:
        REGISTRIES[US] = original


def test_full_size_floor_rejects_a_tiny_list():
    with pytest.raises(ListRejected) as raised:
        parse_us(US_CSV)
    assert raised.value.label == "too_few_rows"
    with pytest.raises(ListRejected) as raised:
        parse_uk(UK_CSV)
    assert raised.value.label == "too_few_rows"


def test_uk_attachment_choice_takes_the_newest_csv_on_the_asset_host_only():
    assert newest_uk_csv(UK_API) == ("2026-03-31", UK_CSV_URL)   # not the 2030 PDF, not the older lists
    document = json.loads(UK_API)
    document["details"]["attachments"][0]["url"] = "https://evil.example/media/x/list.csv"
    assert newest_uk_csv(json.dumps(document).encode())[0] == "2025-01-13"
    for broken in (b"{}", b"not json", json.dumps({"details": {"attachments": []}}).encode()):
        with pytest.raises(ListRejected):
            newest_uk_csv(broken)


def test_downloader_refuses_any_host_that_is_not_a_list_publisher():
    # `http_get` here is the real function (imported at collection time; conftest replaces the module attribute
    # for the tests that run). The host check comes before any network use, so this touches nothing.
    for url in ("https://irs.gov/", "https://example.com/list.csv", "http://www.gov.uk/api/content/x", "https://data.go.kr/",
                "https://raw.githubusercontent.com.evil.example/x", "https://assets.publishing.service.gov.uk.evil.example/x.csv"):
        assert http_get(url, cap_bytes=10) == Fetched("error", detail="host_not_allowed")
    assert ALLOWED_HOSTS == {"raw.githubusercontent.com", "www.gov.uk", "assets.publishing.service.gov.uk"}


# --- Host rules (pure) -------------------------------------------------------------------------------

@pytest.mark.parametrize("host, suffix", [
    ("irs.gov", "gov"), ("hmrc.gov.uk", "gov.uk"), ("www.gov.uk", "gov.uk"), ("data.go.kr", "go.kr"), ("irs.gov.uk", "gov.uk"),
    ("example.com", None), ("naver.co.kr", None), ("or.kr", None), ("go.kr", None), ("gov.uk", None), ("xgov.uk", None),
    ("tenant.irs.gov", None), ("a.b.go.kr", None), ("github.io", None),
])
def test_suffix_rule_applies_to_registrable_names_only(host, suffix):
    rule = gov_registry._suffix_rule_for(host)
    assert (rule.suffix if rule else None) == suffix


def test_www_alias_is_answered_as_its_apex_and_other_subdomains_are_not():
    # host_facts is called with policy_host(): www.irs.gov is irs.gov, tenant.irs.gov stays ineligible (no facts).
    assert policy_host("www.irs.gov") == "irs.gov" and aggregate_eligible_host("irs.gov")
    assert policy_host("www.data.go.kr") == "data.go.kr"
    assert policy_host("www.gov.uk") == "www.gov.uk"           # already a registrable name under the gov.uk suffix
    assert policy_host("tenant.irs.gov") == "tenant.irs.gov" and not aggregate_eligible_host("tenant.irs.gov")
    assert policy_host("a.b.go.kr") == "a.b.go.kr" and not aggregate_eligible_host("a.b.go.kr")


def test_texts_of_this_layer_make_no_verdict():
    texts = [gov_registry.HOST_FACTS_NOTE]
    texts += [rule.text for rule in gov_registry.SUFFIX_RULES.values()]
    gov_codes = ("government_registry_listed", "government_suffix_restricted", "government_list_version_missing")
    texts += [CHECKS[code] for code in gov_codes] + [CHECKS_KO[code] for code in gov_codes]
    texts += [gov_registry.UK_ATTRIBUTION, *(r.name for r in REGISTRIES.values())]
    for text in texts:
        assert not VERDICT_WORDS.search(text), text
    for code in gov_codes:
        assert code in CHECKS and code in CHECKS_KO   # the search page shows the Korean text next to the English one


def test_new_next_check_codes_are_declared_in_the_response_model():
    declared = CheckResponse.model_json_schema()["$defs"]["NextCheck"]["properties"]["code"]["enum"]
    assert set(declared) == set(CHECKS)


def test_registry_facts_never_change_the_recommendation():
    facts = {"government_registry": {"status": "listed"}, "suffix_rule": {"suffix": "gov"}}
    for args in ((None, None, None, None), ("site_access", None, None, {"name": "X", "api_docs": "https://x.example/docs"})):
        assert recommendation(*args) == recommendation(*args)
        without = next_checks(args[0], args[1], "no_reports", None, args[2], args[3])
        with_facts = next_checks(args[0], args[1], "no_reports", None, args[2], args[3], host_facts=facts)
        assert [c for c in with_facts if c["code"] != "government_registry_listed"] == without
        assert [c["code"] for c in with_facts].count("government_registry_listed") == 1


# --- Database ----------------------------------------------------------------------------------------

TEST_URL = os.environ.get("AHN_TEST_DATABASE_URL", "")
needs_db = pytest.mark.skipif(not TEST_URL, reason="AHN_TEST_DATABASE_URL is required")

if TEST_URL:
    from fastapi.testclient import TestClient

    from ahn import service
    from ahn.access_profile import cached_profile
    from ahn.db import connect, migrate
    from ahn.main import create_app
    from ahn.service import PUBLIC_REPORTER_ID
    from ahn.settings import Settings


@pytest.fixture
def db(monkeypatch):
    settings = Settings(TEST_URL, "test-pepper-must-be-at-least-thirty-two-chars", public_mode=True, reporting_enabled=True,
                        collect_lookup_metrics=True)
    with connect(settings) as conn:
        if conn.info.dbname != "ahn_test":
            raise RuntimeError("Refusing to reset a database other than ahn_test")
    migrate(settings)
    with connect(settings) as conn:
        conn.execute("TRUNCATE audit_events, report_submissions, observations, incidents, reporters, lookup_metrics, "
                     "public_intake_daily, public_intake_host_daily, status_cache, status_sources, queried_hosts, "
                     "host_access_profile, background_reads, mcp_check_metrics, gov_domain_registry, gov_registry_versions "
                     "RESTART IDENTITY CASCADE")
        conn.execute("INSERT INTO reporters (id,key_hash,label) VALUES (%s,repeat('0',64),'public-unverified')", (PUBLIC_REPORTER_ID,))
        conn.execute("UPDATE service_flags SET enabled=true WHERE name IN ('public_intake','public_publication')")
    monkeypatch.delenv("AHN_HOST_FACTS", raising=False)
    # The fixtures are tiny: lower the full-size floors, keep every other protection.
    for key in (US, UK):
        monkeypatch.setitem(REGISTRIES, key, dataclasses.replace(REGISTRIES[key], min_rows=1))
    return settings


class Publisher:
    """A stand-in for the lists' publishers. ``routes`` maps a URL to a Fetched (or a function of the call)."""

    def __init__(self, routes):
        self.routes, self.calls = dict(routes), []

    def __call__(self, url, *, cap_bytes, etag=None):
        self.calls.append((url, etag))
        route = self.routes[url]
        return route(etag) if callable(route) else route

    def hosts(self):
        return {url.split("/")[2] for url, _ in self.calls}


US_URL = REGISTRIES[US].url
UK_API_URL = REGISTRIES[UK].url


def ok(body: bytes, etag: str | None = '"v1"') -> Fetched:
    return Fetched("ok", body, etag)


def rows(settings, sql, params=()):
    with connect(settings) as conn:
        return conn.execute(sql, params).fetchall()


def us_publisher(body=US_CSV, etag='"v1"'):
    return Publisher({US_URL: ok(body, etag)})


def uk_publisher(api=UK_API, csv=UK_CSV):
    return Publisher({UK_API_URL: ok(api, None), UK_CSV_URL: ok(csv, None)})


def run(settings, fetch, *, now=NOW, force=False):
    return {r["registry"]: r for r in gov_registry.refresh_due(settings, now=now, force=force, fetch=fetch)}


def stored(settings, key):
    return {r["domain"]: r for r in rows(settings, "SELECT * FROM gov_domain_registry WHERE registry=%s", (key,))}


def version(settings, key):
    return rows(settings, "SELECT * FROM gov_registry_versions WHERE registry=%s", (key,))[0]


@needs_db
def test_first_load_stores_the_us_list_and_its_provenance(db):
    result = run(db, Publisher({**us_publisher().routes, **uk_publisher().routes}))[US]
    assert result["outcome"] == "loaded" and result["rows"] == 9
    data = stored(db, US)
    assert data["irs.gov"]["organization"] == "Department of the Treasury" and data["irs.gov"]["suborganization"] == "Internal Revenue Service"
    assert data["ca.gov"]["suborganization"] is None
    header = version(db, US)
    assert header["row_count"] == 9 and header["etag"] == '"v1"' and len(header["content_sha256"]) == 64
    assert header["list_version"] == "2026-10-20"           # the UTC date these contents were first received
    assert header["fetched_at"] == NOW and header["last_error"] is None
    assert header["origin_operator"] and header["reader_version"].startswith("AgentHelpNetwork/")
    # No contact data reached the database in any column of any table of this layer.
    everything = json.dumps(rows(db, "SELECT * FROM gov_domain_registry") + rows(db, "SELECT * FROM gov_registry_versions"), default=str)
    assert "@" not in everything and "example.test" not in everything


@needs_db
def test_the_uk_list_is_found_through_the_content_api_and_versioned_by_its_date(db):
    publisher = Publisher({**us_publisher().routes, **uk_publisher().routes})
    result = run(db, publisher)[UK]
    assert result["outcome"] == "loaded" and result["rows"] == 7
    assert set(stored(db, UK)) == {"gsx.gov.uk", "hmrc.gov.uk", "ons.gov.uk", "service.gov.uk", "surreycc.gov.uk", "tfl.gov.uk", "www.gov.uk"}
    assert version(db, UK)["list_version"] == "2026-03-31" and version(db, UK)["source_url"] == UK_CSV_URL
    # Only the publishers were contacted: no request to any site named on a list.
    assert publisher.hosts() <= ALLOWED_HOSTS


@needs_db
def test_schedule_us_daily_uk_monthly_and_not_modified_costs_no_rewrite(db):
    publisher = Publisher({**us_publisher().routes, **uk_publisher().routes})
    run(db, publisher)
    calls = len(publisher.calls)
    assert [r["outcome"] for r in gov_registry.refresh_due(db, now=NOW + timedelta(hours=23), fetch=publisher)] == ["not_due", "not_due"]
    assert len(publisher.calls) == calls
    # 25 hours later the U.S. list is due (conditional request with the stored ETag); the UK list is not (30 days).
    publisher.routes[US_URL] = Fetched("not_modified", etag='"v1"')
    outcome = run(db, publisher, now=NOW + timedelta(hours=25))
    assert outcome[US]["outcome"] == "not_modified" and outcome[UK]["outcome"] == "not_due"
    assert publisher.calls[-1] == (US_URL, '"v1"')
    assert version(db, US)["fetched_at"] == NOW + timedelta(hours=25) and version(db, US)["loaded_at"] == NOW
    # After 31 days the UK check asks the content API only; the same "as of" date downloads nothing.
    before = len(publisher.calls)
    outcome = run(db, publisher, now=NOW + timedelta(days=31))
    assert outcome[UK]["outcome"] == "unchanged"
    assert [url for url, _ in publisher.calls[before:] if url == UK_CSV_URL] == []


@needs_db
def test_a_new_version_replaces_rows_and_drops_vanished_domains(db):
    run(db, Publisher({**us_publisher().routes, **uk_publisher().routes}))
    lines = US_CSV.decode().splitlines()
    changed = [line for line in lines if not line.startswith("unkira.gov")]
    changed = [line.replace("Internal Revenue Service", "Revenue Service") for line in changed]
    changed.append("brandnew.gov,City,City of Brandnew,,Brandnew,TX,(blank)")
    publisher = Publisher({US_URL: ok("\n".join(changed).encode(), '"v2"'), **uk_publisher().routes})
    outcome = run(db, publisher, now=NOW + timedelta(days=1, hours=1))
    assert outcome[US]["outcome"] == "loaded" and outcome[US]["rows"] == 9
    data = stored(db, US)
    assert "unkira.gov" not in data and data["brandnew.gov"]["organization"] == "City of Brandnew"
    assert data["irs.gov"]["suborganization"] == "Revenue Service"
    assert version(db, US)["list_version"] == "2026-10-21" and version(db, US)["etag"] == '"v2"'
    # Identical contents under a new ETag change nothing but the check time.
    publisher.routes[US_URL] = ok("\n".join(changed).encode(), '"v3"')
    outcome = run(db, publisher, now=NOW + timedelta(days=2, hours=2))
    assert outcome[US]["outcome"] == "unchanged" and version(db, US)["list_version"] == "2026-10-21"


@needs_db
@pytest.mark.parametrize("failure, label", [
    (Fetched("error", detail="http_503"), "http_503"),
    (Fetched("error", detail="redirected"), "redirected"),
    (Fetched("error", detail="too_large"), "too_large"),
    (Fetched("error", detail="read_timeout"), "read_timeout"),
    (ok(b"<!doctype html><html>Service unavailable</html>"), "format_changed"),
    (ok(b"Domain name,Domain type,Organization name\nirs.gov,Federal,Treasury\n"), "format_changed"),
    (ok(b""), "format_changed"),
])
def test_a_failed_or_odd_download_keeps_the_stored_data_and_logs_a_warning(db, caplog, failure, label):
    run(db, Publisher({**us_publisher().routes, **uk_publisher().routes}))
    before = stored(db, US)
    publisher = Publisher({US_URL: failure, **uk_publisher().routes})
    with caplog.at_level(logging.WARNING, logger="ahn.gov_registry"):
        result = run(db, publisher, now=NOW + timedelta(days=2))[US]
    assert result["outcome"] == "kept_previous" and result["reason"] == label and result["rows"] == 9
    assert stored(db, US) == before                                  # not one row changed
    header = version(db, US)
    assert header["last_error"] == label and header["fetched_at"] == NOW and header["content_sha256"]
    assert any(f"reason={label}" in record.getMessage() and record.levelno == logging.WARNING for record in caplog.records)
    # The warning carries a label and counts, never file content.
    assert not any("Service unavailable" in record.getMessage() for record in caplog.records)
    # The next attempt waits six hours, then succeeds and clears the error.
    assert gov_registry.refresh_due(db, now=NOW + timedelta(days=2, hours=5), fetch=publisher)[0]["outcome"] == "not_due"
    publisher.routes[US_URL] = ok(US_CSV, '"v9"')
    result = run(db, publisher, now=NOW + timedelta(days=2, hours=7))[US]
    assert result["outcome"] == "unchanged" and version(db, US)["last_error"] is None


@needs_db
def test_a_sharp_drop_in_rows_is_rejected_at_half_and_accepted_above(db):
    run(db, Publisher({**us_publisher().routes, **uk_publisher().routes}))
    head, body = US_CSV.decode().splitlines()[0], US_CSV.decode().splitlines()[1:]
    # 9 rows stored: 4 rows is under half and is rejected; 5 rows is accepted.
    four = Publisher({US_URL: ok((head + "\n" + "\n".join(body[:4])).encode(), '"v2"'), **uk_publisher().routes})
    result = run(db, four, now=NOW + timedelta(days=1, hours=1))[US]
    assert result["outcome"] == "kept_previous" and result["reason"] == "row_count_drop"
    assert len(stored(db, US)) == 9
    five = Publisher({US_URL: ok((head + "\n" + "\n".join(body[:5])).encode(), '"v3"'), **uk_publisher().routes})
    result = run(db, five, now=NOW + timedelta(days=1, hours=8))[US]
    assert result["outcome"] == "loaded" and len(stored(db, US)) == 5


@needs_db
def test_a_first_download_that_is_too_small_is_not_stored(db, monkeypatch):
    monkeypatch.setitem(REGISTRIES, US, dataclasses.replace(REGISTRIES[US], min_rows=5000))
    result = run(db, Publisher({**us_publisher().routes, **uk_publisher().routes}))[US]
    assert result["outcome"] == "kept_previous" and result["reason"] == "too_few_rows"
    assert stored(db, US) == {} and version(db, US)["list_version"] is None and version(db, US)["last_error"] == "too_few_rows"
    # Nothing loaded: the answer simply has no list entry, and says nothing wrong about the domain.
    with connect(db) as conn:
        facts = gov_registry.host_facts(conn, "irs.gov")
    assert "government_registry" not in facts and facts["suffix_rule"]["suffix"] == "gov"


@needs_db
def test_a_uk_api_failure_or_unusable_api_keeps_the_stored_list(db):
    for index, api in enumerate((Fetched("error", detail="http_500"), ok(b"<html>moved</html>", None))):
        run(db, Publisher({**us_publisher().routes, **uk_publisher().routes}), now=NOW + timedelta(days=100 * index))
        publisher = Publisher({US_URL: ok(US_CSV), UK_API_URL: api})
        later = NOW + timedelta(days=100 * index + 40)
        result = run(db, publisher, now=later)[UK]
        assert result["outcome"] == "kept_previous" and len(stored(db, UK)) == 7
        assert version(db, UK)["list_version"] == "2026-03-31"
        # The retry delay after a failure is six hours, not another 30 days.
        assert gov_registry.refresh_due(db, now=later + timedelta(hours=1), fetch=publisher)[1]["outcome"] == "not_due"
        publisher.routes[UK_API_URL] = ok(UK_API, None)
        assert run(db, publisher, now=later + timedelta(hours=7))[UK]["outcome"] == "unchanged"


@needs_db
def test_only_one_refresh_runs_at_a_time(db):
    publisher = Publisher({**us_publisher().routes, **uk_publisher().routes})
    with connect(db) as holder:
        holder.execute("SELECT pg_advisory_xact_lock(%s)", (gov_registry.LOCK_KEY,))
        assert gov_registry.refresh_due(db, now=NOW, fetch=publisher) == []
    assert publisher.calls == []


@needs_db
def test_the_switch_turns_the_download_and_the_answer_off(db, monkeypatch):
    run(db, Publisher({**us_publisher().routes, **uk_publisher().routes}))
    monkeypatch.setenv("AHN_HOST_FACTS", "0")
    publisher = Publisher({})
    assert gov_registry.refresh_due(db, now=NOW + timedelta(days=60), fetch=publisher) == [] and publisher.calls == []
    assert "host_facts" not in service.check(db, "irs.gov")


# --- Answer ------------------------------------------------------------------------------------------

def loaded(db):
    run(db, Publisher({**us_publisher().routes, **uk_publisher().routes}))
    return db


@needs_db
def test_listed_us_domain_carries_the_list_organisation_version_and_time(db):
    answer = service.check(loaded(db), "irs.gov")
    registry = answer["host_facts"]["government_registry"]
    assert registry["status"] == "listed" and registry["registry"] == "us_dotgov"
    assert registry["organization"] == "Department of the Treasury" and registry["suborganization"] == "Internal Revenue Service"
    assert registry["domain_type"] == "Federal - Executive" and registry["state"] == "DC"
    assert registry["list_version"] == "2026-10-20" and registry["fetched_at"] == NOW and registry["license"] == "CC0-1.0"
    assert answer["host_facts"]["suffix_rule"]["suffix"] == "gov"
    codes = [check["code"] for check in answer["next_checks"]]
    assert "government_registry_listed" in codes and "government_suffix_restricted" not in codes
    CheckResponse(**answer)   # the response model accepts exactly this shape


@needs_db
def test_www_alias_gets_the_apex_facts_and_other_subdomains_get_none(db):
    loaded(db)
    assert service.check(db, "www.irs.gov")["host_facts"]["government_registry"]["organization"] == "Department of the Treasury"
    assert "host_facts" not in service.check(db, "tenant.irs.gov")
    assert "host_facts" not in service.check(db, "www.tenant.irs.gov")


@needs_db
def test_uk_listing_carries_the_ogl_attribution_and_no_organisation(db):
    answer = service.check(loaded(db), "hmrc.gov.uk")
    registry = answer["host_facts"]["government_registry"]
    assert registry["status"] == "listed" and registry["registry"] == "uk_govuk" and registry["list_version"] == "2026-03-31"
    assert registry["license"] == "OGL-UK-3.0" and registry["attribution"] == gov_registry.UK_ATTRIBUTION
    assert "organization" not in registry and "domain_type" not in registry
    assert answer["host_facts"]["suffix_rule"]["suffix"] == "gov.uk"
    assert answer["host_facts"]["suffix_rule"]["source_url"].startswith("https://www.gov.uk/")


@needs_db
@pytest.mark.parametrize("host, suffix", [("not-in-the-list.gov.uk", "gov.uk"), ("not-in-the-list.gov", "gov")])
def test_an_unlisted_name_under_a_listed_suffix_is_a_statement_about_the_list_version(db, host, suffix):
    answer = service.check(loaded(db), host)
    registry = answer["host_facts"]["government_registry"]
    assert registry["status"] == "not_in_list_version" and registry["list_version"] and "organization" not in registry
    codes = [check["code"] for check in answer["next_checks"]]
    assert "government_list_version_missing" in codes and "government_registry_listed" not in codes
    assert answer["host_facts"]["suffix_rule"]["suffix"] == suffix
    assert answer["recommendation"]["action"] == "unknown"      # never turned into advice against the site
    assert not VERDICT_WORDS.search(json.dumps(answer["host_facts"], default=str) + json.dumps(answer["next_checks"]))


@needs_db
def test_go_kr_gets_the_suffix_rule_and_no_list_entry(db):
    answer = service.check(loaded(db), "data.go.kr")
    facts = answer["host_facts"]
    assert "government_registry" not in facts
    assert facts["suffix_rule"]["suffix"] == "go.kr" and facts["suffix_rule"]["rule"] == "registration_limited_to_government"
    assert facts["suffix_rule"]["source_url"] == "https://krnic.kisa.or.kr/jsp/eng/policy/domainNewRules.jsp"
    codes = [check["code"] for check in answer["next_checks"]]
    assert "government_suffix_restricted" in codes
    # .or.kr and .co.kr are not government-only: no field at all.
    for host in ("naver.co.kr", "kisa.or.kr"):
        assert "host_facts" not in service.check(db, host)


@needs_db
def test_ordinary_hosts_get_no_field_and_a_short_answer(db):
    answer = service.check(loaded(db), "github.com")
    assert "host_facts" not in answer
    assert all(not check["code"].startswith("government_") for check in answer["next_checks"])


@needs_db
def test_the_verdict_fields_do_not_depend_on_registry_facts(db, monkeypatch):
    loaded(db)
    for host in ("irs.gov", "not-in-the-list.gov", "hmrc.gov.uk", "data.go.kr", "github.com"):
        for error_class in (None, "site_access", "login_auth", "human_verification"):
            monkeypatch.setenv("AHN_HOST_FACTS", "1")
            with_facts = service.check(db, host, error_class)
            monkeypatch.setenv("AHN_HOST_FACTS", "0")
            without = service.check(db, host, error_class)
            assert with_facts["recommendation"] == without["recommendation"]
            assert with_facts["summary_code"] == without["summary_code"] and with_facts["evidence_level"] == without["evidence_level"]
            plain = [c for c in with_facts["next_checks"] if not c["code"].startswith("government_")]
            assert plain == without["next_checks"]
            ignored = {"next_checks", "generated_at", "lookup_activity", "feedback"}   # the lookup counter moves with every call; the token is new per answer
            assert {k: v for k, v in with_facts.items() if k not in ignored | {"host_facts"}} == \
                   {k: v for k, v in without.items() if k not in ignored}


@needs_db
def test_the_lookup_path_never_reaches_a_publisher(db, monkeypatch):
    loaded(db)
    called = []
    monkeypatch.setattr(gov_registry, "http_get", lambda *a, **k: called.append(a) or Fetched("error", detail="x"))
    for host in ("irs.gov", "hmrc.gov.uk", "data.go.kr", "example.com"):
        service.check(db, host)
    assert called == []


@needs_db
def test_rest_response_includes_the_field_only_where_it_applies(db):
    loaded(db)
    client = TestClient(create_app(db))
    answer = client.get("/v1/incidents/check", params={"site_host": "irs.gov"}).json()
    assert answer["host_facts"]["government_registry"]["organization"] == "Department of the Treasury"
    assert answer["host_facts"]["suffix_rule"]["rule"] == "registration_limited_to_government"
    assert "domain_type" in answer["host_facts"]["government_registry"]
    uk = client.get("/v1/incidents/check", params={"site_host": "hmrc.gov.uk"}).json()["host_facts"]
    assert "organization" not in uk["government_registry"] and uk["government_registry"]["attribution"]
    plain = client.get("/v1/incidents/check", params={"site_host": "example.com"}).json()
    assert "host_facts" not in plain
    assert "host_facts" in client.get("/openapi.json").json()["components"]["schemas"]["CheckResponse"]["properties"]


# --- Documents ---------------------------------------------------------------------------------------

def test_every_document_that_republishes_the_uk_list_carries_the_ogl_attribution():
    for relative in ("docs/api-contract.md", "docs/api-contract.en.md", "README.md", "README.ko.md", "ahn/llms.txt"):
        text = (ROOT / relative).read_text(encoding="utf-8")
        assert gov_registry.UK_ATTRIBUTION in text, f"{relative} lacks the Open Government Licence attribution"
        assert "host_facts" in text, f"{relative} does not describe host_facts"
