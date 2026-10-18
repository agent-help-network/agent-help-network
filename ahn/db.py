from __future__ import annotations

import hmac
import secrets
from hashlib import sha256
from pathlib import Path

import psycopg
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from psycopg.rows import dict_row

from .settings import Settings


DEFAULT_CONNECT_TIMEOUT = "10"


def conninfo(database_url: str) -> str:
    # A URL that sets its own connect_timeout wins; otherwise never hang on a
    # dead database longer than the request budget allows.
    params = conninfo_to_dict(database_url)
    params.setdefault("connect_timeout", DEFAULT_CONNECT_TIMEOUT)
    return make_conninfo(**params)


def connect(settings: Settings):
    return psycopg.connect(conninfo(settings.database_url), row_factory=dict_row)


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
