"""Offline data bundle: one gzip JSON Lines file of data anyone can re-check at its source.

The file is Export v1 (``schema_version`` 1, see ahn/export.py): a ``bundle_meta`` head record, then,
sorted by kind and hostname,

- ``access_profile``: the latest definitive robots.txt/llms.txt read of each host on the curated part of
  the public seed list (``ahn/seed_hosts.txt`` above the comment that opens its Tranco block), as the
  operator CLI exports it except that its time is cut to the UTC day;
- ``official_path``: the curated official developer paths (``ahn/official_paths.py``), with an ``access``
  bundle only while it is current under the same 90-day rule as the API;
- ``status_source``: the curated status-page mappings (``SOURCES`` and ``OFFICIAL_STATUS_PAGES`` in
  ``ahn/status_refresh.py``), the mapping only and never a feed's current value. Many of these hosts were
  found by scanning bands of the Tranco ranking; each mapping was checked on that host's own status page,
  and no rank is written.

Nothing else goes in. Reports, hosts people looked up that are not on the seed list, lookup counts,
request logs, IP digests and reporter keys never do: a looked-up host in a public file would tell
what someone looked up. Mappings come from the code, not from the ``status_sources`` table, whose
rows exist only after a lookup. The seed hosts taken from the Tranco ranking (the block below the
``RANKING_BLOCK_MARKER`` comment) are left out until that list's terms are settled. Records are sorted
by hostname, never in seed-file order; no rank is written. ``check_record`` enforces all of this on
every record, and a build that breaks a rule or a size limit fails instead of publishing.

The review cron (``python -m ahn.auto_review``) rebuilds the file about once a day and keeps the latest
one in ``public_bundle``; the web service serves it at ``/v1/bundle/latest.jsonl.gz`` with its SHA-256.
Both happen only while ``AHN_PUBLIC_BUNDLE=1``. With the switch off the cron deletes the stored row and
the web paths do not exist (404). Records are signed exactly as the operator CLI signs them, only when
``AHN_EXPORT_SIGNING_KEY`` is set.
"""

from __future__ import annotations

import base64
import gzip
import hashlib
import hmac
import io
import json
import os
import threading
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from email.utils import format_datetime

from fastapi import Request, Response
from fastapi.responses import JSONResponse

from . import export
from .official_paths import OFFICIAL_ACCESS, OFFICIAL_PATHS, access_is_current
from .provenance import operator_id
from .status_refresh import OFFICIAL_STATUS_PAGES, SOURCES

SWITCH_ENV = "AHN_PUBLIC_BUNDLE"
PATH = "/v1/bundle/latest.jsonl.gz"
SHA256_PATH = PATH + ".sha256"
LICENSE = "CC-BY-4.0"
LICENSE_URL = "https://creativecommons.org/licenses/by/4.0/"
TITLE = "Agent Help Network offline bundle"
SCHEMA_URL = "https://github.com/agent-help-network/agent-help-network/blob/main/docs/api-contract.en.md#offline-bundle"
THIRD_PARTY_QUOTES = ("Quoted publisher text (official_path access.free_tier.quote and access.terms.quote) is the "
                      "publishers' own material, not licensed under CC BY 4.0 by this bundle.")
STATUS_SOURCE_NOTE = ("Many status_source hosts were found by scanning bands of the Tranco ranking; each mapping was "
                      "checked on that host's own status page, and no rank is included.")

KINDS = ("access_profile", "official_path", "status_source")  # in file order, after the bundle_meta head
# What the file never holds, as the head record states it. The privacy policy names each one (tests/test_policy_coverage.py).
EXCLUDED = ("reports", "looked_up_hosts_outside_seed_list", "lookup_counts", "request_logs", "ip_digests",
            "reporter_keys", "status_feed_values", "ranking_list_seed_hosts", "seed_order_or_rank")
# The comment line that opens the seed list's Tranco block. The hosts below it come from a popularity ranking
# whose terms are not settled; they stay out. Without this line the build fails rather than guess.
RANKING_BLOCK_MARKER = "# Hosts chosen from the Tranco top 1,000"

# Limits. Measured with every curated seed host and heavy synthetic robots summaries: about 3,600 records,
# 20 MB uncompressed, 0.84 MB compressed (1.1 MB signed), built in about 1.5 seconds.
MAX_RECORDS = 20_000
MAX_COMPRESSED_BYTES = 8 * 1024 * 1024
MAX_RAW_BYTES = 128 * 1024 * 1024    # what a consumer must be ready to inflate
REBUILD_AFTER = timedelta(hours=23)   # the 5-minute cron then rebuilds about once a day
STALE_AFTER = timedelta(hours=48)     # an older stored file is not served (503 bundle_stale)
VALID_FOR = timedelta(days=8)         # valid_until: the seed list is re-read about weekly
DAILY_BYTES = 25 * 1024 * 1024        # full downloads per UTC day, counted in the database (under 0.8 GB a month)
CLIENT_DAILY_DOWNLOADS = 3            # full downloads per client per UTC day, counted in memory only
CACHE_SECONDS = 3600
_LOCK_KEY = 81260531022


def enabled() -> bool:
    return os.environ.get(SWITCH_ENV, "").strip() == "1"


def creator_from_env() -> str:
    """The licensor named in the notice: the operator's display name (AHN_OPERATOR_NAME), which the build requires."""
    name = " ".join(os.environ.get("AHN_OPERATOR_NAME", "").split())
    if not name or len(name) > 80 or not name.isprintable():
        raise BundleError("AHN_OPERATOR_NAME (1-80 printable characters) names the licensor; set it where the bundle is built")
    return name


# --- What may be in the file ------------------------------------------------------------------

ENVELOPE_KEYS = frozenset({"schema_version", "kind", "record_id", "origin_operator", "vantage", "reader_version",
                           "observed_at", "received_at", "expires_at", "source_url", "content_sha256", "payload",
                           "signature"})
PAYLOAD_KEYS = {
    "bundle_meta": frozenset({"title", "creator", "generated_at", "valid_until", "record_count", "counts", "license",
                              "license_url", "notice", "attribution", "third_party_quotes", "status_source_note",
                              "schema_url", "includes", "excludes"}),
    # content_use: robots.txt Content-Signal/Content-Usage preferences, parsed from the same file (migration 021).
    "access_profile": frozenset({"site_host", "read_host", "robots_status", "robots", "robots_detail", "llms_txt_status",
                                 "llms_detail", "llms_txt_url", "llms_sha256", "content_use"}),
    "official_path": frozenset({"site_host", "name", "api_docs", "developer_portal", "note", "access"}),
    "status_source": frozenset({"site_host", "source_name", "status_page_url", "api_url", "feed_kind"}),
}
ROBOTS_KEYS = frozenset({"rules", "wildcard_root_allowed", "sitemap", "sitemaps", "wildcard_crawl_delay",
                         "wildcard_disallow_paths", "agents_blocked", "agents_allowed", "grouping_differs_by_parser"})
RULE_KEYS = frozenset({"agent", "kind", "explicit_group", "root_allowed", "disallow_count", "crawl_delay", "disallow_paths"})
# The stored content_use of PR #38 (head 0f74955): parse_content_use() plus the hash of the robots.txt it came from.
CONTENT_USE_KEYS = frozenset({"vocab", "formats", "signals", "usage", "path_specific_samples", "named_groups",
                              "named_groups_total", "vendor_default_hint", "ai_input_declared_no", "ai_input_no_agents",
                              "ignored_keys", "robots_sha256"})
CONTENT_SIGNAL_KEYS = frozenset({"search", "ai_input", "ai_train", "use"})
CONTENT_USAGE_KEYS = frozenset({"train_ai", "ai_use", "search"})
NAMED_GROUP_KEYS = frozenset({"agents", "other_agents", "signals", "usage", "path_specific_samples"})
# official_paths.access (ahn/official_paths.py OFFICIAL_ACCESS).
ACCESS_KEYS = frozenset({"auth", "free_tier", "key_signup_url", "official_mcp", "openapi_url", "rate_limits_url",
                         "sandbox_url", "terms", "verified_at"})
ACCESS_SUB_KEYS = {"auth": frozenset({"method", "source_url"}), "official_mcp": frozenset({"endpoint", "doc_url"}),
                   "terms": frozenset({"url", "quote"}), "free_tier": frozenset({"quote", "source_url"})}
VANTAGE_KEYS = frozenset({"kind", "region", "asn", "declared"})
SIGNATURE_KEYS = frozenset({"alg", "key_id", "sig"})
# Names that must not appear at any depth, whatever the allowlists above say: the request-derived and
# report data of the service, a feed's current value, and seed rank.
FORBIDDEN_KEYS = frozenset({
    "ip", "ip_address", "ip_digest", "client_digest", "client_key", "key_hash", "reporter", "reporter_id",
    "reporter_label", "api_key", "idempotency_key", "request_hash", "withdrawal_token", "observation_id",
    "origin_observation_id", "incident_id", "observations", "agent_family", "received_channel", "attempt_actor",
    "attempt_env", "lookup_activity", "lookup_count", "lookups", "queried_at", "first_seen_at", "last_seen_at",
    "indicator", "source_updated_at", "rank", "seed_rank", "tranco_rank", "served_bytes", "served_on",
})


class BundleError(ValueError):
    """A record or the whole file breaks a bundle rule; nothing is published."""


def _check_keys(where: str, value, allowed: frozenset) -> None:
    if value is None:
        return
    if not isinstance(value, dict):
        raise BundleError(f"{where} is not an object")
    extra = set(value) - allowed
    if extra:
        raise BundleError(f"{where} has fields outside the bundle allowlist: {sorted(extra)}")


def _check_items(where: str, value, allowed: frozenset) -> None:
    """An object, or a list of objects, each within ``allowed``."""
    for item in value if isinstance(value, list) else [value]:
        _check_keys(where, item, allowed)


def _forbidden(value, where: str = "record") -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if str(key).lower() in FORBIDDEN_KEYS:
                raise BundleError(f"{where} carries the forbidden field {key!r}")
            _forbidden(item, f"{where}.{key}")
    elif isinstance(value, list):
        for item in value:
            _forbidden(item, where)


def _check_content_use(content_use) -> None:
    _check_keys("content_use", content_use, CONTENT_USE_KEYS)
    if not content_use:
        return
    _check_keys("content_use signals", content_use.get("signals"), CONTENT_SIGNAL_KEYS)
    _check_keys("content_use usage", content_use.get("usage"), CONTENT_USAGE_KEYS)
    for group in content_use.get("named_groups") or []:
        _check_keys("content_use named group", group, NAMED_GROUP_KEYS)
        _check_keys("content_use named group signals", group.get("signals"), CONTENT_SIGNAL_KEYS)
        _check_keys("content_use named group usage", group.get("usage"), CONTENT_USAGE_KEYS)


def _check_access(access) -> None:
    _check_keys("access", access, ACCESS_KEYS)
    for name, allowed in ACCESS_SUB_KEYS.items():
        if access and access.get(name) is not None:
            _check_items(f"access {name}", access[name], allowed)


def check_record(record: dict, *, seed_hosts: frozenset[str], operator: str) -> None:
    """Raise BundleError unless ``record`` holds only allowed fields and an allowed host for its kind."""
    _check_keys("envelope", record, ENVELOPE_KEYS)
    kind = record.get("kind")
    if kind not in PAYLOAD_KEYS or record.get("schema_version") != export.SCHEMA_VERSION:
        raise BundleError(f"kind {kind!r} or schema_version is not part of the bundle")
    payload = record.get("payload")
    if not isinstance(payload, dict):
        raise BundleError("payload is not an object")
    _check_keys(f"{kind} payload", payload, PAYLOAD_KEYS[kind])
    _check_keys("vantage", record.get("vantage"), VANTAGE_KEYS)
    _check_keys("signature", record.get("signature"), SIGNATURE_KEYS)
    if kind == "access_profile":
        _check_keys("robots", payload.get("robots"), ROBOTS_KEYS)
        for rule in (payload.get("robots") or {}).get("rules") or []:
            _check_keys("robots rule", rule, RULE_KEYS)
        _check_content_use(payload.get("content_use"))
    if kind == "official_path":
        _check_access(payload.get("access"))
    _forbidden(record)
    host = payload.get("site_host") if kind != "bundle_meta" else operator
    allowed = {"access_profile": seed_hosts, "official_path": OFFICIAL_PATHS,
               "status_source": SOURCES.keys() | OFFICIAL_STATUS_PAGES.keys(), "bundle_meta": {operator}}[kind]
    if host not in allowed:
        raise BundleError(f"{kind} record for a host outside the seed list and the curated tables")
    if not str(record.get("record_id", "")).startswith(f"ahn:{record.get('origin_operator')}:{kind}:{host}:"):
        raise BundleError(f"{kind} record_id does not name its own host")


# --- Records ----------------------------------------------------------------------------------

def utc_day(value: datetime) -> datetime:
    """Midnight UTC of the value's UTC day: read times are published to the day only."""
    return value.astimezone(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)


def access_profile_record(row: dict) -> dict:
    """The CLI export record of one read, with the read time cut to the UTC day (observed_at, received_at, record_id)."""
    return export.access_profile_envelope({**row, "fetched_at": utc_day(row["fetched_at"])})


def official_path_record(site_host: str, generated_at: datetime) -> dict:
    """One curated entry. Its access bundle goes in only while current for the whole validity of the file."""
    entry = OFFICIAL_PATHS[site_host]
    payload = {"site_host": site_host, "name": entry["name"], "api_docs": entry["api_docs"],
               "developer_portal": entry["developer_portal"], "note": entry["note"]}
    access = OFFICIAL_ACCESS.get(site_host)
    if access is not None and all(access_is_current(access, day) for day in
                                  (generated_at.date(), (generated_at + VALID_FOR).date())):
        payload["access"] = json.loads(json.dumps(access))
    return export.envelope(
        "official_path", site_host=site_host, observed_at=generated_at, received_at=None,
        source_url=entry["api_docs"] or entry["developer_portal"], payload=payload,
    )


def status_source_record(site_host: str, generated_at: datetime) -> dict:
    """The mapping a lookup would use: a verified feed first, else a link-only official status page."""
    source = SOURCES.get(site_host)
    if source is not None:
        name, page_url, api_url, feed_kind = source.name, source.page_url, source.api_url, source.kind
    else:
        (name, page_url), api_url, feed_kind = OFFICIAL_STATUS_PAGES[site_host], None, None
    return export.envelope(
        "status_source", site_host=site_host, observed_at=generated_at, received_at=None, source_url=api_url or page_url,
        payload={"site_host": site_host, "source_name": name, "status_page_url": page_url, "api_url": api_url,
                 "feed_kind": feed_kind},
    )


def meta_record(generated_at: datetime, counts: dict[str, int], creator: str) -> dict:
    operator = operator_id()
    year = generated_at.astimezone(timezone.utc).year
    return export.envelope(
        "bundle_meta", site_host=operator, observed_at=generated_at, received_at=generated_at,
        source_url=f"https://{operator}{PATH}",
        payload={
            "title": TITLE, "creator": creator, "generated_at": export.iso(generated_at),
            "valid_until": export.iso(generated_at + VALID_FOR),
            "record_count": sum(counts.values()), "counts": dict(counts),
            "license": LICENSE, "license_url": LICENSE_URL,
            # The marking format of the Creative Commons license chooser.
            "notice": f"{TITLE} © {year} by {creator} is licensed under CC BY 4.0. "
                      f"To view a copy of this license, visit {LICENSE_URL}",
            "attribution": f"Source: {TITLE} by {creator} ({operator}), {export.iso(generated_at)}, CC BY 4.0",
            "third_party_quotes": THIRD_PARTY_QUOTES, "status_source_note": STATUS_SOURCE_NOTE,
            "schema_url": SCHEMA_URL, "includes": list(KINDS), "excludes": list(EXCLUDED),
        },
    )


# --- The file ---------------------------------------------------------------------------------

@dataclass(frozen=True)
class Bundle:
    body: bytes = field(repr=False)  # gzip
    sha256: str
    generated_at: datetime
    counts: dict
    raw_bytes: int

    @property
    def record_count(self) -> int:
        return sum(self.counts.values())

    @property
    def filename(self) -> str:
        return filename(self.generated_at)


def filename(generated_at: datetime) -> str:
    return f"ahn-bundle-{generated_at.astimezone(timezone.utc):%Y%m%dT%H%M%SZ}.jsonl.gz"


def build(profile_rows, seed_hosts, *, now: datetime | None = None, signing_key=None, kid: str | None = None,
          creator: str | None = None) -> Bundle:
    """The whole file from access-profile rows (as export reads them) and the curated tables in the code.

    Rows of hosts not on ``seed_hosts`` are dropped here; ``check_record`` then refuses any record that
    still breaks a rule, so a mistake upstream fails the build instead of reaching the file.
    """
    generated_at = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    creator = creator or creator_from_env()
    seeds = frozenset(seed_hosts)
    operator = operator_id()
    records = {kind: [] for kind in KINDS}
    for row in profile_rows:
        if row["site_host"] in seeds:
            records["access_profile"].append(access_profile_record(row))
    records["official_path"] = [official_path_record(host, generated_at) for host in OFFICIAL_PATHS]
    records["status_source"] = [status_source_record(host, generated_at) for host in SOURCES.keys() | OFFICIAL_STATUS_PAGES.keys()]
    for kind in KINDS:
        records[kind].sort(key=lambda record: (record["payload"]["site_host"], record["record_id"]))
    counts = {kind: len(records[kind]) for kind in KINDS}
    if sum(counts.values()) > MAX_RECORDS:
        raise BundleError(f"{sum(counts.values())} records exceed the limit of {MAX_RECORDS}")
    ordered = [meta_record(generated_at, counts, creator)] + [record for kind in KINDS for record in records[kind]]
    # Streamed into the gzip writer line by line, so the uncompressed file is never held in memory at once.
    # A fixed mtime and no file name make the bytes (and the SHA-256) depend on the content only.
    out, raw_bytes = io.BytesIO(), 0
    with gzip.GzipFile(filename="", mode="wb", fileobj=out, compresslevel=6, mtime=int(generated_at.timestamp())) as zipped:
        for record in ordered:
            check_record(record, seed_hosts=seeds, operator=operator)
            if signing_key is not None:
                record = export.sign(record, signing_key, kid or export.key_id(generated_at))
            line = (json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
            raw_bytes += len(line)
            if raw_bytes > MAX_RAW_BYTES:
                raise BundleError(f"more than {MAX_RAW_BYTES} bytes uncompressed exceed the limit")
            zipped.write(line)
    body = out.getvalue()
    if len(body) > MAX_COMPRESSED_BYTES:
        raise BundleError(f"{len(body)} bytes compressed exceed the limit of {MAX_COMPRESSED_BYTES}")
    return Bundle(body, hashlib.sha256(body).hexdigest(), generated_at, counts, raw_bytes)


def read_records(body: bytes) -> list[dict]:
    """Inflate and parse a bundle (for tests and consumers' checks); refuses more than MAX_RAW_BYTES."""
    with gzip.GzipFile(fileobj=io.BytesIO(body)) as handle:
        raw = handle.read(MAX_RAW_BYTES + 1)
    if len(raw) > MAX_RAW_BYTES:
        raise BundleError("bundle inflates past MAX_RAW_BYTES")
    return [json.loads(line) for line in raw.decode("utf-8").splitlines() if line]


# --- Database ---------------------------------------------------------------------------------

def signing_from_env():
    path = os.environ.get("AHN_EXPORT_SIGNING_KEY", "").strip()
    return (export.load_signing_key(path), export.key_id()) if path else (None, None)


def bundle_seed_hosts(path=None) -> list[str]:
    """Seed hosts listed above the Tranco block's opening comment (the curated part), under the reader's eligibility rules.

    The file's leading comment block may cite Tranco. Any later comment above the marker that names Tranco would
    mean ranking-derived hosts sit in the curated part, so the build fails instead of publishing them.
    """
    from .profile_warm import SEED_PATH, load_seed_hosts

    curated: set[str] = set()
    in_header = True
    with open(path or SEED_PATH, encoding="utf-8-sig") as handle:
        for line in handle:
            if line.startswith(RANKING_BLOCK_MARKER):
                break
            text = line.strip()
            if text.startswith("#"):
                if not in_header and "tranco" in text.lower():
                    raise BundleError("a comment above the Tranco block marker names Tranco; "
                                      "ranking-derived hosts may sit in the curated part")
                continue
            if text:
                in_header = False
                curated.add(text)
        else:
            raise BundleError("the seed list has no Tranco block marker; refusing to guess which hosts are curated")
    return [host for host in load_seed_hosts(path) if host in curated]


def build_from_db(conn, *, now: datetime | None = None, signing_key=None, kid: str | None = None) -> Bundle:
    return build(export._rows(conn, "access_profile", None), bundle_seed_hosts(), now=now, signing_key=signing_key, kid=kid)


def lock(conn) -> bool:
    """The build lock, held until the transaction ends; the cron and `ahn.cli bundle --store` both take it."""
    return bool(conn.execute("SELECT pg_try_advisory_xact_lock(%s) AS ok", (_LOCK_KEY,)).fetchone()["ok"])


def store(conn, bundle: Bundle) -> None:
    """Replace the stored file. The day's served-bytes count is kept: a rebuild does not reset the budget."""
    conn.execute(
        """INSERT INTO public_bundle (id,generated_at,sha256,record_count,byte_size,body) VALUES (1,%s,%s,%s,%s,%s)
           ON CONFLICT (id) DO UPDATE SET generated_at=EXCLUDED.generated_at,sha256=EXCLUDED.sha256,
           record_count=EXCLUDED.record_count,byte_size=EXCLUDED.byte_size,body=EXCLUDED.body""",
        (bundle.generated_at, bundle.sha256, bundle.record_count, len(bundle.body), bundle.body),
    )


def refresh_if_due(database_url: str, *, now: datetime | None = None, force: bool = False) -> dict:
    """Switch on: build and store a new bundle when the stored one is older than REBUILD_AFTER.

    Switch off: delete the stored row, so nothing is left to serve if the web switch is turned on alone.
    """
    import psycopg
    from psycopg.rows import dict_row

    from .db import conninfo

    with psycopg.connect(conninfo(database_url), row_factory=dict_row) as conn:
        if not enabled():
            deleted = conn.execute("DELETE FROM public_bundle").rowcount
            return {"skipped": "switch_off", "deleted": deleted}
        now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        if not lock(conn):
            return {"skipped": "locked"}
        row = conn.execute("SELECT generated_at FROM public_bundle WHERE id=1").fetchone()
        if row and not force and now - row["generated_at"] < REBUILD_AFTER:
            return {"skipped": "fresh"}
        signing_key, kid = signing_from_env()
        bundle = build_from_db(conn, now=now, signing_key=signing_key, kid=kid)
        store(conn, bundle)
    return {"built": bundle.record_count, "bytes": len(bundle.body), "sha256": bundle.sha256,
            "signed": signing_key is not None}


# --- Serving ----------------------------------------------------------------------------------

LLMS_SECTION = (
    "## Offline bundle\n\n"
    "- `https://agenthelpnetwork.com/v1/bundle/latest.jsonl.gz` (SHA-256 at the same path + `.sha256`): a daily gzip "
    "JSON Lines file, CC BY 4.0, with the robots.txt/llms.txt reads of curated seed-list hosts, the curated official "
    "paths and the curated status-page mappings, so no hostname has to leave your agent. No reports, looked-up hosts, "
    "counts, feed values or ranking-list hosts. Fresh until the head record's `valid_until`. Pull at most daily, with "
    "`If-None-Match`; each client may download it 3 times a day. Format: " + SCHEMA_URL + "\n\n"
)


class _Snapshot:
    """The stored bundle, read once per change: a cheap check at most once a minute, the body only when it changed."""

    def __init__(self):
        self.lock = threading.Lock()
        self.checked = None
        self.sha256 = None
        self.generated_at = None
        self.body = None

    def current(self, settings, now: datetime) -> tuple[str, datetime, bytes] | None:
        from .db import connect

        with self.lock:
            if self.checked is None or now - self.checked >= timedelta(seconds=60):
                with connect(settings) as conn:
                    row = conn.execute("SELECT sha256,generated_at FROM public_bundle WHERE id=1").fetchone()
                    if row is None:
                        self.sha256 = self.generated_at = self.body = None
                    elif row["sha256"] != self.sha256:
                        full = conn.execute("SELECT sha256,generated_at,body FROM public_bundle WHERE id=1").fetchone()
                        body = bytes(full["body"])
                        if hashlib.sha256(body).hexdigest() != full["sha256"]:
                            raise BundleError("stored bundle does not match its SHA-256")
                        self.sha256, self.generated_at, self.body = full["sha256"], full["generated_at"], body
                self.checked = now
            if self.body is None:
                return None
            return self.sha256, self.generated_at, self.body


class _DbBudget:
    """Bytes of full downloads per UTC day, counted in the stored row so a restart does not reset them."""

    @staticmethod
    def take(settings, size: int, day: date) -> bool:
        from .db import connect

        with connect(settings) as conn:
            row = conn.execute(
                """UPDATE public_bundle SET served_on=%(day)s,
                          served_bytes=(CASE WHEN served_on=%(day)s THEN served_bytes ELSE 0 END) + %(size)s
                   WHERE id=1 AND (CASE WHEN served_on=%(day)s THEN served_bytes ELSE 0 END) + %(size)s <= %(limit)s
                   RETURNING served_bytes""",
                {"day": day, "size": size, "limit": DAILY_BYTES},
            ).fetchone()
        return row is not None


class _ClientDownloads:
    """Full downloads per client per UTC day, keyed by a keyed digest of the client IP, in memory only."""

    def __init__(self, pepper: str):
        self.pepper, self.lock, self.day, self.counts = pepper.encode("utf-8"), threading.Lock(), None, {}

    def _key(self, client: str, day: date) -> str:
        return hmac.new(self.pepper, f"bundle:{day.isoformat()}:{client}".encode("utf-8"), hashlib.sha256).hexdigest()[:32]

    def allowed(self, client: str, day: date) -> bool:
        with self.lock:
            if day != self.day:
                self.day, self.counts = day, {}
            return self.counts.get(self._key(client, day), 0) < CLIENT_DAILY_DOWNLOADS

    def count(self, client: str, day: date) -> None:
        with self.lock:
            key = self._key(client, day)
            self.counts[key] = self.counts.get(key, 0) + 1


def add_routes(app, settings, *, clock=lambda: datetime.now(timezone.utc)) -> None:
    """Register the two bundle paths. Called only while the switch is on; otherwise they do not exist (404)."""
    from .rate_limit import RequestLimiter

    snapshot, clients = _Snapshot(), _ClientDownloads(settings.key_pepper)

    def headers(sha: str, generated_at: datetime) -> dict:
        return {"ETag": f'"{sha}"', "Cache-Control": f"public, max-age={CACHE_SECONDS}",
                "Last-Modified": format_datetime(generated_at.astimezone(timezone.utc), usegmt=True),
                "X-Content-Type-Options": "nosniff", "Accept-Ranges": "none"}

    def unavailable(code: str) -> Response:
        return JSONResponse({"error": code}, status_code=503, headers={"Retry-After": "300", "Cache-Control": "no-store"})

    def until_midnight(now: datetime) -> dict:
        midnight = datetime.combine(now.date() + timedelta(days=1), datetime.min.time(), timezone.utc)
        return {"Retry-After": str(max(60, int((midnight - now).total_seconds()))), "Cache-Control": "no-store"}

    def unchanged(request: Request, sha: str) -> bool:
        tags = {tag.strip().removeprefix("W/").strip('"') for tag in request.headers.get("if-none-match", "").split(",")}
        return sha in tags or "*" in tags

    def current_or_error(now: datetime):
        current = snapshot.current(settings, now)
        if current is None:
            return None, unavailable("bundle_not_ready")
        if now - current[1] > STALE_AFTER:
            return None, unavailable("bundle_stale")
        return current, None

    @app.api_route(PATH, methods=["GET", "HEAD"], include_in_schema=False)
    def bundle_file(request: Request):
        now = clock()
        current, error = current_or_error(now)
        if error is not None:
            return error
        sha, generated_at, body = current
        head = {**headers(sha, generated_at), "Content-Disposition": f'attachment; filename="{filename(generated_at)}"',
                "Repr-Digest": f"sha-256=:{base64.b64encode(bytes.fromhex(sha)).decode('ascii')}:"}
        if unchanged(request, sha):
            return Response(status_code=304, headers=head)
        if request.method == "HEAD":
            return Response(content=b"", media_type="application/gzip", headers={**head, "Content-Length": str(len(body))})
        # A Range request gets the whole file (Accept-Ranges: none) and counts as a full download.
        client, day = RequestLimiter.client_key(request), now.date()
        if not clients.allowed(client, day):
            return JSONResponse({"error": "bundle_client_limit"}, status_code=429, headers=until_midnight(now))
        if not _DbBudget.take(settings, len(body), day):
            return JSONResponse({"error": "bundle_daily_limit"}, status_code=429, headers=until_midnight(now))
        clients.count(client, day)
        return Response(content=body, media_type="application/gzip", headers=head)

    @app.api_route(SHA256_PATH, methods=["GET", "HEAD"], include_in_schema=False)
    def bundle_sha256(request: Request):
        current, error = current_or_error(clock())
        if error is not None:
            return error
        sha, generated_at, _body = current
        head = headers(sha, generated_at)
        if unchanged(request, sha):
            return Response(status_code=304, headers=head)
        # sha256sum format, so `sha256sum -c` checks a download saved under the same name.
        return Response(content=f"{sha}  {filename(generated_at)}\n", media_type="text/plain", headers=head)
