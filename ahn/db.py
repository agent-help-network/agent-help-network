from __future__ import annotations

import hmac
import secrets
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path

import psycopg
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from psycopg.rows import dict_row

from .settings import Settings


DEFAULT_CONNECT_TIMEOUT = "10"
UTC_OPTION = "-c TimeZone=UTC"


def conninfo(database_url: str) -> str:
    # A URL that sets its own connect_timeout wins; otherwise never hang on a
    # dead database longer than the request budget allows.
    params = conninfo_to_dict(database_url)
    params.setdefault("connect_timeout", DEFAULT_CONNECT_TIMEOUT)
    # Every session runs in UTC whatever the server, the PGTZ variable or the role says. psycopg hands timestamptz back
    # in the session zone, and "+ timedelta" on a DST-zone value moves the wall clock (a day across a fall-back is 25
    # hours) while SQL day intervals and CURRENT_DATE follow the session zone too. Sent as a start-up option, so it costs
    # no round trip. Appended after any options the URL carries, so it wins. (Render has no connection pooler in front
    # of the database, which is the setup that rejects start-up options.)
    existing = params.get("options")
    params["options"] = f"{existing} {UTC_OPTION}" if existing else UTC_OPTION
    return make_conninfo(**params)


def as_utc(moment: datetime) -> datetime:
    """The same instant in UTC. A naive datetime is refused rather than read as local time."""
    if moment.tzinfo is None or moment.utcoffset() is None:
        raise ValueError("a timezone-aware datetime is required")
    return moment.astimezone(timezone.utc)


def connect_url(database_url: str, **kwargs):
    """Open a session that is in UTC. The start-up option covers the usual case at no cost; a client PGTZ variable
    outranks it (the server applies it last), so a session still reporting another zone is set once, outside any
    transaction so that a later rollback cannot undo it."""
    conn = psycopg.connect(conninfo(database_url), **kwargs)
    if conn.info.parameter_status("TimeZone") != "UTC":
        autocommit = conn.autocommit
        conn.autocommit = True
        conn.execute("SET TimeZone TO 'UTC'")
        conn.autocommit = autocommit
    return conn


def connect(settings: Settings):
    return connect_url(settings.database_url, row_factory=dict_row)


def migrate(settings: Settings) -> list[int]:
    directory = Path(__file__).resolve().parent.parent / "migrations"
    applied: list[int] = []
    with connect(settings) as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS schema_migrations (version integer PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())")
        existing = {row["version"] for row in conn.execute("SELECT version FROM schema_migrations")}
        for path in sorted(directory.glob("[0-9][0-9][0-9]_*.sql")):
            version = int(path.name.split("_", 1)[0])
            if version in existing:
                continue
            conn.execute(path.read_text(encoding="utf-8"))
            conn.execute("INSERT INTO schema_migrations (version) VALUES (%s)", (version,))
            applied.append(version)
    return applied


def hash_key(key: str, pepper: str) -> str:
    return hmac.new(pepper.encode("utf-8"), key.encode("utf-8"), sha256).hexdigest()


def create_reporter(settings: Settings, label: str) -> str:
    if not (1 <= len(label) <= 64) or not all(c.isascii() and (c.isalnum() or c in "-_ ") for c in label):
        raise ValueError("label must be 1-64 ASCII letters, digits, spaces, - or _")
    key = secrets.token_urlsafe(36)
    with connect(settings) as conn:
        conn.execute("INSERT INTO reporters (key_hash, label) VALUES (%s, %s)", (hash_key(key, settings.key_pepper), label))
    return key


def get_reporter_id(conn, key: str, settings: Settings):
    if not key:
        return None
    row = conn.execute(
        "SELECT id FROM reporters WHERE key_hash = %s AND disabled_at IS NULL",
        (hash_key(key, settings.key_pepper),),
    ).fetchone()
    return row["id"] if row else None
