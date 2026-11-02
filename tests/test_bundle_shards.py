"""Thin shards, manifest and changes file of the offline bundle (ahn/bundle_shards.py, D74). Pure: no database, no network."""

from __future__ import annotations

import gzip
import hashlib
import json
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("AHN_DATABASE_URL", "postgresql://invalid/unused")
os.environ.setdefault("AHN_KEY_PEPPER", "x" * 32)

from ahn import bundle, bundle_shards, export  # noqa: E402
from ahn.access_profile import parse_robots  # noqa: E402
from ahn.main import create_app  # noqa: E402
from ahn.profile_warm import load_seed_hosts  # noqa: E402
from test_bundle import CLOCK, NOW, ROWS, SEEDS, bundle_settings, frozen_clock, operator, profile_row  # noqa: E402,F401

ROOT = Path(__file__).resolve().parent.parent
MIB = 1024 * 1024


def wire_size(client: TestClient, path: str, **kwargs) -> tuple[int, bytes]:
    """The bytes sent on the wire (still gzip) and the body a client sees."""
    with client.stream("GET", path, **kwargs) as response:
        response.read()
    assert response.status_code == 200, (path, response.status_code)
    return response.num_bytes_downloaded, response.content


@pytest.fixture
def served(monkeypatch):
    seeds = load_seed_hosts()
    built = bundle.build([profile_row(host) for host in seeds], seeds, now=NOW)
    state = {"current": (built.sha256, built.generated_at, built.body), "used": 0}

    def take(settings, size, day):
        if state["used"] + size > bundle.DAILY_BYTES:
            return False
        state["used"] += size
        return True

    monkeypatch.setattr(bundle._Snapshot, "current", lambda self, settings, now: state["current"])
    monkeypatch.setattr(bundle._DbBudget, "take", staticmethod(take))
    monkeypatch.delenv(bundle_shards.SWITCH_ENV, raising=False)
    client = TestClient(create_app(bundle_settings()), headers={"CF-Connecting-IP": "203.0.113.7"})
    return client, built, state


def shard_path(key: str) -> str:
    return f"{bundle_shards.SHARDS_PATH}/{key}.json"


def small_bundle(rows, now=NOW):
    return bundle.build(rows, SEEDS, now=now)


def shard_set(rows, now=NOW) -> bundle_shards.ShardSet:
    built = small_bundle(rows, now)
    return bundle_shards.ShardSet(built.sha256, built.generated_at, built.body)


# --- Assignment ------------------------------------------------------------------------------------

def test_a_host_goes_to_the_first_byte_of_the_sha256_of_its_lowercase_name():
    for host in ("github.com", "wikipedia.org", "example.org"):
        assert bundle_shards.shard_id(host) == hashlib.sha256(host.encode()).hexdigest()[:2]
    assert bundle_shards.shard_id("GitHub.COM.") == bundle_shards.shard_id("github.com") == bundle_shards.shard_id(" github.com ")
    seen = {bundle_shards.shard_id(host) for host in load_seed_hosts()}
    assert all(re.fullmatch(r"[0-9a-f]{2}", key) for key in seen) and len(seen) > 200  # spread over the 256 keys


def test_assignment_is_deterministic_across_builds():
    first, second = shard_set(ROWS), shard_set(list(reversed(ROWS)))
    assert {k: v[2] for k, v in first.files.items()} == {k: v[2] for k, v in second.files.items()}
    assert first.entries == second.entries


def test_every_record_lands_in_exactly_one_shard_and_the_union_is_the_thin_form():
    seeds = load_seed_hosts()
    built = bundle.build([profile_row(host) for host in seeds], seeds, now=NOW)
    records = bundle.read_records(built.body)
    shards = bundle_shards.ShardSet(built.sha256, built.generated_at, built.body)
    assert sorted(shards.files) == [f"{n:02x}" for n in range(256)]  # every shard exists, empty or not
    union, placed = {}, 0
    for key, (raw, _gz, _digest) in shards.files.items():
        document = json.loads(raw)
        assert document["shard"] == key and document["schema"] == bundle_shards.SHARD_SCHEMA
        for host, sections in document["hosts"].items():
            assert bundle_shards.shard_id(host) == key and host not in union, host
            union[host] = sections
            placed += len(sections)
    assert union == bundle_shards.thin_hosts(records)
    assert placed == len(records) - 1 == built.record_count  # every record but the head, once
    assert sum(entry["hosts"] for entry in shards.entries) == len(union)


def test_thin_records_hold_no_quotes_signatures_or_rule_lists():
    shards = shard_set([profile_row("github.com")])
    allowed = {"access": {"robots", "llms_txt", "root_allowed", "crawl_delay", "agents_blocked", "content_signals",
                          "ai_input_declared_no", "read_host", "read_on"},
               "official": {"name", "api_docs", "developer_portal", "auth", "key_signup_url", "openapi_url",
                            "rate_limits_url", "sandbox_url", "official_mcp", "terms_url"},
               "status": {"name", "page", "api", "feed"}}
    seen = set()
    for host, sections in shards.hosts.items():
        for section, thin in sections.items():
            assert set(thin) <= allowed[section], (host, section, set(thin) - allowed[section])
            seen.add(section)
    assert seen == {"access", "official", "status"}
    text = b"".join(raw for raw, _gz, _digest in shards.files.values()).decode()
    assert '"rules"' not in text and '"signature"' not in text and '"quote"' not in text
    assert bundle._forbidden(json.loads(shards.files[bundle_shards.shard_id("github.com")][0])) is None
    assert shards.hosts["github.com"]["access"]["agents_blocked"] == ["GPTBot"]
    assert shards.hosts["github.com"]["access"]["read_on"] == "2026-10-20"


# --- Serving ---------------------------------------------------------------------------------------

def test_manifest_hashes_match_the_files_and_the_bytes_sent(served):
    client, built, _state = served
    manifest = client.get(bundle_shards.MANIFEST_PATH)
    assert manifest.status_code == 200
    assert manifest.headers["content-encoding"] == "gzip" and manifest.headers["content-type"].startswith("application/json")
    document = manifest.json()
    assert document["schema"] == bundle_shards.MANIFEST_SCHEMA and document["bundle_sha256"] == built.sha256
    assert [entry["id"] for entry in document["shards"]] == [f"{n:02x}" for n in range(256)]
    assert "signature" not in document  # no signing key here: a hash-only manifest
    for entry in document["shards"][:: 17] + document["shards"][-1:]:
        sent, body = wire_size(client, shard_path(entry["id"]))
        assert hashlib.sha256(body).hexdigest() == entry["sha256"] and len(body) == entry["bytes"]
        assert sent == entry["gzip_bytes"] and gzip.decompress(gzip.compress(body)) == body
        assert entry["built_at"] == document["generated_at"] == "2026-10-20T06:00:00Z"
    changes = document["changes"]
    sent, body = wire_size(client, changes["path"])
    assert hashlib.sha256(body).hexdigest() == changes["sha256"] and len(body) == changes["bytes"] and sent == changes["gzip_bytes"]


def test_the_manifest_is_signed_with_the_bundle_key_when_this_process_has_one(served, monkeypatch):
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    client, _built, _state = served
    key = Ed25519PrivateKey.generate()
    monkeypatch.setattr(bundle, "signing_from_env", lambda: (key, "agenthelpnetwork.com:2026-10"))
    document = client.get(bundle_shards.MANIFEST_PATH).json()
    assert document["signature"]["alg"] == "ed25519" and document["signature"]["key_id"] == "agenthelpnetwork.com:2026-10"
    assert export.verify(document, key.public_key())
    document["shards"][0]["sha256"] = "0" * 64
    assert not export.verify(document, key.public_key())


def test_shard_manifest_and_changes_answer_304_for_their_etag(served):
    client, _built, _state = served
    paths = [bundle_shards.MANIFEST_PATH, bundle_shards.CHANGES_PATH, shard_path("00"), shard_path("ff")]
    for path in paths:
        first = client.get(path)
        etag = first.headers["etag"]
        assert re.fullmatch(r'"[0-9a-f]{64}"', etag) and first.headers["cache-control"] == "public, max-age=3600"
        assert hashlib.sha256(first.content).hexdigest() == etag.strip('"')  # strong tag: the SHA-256 of the JSON
        again = client.get(path, headers={"If-None-Match": etag})
        assert again.status_code == 304 and again.content == b"" and again.headers["etag"] == etag
        assert client.get(path, headers={"If-None-Match": f'W/{etag}, "other"'}).status_code == 304
        assert client.get(path, headers={"If-None-Match": '"older"'}).status_code == 200
        head = client.head(path)
        assert head.status_code == 200 and head.content == b"" and head.headers["etag"] == etag


def test_unknown_shard_keys_are_404(served):
    client, _built, _state = served
    for key in ("zz", "FF", "0", "000", "0g", "manifest.js"):
        assert client.get(f"{bundle_shards.SHARDS_PATH}/{key}.json").status_code == 404, key
    assert client.get(f"{bundle_shards.SHARDS_PATH}/00").status_code == 404


def test_missing_or_stale_bundle_gives_the_same_503_as_the_bundle(served):
    client, built, state = served
    state["current"] = None
    for path in (bundle_shards.MANIFEST_PATH, bundle_shards.CHANGES_PATH, shard_path("00")):
        response = client.get(path)
        assert response.status_code == 503 and response.json() == {"error": "bundle_not_ready"}
    state["current"] = (built.sha256, CLOCK - timedelta(hours=49), built.body)
    for path in (bundle_shards.MANIFEST_PATH, bundle_shards.CHANGES_PATH, shard_path("00")):
        response = client.get(path)
        assert response.status_code == 503 and response.json() == {"error": "bundle_stale"}


# --- Budget ----------------------------------------------------------------------------------------

def test_the_shard_budget_is_50_mib_and_the_bundles_is_unchanged():
    assert bundle.SHARD_DAILY_BYTES == 50 * MIB and bundle.DAILY_BYTES == 25 * MIB and bundle.CLIENT_DAILY_DOWNLOADS == 3


def test_over_the_shard_budget_is_429_with_retry_after_and_304_and_head_are_free(served, monkeypatch):
    client, built, state = served
    sizes = {key: len(gz) for key, (_raw, gz, _d) in bundle_shards.ShardSet(built.sha256, built.generated_at, built.body).files.items()}
    first, second = "00", "01"
    monkeypatch.setattr(bundle, "SHARD_DAILY_BYTES", sizes[first] + sizes[second] - 1)
    assert client.get(shard_path(first)).status_code == 200
    refused = client.get(shard_path(second))
    assert refused.status_code == 429 and refused.json() == {"error": "bundle_shard_daily_limit"}
    assert int(refused.headers["retry-after"]) >= 60 and refused.headers["cache-control"] == "no-store"
    etag = client.head(shard_path(second)).headers["etag"]
    assert client.get(shard_path(second), headers={"If-None-Match": etag}).status_code == 304
    assert client.head(shard_path(second)).status_code == 200
    assert client.get(bundle_shards.MANIFEST_PATH).status_code == 429  # every new file draws on the same day
    assert state["used"] == 0  # nothing moved in the full bundle's database counter


def test_shard_and_bundle_budgets_do_not_mix(served, monkeypatch):
    client, built, state = served
    monkeypatch.setattr(bundle, "SHARD_DAILY_BYTES", 0)
    assert client.get(shard_path("00")).status_code == 429
    full = client.get(bundle.PATH)
    assert full.status_code == 200 and full.content == built.body and state["used"] == len(built.body)
    monkeypatch.setattr(bundle, "SHARD_DAILY_BYTES", 50 * MIB)
    monkeypatch.setattr(bundle, "DAILY_BYTES", 0)
    assert client.get(bundle.PATH).status_code == 429 and client.get(bundle.PATH).json() == {"error": "bundle_daily_limit"}
    for path in (bundle_shards.MANIFEST_PATH, bundle_shards.CHANGES_PATH, shard_path("00")):
        assert client.get(path).status_code == 200


def test_the_shard_budget_resets_each_utc_day():
    budget = bundle_shards.ShardBudget()
    day = NOW.date()
    assert budget.take(bundle.SHARD_DAILY_BYTES, day) and not budget.take(1, day)
    assert budget.take(1, day + timedelta(days=1))


def test_a_day_of_fifty_mib_buys_many_times_the_full_bundle(served):
    client, built, _state = served
    manifest = client.get(bundle_shards.MANIFEST_PATH).json()
    mean_gzip = sum(entry["gzip_bytes"] for entry in manifest["shards"]) / 256
    assert mean_gzip * 16 < len(built.body) / 3  # sixteen shards cost a fraction of the full file


# --- The switch ------------------------------------------------------------------------------------

def test_switch_off_means_404_and_the_bundle_still_serves(served, monkeypatch):
    _client, built, state = served
    monkeypatch.setenv(bundle_shards.SWITCH_ENV, "0")
    client = TestClient(create_app(bundle_settings()), headers={"CF-Connecting-IP": "203.0.113.7"})
    for path in (bundle_shards.MANIFEST_PATH, bundle_shards.CHANGES_PATH, shard_path("00")):
        assert client.get(path).status_code == 404 and client.head(path).status_code in {404, 405}, path
    assert client.get(bundle.PATH).content == built.body


@pytest.mark.parametrize("value", ["", "1", "yes", "true"])
def test_shards_are_on_by_default_and_only_zero_turns_them_off(monkeypatch, value):
    monkeypatch.setenv(bundle_shards.SWITCH_ENV, value)
    assert bundle_shards.shards_enabled()
    monkeypatch.setenv(bundle_shards.SWITCH_ENV, " 0 ")
    assert not bundle_shards.shards_enabled()
    monkeypatch.delenv(bundle_shards.SWITCH_ENV)
    assert bundle_shards.shards_enabled()


def test_with_the_bundle_off_there_are_no_shard_paths_either(monkeypatch):
    monkeypatch.delenv(bundle_shards.SWITCH_ENV, raising=False)
    client = TestClient(create_app(__import__("ahn.settings", fromlist=["Settings"]).Settings("postgresql://invalid/unused", "x" * 32, public_mode=True)))
    for path in (bundle_shards.MANIFEST_PATH, bundle_shards.CHANGES_PATH, shard_path("00")):
        assert client.get(path).status_code == 404


def test_lookup_routes_are_untouched_by_the_shards(monkeypatch):
    def routes(value):
        monkeypatch.setenv(bundle_shards.SWITCH_ENV, value)
        app = create_app(bundle_settings())
        return {(route.path, tuple(sorted(getattr(route, "methods", None) or ()))) for route in app.routes}, app.openapi()

    on_routes, on_schema = routes("1")
    off_routes, off_schema = routes("0")
    assert {path for path, _m in on_routes - off_routes} == {bundle_shards.MANIFEST_PATH, bundle_shards.CHANGES_PATH,
                                                              bundle_shards.SHARDS_PATH + "/{shard}.json"}
    assert off_routes < on_routes and on_schema == off_schema  # the public OpenAPI document does not list them


# --- Changes ---------------------------------------------------------------------------------------

DAY = timedelta(days=1)
T0 = datetime(2026, 11, 1, 6, 0, tzinfo=timezone.utc)  # after every curated access bundle was verified, so only the rows differ
BLOCKED = parse_robots("User-agent: ClaudeBot\nDisallow: /\n")


def rows_at(moment, *, skip=(), **per_host):
    return [profile_row(r["site_host"], fetched_at=moment - timedelta(hours=3), **per_host.get(r["site_host"], {}))
            for r in ROWS if r["site_host"] not in skip]


def test_a_changed_section_is_listed_and_a_reread_alone_is_not():
    changes = bundle_shards._Changes()
    base = shard_set(rows_at(T0), T0)
    changes.observe(base)
    assert changes.document(base)["changes"] == []
    second = shard_set(rows_at(T0 + DAY), T0 + DAY)
    changes.observe(second)
    assert changes.document(second)["changes"] == []  # only the read day moved
    third = shard_set(rows_at(T0 + 2 * DAY, **{"github.com": {"robots": BLOCKED}}), T0 + 2 * DAY)
    changes.observe(third)
    document = changes.document(third)
    assert [(c["host"], c["section"], c["changed_on"]) for c in document["changes"]] == [("github.com", "access", "2026-11-03")]
    assert document["changes"][0]["record"]["agents_blocked"] == ["ClaudeBot"]
    assert document["covers_since"] == export.iso(T0) and document["window_days"] == 7


def test_a_host_that_leaves_the_bundle_is_listed_as_removed():
    changes = bundle_shards._Changes()
    changes.observe(shard_set(rows_at(T0), T0))
    second = shard_set(rows_at(T0 + DAY, skip=("wikipedia.org",)), T0 + DAY)
    changes.observe(second)
    removed = [c for c in changes.document(second)["changes"] if c["host"] == "wikipedia.org" and c["section"] == "access"]
    assert removed == [{"host": "wikipedia.org", "section": "access", "changed_on": "2026-11-02", "removed": True}]


def test_changes_expire_exactly_seven_days_after_the_bundle_that_showed_them():
    changes = bundle_shards._Changes()
    changes.observe(shard_set(rows_at(T0), T0))
    changing = shard_set(rows_at(T0 + DAY, **{"github.com": {"robots": BLOCKED}}), T0 + DAY)
    changes.observe(changing)

    def listed(moment):
        later = shard_set(rows_at(moment, **{"github.com": {"robots": BLOCKED}}), moment)
        return [c["host"] for c in changes.document(later)["changes"]]

    assert listed(T0 + DAY + timedelta(days=7)) == ["github.com"]  # the seventh day is still in
    assert listed(T0 + DAY + timedelta(days=7, seconds=1)) == []
    assert listed(T0 + DAY + timedelta(days=7)) == []  # and an expired entry does not come back


def test_covers_since_never_claims_more_than_seven_days_or_more_than_was_seen():
    changes = bundle_shards._Changes()
    base = shard_set(rows_at(T0), T0)
    changes.observe(base)
    assert changes.document(base)["covers_since"] == export.iso(T0)
    old = shard_set(rows_at(T0 + timedelta(days=30)), T0 + timedelta(days=30))
    assert changes.document(old)["covers_since"] == export.iso(T0 + timedelta(days=23))


def test_the_same_stored_bundle_gives_the_same_changes_and_manifest_bytes_whatever_the_clock(served, monkeypatch):
    client, built, _state = served
    first = {p: client.get(p) for p in (bundle_shards.CHANGES_PATH, bundle_shards.MANIFEST_PATH)}
    # the stored bundle stays valid, but the clock runs on past the seven-day window

    shards = bundle_shards.ShardSet(built.sha256, built.generated_at, built.body)
    changes = bundle_shards._Changes()
    changes.observe(shards)
    one = bundle_shards.encode(changes.document(shards))
    assert one[2] == first[bundle_shards.CHANGES_PATH].headers["etag"].strip('"')
    for later in (timedelta(days=3), timedelta(days=8), timedelta(days=60)):
        assert bundle_shards.encode(changes.document(shards)) == one  # no clock argument exists to move it
        manifest = bundle_shards.manifest_document(shards, one, (None, None))
        assert bundle_shards.encode(manifest)[2] == first[bundle_shards.MANIFEST_PATH].headers["etag"].strip('"'), later


def test_the_manifest_and_changes_are_built_once_and_the_key_is_read_once_per_bundle(served, monkeypatch):
    client, _built, _state = served
    loads, builds = [], []
    monkeypatch.setattr(bundle, "signing_from_env", lambda: loads.append(1) or (None, None))
    real = bundle_shards.manifest_document
    monkeypatch.setattr(bundle_shards, "manifest_document", lambda *a, **k: builds.append(1) or real(*a, **k))
    etag = client.get(bundle_shards.MANIFEST_PATH).headers["etag"]
    for _ in range(3):
        assert client.get(bundle_shards.MANIFEST_PATH, headers={"If-None-Match": etag}).status_code == 304
        assert client.get(bundle_shards.MANIFEST_PATH).headers["etag"] == etag
    assert len(loads) == 1 and len(builds) == 1


def test_the_same_bundle_seen_twice_is_not_a_change():
    changes = bundle_shards._Changes()
    shards = shard_set(rows_at(T0), T0)
    changes.observe(shards)
    changes.observe(shards)
    assert changes.entries == {}


def test_changes_route_serves_the_document(served):
    client, _built, _state = served
    document = client.get(bundle_shards.CHANGES_PATH).json()
    assert document["schema"] == bundle_shards.CHANGES_SCHEMA and document["changes"] == []
    assert document["covers_since"] == "2026-10-20T06:00:00Z" and document["window_days"] == 7


# --- Documents -------------------------------------------------------------------------------------

def test_documents_carry_the_constants_of_the_code():
    shard_mib = f"{bundle.SHARD_DAILY_BYTES // MIB} MiB"
    contracts = {"en": (ROOT / "docs" / "api-contract.en.md").read_text(encoding="utf-8"),
                 "ko": (ROOT / "docs" / "api-contract.md").read_text(encoding="utf-8")}
    for lang, text in contracts.items():
        for needle in (shard_mib, bundle_shards.MANIFEST_PATH, bundle_shards.CHANGES_PATH, bundle_shards.SHARDS_PATH + "/",
                       bundle_shards.SWITCH_ENV, "bundle_shard_daily_limit", f"{bundle.DAILY_BYTES // MIB} MiB"):
            assert needle in text, (lang, needle)
        assert str(bundle_shards.SHARD_COUNT) in text and str(bundle_shards.CHANGE_WINDOW.days) in text
    decisions = (ROOT / "docs" / "decisions.md").read_text(encoding="utf-8")
    row = next(line for line in decisions.splitlines() if line.startswith("| D74 "))
    assert shard_mib in row and str(bundle_shards.SHARD_COUNT) in row and str(bundle_shards.CHANGE_WINDOW.days) in row
    assert bundle_shards.MANIFEST_PATH in bundle_shards.LLMS_SHARDS and bundle_shards.CHANGES_PATH in bundle_shards.LLMS_SHARDS
    assert f"{bundle_shards.CHANGE_WINDOW.days} days" in bundle_shards.LLMS_SHARDS


def test_llms_txt_names_the_shards_only_while_they_are_served(served, monkeypatch):
    client, _built, _state = served
    text = client.get("/llms.txt").text
    assert bundle_shards.MANIFEST_PATH in text and text.index("shards/manifest.json") < text.index("## Pages")
    monkeypatch.setenv(bundle_shards.SWITCH_ENV, "0")
    text = TestClient(create_app(bundle_settings())).get("/llms.txt").text
    assert "## Offline bundle" in text and "shards" not in text
