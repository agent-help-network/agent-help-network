"""Thin shards of the offline bundle: 256 small files, a manifest and a seven-day changes file (D74).

The full bundle (ahn/bundle.py) is one file of every record, tens of megabytes once inflated. An agent that
only wants to compare a handful of hosts should not have to download or parse it, and a browser-style agent
cannot run a script over it. This module cuts the same stored bundle into small JSON files:

- ``/v1/bundle/shards/<xx>.json``: the hosts whose shard key is ``xx``. The key is the first byte of the
  SHA-256 of the lowercase hostname without a trailing dot, as two lowercase hex digits. Every shard exists,
  empty or not, so asking for one says nothing about which hosts it holds. A client computes the key of its
  own host locally; the server never receives a hostname and cannot tell which host one shard was fetched for.
- ``/v1/bundle/shards/manifest.json``: per shard its SHA-256, size and build time, so a client can verify what
  it downloaded. Signed with the same Ed25519 key as the bundle's records when this process has it.
- ``/v1/bundle/changes.json``: the host sections that changed during the last seven days.

A host record is the thin form of the bundle's records: the verdict-relevant fields of its ``access_profile``,
``official_path`` and ``status_source`` records, under ``access``, ``official`` and ``status``. Quoted publisher
text, robots rule lists and signatures are not in it; the full bundle keeps them. Nothing is stored for this:
the shards are derived in memory from the stored bundle each time that file changes, so there is no table and
no migration, and a lookup (ahn/main.py check) is untouched.

Every file is sent gzip-encoded (``Content-Encoding: gzip``, ``Content-Type: application/json``), so a browser
or an HTTP library hands the client plain JSON. Their transfer is counted against ``SHARD_DAILY_BYTES``, a
daily budget separate from the full bundle's ``DAILY_BYTES``.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import re
import threading
from datetime import date, datetime, timedelta, timezone
from email.utils import format_datetime

from fastapi import Request, Response
from fastapi.responses import JSONResponse

from . import bundle, export

SWITCH_ENV = "AHN_BUNDLE_SHARDS"
SHARD_COUNT = 256
SHARDS_PATH = "/v1/bundle/shards"
MANIFEST_PATH = SHARDS_PATH + "/manifest.json"
CHANGES_PATH = "/v1/bundle/changes.json"
CHANGE_WINDOW = timedelta(days=7)
SHARD_SCHEMA = "ahn-shard/1"
MANIFEST_SCHEMA = "ahn-shards/1"
CHANGES_SCHEMA = "ahn-changes/1"
SECTIONS = ("access", "official", "status")  # bundle kinds access_profile, official_path, status_source
_SHARD_ID = re.compile(r"[0-9a-f]{2}")
_KIND_SECTION = {"access_profile": "access", "official_path": "official", "status_source": "status"}


LLMS_SHARDS = (
    "- Thin shards of the same data, for one host at a time: compute the first byte of the SHA-256 of the lowercase "
    "hostname as two hex digits `xx` and fetch `https://agenthelpnetwork.com/v1/bundle/shards/xx.json` (a few KB of JSON; the "
    "server never sees which host you wanted). `/v1/bundle/shards/manifest.json` lists every shard's SHA-256 and size; "
    "`/v1/bundle/changes.json` lists what changed in the last 7 days. They have a daily budget of their own: pull each at most "
    "daily, with `If-None-Match`.\n\n"
)


def shards_enabled() -> bool:
    """On unless AHN_BUNDLE_SHARDS is "0". They exist only while the bundle itself is served."""
    import os

    return os.environ.get(SWITCH_ENV, "").strip() != "0"


def normalize_host(host: str) -> str:
    return host.strip().lower().rstrip(".")


def shard_id(host: str) -> str:
    """Two lowercase hex digits: the first byte of the SHA-256 of the normalized hostname."""
    return f"{hashlib.sha256(normalize_host(host).encode('utf-8')).digest()[0]:02x}"


# --- The thin form -------------------------------------------------------------------------------

def _drop_empty(value: dict) -> dict:
    return {key: item for key, item in value.items() if item not in (None, [], {})}


def thin_access(payload: dict, observed_at: str | None, site_host: str) -> dict:
    robots = payload.get("robots") or {}
    content_use = payload.get("content_use") or {}
    signals = {key: value for key, value in (content_use.get("signals") or {}).items()
               if key in ("search", "ai_input", "ai_train") and value not in (None, "unset")}
    read_host = payload.get("read_host")
    return _drop_empty({
        "robots": payload.get("robots_status"), "llms_txt": payload.get("llms_txt_status"),
        "root_allowed": robots.get("wildcard_root_allowed"), "crawl_delay": robots.get("wildcard_crawl_delay"),
        "agents_blocked": sorted(robots.get("agents_blocked") or []),
        "content_signals": signals, "ai_input_declared_no": True if content_use.get("ai_input_declared_no") else None,
        "read_host": read_host if read_host and read_host != site_host else None,
        "read_on": observed_at[:10] if observed_at else None,
    })


def thin_official(payload: dict) -> dict:
    access = payload.get("access") or {}
    terms, mcp = access.get("terms") or {}, access.get("official_mcp") or []
    return _drop_empty({
        "name": payload.get("name"), "api_docs": payload.get("api_docs"), "developer_portal": payload.get("developer_portal"),
        "auth": [item["method"] for item in access.get("auth") or [] if item.get("method")],
        "key_signup_url": access.get("key_signup_url"), "openapi_url": access.get("openapi_url"),
        "rate_limits_url": access.get("rate_limits_url"), "sandbox_url": access.get("sandbox_url"),
        "official_mcp": [item["endpoint"] for item in (mcp if isinstance(mcp, list) else [mcp]) if item.get("endpoint")],
        "terms_url": terms.get("url") if isinstance(terms, dict) else None,
    })


def thin_status(payload: dict) -> dict:
    return _drop_empty({"name": payload.get("source_name"), "page": payload.get("status_page_url"),
                        "api": payload.get("api_url"), "feed": payload.get("feed_kind")})


def thin_hosts(records: list[dict]) -> dict[str, dict[str, dict]]:
    """Host -> section -> thin record, from the records of a bundle (the head record is skipped)."""
    hosts: dict[str, dict[str, dict]] = {}
    for record in records:
        section = _KIND_SECTION.get(record.get("kind"))
        if section is None:
            continue
        payload = record["payload"]
        host = normalize_host(payload["site_host"])
        if section == "access":
            thin = thin_access(payload, record.get("observed_at"), host)
        else:
            thin = thin_official(payload) if section == "official" else thin_status(payload)
        hosts.setdefault(host, {})[section] = thin
    return hosts


def fingerprint(section: str, thin: dict) -> str:
    """What counts as a change: the thin record without the day the host was read, so a re-read alone is none."""
    stable = {key: value for key, value in thin.items() if key != "read_on"} if section == "access" else thin
    return json.dumps(stable, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


# --- Files ---------------------------------------------------------------------------------------

def encode(document: dict) -> tuple[bytes, bytes, str]:
    """(raw JSON, gzip of it, SHA-256 of the raw JSON). The gzip header is fixed, so the bytes depend on the content only."""
    raw = json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return raw, gzip.compress(raw, compresslevel=9, mtime=0), hashlib.sha256(raw).hexdigest()


class ShardSet:
    """All shards of one stored bundle."""

    def __init__(self, sha256: str, generated_at: datetime, body: bytes):
        records = bundle.read_records(body)
        meta = records[0]["payload"] if records and records[0].get("kind") == "bundle_meta" else {}
        self.bundle_sha256, self.generated_at = sha256, generated_at
        self.valid_until = meta.get("valid_until")
        self.hosts = thin_hosts(records)
        by_shard: dict[str, dict] = {f"{index:02x}": {} for index in range(SHARD_COUNT)}
        for host, sections in self.hosts.items():
            by_shard[shard_id(host)][host] = sections
        built = export.iso(generated_at)
        self.files: dict[str, tuple[bytes, bytes, str]] = {}
        self.entries = []
        for key, hosts in by_shard.items():
            raw, gz, digest = encode({"schema": SHARD_SCHEMA, "shard": key, "generated_at": built,
                                      "valid_until": self.valid_until, "hosts": hosts})
            self.files[key] = (raw, gz, digest)
            self.entries.append({"id": key, "sha256": digest, "bytes": len(raw), "gzip_bytes": len(gz),
                                 "built_at": built, "hosts": len(hosts)})
        self.built = built


class _Changes:
    """Host sections that changed between the stored bundles this process has seen, kept for seven days.

    There is no history table, so this is a memory of the bundles the process served: the first bundle it sees is
    only the baseline, and ``covers_since`` in the file says from when changes could be seen. A restart starts a
    new baseline.
    """

    def __init__(self):
        self.lock = threading.Lock()
        self.bundle_sha256 = self.since = None
        self.prints: dict[tuple[str, str], str] = {}
        self.entries: dict[tuple[str, str], tuple[datetime, dict | None]] = {}

    def observe(self, shards: ShardSet) -> None:
        with self.lock:
            if shards.bundle_sha256 == self.bundle_sha256:
                return
            prints = {(host, section): fingerprint(section, thin)
                      for host, sections in shards.hosts.items() for section, thin in sections.items()}
            if self.bundle_sha256 is None:
                self.since = shards.generated_at
            else:
                for (host, section), digest in prints.items():
                    if self.prints.get((host, section)) != digest:
                        self.entries[(host, section)] = (shards.generated_at, shards.hosts[host][section])
                for host, section in self.prints.keys() - prints.keys():
                    self.entries[(host, section)] = (shards.generated_at, None)
            self.prints, self.bundle_sha256 = prints, shards.bundle_sha256

    def document(self, shards: ShardSet, now: datetime) -> dict:
        with self.lock:
            self.entries = {key: value for key, value in self.entries.items() if now - value[0] <= CHANGE_WINDOW}
            rows = []
            for (host, section), (changed_at, thin) in sorted(self.entries.items()):
                row = {"host": host, "section": section, "changed_on": changed_at.date().isoformat()}
                rows.append({**row, "removed": True} if thin is None else {**row, "record": thin})
            since = max(self.since or shards.generated_at, now - CHANGE_WINDOW)
        return {"schema": CHANGES_SCHEMA, "generated_at": shards.built, "window_days": CHANGE_WINDOW.days,
                "covers_since": export.iso(since), "changes": rows}


class _Cache:
    """The shard set of the stored bundle, derived once per stored file."""

    def __init__(self):
        self.lock = threading.Lock()
        self.shards: ShardSet | None = None

    def get(self, sha256: str, generated_at: datetime, body: bytes) -> ShardSet:
        with self.lock:
            if self.shards is None or self.shards.bundle_sha256 != sha256:
                self.shards = ShardSet(sha256, generated_at, body)
            return self.shards


class ShardBudget:
    """Bytes of shard, manifest and changes downloads per UTC day, counted in memory (a restart resets them).

    Separate from the full bundle's counter in the database: neither can use up the other's day.
    """

    def __init__(self):
        self.lock, self.day, self.used = threading.Lock(), None, 0

    def take(self, size: int, day: date) -> bool:
        with self.lock:
            if day != self.day:
                self.day, self.used = day, 0
            if self.used + size > bundle.SHARD_DAILY_BYTES:
                return False
            self.used += size
            return True


def _signer():
    """(key, key id) when AHN_EXPORT_SIGNING_KEY is readable here, else (None, None): the manifest is then hash-only."""
    try:
        return bundle.signing_from_env()
    except (OSError, ValueError):
        return None, None


def manifest_document(shards: ShardSet, changes_file: tuple[bytes, bytes, str]) -> dict:
    raw, gz, digest = changes_file
    document = {
        "schema": MANIFEST_SCHEMA, "generated_at": shards.built, "valid_until": shards.valid_until,
        "bundle_sha256": shards.bundle_sha256,
        "shard_key": "first byte of the SHA-256 of the lowercase hostname without a trailing dot, two lowercase hex digits",
        "shards": shards.entries,
        "changes": {"path": CHANGES_PATH, "sha256": digest, "bytes": len(raw), "gzip_bytes": len(gz),
                    "window_days": CHANGE_WINDOW.days},
    }
    key, kid = _signer()
    return export.sign(document, key, kid or export.key_id(shards.generated_at)) if key is not None else document


def add_routes(app, *, snapshot_error, unchanged, until_midnight, clock) -> None:
    """Register the shard paths. ``snapshot_error(now)`` returns ``((sha, generated_at, body), None)`` or ``(None, response)``."""
    cache, changes, budget = _Cache(), _Changes(), ShardBudget()

    def headers(digest: str, generated_at: datetime) -> dict:
        return {"ETag": f'"{digest}"', "Cache-Control": f"public, max-age={bundle.CACHE_SECONDS}",
                "Last-Modified": format_datetime(generated_at.astimezone(timezone.utc), usegmt=True),
                "Content-Encoding": "gzip", "X-Content-Type-Options": "nosniff", "Accept-Ranges": "none"}

    def serve(request: Request, now: datetime, make):
        """``make(shards)`` returns (raw, gzip, sha256) of the wanted file; budget, 304 and HEAD as for the bundle."""
        current, error = snapshot_error(now)
        if error is not None:
            return error
        shards = cache.get(*current)
        changes.observe(shards)
        file = make(shards, now)
        if file is None:
            return JSONResponse({"error": "not_found"}, status_code=404)
        _raw, gz, digest = file
        head = headers(digest, shards.generated_at)
        if unchanged(request, digest):
            return Response(status_code=304, headers=head)
        if request.method == "HEAD":
            return Response(content=b"", media_type="application/json", headers={**head, "Content-Length": str(len(gz))})
        if not budget.take(len(gz), now.date()):
            return JSONResponse({"error": "bundle_shard_daily_limit"}, status_code=429, headers=until_midnight(now))
        return Response(content=gz, media_type="application/json", headers=head)

    def changes_file(shards: ShardSet, now: datetime):
        return encode(changes.document(shards, now))

    @app.api_route(MANIFEST_PATH, methods=["GET", "HEAD"], include_in_schema=False)
    def shard_manifest(request: Request):
        return serve(request, clock(), lambda shards, now: encode(manifest_document(shards, changes_file(shards, now))))

    @app.api_route(CHANGES_PATH, methods=["GET", "HEAD"], include_in_schema=False)
    def shard_changes(request: Request):
        return serve(request, clock(), changes_file)

    @app.api_route(SHARDS_PATH + "/{shard}.json", methods=["GET", "HEAD"], include_in_schema=False)
    def shard_file(shard: str, request: Request):
        if not _SHARD_ID.fullmatch(shard):
            return JSONResponse({"error": "not_found"}, status_code=404)
        return serve(request, clock(), lambda shards, now: shards.files[shard])
