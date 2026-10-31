"""Format check for data contributions in contrib/*.jsonl. No network: it reads the files and nothing else.

    python scripts/check_contrib.py                 # every contrib/*.jsonl
    python scripts/check_contrib.py path/a.jsonl    # given files

A line is a bundle record trimmed to what a contributor knows (see contrib/README.md):
schema_version, kind (official_path | status_source), observed_at, source_url, payload, and an optional credit.
The check is about shape only: allowed fields, hostname grammar, plain https URLs, no private or secret-looking
content. Whether the data is true is decided by the maintainers' usual curation, which re-reads the public source.

Errors fail the check (exit 1). Warnings (a URL outside the publisher's own domain, for instance) do not: they are
what a reviewer looks at first.
"""

from __future__ import annotations

import importlib.util
import json
import re
import sys
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ahn import bundle  # noqa: E402
from ahn.models import valid_host  # noqa: E402
from ahn.status_refresh import FEED_KINDS  # noqa: E402

CONTRIB_DIR = ROOT / "contrib"
KINDS = ("official_path", "status_source")
LINE_KEYS = frozenset({"schema_version", "kind", "observed_at", "source_url", "payload", "credit"})
# Payload fields a contributor can send, taken from the bundle's own allowlist so the two cannot drift apart.
PAYLOAD_KEYS = {kind: bundle.PAYLOAD_KEYS[kind] for kind in KINDS}
URL_FIELDS = {"official_path": ("api_docs", "developer_portal"), "status_source": ("status_page_url", "api_url")}
TEXT_LIMITS = {"name": 80, "note": 300, "source_name": 80, "credit": 60}
# Extra names that never belong in a contribution, on top of the bundle's own forbidden list.
FORBIDDEN_KEYS = bundle.FORBIDDEN_KEYS | frozenset({"password", "passwd", "secret", "token", "access_token", "cookie", "cookies",
                                                    "authorization", "email", "session", "session_id", "credentials"})
MAX_FILE_BYTES = 256 * 1024
MAX_LINES = 200
MAX_URL = 300
MAX_STRING = 2000      # any single string in a line; checked before anything is scanned, so a huge value is cheap to refuse
MAX_DEPTH = 12
FILE_NAME = re.compile(r"^[a-z0-9][a-z0-9._-]{0,100}\.jsonl$")
TIME = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
# Every quantifier is bounded and a match cannot start inside a run of address characters, so a long string of
# "aaaa..." costs a few steps per character instead of a re-scan from every position.
_AT = r"(?:@|\uff20|\s?\(at\)\s?|\s?\[at\]\s?)"
_DOT = r"(?:\.|\uff0e|\s?\(dot\)\s?|\s?\[dot\]\s?)"
EMAIL = re.compile(rf"(?<![A-Za-z0-9._%+-])[A-Za-z0-9._%+-]{{1,64}}{_AT}[A-Za-z0-9-]{{1,63}}{_DOT}[A-Za-z]{{2,24}}")
SECRET_VALUES = (
    re.compile(r"(?i)\b(?:bearer\s+[a-z0-9._~+/=-]{12,}|sk-[a-z0-9_-]{16,}|ghp_[a-z0-9]{20,}|xox[abp]-[a-z0-9-]{10,})"),
    re.compile(r"\bAKIA[0-9A-Z]{16}"),
    re.compile(r"\b[sr]k_(?:live|test)_[A-Za-z0-9]{8,}"),                    # Stripe secret and restricted keys
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"\bglpat-[A-Za-z0-9_-]{16,}"),
    re.compile(r"\bAIza[0-9A-Za-z_-]{35}"),                                  # Google API key
    re.compile(r"\beyJ[\w-]{5,}\.eyJ[\w-]{5,}\.[\w-]{5,}"),                  # JWT
    re.compile(r"(?i)hooks\.slack\.com/services/[a-z0-9]+/[a-z0-9]+"),       # Slack webhook token in the path
)
# Names whose value in a query string or fragment is a credential, wherever the address is.
SECRET_PARAMS = frozenset({"key", "apikey", "api_key", "api-key", "token", "access_token", "auth", "authorization", "secret",
                           "client_secret", "password", "passwd", "pwd", "sig", "signature", "session", "sessionid", "sid",
                           "code", "jwt", "bearer", "x-amz-signature", "x-amz-credential"})
# Right-to-left and isolate controls reorder what a reviewer sees.
BIDI = re.compile(r"[\u202a-\u202e\u2066-\u2069]")


def _verifier():
    spec = importlib.util.spec_from_file_location("verify_official_paths", ROOT / "scripts" / "verify_official_paths.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


VERIFIER = _verifier()


SECRET_QUERY = "must not carry a key, token or session value in its query string or fragment"


def url_problem(value) -> str | None:
    """A plain https URL on a public hostname: no credentials or port. A query string is only a warning (url_warning)."""
    if not isinstance(value, str) or not value.startswith("https://") or any(ch.isspace() for ch in value):
        return "must be a plain https URL"
    if len(value) > MAX_URL:
        return f"is longer than {MAX_URL} characters"
    parts = urlsplit(value)
    try:
        if parts.port is not None or parts.username is not None or parts.password is not None:
            return "must not carry a port or credentials"
        valid_host(parts.hostname or "")
    except ValueError:
        return "must be on a public hostname"
    for part in (parts.query, parts.fragment):
        if "=" not in part:                       # "#auth" is an anchor, not a parameter
            continue
        names = {name.lower() for name, _ in parse_qsl(part, keep_blank_values=True)}
        if names & SECRET_PARAMS:
            return SECRET_QUERY
    return None


def url_warning(value) -> str | None:
    """Things a reviewer should look at but that do not make the address wrong: a query string, a fragment that is not an anchor."""
    parts = urlsplit(value)
    if parts.query or "?" in value:
        return "has a query string; a plain page address is better if the page works without it"
    if "=" in parts.fragment or "&" in parts.fragment:
        return "has a fragment that looks like parameters rather than a page anchor"
    return None


def _scan(value, where: str, errors: list[str], depth: int = 0) -> bool:
    """Forbidden names at any depth, and secret- or address-looking text anywhere. Returns True when a size or depth
    limit was hit: those are checked before any pattern runs, so nothing long or deep ever reaches one."""
    if depth > MAX_DEPTH:
        errors.append(f"{where}: nested more than {MAX_DEPTH} levels deep")
        return True
    over = False
    if isinstance(value, dict):
        for key, item in value.items():
            name = str(key)
            if len(name) > 100:
                errors.append(f"{where}: a field name is longer than 100 characters")
                over = True
                continue
            if name.lower() in FORBIDDEN_KEYS:
                errors.append(f"{where}: the field {key!r} is not accepted")
            if BIDI.search(name):
                errors.append(f"{where}: a field name contains a direction-control character")
            over = _scan(item, f"{where}.{name}", errors, depth + 1) or over
    elif isinstance(value, list):
        if len(value) > 50:
            errors.append(f"{where}: a list of {len(value)} items, over 50")
            return True
        for item in value:
            over = _scan(item, where, errors, depth + 1) or over
    elif isinstance(value, str):
        if len(value) > MAX_STRING:
            errors.append(f"{where}: a text of {len(value)} characters, over {MAX_STRING}")
            return True
        if any(EMAIL.search(text) for text in (value, unicodedata.normalize("NFKC", value))):
            errors.append(f"{where}: looks like it contains an email address; leave personal details out")
        if any(pattern.search(value) for pattern in SECRET_VALUES):
            errors.append(f"{where}: looks like it contains a key or token; remove it and rotate it if it was real")
        if any(ord(ch) < 32 for ch in value):
            errors.append(f"{where}: contains a control character")
        if BIDI.search(value):
            errors.append(f"{where}: contains a direction-control character")
    return over


def _urls(value, where: str, depth: int = 0) -> list[tuple[str, str]]:
    """Every https:// string inside a nested value, with where it sits (the access bundle has them at several depths)."""
    if depth > MAX_DEPTH + 1:
        return []
    if isinstance(value, str):
        return [(where, value)] if value.startswith("https://") else []
    found: list[tuple[str, str]] = []
    if isinstance(value, dict):
        for key, item in value.items():
            found += _urls(item, f"{where}.{key}", depth + 1)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            found += _urls(item, f"{where}[{index}]", depth + 1)
    return found


def _text(payload: dict, field: str, errors: list[str]) -> None:
    value = payload.get(field)
    if value is None:
        return
    if not isinstance(value, str) or not value or value != value.strip() or "\n" in value:
        errors.append(f"payload.{field}: must be a non-empty single-line string without outer spaces (or left out)")
    elif len(value) > TEXT_LIMITS[field]:
        errors.append(f"payload.{field}: is {len(value)} characters, over {TEXT_LIMITS[field]}")


def check_record(record, *, now: datetime | None = None) -> tuple[list[str], list[str]]:
    """(errors, warnings) for one parsed line."""
    errors: list[str] = []
    warnings: list[str] = []
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    if not isinstance(record, dict):
        return ["the line is not a JSON object"], warnings
    if _scan(record, "record", errors):      # first: size, depth, names and secret-looking text, before any other field is read
        return errors, warnings
    extra = set(record) - LINE_KEYS
    if extra:
        errors.append(f"fields outside the contribution format: {sorted(str(k)[:40] for k in extra)} (we fill in the other bundle envelope fields)")
    if record.get("schema_version") != 1:
        errors.append("schema_version must be 1")
    kind = record.get("kind")
    if kind not in KINDS:
        errors.append(f"kind must be one of {list(KINDS)}")
    observed = record.get("observed_at")
    if not isinstance(observed, str) or not TIME.fullmatch(observed):
        errors.append("observed_at must be a UTC time like 2026-10-31T09:30:00Z (when you looked at the source)")
    else:
        try:
            when = datetime.strptime(observed, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        except ValueError:
            errors.append("observed_at is not a real time")
        else:
            if when > now + timedelta(days=1):
                errors.append("observed_at is in the future")
    payload = record.get("payload")
    # A host with no public API has no page of its own to point at; the name and note then carry the finding.
    no_public_api = (kind == "official_path" and isinstance(payload, dict)
                     and not (payload.get("api_docs") or payload.get("developer_portal")))
    source_url = record.get("source_url")
    if source_url is None and no_public_api:
        pass
    else:
        problem = url_problem(source_url)
        if problem:
            errors.append(f"source_url {problem} (the public page where you confirmed it)")
        elif url_warning(source_url):
            warnings.append(f"source_url {url_warning(source_url)}")
    credit = record.get("credit")
    if credit is not None:
        if not isinstance(credit, str) or not credit.strip() or credit != credit.strip() or not credit.isprintable():
            errors.append("credit must be a short printable string (a name or handle) or left out")
        elif len(credit) > TEXT_LIMITS["credit"]:
            errors.append(f"credit is longer than {TEXT_LIMITS['credit']} characters")
    if not isinstance(payload, dict):
        errors.append("payload must be an object")
        return errors, warnings
    if kind not in KINDS:
        return errors, warnings
    extra = set(payload) - PAYLOAD_KEYS[kind]
    if extra:
        errors.append(f"payload has fields outside the {kind} format: {sorted(extra)}")
    host = payload.get("site_host")
    try:
        valid_host(host)
    except ValueError:
        errors.append("payload.site_host must be a lowercase public hostname such as example.com (no scheme, path or port)")
        host = None
    for field in TEXT_LIMITS:
        if field in PAYLOAD_KEYS[kind]:
            _text(payload, field, errors)
    for field in URL_FIELDS[kind]:
        if payload.get(field) is not None:
            problem = url_problem(payload[field])
            if problem:
                errors.append(f"payload.{field} {problem}")
                continue
            if url_warning(payload[field]):
                warnings.append(f"payload.{field} {url_warning(payload[field])}")
            if host and not VERIFIER.publisher_owned(host, payload[field]):
                warnings.append(f"payload.{field} is not on the host's own domain; the reviewer will look for the page that links it")
    if kind == "official_path":
        if not payload.get("name"):
            errors.append("payload.name is needed (the name of the developer documentation)")
        if no_public_api and not payload.get("note"):
            errors.append("payload needs api_docs or developer_portal; if the site has no public API, say so in note")
        if payload.get("access") is not None:
            for text in VERIFIER.access_problems(host or "invalid.test", payload["access"], today=now.date()):
                label = text.split(": ", 1)[-1]
                (warnings if "publisher-owned" in label else errors).append(f"payload.{label}")
            # The bundle rules do not look at query strings; the same two checks as every other address apply here.
            for where, url in _urls(payload["access"], "payload.access"):
                if SECRET_QUERY in (url_problem(url) or ""):
                    errors.append(f"{where} {SECRET_QUERY}")
                elif url_warning(url):
                    warnings.append(f"{where} {url_warning(url)}")
    else:
        if not payload.get("source_name"):
            errors.append("payload.source_name is needed (the name of the status page)")
        if payload.get("status_page_url") is None:
            errors.append("payload.status_page_url is needed")
        feed_kind, api_url = payload.get("feed_kind"), payload.get("api_url")
        if (feed_kind is None) != (api_url is None):
            errors.append("api_url and feed_kind go together; leave both out for a plain link to the status page")
        if feed_kind is not None and feed_kind not in FEED_KINDS:
            errors.append(f"payload.feed_kind must be one of {sorted(FEED_KINDS)}")
    return errors, warnings


def check_file(path: Path, *, now: datetime | None = None) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    if not FILE_NAME.fullmatch(path.name):
        errors.append(f"{path.name}: file names are lowercase letters, digits, dots, dashes and underscores, ending in .jsonl")
    if path.is_symlink():
        return errors + [f"{path.name}: is a symbolic link; send a regular file"], warnings
    try:
        size = path.stat().st_size
        if size > MAX_FILE_BYTES:
            return errors + [f"{path.name}: over {MAX_FILE_BYTES // 1024} KB; split it into more than one file"], warnings
        with path.open("rb") as handle:
            data = handle.read(MAX_FILE_BYTES + 1)     # never more than the limit, even if the file grew since stat
    except OSError as exc:
        return errors + [f"{path.name}: cannot be read ({exc.__class__.__name__})"], warnings
    if len(data) > MAX_FILE_BYTES:
        return errors + [f"{path.name}: over {MAX_FILE_BYTES // 1024} KB; split it into more than one file"], warnings
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return errors + [f"{path.name}: not UTF-8"], warnings
    if BIDI.search(text):
        errors.append(f"{path.name}: contains a direction-control character (U+202A-202E, U+2066-2069)")
    # Split on the newline JSON Lines uses, not str.splitlines(): U+2028 and friends are legal inside a JSON string.
    lines = [(number, line) for number, line in enumerate(text.split("\n"), 1) if line.strip()]
    if not lines:
        errors.append(f"{path.name}: no records")
    if len(lines) > MAX_LINES:
        errors.append(f"{path.name}: {len(lines)} records, over {MAX_LINES} per file; split it")
    for number, line in lines[:MAX_LINES]:
        try:
            record = json.loads(line)
        except (ValueError, RecursionError) as exc:
            errors.append(f"{path.name}:{number}: not valid JSON ({exc.__class__.__name__})")
            continue
        line_errors, line_warnings = check_record(record, now=now)
        errors += [f"{path.name}:{number}: {item}" for item in line_errors]
        warnings += [f"{path.name}:{number}: {item}" for item in line_warnings]
    return errors, warnings


def contrib_files(directory: Path = CONTRIB_DIR) -> list[Path]:
    return sorted(path for path in directory.glob("*.jsonl") if path.is_file())


def main(argv: list[str]) -> int:
    files = [Path(arg) for arg in argv] or contrib_files()
    failed = False
    for path in files:
        errors, warnings = check_file(path)
        for item in warnings:
            print(f"warning: {item}")
        for item in errors:
            print(f"error: {item}")
        failed = failed or bool(errors)
    print(f"checked {len(files)} file(s): {'problems found' if failed else 'ok'}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
