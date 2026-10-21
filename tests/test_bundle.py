"""Offline data bundle (ahn/bundle.py): contents, exclusions, limits, gzip and the switch. Pure: no database, no network."""

from __future__ import annotations

import copy
import gzip
import hashlib
import json
import os
from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("AHN_DATABASE_URL", "postgresql://invalid/unused")
os.environ.setdefault("AHN_KEY_PEPPER", "x" * 32)

from ahn import bundle, export  # noqa: E402
from ahn.access_profile import parse_robots  # noqa: E402
from ahn.main import create_app  # noqa: E402
from ahn.official_paths import ACCESS_MAX_AGE_DAYS, OFFICIAL_ACCESS, OFFICIAL_PATHS  # noqa: E402
from ahn.profile_warm import load_seed_hosts  # noqa: E402
from ahn.settings import Settings  # noqa: E402
from ahn.status_refresh import OFFICIAL_STATUS_PAGES, SOURCES  # noqa: E402

NOW = datetime(2026, 10, 20, 6, 0, tzinfo=timezone.utc)
CLOCK = NOW + timedelta(hours=1)  # what the routes take as "now"
SEEDS = frozenset({"github.com", "wikipedia.org", "zz-seed.example"})
LOOKED_UP = "looked-up-only.example"  # a host someone looked up that is on no seed list or curated table
OPERATOR = "agenthelpnetwork.com"
CREATOR = "Example Operator"  # AHN_OPERATOR_NAME; the real display name is kept out of the repository


def profile_row(host: str, **extra) -> dict:
    row = {"site_host": host, "fetched_at": NOW - timedelta(hours=3), "robots_status": "ok",
           "robots": parse_robots("User-agent: GPTBot\nDisallow: /\n\nUser-agent: *\nDisallow: /private\n"),
           "robots_detail": "http_200", "llms_txt_status": "absent", "llms_detail": "http_404", "read_host": host,
           "origin_operator": OPERATOR, "vantage": {"kind": "datacenter", "region": "US", "asn": None, "declared": True},
           "reader_version": "AgentHelpNetwork/0.2", "robots_sha256": "a" * 64, "llms_sha256": None}
    row.update(extra)
    return row


ROWS = [profile_row("zz-seed.example"), profile_row(LOOKED_UP), profile_row("github.com"), profile_row("wikipedia.org")]


@pytest.fixture(autouse=True)
def operator(monkeypatch):
    monkeypatch.setenv("AHN_OPERATOR_ID", OPERATOR)
    monkeypatch.setenv("AHN_OPERATOR_NAME", CREATOR)
    monkeypatch.delenv("AHN_EXPORT_KEY_ID", raising=False)


def bundle_settings(**extra) -> Settings:
    return Settings("postgresql://invalid/unused", "x" * 32, public_mode=True, public_bundle=True,
                    public_bundle_since=date(2026, 11, 3), **extra)


def records_of(built: bundle.Bundle) -> list[dict]:
    return bundle.read_records(built.body)


# --- What goes in ------------------------------------------------------------------------------

def test_meta_record_heads_the_file():
    records = records_of(bundle.build(ROWS, SEEDS, now=NOW))
    meta = records[0]
    assert meta["kind"] == "bundle_meta" and meta["schema_version"] == 1
    assert [r["kind"] for r in records[1:]].count("bundle_meta") == 0
    payload = meta["payload"]
    assert payload["generated_at"] == "2026-10-20T06:00:00Z"
    assert payload["valid_until"] == "2026-10-28T06:00:00Z"  # build + 8 days: the seed list is re-read about weekly
    assert payload["record_count"] == len(records) - 1
    assert payload["counts"] == {kind: sum(1 for r in records if r["kind"] == kind) for kind in bundle.KINDS}
    assert payload["license"] == "CC-BY-4.0" and payload["license_url"] == "https://creativecommons.org/licenses/by/4.0/"
    # The Creative Commons license chooser's marking format: "<title> © <year> by <creator> is licensed under CC BY 4.0. ..."
    assert payload["creator"] == CREATOR
    assert payload["notice"] == ("Agent Help Network offline bundle © 2026 by Example Operator is licensed under CC BY 4.0. "
                                 "To view a copy of this license, visit https://creativecommons.org/licenses/by/4.0/")
    assert payload["attribution"] == ("Source: Agent Help Network offline bundle by Example Operator (agenthelpnetwork.com), "
                                      "2026-10-20T06:00:00Z, CC BY 4.0")
    assert "not licensed under CC BY 4.0" in payload["third_party_quotes"]
    assert "Tranco" in payload["status_source_note"] and "no rank" in payload["status_source_note"]
    assert payload["schema_url"].endswith("docs/api-contract.en.md#offline-bundle")
    assert payload["includes"] == list(bundle.KINDS) and payload["excludes"] == list(bundle.EXCLUDED)
    assert meta["source_url"] == "https://agenthelpnetwork.com/v1/bundle/latest.jsonl.gz"
    assert meta["record_id"] == "ahn:agenthelpnetwork.com:bundle_meta:agenthelpnetwork.com:2026-10-20T06:00:00Z"
    assert meta["expires_at"] == "2026-10-22T06:00:00Z"


def test_only_seed_hosts_reads_and_curated_tables_go_in():
    built = bundle.build(ROWS, SEEDS, now=NOW)
    records = records_of(built)
    profiles = [r["payload"]["site_host"] for r in records if r["kind"] == "access_profile"]
    assert profiles == ["github.com", "wikipedia.org", "zz-seed.example"]
    assert LOOKED_UP.encode() not in gzip.decompress(built.body)  # anywhere in the file, not just as a record
    assert {r["payload"]["site_host"] for r in records if r["kind"] == "official_path"} == set(OFFICIAL_PATHS)
    assert {r["payload"]["site_host"] for r in records if r["kind"] == "status_source"} == set(SOURCES) | set(OFFICIAL_STATUS_PAGES)
    assert built.counts == {"access_profile": 3, "official_path": len(OFFICIAL_PATHS),
                            "status_source": len(set(SOURCES) | set(OFFICIAL_STATUS_PAGES))}


def test_the_licensor_is_the_operator_display_name(monkeypatch):
    monkeypatch.delenv("AHN_OPERATOR_NAME")
    with pytest.raises(bundle.BundleError, match="AHN_OPERATOR_NAME"):
        bundle.build(ROWS, SEEDS, now=NOW)


def test_access_profile_records_are_the_cli_export_records_cut_to_the_day():
    records = records_of(bundle.build(ROWS, SEEDS, now=NOW))
    github = next(r for r in records if r["kind"] == "access_profile" and r["payload"]["site_host"] == "github.com")
    day = datetime(2026, 10, 20, tzinfo=timezone.utc)  # read at 03:00 UTC, published as the day
    assert github == json.loads(json.dumps(export.access_profile_envelope(profile_row("github.com", fetched_at=day))))
    assert github["observed_at"] == github["received_at"] == "2026-10-20T00:00:00Z"
    assert github["record_id"] == "ahn:agenthelpnetwork.com:access_profile:github.com:2026-10-20T00:00:00Z"
    assert all(r["observed_at"].endswith("T00:00:00Z") for r in records if r["kind"] == "access_profile")


CONTENT_USE = {  # the stored shape of PR #38 (head 0f74955)
    "vocab": "ahn-content-use/1", "formats": ["cloudflare_content_signal"],
    "signals": {"search": "yes", "ai_input": "no", "ai_train": "no", "use": None}, "usage": None,
    "path_specific_samples": ["/docs/"],
    "named_groups": [{"agents": ["GPTBot"], "other_agents": 1, "signals": {"search": "yes", "ai_input": "unset", "ai_train": "no", "use": None},
                      "usage": {"train_ai": "no", "ai_use": "unset", "search": "yes"}, "path_specific_samples": []}],
    "named_groups_total": 1, "vendor_default_hint": False, "ai_input_declared_no": True, "ai_input_no_agents": ["ChatGPT-User"],
    "ignored_keys": [], "robots_sha256": "b" * 64,
}


def test_content_use_passes_its_own_allowlist():
    record = export.access_profile_envelope(profile_row("github.com"))
    record["payload"]["content_use"] = copy.deepcopy(CONTENT_USE)
    bundle.check_record(record, seed_hosts=SEEDS, operator=OPERATOR)


@pytest.mark.parametrize("mutate", [
    lambda c: c.__setitem__("raw_lines", ["Content-Signal: x"]),
    lambda c: c["signals"].__setitem__("extra", "yes"),
    lambda c: c["named_groups"][0].__setitem__("user_agents_raw", ["x"]),
    lambda c: c["named_groups"][0]["usage"].__setitem__("other", "no"),
])
def test_content_use_outside_its_allowlist_fails(mutate):
    record = export.access_profile_envelope(profile_row("github.com"))
    record["payload"]["content_use"] = copy.deepcopy(CONTENT_USE)
    mutate(record["payload"]["content_use"])
    with pytest.raises(bundle.BundleError):
        bundle.check_record(record, seed_hosts=SEEDS, operator=OPERATOR)


def test_official_path_access_goes_in_only_while_current():
    host = next(iter(OFFICIAL_ACCESS))
    verified = date.fromisoformat(OFFICIAL_ACCESS[host]["verified_at"])
    fresh = bundle.official_path_record(host, datetime.combine(verified, datetime.min.time(), timezone.utc))
    assert fresh["payload"]["access"] == json.loads(json.dumps(OFFICIAL_ACCESS[host]))
    bundle.check_record(fresh, seed_hosts=SEEDS, operator=OPERATOR)
    # The same 90-day rule as the API, held for the whole validity of the file (build + 8 days).
    late = datetime.combine(verified + timedelta(days=ACCESS_MAX_AGE_DAYS - 7), datetime.min.time(), timezone.utc)
    assert "access" not in bundle.official_path_record(host, late)["payload"]
    assert "access" in bundle.official_path_record(host, late - timedelta(days=1))["payload"]


@pytest.mark.parametrize("mutate", [
    lambda a: a.__setitem__("contact_email", "x"),
    lambda a: a["auth"][0].__setitem__("secret", "x"),
    lambda a: a["terms"].__setitem__("interpretation", "allowed"),
])
def test_official_path_access_outside_its_allowlist_fails(mutate):
    host = next(h for h, a in OFFICIAL_ACCESS.items() if a.get("auth") and a.get("terms"))
    verified = date.fromisoformat(OFFICIAL_ACCESS[host]["verified_at"])
    record = bundle.official_path_record(host, datetime.combine(verified, datetime.min.time(), timezone.utc))
    mutate(record["payload"]["access"])
    with pytest.raises(bundle.BundleError):
        bundle.check_record(record, seed_hosts=SEEDS, operator=OPERATOR)


def test_status_sources_carry_the_mapping_never_a_feed_value():
    records = {r["payload"]["site_host"]: r for r in records_of(bundle.build([], SEEDS, now=NOW)) if r["kind"] == "status_source"}
    github = records["github.com"]
    assert github["payload"] == {"site_host": "github.com", "source_name": SOURCES["github.com"].name,
                                 "status_page_url": SOURCES["github.com"].page_url, "api_url": SOURCES["github.com"].api_url,
                                 "feed_kind": "statuspage"}
    link_only = next(host for host in OFFICIAL_STATUS_PAGES if host not in SOURCES)
    assert records[link_only]["payload"]["api_url"] is None and records[link_only]["payload"]["feed_kind"] is None
    assert records[link_only]["source_url"] == OFFICIAL_STATUS_PAGES[link_only][1]
    for record in records.values():
        assert "indicator" not in record["payload"] and "source_updated_at" not in record["payload"]


def test_records_are_sorted_by_host_never_in_seed_order_and_carry_no_rank():
    seeds = load_seed_hosts()
    rows = [profile_row(host) for host in seeds[:60]]  # file order follows a popularity ranking in part
    records = records_of(bundle.build(list(reversed(rows)), seeds, now=NOW))
    profiles = [r["payload"]["site_host"] for r in records if r["kind"] == "access_profile"]
    assert profiles == sorted(seeds[:60])

    def keys(value):
        if isinstance(value, dict):
            for key, item in value.items():
                yield key
                yield from keys(item)
        elif isinstance(value, list):
            for item in value:
                yield from keys(item)

    found = {key.lower() for record in records for key in keys(record)}
    assert not {key for key in found if "rank" in key or "seed" in key or "tranco" in key}


def test_seed_hosts_from_the_popularity_ranking_stay_out(tmp_path):
    every, curated = load_seed_hosts(), bundle.bundle_seed_hosts()
    ranked = set(every) - set(curated)
    assert curated and ranked and curated == [host for host in every if host not in ranked]  # same order, a strict cut
    assert "github.com" in curated and "gstatic.com" in ranked
    # The block opened by the Tranco comment (PR #40): about 570 eligible hosts; everything above it stays in.
    assert len(curated) == 1798 and 560 <= len(ranked) <= 580
    built = bundle.build([profile_row(host) for host in every], curated, now=NOW)
    assert {r["payload"]["site_host"] for r in records_of(built) if r["kind"] == "access_profile"} == set(curated)
    # Without the marker nobody can tell which hosts are curated: refuse rather than publish the ranking's hosts.
    # The leading comment block may cite Tranco; a later comment above the marker naming it fails the build.
    cited = tmp_path / "cited.txt"
    cited.write_text("# Tranco: cited in the header\ngithub.com\n# Hosts chosen from the Tranco top 1,000\ngstatic.com\n",
                     encoding="utf-8")
    assert bundle.bundle_seed_hosts(cited) == ["github.com"]
    second_block = tmp_path / "second.txt"
    second_block.write_text("# header\ngithub.com\n# More hosts from the Tranco list\nexample.org\n"
                            "# Hosts chosen from the Tranco top 1,000\ngstatic.com\n", encoding="utf-8")
    with pytest.raises(bundle.BundleError, match="names Tranco"):
        bundle.bundle_seed_hosts(second_block)
    unmarked = tmp_path / "seed_hosts.txt"
    unmarked.write_text("github.com\n# Ranking block: an older marker is not accepted\ngstatic.com\n", encoding="utf-8")
    with pytest.raises(bundle.BundleError, match="marker"):
        bundle.bundle_seed_hosts(unmarked)


def test_every_curated_entry_passes_the_allowlist():
    # A new field in OFFICIAL_PATHS or a status Source fails here until the bundle allowlist (and the policy) cover it.
    seeds = frozenset(load_seed_hosts())
    for host in OFFICIAL_PATHS:
        bundle.check_record(bundle.official_path_record(host, NOW), seed_hosts=seeds, operator=OPERATOR)
    for host in set(SOURCES) | set(OFFICIAL_STATUS_PAGES):
        bundle.check_record(bundle.status_source_record(host, NOW), seed_hosts=seeds, operator=OPERATOR)


# --- What never goes in ------------------------------------------------------------------------

def good_record() -> dict:
    return export.access_profile_envelope(profile_row("github.com"))


@pytest.mark.parametrize("mutate", [
    lambda r: r.__setitem__("ip_digest", "x"),                                   # envelope: forbidden
    lambda r: r.__setitem__("extra", 1),                                         # envelope: not allowlisted
    lambda r: r["payload"].__setitem__("reporter_id", "x"),                      # payload: forbidden
    lambda r: r["payload"].__setitem__("lookup_activity", "low"),                # payload: forbidden
    lambda r: r["payload"].__setitem__("observations", []),                      # payload: reports
    lambda r: r["payload"].__setitem__("seed_rank", 7),                          # payload: rank
    lambda r: r["payload"].__setitem__("unknown_field", 1),                      # payload: not allowlisted
    lambda r: r["payload"]["robots"].__setitem__("key_hash", "x"),               # nested: forbidden
    lambda r: r["payload"]["robots"]["rules"][0].__setitem__("client_digest", "x"),  # deeper: forbidden
    lambda r: r["payload"]["robots"]["rules"][0].__setitem__("note", "x"),       # rule: not allowlisted
    lambda r: r["vantage"].__setitem__("ip", "203.0.113.9"),                     # vantage: forbidden
    lambda r: r.__setitem__("signature", {"alg": "ed25519", "key_id": "k", "sig": "s", "idempotency_key": "x"}),
])
def test_a_single_forbidden_or_unlisted_field_fails_the_record(mutate):
    record = copy.deepcopy(good_record())
    bundle.check_record(record, seed_hosts=SEEDS, operator=OPERATOR)  # the unchanged record passes
    mutate(record)
    with pytest.raises(bundle.BundleError):
        bundle.check_record(record, seed_hosts=SEEDS, operator=OPERATOR)


def test_a_forbidden_field_fails_the_whole_build(monkeypatch):
    original = export.access_profile_envelope

    def leaky(row):
        record = original(row)
        record["payload"]["lookup_count"] = 12
        return record

    monkeypatch.setattr(export, "access_profile_envelope", leaky)
    with pytest.raises(bundle.BundleError, match="lookup_count"):
        bundle.build(ROWS, SEEDS, now=NOW)


@pytest.mark.parametrize("record", [
    lambda: export.access_profile_envelope(profile_row(LOOKED_UP)),
    lambda: {**bundle.official_path_record("github.com", NOW), "payload": {**bundle.official_path_record("github.com", NOW)["payload"], "site_host": LOOKED_UP}},
    lambda: {**bundle.status_source_record("github.com", NOW), "payload": {**bundle.status_source_record("github.com", NOW)["payload"], "site_host": LOOKED_UP}},
    lambda: export.envelope("observation", site_host="github.com", observed_at=NOW, received_at=NOW, payload={"site_host": "github.com"}),
    lambda: export.envelope("status", site_host="github.com", observed_at=NOW, received_at=NOW, payload={"site_host": "github.com"}),
])
def test_hosts_outside_the_seed_list_and_curated_tables_and_other_kinds_are_refused(record):
    with pytest.raises(bundle.BundleError):
        bundle.check_record(record(), seed_hosts=SEEDS, operator=OPERATOR)


def test_record_id_must_name_its_own_host():
    record = good_record()
    record["record_id"] = record["record_id"].replace("github.com", "wikipedia.org")
    with pytest.raises(bundle.BundleError, match="record_id"):
        bundle.check_record(record, seed_hosts=SEEDS, operator=OPERATOR)


# --- Limits and integrity ----------------------------------------------------------------------

@pytest.mark.parametrize("name,value,match", [
    ("MAX_RECORDS", 10, "records exceed"),
    ("MAX_RAW_BYTES", 10_000, "uncompressed exceed"),
    ("MAX_COMPRESSED_BYTES", 10_000, "compressed exceed"),
])
def test_size_limits_fail_the_build(monkeypatch, name, value, match):
    monkeypatch.setattr(bundle, name, value)
    with pytest.raises(bundle.BundleError, match=match):
        bundle.build(ROWS, SEEDS, now=NOW)


def test_limits_hold_for_the_real_seed_list_with_heavy_robots_files():
    # Every seed host with a heavy robots summary: six 80-character sample paths for every agent token.
    heavy = parse_robots("User-agent: *\n" + "\n".join(f"Disallow: /{n}/" + "p" * 76 for n in range(8))
                         + "\n" + "\n".join(f"Sitemap: https://example.org/{n}.xml" for n in range(5)))
    seeds = load_seed_hosts()
    built = bundle.build([profile_row(host, robots=heavy) for host in seeds], seeds, now=NOW)
    assert built.record_count < bundle.MAX_RECORDS
    assert len(built.body) < bundle.MAX_COMPRESSED_BYTES and built.raw_bytes < bundle.MAX_RAW_BYTES


def test_gzip_is_whole_deterministic_and_matches_its_sha256():
    built = bundle.build(ROWS, SEEDS, now=NOW)
    assert built.body[:2] == b"\x1f\x8b"
    assert hashlib.sha256(built.body).hexdigest() == built.sha256
    raw = gzip.decompress(built.body)
    assert len(raw) == built.raw_bytes and raw.endswith(b"\n")
    assert all(json.loads(line)["schema_version"] == 1 for line in raw.splitlines())
    assert bundle.build(ROWS, SEEDS, now=NOW).body == built.body  # same input, same bytes, same hash
    assert built.filename == "ahn-bundle-20261020T060000Z.jsonl.gz"
    corrupt = bytearray(built.body)
    corrupt[len(corrupt) // 2] ^= 0xFF
    with pytest.raises(Exception):
        bundle.read_records(bytes(corrupt))  # the gzip CRC catches a damaged download
    with pytest.raises(Exception):
        bundle.read_records(built.body[:-8])  # and a truncated one


def test_records_are_signed_only_with_a_key():
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    assert all(r["signature"] is None for r in records_of(bundle.build(ROWS, SEEDS, now=NOW)))
    key = Ed25519PrivateKey.generate()
    records = records_of(bundle.build(ROWS, SEEDS, now=NOW, signing_key=key, kid="agenthelpnetwork.com:2026-10"))
    assert all(r["signature"]["key_id"] == "agenthelpnetwork.com:2026-10" for r in records)
    assert all(export.verify(r, key.public_key()) for r in records)


# --- The switch ----------------------------------------------------------------------------------

def test_the_switch_is_off_by_default(monkeypatch):
    monkeypatch.setenv("AHN_DATABASE_URL", "postgresql://invalid/unused")
    monkeypatch.setenv("AHN_KEY_PEPPER", "x" * 32)
    monkeypatch.delenv("AHN_PUBLIC_BUNDLE", raising=False)
    monkeypatch.delenv("AHN_PUBLIC_BUNDLE_SINCE", raising=False)
    assert Settings.from_env().public_bundle is False and not bundle.enabled()
    monkeypatch.setenv("AHN_PUBLIC_BUNDLE", "1")
    with pytest.raises(RuntimeError, match="AHN_PUBLIC_BUNDLE_SINCE"):  # the policy date is required with the switch
        Settings.from_env()
    monkeypatch.setenv("AHN_PUBLIC_BUNDLE_SINCE", "2026-11-03")
    settings = Settings.from_env()
    assert settings.public_bundle is True and settings.public_bundle_since == date(2026, 11, 3) and bundle.enabled()
    for value in ("0", "true", "yes", ""):
        monkeypatch.setenv("AHN_PUBLIC_BUNDLE", value)
        assert Settings.from_env().public_bundle is False


def test_switch_off_means_404_and_no_mention():
    client = TestClient(create_app(Settings("postgresql://invalid/unused", "x" * 32, public_mode=True)))
    for path in (bundle.PATH, bundle.SHA256_PATH):
        assert client.get(path).status_code == 404
        assert client.head(path).status_code in {404, 405}
    assert "Offline bundle" not in client.get("/llms.txt").text  # described only while it is served


@pytest.fixture
def served(monkeypatch):
    built = bundle.build(ROWS, SEEDS, now=NOW)
    state = {"current": (built.sha256, built.generated_at, built.body), "used": 0}

    def take(settings, size, day):  # the database budget, in memory for these pure tests
        if state["used"] + size > bundle.DAILY_BYTES:
            return False
        state["used"] += size
        return True

    monkeypatch.setattr(bundle._Snapshot, "current", lambda self, settings, now: state["current"])
    monkeypatch.setattr(bundle._DbBudget, "take", staticmethod(take))
    client = TestClient(create_app(bundle_settings()), headers={"CF-Connecting-IP": "203.0.113.7"})
    return client, built, state


@pytest.fixture(autouse=True)
def frozen_clock(monkeypatch):
    # The routes compare the stored file's age with "now"; tests run against the 2026-10-20 06:00 build.
    original = bundle.add_routes
    monkeypatch.setattr(bundle, "add_routes", lambda app, settings, **kw: original(app, settings, clock=lambda: CLOCK))


def test_switch_on_serves_the_file_and_its_sha256(served):
    client, built, _state = served
    response = client.get(bundle.PATH)
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/gzip" and "content-encoding" not in response.headers
    assert response.content == built.body
    assert response.headers["etag"] == f'"{built.sha256}"'
    assert response.headers["cache-control"] == "public, max-age=3600" and response.headers["accept-ranges"] == "none"
    assert response.headers["last-modified"] == "Tue, 20 Oct 2026 06:00:00 GMT"
    assert built.filename in response.headers["content-disposition"]
    digest = hashlib.sha256(response.content).digest()
    import base64
    assert response.headers["repr-digest"] == f"sha-256=:{base64.b64encode(digest).decode()}:"
    sha = client.get(bundle.SHA256_PATH)
    assert sha.status_code == 200 and sha.text == f"{built.sha256}  {built.filename}\n"
    head = client.head(bundle.PATH)
    assert head.status_code == 200 and head.headers["content-length"] == str(len(built.body)) and head.content == b""


def test_unchanged_file_answers_304(served):
    client, built, _state = served
    assert client.get(bundle.PATH, headers={"If-None-Match": f'"{built.sha256}"'}).status_code == 304
    assert client.get(bundle.PATH, headers={"If-None-Match": f'W/"{built.sha256}", "other"'}).status_code == 304
    assert client.get(bundle.PATH, headers={"If-None-Match": '"older"'}).status_code == 200


def test_switch_on_without_a_stored_file_is_503(served):
    client, _built, state = served
    state["current"] = None
    for path in (bundle.PATH, bundle.SHA256_PATH):
        response = client.get(path)
        assert response.status_code == 503 and response.json() == {"error": "bundle_not_ready"}


def test_a_file_older_than_48_hours_is_not_served(served):
    client, built, state = served
    state["current"] = (built.sha256, CLOCK - timedelta(hours=47, minutes=59), built.body)
    assert client.get(bundle.PATH).status_code == 200
    state["current"] = (built.sha256, CLOCK - timedelta(hours=48, minutes=1), built.body)
    for path in (bundle.PATH, bundle.SHA256_PATH):
        response = client.get(path)
        assert response.status_code == 503 and response.json() == {"error": "bundle_stale"}


def test_a_range_request_gets_the_whole_file_and_counts(served):
    client, built, state = served
    response = client.get(bundle.PATH, headers={"Range": "bytes=0-99"})
    assert response.status_code == 200 and response.content == built.body and state["used"] == len(built.body)


def test_each_client_gets_three_full_downloads_a_day(served):
    client, built, state = served
    for _ in range(bundle.CLIENT_DAILY_DOWNLOADS):
        assert client.get(bundle.PATH).status_code == 200
    refused = client.get(bundle.PATH)
    assert refused.status_code == 429 and refused.json() == {"error": "bundle_client_limit"}
    assert int(refused.headers["retry-after"]) >= 60
    # Conditional and HEAD requests are not downloads; another client is counted on its own.
    assert client.get(bundle.PATH, headers={"If-None-Match": f'"{built.sha256}"'}).status_code == 304
    assert client.head(bundle.PATH).status_code == 200
    assert client.get(bundle.PATH, headers={"CF-Connecting-IP": "198.51.100.4"}).status_code == 200
    assert state["used"] == 4 * len(built.body)


def test_the_bundle_is_in_llms_txt_only_while_it_is_served(served):
    client, _built, _state = served
    text = client.get("/llms.txt").text
    assert "## Offline bundle" in text and text.index("## Offline bundle") < text.index("## Pages")


def test_daily_download_budget_returns_429(served, monkeypatch):
    client, built, _state = served
    monkeypatch.setattr(bundle, "DAILY_BYTES", 0)
    response = client.get(bundle.PATH)
    assert response.status_code == 429 and response.json() == {"error": "bundle_daily_limit"}
    assert int(response.headers["retry-after"]) >= 60
    # Conditional and HEAD requests send no body and are not counted.
    assert client.get(bundle.PATH, headers={"If-None-Match": f'"{built.sha256}"'}).status_code == 304
    assert client.head(bundle.PATH).status_code == 200


def test_client_counts_reset_each_utc_day_and_keep_no_address():
    clients = bundle._ClientDownloads("x" * 32)
    for _ in range(bundle.CLIENT_DAILY_DOWNLOADS):
        assert clients.allowed("203.0.113.7", NOW.date())
        clients.count("203.0.113.7", NOW.date())
    assert not clients.allowed("203.0.113.7", NOW.date())
    assert "203.0.113.7" not in repr(clients.counts)
    assert clients.allowed("203.0.113.7", NOW.date() + timedelta(days=1))
