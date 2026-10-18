"""Upgrade regression for databases that already cached the original seed rows."""

from __future__ import annotations

import os
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo


TEST_URL = os.environ.get("AHN_TEST_DATABASE_URL", "")
pytestmark = pytest.mark.skipif(not TEST_URL, reason="AHN_TEST_DATABASE_URL is required")


def test_009_removes_old_source_with_cached_foreign_key():
    parsed = conninfo_to_dict(TEST_URL)
    if parsed.get("host") not in {"127.0.0.1", "localhost"}:
        pytest.skip("Disposable upgrade test is limited to loopback PostgreSQL")
    name = "ahn_upgrade_" + uuid4().hex[:12]
    admin_url = make_conninfo(TEST_URL, dbname="postgres")
    new_url = make_conninfo(TEST_URL, dbname=name)
    with psycopg.connect(admin_url, autocommit=True) as conn:
        conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    try:
        directory = Path(__file__).resolve().parent.parent / "migrations"
        with psycopg.connect(new_url) as conn:
            for path in sorted(directory.glob("[0-9][0-9][0-9]_*.sql")):
                if int(path.name[:3]) <= 8:
                    conn.execute(path.read_text(encoding="utf-8"))
            conn.execute(
                """INSERT INTO status_sources (site_host,source_name,status_page_url,api_url)
                   VALUES ('github.com','GitHub Status','https://www.githubstatus.com/',
                           'https://www.githubstatus.com/api/v2/status.json')"""
            )
            conn.execute(
                "INSERT INTO status_cache (site_host,indicator,fetched_at) VALUES ('github.com','none',now())"
            )
            conn.execute((directory / "009_queried_hosts.sql").read_text(encoding="utf-8"))
            assert conn.execute("SELECT count(*) FROM status_sources").fetchone()[0] == 0
            assert conn.execute("SELECT count(*) FROM status_cache").fetchone()[0] == 0
            assert conn.execute("SELECT count(*) FROM queried_hosts").fetchone()[0] == 0
    finally:
        with psycopg.connect(admin_url, autocommit=True) as conn:
            conn.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name)))
