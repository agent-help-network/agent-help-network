"""Recreate the service in a disposable, uniquely named local PostgreSQL DB."""

from __future__ import annotations

from dataclasses import replace
from uuid import uuid4

import psycopg
from fastapi.testclient import TestClient
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from ahn.db import connect, migrate
from ahn.main import create_app
from ahn.settings import Settings


def main() -> None:
    local = Settings.from_env()
    target = conninfo_to_dict(local.database_url)
    if target.get("host") not in ("127.0.0.1", "localhost"):
        raise RuntimeError("Fresh DB verification is limited to loopback PostgreSQL")
    name = "ahn_repro_" + uuid4().hex[:12]
    admin = make_conninfo(local.database_url, dbname="postgres")
    new_url = make_conninfo(local.database_url, dbname=name)
    with psycopg.connect(admin, autocommit=True) as conn:
        conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    try:
        settings = replace(local, database_url=new_url, test_mode=True)
        applied = migrate(settings)
        with connect(settings) as conn:
            versions = [row["version"] for row in conn.execute("SELECT version FROM schema_migrations ORDER BY version")]
        with TestClient(create_app(settings)) as client:
            assert client.get("/health").json() == {"status": "ok"}
            result = client.get("/v1/incidents/check", params={"site_host": "fresh.agenthelpnetwork.test"})
            assert result.status_code == 200
            assert result.json()["evidence_level"] == "no_reports"
            assert result.json()["current_site_status"] == "unknown"
        assert applied == versions and len(versions) >= 3
        print(f"PASS: empty loopback PostgreSQL DB migrated, API health and synthetic unknown response; versions={versions}")
    finally:
        with psycopg.connect(admin, autocommit=True) as conn:
            conn.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name)))


if __name__ == "__main__":
    main()
