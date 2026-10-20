"""Public government domain lists: download whole, store, join on lookup.

Two lists are read, and only these two:

* the U.S. .gov registry's ``current-full.csv`` (CISA, public repository ``cisagov/dotgov-data``, CC0-1.0),
  checked once a day with a conditional request;
* the UK "List of .gov.uk domain names" (GOV.UK, Open Government Licence v3.0), whose newest CSV is found
  through the GOV.UK content API and checked once every 30 days.

The files are the publishers' own whole-file downloads. No request goes to any site on a list or to any
looked-up host, and a lookup never waits on a publisher: ``host_facts`` reads the tables below and nothing else.
The review cron (``python -m ahn.auto_review``) runs ``refresh_due``; ``python -m ahn.gov_registry`` runs it by hand.

Protection of what is already stored. A download that fails, redirects, is too large, no longer has the
expected columns, has too many unusable rows, has too few rows, or has fewer than half the rows of the
stored version is rejected: the stored rows stay, the reason is kept as ``last_error`` and a warning is
logged (a label only, never file content). After a failure the next attempt comes six hours later.

What is kept. For each domain: its name and, for the U.S. list, the domain type, organisation,
sub-organisation and state. The U.S. file's security contact email and city are read and dropped, the UK
file has the domain name only. Per list: the version, source address, ETag, a SHA-256 of the file as
received, the row count and the check times.

What is answered. A fact about a domain (which list names it, and the organisation the list gives), and the
registry rule that a suffix is reserved for government bodies. Never a judgement: nothing here says that a
site, page or link is genuine, and ``recommendation`` does not depend on it.
"""

from __future__ import annotations

import csv
import io
import json
import logging
import os
import re
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from urllib.parse import urlsplit

import urllib3
from psycopg.types.json import Jsonb

from . import access_profile
from .db import connect
from .provenance import operator_id, vantage, sha256_hex

LOG = logging.getLogger(__name__)

US, UK = "us_dotgov", "uk_govuk"
USER_AGENT = access_profile.USER_AGENT
UK_ATTRIBUTION = "Contains public sector information licensed under the Open Government Licence v3.0."
FAILURE_RETRY = timedelta(hours=6)
MIN_KEEP_RATIO = 0.5          # a download with fewer rows than this share of the stored version is rejected
MAX_BAD_ROW_RATIO = 0.02      # more unusable rows than this share means the format changed
MAX_FIELD_CHARS = 200
UK_API_CAP_BYTES = 524_288
ALLOWED_HOSTS = frozenset({"raw.githubusercontent.com", "www.gov.uk", "assets.publishing.service.gov.uk"})
LOCK_KEY = 81260531004        # advisory lock: one refresh at a time across the cron and a manual run
CONNECT_TIMEOUT = 5.0
READ_TIMEOUT = 15.0
BODY_BUDGET = 30.0


@dataclass(frozen=True)
class Registry:
    key: str
    name: str
    url: str                  # what is downloaded (UK: the content API that names the CSV)
    source_url: str           # the page a reader can open to see the list
    license: str
    attribution: str | None
    check_every: timedelta
    max_bytes: int
    min_rows: int


REGISTRIES = {
    US: Registry(
        US, "U.S. .gov domain list (CISA)",
        "https://raw.githubusercontent.com/cisagov/dotgov-data/main/current-full.csv",
        "https://github.com/cisagov/dotgov-data/blob/main/current-full.csv",
        "CC0-1.0", None, timedelta(hours=24), 8_000_000, 5_000),
    UK: Registry(
        UK, "UK .gov.uk domain list (GOV.UK)",
        "https://www.gov.uk/api/content/government/publications/list-of-gov-uk-domain-names",
        "https://www.gov.uk/government/publications/list-of-gov-uk-domain-names",
        "OGL-UK-3.0", UK_ATTRIBUTION, timedelta(days=30), 2_000_000, 2_000),
}


@dataclass(frozen=True)
class SuffixRule:
    suffix: str
    labels: int               # labels of a registrable name under the suffix (irs.gov = 2, hmrc.gov.uk = 3)
    registry: str | None      # the list that names individual domains under it, if this service reads one
    text: str
    source_url: str


# Suffixes whose registry says that only government bodies may register under them. A rule is a statement
# about who may register a name; it carries no information about any page.
SUFFIX_RULES = {
    "gov": SuffixRule("gov", 2, US,
                      "Only verified U.S. government organizations can register a .gov domain.",
                      "https://get.gov/domains/eligibility/"),
    "gov.uk": SuffixRule("gov.uk", 3, UK,
                         "A .gov.uk domain is for UK public sector organisations and needs the GOV.UK Domains Team's permission.",
                         "https://www.gov.uk/guidance/get-permission-to-apply-for-a-govuk-domain-name"),
    "go.kr": SuffixRule("go.kr", 3, None,
                        "Under the KRNIC domain name management rules, .go.kr is for administrative agencies and legislative or judicial agencies.",
                        "https://krnic.kisa.or.kr/jsp/eng/policy/domainNewRules.jsp"),
}
HOST_FACTS_NOTE = ("Registry facts about a domain: which public government list names it, and whether its suffix is reserved for "
                   "government bodies. They are not a judgement about the site, any page or any link.")


def enabled() -> bool:
    """AHN_HOST_FACTS=0 turns the answer field and the cron download off."""
    return os.environ.get("AHN_HOST_FACTS", "1").strip() != "0"


# --- Parsing (pure) ----------------------------------------------------------------------------------

class ListRejected(Exception):
    """A download that must not replace the stored version; ``label`` is a short machine-readable reason."""

    def __init__(self, label: str):
        super().__init__(label)
        self.label = label


_DOMAIN_RE = re.compile(r"^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z][a-z0-9-]{0,62}$")
_SPACE_RE = re.compile(r"\s+")


def _cell(row: list[str], index: int) -> str | None:
    if index >= len(row):
        return None
    value = _SPACE_RE.sub(" ", "".join(ch for ch in row[index] if ch.isprintable() or ch.isspace())).strip()
    if not value or value.lower() == "(blank)":
        return None
    return value[:MAX_FIELD_CHARS]


def _rows(data: bytes):
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise ListRejected("invalid_encoding") from None
    return csv.reader(io.StringIO(text, newline=""))


def _domain(value: str | None, suffix: str, labels: int) -> str | None:
    if not value:
        return None
    name = value.strip().lower().rstrip(".")
    if not _DOMAIN_RE.match(name) or not name.endswith("." + suffix) or len(name.split(".")) != labels:
        return None
    return name


def parse_us(data: bytes) -> dict[str, tuple]:
    """The U.S. file as ``{domain: (domain_type, organization, suborganization, state)}``.

    Columns are found by header name. The security contact email and city are never indexed, so they
    are not copied out of the file.
    """
    reader = _rows(data)
    header = next(reader, None)
    if not header:
        raise ListRejected("format_changed")
    names = [name.strip().lower() for name in header]
    try:
        index = [names.index(wanted) for wanted in ("domain name", "domain type", "organization name", "suborganization name", "state")]
    except ValueError:
        raise ListRejected("format_changed") from None
    domains: dict[str, tuple] = {}
    total = bad = 0
    for row in reader:
        if not row or not any(part.strip() for part in row):
            continue
        total += 1
        name = _domain(_cell(row, index[0]), "gov", 2)
        if name is None:
            bad += 1
            continue
        domains[name] = tuple(_cell(row, i) for i in index[1:])
    _check_rows("us_dotgov", total, bad, len(domains))
    return domains


def parse_uk(data: bytes) -> dict[str, tuple]:
    """The UK file as ``{domain: ()}``. Only the first column is read: a later list that adds owner or
    contact columns changes nothing here, and none of those columns is kept."""
    reader = _rows(data)
    domains: dict[str, tuple] = {}
    total = bad = 0
    for position, row in enumerate(reader):
        first = _cell(row, 0) if row else None
        if first is None:
            continue
        if position == 0 and " " in first and not _DOMAIN_RE.match(first.lower()):
            continue  # the header line ("Domain: Domain Name")
        if first.startswith("_"):
            continue  # DNS-record names listed with the domains (_dmarc.gov.uk); not a format problem
        total += 1
        name = _domain(first, "gov.uk", 3) or (first.strip().lower() if first.strip().lower() == "gov.uk" else None)
        if name is None:
            bad += 1
            continue
        domains[name] = ()
    _check_rows("uk_govuk", total, bad, len(domains))
    return domains


def _check_rows(key: str, total: int, bad: int, kept: int) -> None:
    if total == 0 or kept == 0:
        raise ListRejected("format_changed")
    if bad > total * MAX_BAD_ROW_RATIO:
        raise ListRejected("format_changed")
    if kept < REGISTRIES[key].min_rows:
        raise ListRejected("too_few_rows")


_MONTHS = {name: number for number, name in enumerate(
    ("january", "february", "march", "april", "may", "june", "july", "august", "september", "october", "november", "december"), 1)}
_DATE_IN_TEXT = re.compile(r"(\d{1,2})[\s_]+([A-Za-z]+)[\s_]+(\d{4})")


def _as_of(text: str) -> date | None:
    found = None
    for day, month, year in _DATE_IN_TEXT.findall(text or ""):
        number = _MONTHS.get(month.lower())
        if number:
            try:
                found = date(int(year), number, int(day))
            except ValueError:
                continue
    return found


def newest_uk_csv(api_body: bytes) -> tuple[str, str]:
    """(as-of date, CSV address) of the newest list named by the GOV.UK content API.

    Only a ``text/csv`` attachment on GOV.UK's asset host qualifies, so a changed document can never
    point the download at another host. The date comes from the attachment title or file name.
    """
    try:
        document = json.loads(api_body)
        attachments = document["details"]["attachments"]
    except (ValueError, KeyError, TypeError):
        raise ListRejected("format_changed") from None
    best: tuple[date, str] | None = None
    for attachment in attachments if isinstance(attachments, list) else []:
        if not isinstance(attachment, dict):
            continue
        url = attachment.get("url")
        if not isinstance(url, str) or attachment.get("content_type") != "text/csv":
            continue
        parts = urlsplit(url)
        if parts.scheme != "https" or parts.hostname != "assets.publishing.service.gov.uk" or not parts.path.endswith(".csv"):
            continue
        when = _as_of(str(attachment.get("title") or "")) or _as_of(str(attachment.get("filename") or ""))
        if when and (best is None or when > best[0]):
            best = (when, url)
    if best is None:
        raise ListRejected("format_changed")
    return best[0].isoformat(), best[1]


# --- Download ----------------------------------------------------------------------------------------

@dataclass(frozen=True)
class Fetched:
    status: str               # ok | not_modified | error
    body: bytes | None = None
    etag: str | None = None
    detail: str | None = None  # label such as http_503, redirected, too_large, read_timeout, dns_failed


def http_get(url: str, *, cap_bytes: int, etag: str | None = None) -> Fetched:
    """One HTTPS GET of a fixed public list address; never raises for network or HTTP failures.

    Only the hosts in ALLOWED_HOSTS are contacted. A redirect is a failure (the publishers' addresses are
    direct), and a body over cap_bytes is a failure, not a truncated file.
    """
    parts = urlsplit(url)
    if parts.scheme != "https" or parts.hostname not in ALLOWED_HOSTS:
        return Fetched("error", detail="host_not_allowed")
    headers = {"User-Agent": USER_AGENT, "Accept": "text/csv,application/json;q=0.9,*/*;q=0.5"}
    if etag:
        headers["If-None-Match"] = etag
    response = pool = None
    try:
        pool = urllib3.HTTPSConnectionPool(parts.hostname, port=parts.port or 443, retries=False,
                                          timeout=urllib3.Timeout(connect=CONNECT_TIMEOUT, read=READ_TIMEOUT))
        response = pool.urlopen("GET", parts.path + (f"?{parts.query}" if parts.query else ""), headers=headers,
                                redirect=False, preload_content=False, assert_same_host=False)
        status = response.status
        if status == 304:
            return Fetched("not_modified", etag=etag)
        if 300 <= status < 400:
            return Fetched("error", detail="redirected")
        if status != 200:
            return Fetched("error", detail=f"http_{status}")
        declared = response.headers.get("Content-Length", "")
        if declared.isdigit() and int(declared) > cap_bytes:
            return Fetched("error", detail="too_large")
        body, problem = access_profile.read_capped(response, cap_bytes, budget=BODY_BUDGET)
        if body is None:
            return Fetched("error", detail=problem or "read_error")
        return Fetched("ok", body, (response.headers.get("ETag") or "").strip()[:200] or None)
    except Exception as exc:  # noqa: BLE001 - every transport failure becomes a label
        return Fetched("error", detail=access_profile._transport_label(exc))
    finally:
        if response is not None:
            response.release_conn()
        if pool is not None:
            pool.close()


# --- Refresh -----------------------------------------------------------------------------------------

def _due(row, registry: Registry, now: datetime, force: bool) -> bool:
    if force or row is None or row["fetched_at"] is None:
        # Never loaded: try now, unless the last attempt failed within the retry delay.
        if not force and row is not None and row["last_attempt_at"] is not None and now - row["last_attempt_at"] < FAILURE_RETRY:
            return False
        return True
    failed_since = row["last_error"] is not None and row["last_attempt_at"] is not None and row["last_attempt_at"] > row["fetched_at"]
    if failed_since:
        return now - row["last_attempt_at"] >= FAILURE_RETRY
    return now - row["fetched_at"] >= registry.check_every


def _record_failure(conn, registry: Registry, now: datetime, label: str) -> None:
    conn.execute(
        """INSERT INTO gov_registry_versions (registry,last_attempt_at,last_error) VALUES (%s,%s,%s)
           ON CONFLICT (registry) DO UPDATE SET last_attempt_at=EXCLUDED.last_attempt_at, last_error=EXCLUDED.last_error""",
        (registry.key, now, label))


def _record_success(conn, registry: Registry, now: datetime, **fields) -> None:
    """Upsert the version row: provenance of this check plus any changed fields; clears last_error."""
    columns = {"registry": registry.key, "fetched_at": now, "last_attempt_at": now, "last_error": None,
               "origin_operator": operator_id(), "vantage": Jsonb(vantage()), "reader_version": USER_AGENT, **fields}
    names = list(columns)
    conn.execute(
        f"INSERT INTO gov_registry_versions ({','.join(names)}) VALUES ({','.join(['%s'] * len(names))}) "
        f"ON CONFLICT (registry) DO UPDATE SET {','.join(f'{n}=EXCLUDED.{n}' for n in names if n != 'registry')}",
        [columns[n] for n in names])


def _replace_rows(conn, key: str, domains: dict[str, tuple]) -> None:
    """Make the stored rows of one list equal to ``domains``; unchanged rows are not rewritten."""
    names = list(domains)
    columns = [[(domains[name][i] if len(domains[name]) > i else None) for name in names] for i in range(4)]
    conn.execute(
        """INSERT INTO gov_domain_registry AS g (registry,domain,domain_type,organization,suborganization,state)
           SELECT %s, u.d, u.t, u.o, u.s, u.st
           FROM unnest(%s::text[],%s::text[],%s::text[],%s::text[],%s::text[]) AS u(d,t,o,s,st)
           ON CONFLICT (registry,domain) DO UPDATE
             SET domain_type=EXCLUDED.domain_type, organization=EXCLUDED.organization,
                 suborganization=EXCLUDED.suborganization, state=EXCLUDED.state
             WHERE (g.domain_type,g.organization,g.suborganization,g.state)
                   IS DISTINCT FROM (EXCLUDED.domain_type,EXCLUDED.organization,EXCLUDED.suborganization,EXCLUDED.state)""",
        (key, names, *columns))
    conn.execute("DELETE FROM gov_domain_registry WHERE registry=%s AND domain <> ALL(%s::text[])", (key, names))


def refresh_one(conn, key: str, *, now: datetime | None = None, force: bool = False, fetch=None) -> dict:
    """Check one list and load it if it changed. Never raises for a download or format problem.

    Runs inside the caller's transaction (the advisory lock is held by it). Returns
    ``{"registry", "outcome", "rows", "reason"}`` with outcome in not_due, not_modified, unchanged, loaded,
    kept_previous.
    """
    registry = REGISTRIES[key]
    fetch = fetch or http_get  # looked up at call time, so tests can replace it
    now = now or datetime.now(timezone.utc)
    row = conn.execute("SELECT * FROM gov_registry_versions WHERE registry=%s", (key,)).fetchone()
    if not _due(row, registry, now, force):
        return {"registry": key, "outcome": "not_due", "rows": row["row_count"] if row else None, "reason": None}
    stored_rows = row["row_count"] if row and row["list_version"] else None

    def keep(reason: str) -> dict:
        _record_failure(conn, registry, now, reason)
        LOG.warning("gov_registry_kept_previous registry=%s reason=%s stored_rows=%s", key, reason, stored_rows)
        return {"registry": key, "outcome": "kept_previous", "rows": stored_rows, "reason": reason}

    try:
        if key == UK:
            api = fetch(registry.url, cap_bytes=UK_API_CAP_BYTES)
            if api.status != "ok":
                return keep(api.detail or "fetch_failed")
            version, url = newest_uk_csv(api.body)
            if row and row["list_version"] == version and row["content_sha256"]:
                _record_success(conn, registry, now)
                return {"registry": key, "outcome": "unchanged", "rows": stored_rows, "reason": None}
            download = fetch(url, cap_bytes=registry.max_bytes)
            parse = parse_uk
        else:
            version, url = None, registry.url
            download = fetch(url, cap_bytes=registry.max_bytes,
                             etag=row["etag"] if row and row["list_version"] and not force else None)
            parse = parse_us
        if download.status == "not_modified":
            _record_success(conn, registry, now)
            return {"registry": key, "outcome": "not_modified", "rows": stored_rows, "reason": None}
        if download.status != "ok":
            return keep(download.detail or "fetch_failed")
        digest = sha256_hex(download.body)
        if row and row["content_sha256"] == digest and row["list_version"]:
            _record_success(conn, registry, now, etag=download.etag)
            return {"registry": key, "outcome": "unchanged", "rows": stored_rows, "reason": None}
        domains = parse(download.body)
        if stored_rows and len(domains) < stored_rows * MIN_KEEP_RATIO:
            raise ListRejected("row_count_drop")
    except ListRejected as exc:
        return keep(exc.label)
    try:
        with conn.transaction():
            _record_success(conn, registry, now, list_version=version or now.date().isoformat(), source_url=url,
                            etag=download.etag, content_sha256=digest, row_count=len(domains), loaded_at=now)
            _replace_rows(conn, key, domains)
    except Exception:  # noqa: BLE001 - the savepoint is rolled back, so the stored version is untouched
        return keep("load_failed")
    return {"registry": key, "outcome": "loaded", "rows": len(domains), "reason": None}


def refresh_due(settings, *, now: datetime | None = None, force: bool = False, fetch=None) -> list[dict]:
    """The cron step: refresh each list that is due. Several processes never download at once."""
    if not enabled():
        return []
    results = []
    with connect(settings) as conn:
        if not conn.execute("SELECT pg_try_advisory_xact_lock(%s) AS ok", (LOCK_KEY,)).fetchone()["ok"]:
            return []
        for key in REGISTRIES:
            try:
                results.append(refresh_one(conn, key, now=now, force=force, fetch=fetch))
            except Exception as exc:  # noqa: BLE001 - one list's trouble must not stop the other
                LOG.warning("gov_registry_step_failed registry=%s error=%s", key, type(exc).__name__)
                results.append({"registry": key, "outcome": "failed", "rows": None, "reason": type(exc).__name__})
                conn.rollback()  # also releases the lock: take it again before the next list, or stop
                if not conn.execute("SELECT pg_try_advisory_xact_lock(%s) AS ok", (LOCK_KEY,)).fetchone()["ok"]:
                    break
    return results


# --- Answer (database only) --------------------------------------------------------------------------

def _suffix_rule_for(host: str) -> SuffixRule | None:
    for suffix in sorted(SUFFIX_RULES, key=len, reverse=True):
        rule = SUFFIX_RULES[suffix]
        if host.endswith("." + suffix) and len(host.split(".")) == rule.labels:
            return rule
    return None


def host_facts(conn, host: str) -> dict | None:
    """Registry facts for one eligible apex host (the caller passes ``policy_host``), or None.

    None when no government list or suffix rule applies, so the answer stays short for ordinary hosts.
    Database reads only; a list that has not been loaded yet is simply left out.
    """
    rule = _suffix_rule_for(host)
    if rule is None:
        return None
    facts: dict = {}
    if rule.registry:
        registry = REGISTRIES[rule.registry]
        row = conn.execute(
            """SELECT v.list_version, v.fetched_at, g.domain AS found, g.domain_type, g.organization, g.suborganization, g.state
               FROM gov_registry_versions v
               LEFT JOIN gov_domain_registry g ON g.registry=v.registry AND g.domain=%s
               WHERE v.registry=%s AND v.list_version IS NOT NULL AND v.fetched_at IS NOT NULL""",
            (host, rule.registry)).fetchone()
        if row:
            entry = {"status": "listed" if row["found"] else "not_in_list_version", "registry": registry.key,
                     "registry_name": registry.name}
            for field in ("domain_type", "organization", "suborganization", "state"):
                if row["found"] and row[field]:
                    entry[field] = row[field]
            entry.update({"list_version": row["list_version"], "source_url": registry.source_url, "license": registry.license})
            if registry.attribution:
                entry["attribution"] = registry.attribution
            entry["fetched_at"] = row["fetched_at"]
            facts["government_registry"] = entry
    facts["suffix_rule"] = {"suffix": rule.suffix, "rule": "registration_limited_to_government", "text": rule.text,
                            "source_url": rule.source_url}
    facts["note"] = HOST_FACTS_NOTE
    return facts


def main(argv: list[str] | None = None) -> int:
    """Run the refresh by hand: ``python -m ahn.gov_registry [--force]``."""
    from .settings import Settings

    force = "--force" in (argv if argv is not None else sys.argv[1:])
    database_url = os.environ.get("AHN_DATABASE_URL", "").strip()
    if not database_url:
        raise RuntimeError("AHN_DATABASE_URL is required")
    for result in refresh_due(Settings(database_url, "unused-by-gov-registry"), force=force):
        print("gov_registry " + " ".join(f"{k}={v}" for k, v in result.items() if v is not None))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
