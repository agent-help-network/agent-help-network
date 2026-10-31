"""Every database session runs in UTC (db.conninfo), so psycopg's datetimes, SQL day intervals and CURRENT_DATE no longer
depend on the server's or the client's zone. A DST zone made "+ 24 hours" mean 25 on 2026-11-01."""

from __future__ import annotations

import ast
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from ahn import db, status_scope
from ahn.db import as_utc, conninfo

ROOT = Path(__file__).resolve().parent.parent
# Files allowed to open a connection by hand, and why. Everything else goes through ahn.db.connect or ahn.db.connect_url.
RAW_CONNECT_ALLOWED = {
    "ahn/db.py": "the one place that builds the connection",
    "scripts/verify_fresh_db.py": "creates and drops throw-away databases with an admin URL; stores no times",
    "tests/test_status_upgrade.py": "creates and drops throw-away databases with an admin URL",
    "tests/test_session_utc.py": "this file",
}


def raw_connects(source: str) -> list[int]:
    """Line numbers of calls that open a psycopg connection by hand, followed through import aliases."""
    tree = ast.parse(source)
    modules, classes, functions = set(), set(), set()   # names bound to psycopg, to its connection classes, to connect()
    found: list[int] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update((a.asname or a.name) for a in node.names if a.name == "psycopg")
        elif isinstance(node, ast.ImportFrom) and node.module in ("psycopg", "psycopg.connection", "psycopg._connection_base"):
            for a in node.names:
                if a.name == "connect":
                    functions.add(a.asname or a.name)
                    found.append(node.lineno)
                elif a.name in ("Connection", "AsyncConnection"):
                    classes.add(a.asname or a.name)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Attribute):
            continue
        base = node.value
        if node.attr == "connect" and isinstance(base, ast.Name) and (base.id in modules or base.id in classes):
            found.append(node.lineno)
        elif node.attr == "connect" and isinstance(base, ast.Attribute) and base.attr in ("Connection", "AsyncConnection"):
            if isinstance(base.value, ast.Name) and base.value.id in modules:
                found.append(node.lineno)
        elif node.attr == "AsyncConnection" and isinstance(base, ast.Name) and base.id in modules:
            found.append(node.lineno)
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in functions:
            found.append(node.lineno)
    return sorted(set(found))


@pytest.mark.parametrize("source", [
    "import psycopg\npsycopg.connect(u)",
    "from psycopg import connect as x\nx(u)",
    "import psycopg as pg\npg.connect(u)",
    "from psycopg import Connection\nConnection.connect(u)",
    "from psycopg import Connection as C\nC.connect(u)",
    "import psycopg as pg\npg.Connection.connect(u)",
    "import psycopg as pg\npg.AsyncConnection.connect(u)",
], ids=["plain", "from-alias", "module-alias", "class", "class-alias", "module-class", "async"])
def test_the_connection_scan_follows_import_aliases(source):
    assert raw_connects(source) != []


def test_the_connection_scan_leaves_type_annotations_alone():
    assert raw_connects("import psycopg\ndef f(conn: psycopg.Connection): ...\nx = psycopg.Error") == []


def test_every_connection_goes_through_the_utc_session_path():
    offenders = []
    for path in list((ROOT / "ahn").rglob("*.py")) + list((ROOT / "scripts").rglob("*.py")) + list((ROOT / "tests").rglob("*.py")):
        rel = path.relative_to(ROOT).as_posix()
        if rel in RAW_CONNECT_ALLOWED:
            continue
        offenders += [f"{rel}:{n}" for n in raw_connects(path.read_text(encoding="utf-8"))]
    assert offenders == []


def test_conninfo_sets_utc_and_keeps_options_the_url_carries():
    assert "TimeZone=UTC" in conninfo("postgresql://u:p@h/d")
    merged = conninfo("postgresql://u:p@h/d?options=-c%20statement_timeout%3D5000")
    assert "statement_timeout=5000" in merged and merged.index("statement_timeout") < merged.index("TimeZone=UTC")


def test_as_utc_converts_aware_values_and_refuses_naive_ones():
    from zoneinfo import ZoneInfo

    moment = datetime(2026, 11, 1, 5, 30, tzinfo=timezone.utc)
    assert as_utc(moment.astimezone(ZoneInfo("America/New_York"))) == moment
    with pytest.raises(ValueError):
        as_utc(datetime(2026, 11, 1, 5, 30))


def test_scope_pause_guard_counts_elapsed_time_and_refuses_naive_input():
    from zoneinfo import ZoneInfo

    failed = datetime(2026, 10, 31, 6, 7, 41, tzinfo=timezone.utc)
    row = {"scope_failures": 6, "scope_failed_at": failed.astimezone(ZoneInfo("America/New_York")), "scope_error": None}
    rest = status_scope.scope_backoff(6, None)
    assert status_scope.scope_paused(row, failed + rest - timedelta(seconds=1))
    assert not status_scope.scope_paused(row, failed + rest)
    with pytest.raises(ValueError):
        status_scope.scope_paused(row, datetime(2026, 10, 31, 6, 7, 41))


TEST_URL = os.environ.get("AHN_TEST_DATABASE_URL", "")
needs_db = pytest.mark.skipif(not TEST_URL, reason="AHN_TEST_DATABASE_URL is required")


@pytest.fixture
def new_york_client(monkeypatch):
    monkeypatch.setenv("PGTZ", "America/New_York")


@needs_db
def test_a_session_is_utc_even_when_the_client_asks_for_new_york(new_york_client):
    import psycopg

    settings = db.Settings(TEST_URL, "test-pepper-must-be-at-least-thirty-two-chars")
    with psycopg.connect(TEST_URL) as raw:  # control: without the option the client zone wins, so this test can fail
        assert raw.execute("SHOW TimeZone").fetchone()[0] == "America/New_York"
    with db.connect(settings) as conn:
        assert conn.execute("SHOW TimeZone").fetchone()["TimeZone"] == "UTC"
        got = conn.execute("SELECT '2026-11-01 05:30Z'::timestamptz AS t, CURRENT_DATE = (now() AT TIME ZONE 'UTC')::date AS d").fetchone()
        assert got["t"].utcoffset() == timedelta(0) and got["d"]


@needs_db
def test_scope_pause_and_episode_confirmation_do_not_depend_on_the_client_zone(new_york_client):
    from ahn.signal_history import Thresholds, _Episodes

    settings = db.Settings(TEST_URL, "test-pepper-must-be-at-least-thirty-two-chars")
    with db.connect(settings) as conn:
        failed = conn.execute("SELECT '2026-10-31 06:07:41Z'::timestamptz AS t").fetchone()["t"]
        row = {"scope_failures": 6, "scope_failed_at": failed, "scope_error": None}
        rest = status_scope.scope_backoff(6, None)
        for now, expected in ((failed + rest - timedelta(seconds=1), True), (failed + rest, False)):
            assert status_scope.scope_paused(row, now) is expected

        # Spring-forward day (2026-03-08 07:00Z): 06:50Z to 07:10Z is 20 real minutes, 1h20 on a New York wall clock.
        conn.execute("TRUNCATE outage_episodes RESTART IDENTITY")
        first = conn.execute("SELECT '2026-03-08 06:50Z'::timestamptz AS t").fetchone()["t"]
        later = conn.execute("SELECT '2026-03-08 07:10Z'::timestamptz AS t").fetchone()["t"]
        counts = {"opened": 0, "confirmed": 0}
        _Episodes(conn, ["gone.example"], set(), Thresholds(), counts).fail("gone.example", first, "timeout")
        _Episodes(conn, ["gone.example"], set(), Thresholds(), counts).fail("gone.example", later, "timeout")
        assert counts["confirmed"] == 0
        assert conn.execute("SELECT confirmed_at FROM outage_episodes").fetchone()["confirmed_at"] is None
        conn.execute("TRUNCATE outage_episodes RESTART IDENTITY")


@needs_db
def test_the_utc_fix_up_leaves_autocommit_as_asked_and_survives_a_rollback(new_york_client):
    settings = db.Settings(TEST_URL, "test-pepper-must-be-at-least-thirty-two-chars")
    with db.connect(settings) as conn:
        assert conn.autocommit is False
        conn.execute("CREATE TEMP TABLE zone_probe (x int)")
        conn.commit()
        conn.execute("INSERT INTO zone_probe VALUES (1)")
        conn.rollback()
        assert conn.execute("SELECT count(*) AS n FROM zone_probe").fetchone()["n"] == 0
        assert conn.execute("SHOW TimeZone").fetchone()["TimeZone"] == "UTC"
    with db.connect_url(TEST_URL, autocommit=True) as conn:
        assert conn.autocommit is True
        assert conn.execute("SHOW TimeZone").fetchone()[0] == "UTC"


@needs_db
def test_a_failed_zone_fix_up_closes_the_connection_and_raises(new_york_client, monkeypatch):
    import psycopg

    opened = []
    real = psycopg.connect

    def connect(*args, **kwargs):
        conn = real(*args, **kwargs)
        opened.append(conn)

        def refuse(*_a, **_k):
            raise psycopg.errors.InsufficientPrivilege("no")

        conn.execute = refuse
        return conn

    monkeypatch.setattr(db.psycopg, "connect", connect)
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        db.connect_url(TEST_URL)
    assert len(opened) == 1 and opened[0].closed
