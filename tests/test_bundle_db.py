"""Offline data bundle against the dedicated ahn_test database (migration 023): build, store, serve."""

from __future__ import annotations

import gzip
import hashlib
import os
from datetime import date, datetime, timedelta, timezone

import pytest

TEST_URL = os.environ.get("AHN_TEST_DATABASE_URL", "")
if not TEST_URL:
    pytest.skip("AHN_TEST_DATABASE_URL is required", allow_module_level=True)

from fastapi.testclient import TestClient  # noqa: E402  (after the skip: ahn.main builds an app from the environment)

from ahn import bundle  # noqa: E402
from ahn.db import connect, migrate  # noqa: E402
from ahn.main import create_app  # noqa: E402
from ahn.profile_warm import load_seed_hosts  # noqa: E402
from ahn.service import PUBLIC_REPORTER_ID  # noqa: E402
from ahn.settings import Settings  # noqa: E402

LOOKED_UP = "looked-up-only.example"  # looked up, discovered and reported, but on no seed list or curated table


@pytest.fixture
def db(monkeypatch):
    settings = Settings(TEST_URL, "test-pepper-must-be-at-least-thirty-two-chars", public_mode=True, reporting_enabled=True)
    with connect(settings) as conn:
        if conn.info.dbname != "ahn_test":
            raise RuntimeError("Refusing to reset a database other than ahn_test")
    migrate(settings)
    with connect(settings) as conn:
        conn.execute("TRUNCATE audit_events, report_submissions, observations, incidents, reporters, lookup_metrics, "
                     "status_cache, status_sources, queried_hosts, host_access_profile, background_reads, public_bundle "
                     "RESTART IDENTITY CASCADE")
        conn.execute("INSERT INTO reporters (id,key_hash,label) VALUES (%s,repeat('0',64),'public-unverified')", (PUBLIC_REPORTER_ID,))
        seed = load_seed_hosts()[0]
        for host in (seed, LOOKED_UP):
            conn.execute("""INSERT INTO host_access_profile (site_host,fetched_at,robots_status,robots,llms_txt_status,
                                                             robots_detail,llms_detail,read_host,origin_operator)
                            VALUES (%s, now() - interval '2 hours', 'ok', '{"rules": []}', 'absent', 'http_200', 'http_404', %s,
                                    'agenthelpnetwork.com')""", (host, host))
            conn.execute("INSERT INTO queried_hosts (site_host,first_seen_at,last_seen_at) VALUES (%s, now(), now())", (host,))
            conn.execute("INSERT INTO lookup_metrics (day,site_host,error_class,checks) VALUES (CURRENT_DATE,%s,'site_access',7)", (host,))
        # A Statuspage discovered after a lookup: the table row exists only because someone looked the host up.
        conn.execute("""INSERT INTO status_sources (site_host,source_name,status_page_url,api_url,curated_by)
                        VALUES (%s,'Looked-up Status','https://status.looked-up-only.example/',
                                'https://status.looked-up-only.example/api/v2/status.json','discovered_cname')""", (LOOKED_UP,))
        conn.execute("INSERT INTO status_cache (site_host,indicator,fetched_at) VALUES (%s,'major',now())", (LOOKED_UP,))
        incident = conn.execute("""INSERT INTO incidents (site_host,task_step,error_class,visibility)
                                   VALUES (%s,'site_open','site_access','published') RETURNING id""", (seed,)).fetchone()["id"]
        conn.execute("""INSERT INTO observations (incident_id,reporter_id,observed_at,agent_family,attempt_actor,attempt_env,outcome,
                                                  challenge_vendor,challenge_kind,review_state,synthetic)
                        VALUES (%s,%s,now(),'other','agent','agent_cloud_browser','blocked','none','none','published',false)""",
                     (incident, PUBLIC_REPORTER_ID))
    monkeypatch.setenv("AHN_PUBLIC_BUNDLE", "1")
    monkeypatch.setenv("AHN_OPERATOR_NAME", "Example Operator")
    monkeypatch.delenv("AHN_EXPORT_SIGNING_KEY", raising=False)
    return settings, seed


def web(settings, public_bundle=True) -> TestClient:
    on = Settings(settings.database_url, settings.key_pepper, public_mode=True, public_bundle=public_bundle,
                  public_bundle_since=date(2026, 11, 3) if public_bundle else None)
    return TestClient(create_app(on), headers={"CF-Connecting-IP": "203.0.113.7"})


def test_migration_023_creates_the_one_row_table(db):
    settings, _seed = db
    with connect(settings) as conn:
        columns = {row["column_name"] for row in conn.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_name='public_bundle'")}
        assert columns == {"id", "generated_at", "sha256", "record_count", "byte_size", "body", "served_on", "served_bytes"}
        with pytest.raises(Exception):
            conn.execute("INSERT INTO public_bundle (id,generated_at,sha256,record_count,byte_size,body) "
                         "VALUES (2,now(),repeat('a',64),0,1,'\\x00')")


def test_cron_build_holds_seed_reads_and_curated_tables_only(db):
    settings, seed = db
    result = bundle.refresh_if_due(settings.database_url)
    assert result["built"] > 0 and result["signed"] is False
    with connect(settings) as conn:
        row = conn.execute("SELECT * FROM public_bundle").fetchone()
    body = bytes(row["body"])
    assert hashlib.sha256(body).hexdigest() == row["sha256"] == result["sha256"] and row["byte_size"] == len(body)
    raw = gzip.decompress(body)
    assert LOOKED_UP.encode() not in raw                      # not its read, its discovered status page, its lookups or reports
    assert b"agent_cloud_browser" not in raw and b'"checks"' not in raw and b'"major"' not in raw
    records = bundle.read_records(body)
    assert records[0]["kind"] == "bundle_meta" and records[0]["payload"]["record_count"] == row["record_count"] == len(records) - 1
    profiles = [r for r in records if r["kind"] == "access_profile"]
    assert [r["payload"]["site_host"] for r in profiles] == [seed]
    assert profiles[0]["observed_at"].endswith("T00:00:00Z")  # the read time is published to the day only


def test_rebuild_waits_a_day_and_switch_off_deletes_the_row(db, monkeypatch):
    settings, _seed = db
    now = datetime.now(timezone.utc)
    first = bundle.refresh_if_due(settings.database_url, now=now)
    assert bundle.refresh_if_due(settings.database_url, now=now + timedelta(hours=1)) == {"skipped": "fresh"}
    later = bundle.refresh_if_due(settings.database_url, now=now + timedelta(hours=24))
    assert later["sha256"] != first["sha256"]  # a new generation time, a new file
    monkeypatch.setenv("AHN_PUBLIC_BUNDLE", "0")
    assert bundle.refresh_if_due(settings.database_url) == {"skipped": "switch_off", "deleted": 1}
    with connect(settings) as conn:
        assert conn.execute("SELECT count(*) AS n FROM public_bundle").fetchone()["n"] == 0
    # Even with the web switch on alone, nothing is left to serve.
    assert web(settings).get(bundle.PATH).json() == {"error": "bundle_not_ready"}


def test_cli_store_and_the_cron_share_one_lock(db):
    settings, _seed = db
    with connect(settings) as holder:
        assert bundle.lock(holder)
        assert bundle.refresh_if_due(settings.database_url) == {"skipped": "locked"}
        with connect(settings) as other:
            assert not bundle.lock(other)


def test_web_serves_the_stored_file_only_while_switched_on(db):
    settings, _seed = db
    assert web(settings).get(bundle.PATH).status_code == 503  # switched on, nothing built yet
    bundle.refresh_if_due(settings.database_url)
    with connect(settings) as conn:
        stored = conn.execute("SELECT sha256,body FROM public_bundle").fetchone()
    on = web(settings)
    response = on.get(bundle.PATH)
    assert response.status_code == 200 and response.content == bytes(stored["body"])
    assert on.get(bundle.SHA256_PATH).text.split()[0] == stored["sha256"]
    off = web(settings, public_bundle=False)
    assert off.get(bundle.PATH).status_code == 404 and off.get(bundle.SHA256_PATH).status_code == 404


def test_daily_budget_lives_in_the_database_and_survives_a_restart(db, monkeypatch):
    settings, _seed = db
    bundle.refresh_if_due(settings.database_url)
    with connect(settings) as conn:
        size = conn.execute("SELECT byte_size FROM public_bundle").fetchone()["byte_size"]
    monkeypatch.setattr(bundle, "DAILY_BYTES", size * 2)  # room for two full downloads today
    assert web(settings).get(bundle.PATH).status_code == 200
    restarted = web(settings)  # a new process: memory is empty, the database still counts
    assert restarted.get(bundle.PATH, headers={"CF-Connecting-IP": "198.51.100.4"}).status_code == 200
    refused = restarted.get(bundle.PATH, headers={"CF-Connecting-IP": "192.0.2.10"})
    assert refused.status_code == 429 and refused.json() == {"error": "bundle_daily_limit"}
    with connect(settings) as conn:
        row = conn.execute("SELECT served_on,served_bytes FROM public_bundle").fetchone()
    assert row["served_bytes"] == 2 * size and row["served_on"] == datetime.now(timezone.utc).date()
    # A rebuild replaces the file but keeps the day's count.
    bundle.refresh_if_due(settings.database_url, force=True)
    with connect(settings) as conn:
        assert conn.execute("SELECT served_bytes FROM public_bundle").fetchone()["served_bytes"] == 2 * size
