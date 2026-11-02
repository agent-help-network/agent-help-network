"""Compare your regular sites with the offline bundle's thin shards on your machine.

``local_digest(hosts)`` works out each host's shard id (the first byte of the SHA-256 of the lowercase hostname, two hex
digits), downloads those shards plus random padding shards up to a fixed count, checks every shard against the
manifest's SHA-256 (and the manifest's Ed25519 signature when it has one and you give the key), and answers per host
from the verified shards. The network sees only GETs of ``/v1/bundle/shards/<xx>.json``, the manifest and the changes
file: no hostname, and the same number of shards whichever hosts you ask about.
"""

from __future__ import annotations

import gzip
import hashlib
import io
import json
import os
import random
import re
import tempfile
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Dict, Iterable, List, Optional, Union

from . import _client
from ._ed25519 import b64url_decode, verify_envelope
from ._hosts import normalize_host, skip_host

__all__ = ["Digest", "local_digest", "digest_markdown", "shard_id"]

BASE_URL = "https://agenthelpnetwork.com"
SHARD_COUNT = 256
DEFAULT_PAD_TO = 16
CHANGE_DAYS = 7
MAX_FILE_BYTES = 4_194_304
_SHARD_ID = re.compile(r"[0-9a-f]{2}")
SHARD_SCHEMA, MANIFEST_SCHEMA, CHANGES_SCHEMA = "ahn-shard/1", "ahn-shards/1", "ahn-changes/1"
# Shard section -> field of a host record ("status" would clash with the record's own status).
SECTIONS = {"access": "access", "official": "official", "status": "provider_status"}
LIVE_CHECK = "Not in the bundle: use a live check (ahn.check, or the check_incident tool) if a step fails."


def shard_id(host: str) -> Optional[str]:
    """The shard id of ``host`` (as ``local_digest`` normalizes it), or None when it is not a hostname."""
    name = normalize_host(host)
    return None if name is None else hashlib.sha256(name.encode("utf-8")).digest()[:1].hex()


@dataclass
class Digest:
    """The answer of ``local_digest``. ``hosts`` has one record per input host, in input order."""

    hosts: List[dict] = field(default_factory=list)
    signature: str = "absent"  # "verified" | "absent" (hash-checked only) | "unchecked" (signed, no key given) | "invalid"
    generated_at: Optional[str] = None
    valid_until: Optional[str] = None
    stale: bool = False
    changes_covers_since: Optional[str] = None
    shards_requested: int = 0
    notes: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"hosts": self.hosts, "signature": self.signature, "generated_at": self.generated_at, "valid_until": self.valid_until,
                "stale": self.stale, "changes_covers_since": self.changes_covers_since,
                "shards_requested": self.shards_requested, "notes": self.notes}


class _PaddingUnreadable(Exception):
    """The stored padding set exists but cannot be read."""


class _NoFile(Exception):
    """A file could not be obtained or did not match its checksum."""


def _read_limited(response, gzipped: bool) -> bytes:
    body = response.read(MAX_FILE_BYTES + 1)
    if gzipped:
        body = gzip.GzipFile(fileobj=io.BytesIO(body)).read(MAX_FILE_BYTES + 1)
    if len(body) > MAX_FILE_BYTES:
        raise _NoFile("too_large")
    return body


class _Fetcher:
    """GET with ETag caching in ``cache_dir`` (optional). Only paths under the fixed base URL are requested."""

    def __init__(self, base_url: str, cache_dir, timeout: float):
        self.base, self.timeout = base_url.rstrip("/"), timeout
        self.cache_dir = os.fspath(cache_dir) if cache_dir is not None else None
        if self.cache_dir:
            try:
                os.makedirs(self.cache_dir, exist_ok=True)
            except OSError:
                self.cache_dir = None

    def _paths(self, name: str):
        return os.path.join(self.cache_dir, name + ".json"), os.path.join(self.cache_dir, name + ".etag")

    def _cached(self, name: str):
        if not self.cache_dir:
            return None, None
        body_path, etag_path = self._paths(name)
        try:
            with open(body_path, "rb") as body, open(etag_path, "r", encoding="ascii") as etag:
                return body.read(), etag.read().strip()
        except (OSError, ValueError):
            return None, None

    def _store(self, name: str, body: bytes, etag: Optional[str]) -> None:
        if not (self.cache_dir and etag):
            return
        body_path, etag_path = self._paths(name)
        try:
            with open(body_path, "wb") as out:
                out.write(body)
            with open(etag_path, "w", encoding="ascii") as out:
                out.write(etag)
        except (OSError, UnicodeError):
            pass

    # The padding file is read, extended and written inside one lock (``padding.lock``, created with O_EXCL), so two
    # processes can never each extend the same stored set in different ways and leave only one of the two behind.
    # Nothing slow (no network call) happens while the lock is held.
    LOCK_WAIT = 10.0   # seconds to wait for another process before giving up (nothing is requested then)
    LOCK_STALE = 60.0  # a lock file older than this was left by a process that died; it is removed

    def _lock(self, path: str) -> None:
        deadline = time.monotonic() + self.LOCK_WAIT
        while True:
            try:
                os.close(os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY))
                return
            except (FileExistsError, PermissionError):  # Windows: PermissionError while the holder deletes it
                try:
                    if time.time() - os.stat(path).st_mtime > self.LOCK_STALE:
                        os.unlink(path)
                        continue
                except OSError:
                    pass  # it vanished or is being removed right now: try again after the check below
                if time.monotonic() >= deadline:
                    raise _PaddingUnreadable()
                time.sleep(0.01)
            except OSError:
                raise _PaddingUnreadable()  # the folder cannot be written: the set could not be kept, so nothing is asked

    def _clean_temp(self) -> None:
        try:
            for name in os.listdir(self.cache_dir):
                if name.startswith(".padding-"):
                    full = os.path.join(self.cache_dir, name)
                    try:
                        if time.time() - os.stat(full).st_mtime > self.LOCK_STALE:
                            os.unlink(full)
                    except OSError:
                        pass
        except OSError:
            pass

    @staticmethod
    def _read_padding(path: str) -> List[str]:
        """The stored padding ids ([] before the first run). Raises _PaddingUnreadable."""
        try:
            with open(path, "r", encoding="ascii") as handle:
                loaded = json.load(handle).get("padding")
            if not isinstance(loaded, list) or not all(isinstance(item, str) and _SHARD_ID.fullmatch(item) for item in loaded):
                raise ValueError("padding")
            return list(dict.fromkeys(loaded))
        except FileNotFoundError:
            return []  # the first run: nothing was chosen yet
        except (OSError, ValueError, TypeError, AttributeError, UnicodeError):
            # The file is there but cannot be read. Drawing new padding would let the service tell yours from the
            # padding by comparing this request with the last one, so nothing is asked for.
            raise _PaddingUnreadable()

    def _write_padding(self, path: str, keep: List[str]) -> None:
        temp = None
        try:  # written whole and renamed in, so a reader never sees half a file
            handle, temp = tempfile.mkstemp(dir=self.cache_dir, prefix=".padding-")
            with os.fdopen(handle, "w", encoding="ascii") as out:
                json.dump({"schema": "ahn-padding/1", "padding": keep}, out)
            os.replace(temp, path)
            temp = None
        except (OSError, UnicodeError):
            raise _PaddingUnreadable()  # the set was not stored, so it must not be used
        finally:
            if temp:
                try:
                    os.unlink(temp)
                except OSError:
                    pass

    def padding(self, wanted: set, others: List[str], count: int, picker) -> List[str]:
        """``count`` padding shard ids outside ``wanted``. With a cache directory the padding set is stored there and
        reused, so repeated calls ask for the same shards; it grows (with new random ids) only when more are needed,
        and every set a process uses is in the file. Without one every call draws a new set."""
        if not self.cache_dir:
            pool = [item for item in others if item not in wanted]
            return picker.sample(pool, min(count, len(pool)))
        path = os.path.join(self.cache_dir, "padding.json")
        lock = os.path.join(self.cache_dir, "padding.lock")
        self._lock(lock)
        try:
            self._clean_temp()
            stored = self._read_padding(path)
            chosen = [item for item in stored if item not in wanted][:count]
            if len(chosen) < count:
                pool = [item for item in others if item not in chosen and item not in stored]
                chosen += picker.sample(pool, min(count - len(chosen), len(pool)))
                self._write_padding(path, stored + [item for item in chosen if item not in stored])
            return chosen
        finally:
            try:
                os.unlink(lock)
            except OSError:
                pass

    def get(self, path: str, name: str) -> bytes:
        """The decoded body of ``path``; a 304 answers from the cache. Raises _NoFile."""
        cached, etag = self._cached(name)
        headers = {"User-Agent": _client.user_agent("digest"), "Accept": "application/json", "Accept-Encoding": "gzip"}
        if cached is not None and etag:
            headers["If-None-Match"] = etag
        request = urllib.request.Request(self.base + path, headers=headers)
        try:
            with _client._urlopen(request, self.timeout) as response:
                status = getattr(response, "status", 200)
                if status == 304 and cached is not None:
                    return cached
                if status != 200:
                    raise _NoFile("http_%s" % status)
                body = _read_limited(response, "gzip" in (response.headers.get("Content-Encoding") or "").lower())
                self._store(name, body, response.headers.get("ETag"))
                return body
        except urllib.error.HTTPError as error:
            if error.code == 304 and cached is not None:
                return cached
            raise _NoFile("http_%s" % error.code)
        except _NoFile:
            raise
        except Exception:
            raise _NoFile("unreachable")


def _json(body: bytes, schema: str) -> dict:
    try:
        document = json.loads(body.decode("utf-8"))
    except (UnicodeError, ValueError):
        raise _NoFile("unreadable")
    if not isinstance(document, dict) or document.get("schema") != schema:
        raise _NoFile("unexpected_schema")
    return document


def _public_key(value) -> Optional[bytes]:
    if value is None:
        return None
    if isinstance(value, (bytes, bytearray)):
        return bytes(value)
    try:
        return b64url_decode(value.strip())
    except Exception:
        return b""


def _parse_time(value) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
    except (AttributeError, ValueError):
        return None


def local_digest(hosts: Iterable[str], *, pad_to: int = DEFAULT_PAD_TO, cache_dir=None, public_key=None,
                 require_signature: bool = False, changes: bool = True, timeout: float = 10.0,
                 base_url: str = BASE_URL, rng: Optional[random.Random] = None,
                 now: Optional[datetime] = None) -> Digest:
    """Answer for each of ``hosts`` from the thin bundle shards; never raises for a network or content problem.

    - ``pad_to``: how many shards are downloaded in all (1 to 256). The shards of your hosts are topped up with random
      ones, so the server cannot tell which hosts you hold. With ``cache_dir`` the padding set is kept there and reused
      (it changes only when more padding is needed), and every shard asked for, yours or padding, is requested the same
      way (``If-None-Match`` when a copy is cached). If the stored padding file exists but cannot be read, nothing is
      requested (note ``padding_unreadable``): delete ``padding.json`` to start a new set. Without ``cache_dir`` each call draws new padding, and over many
      calls the shards that always come back are yours: use ``cache_dir`` for regular use. With more distinct shards than ``pad_to`` all of yours are
      fetched and a note says so.
    - ``cache_dir``: keep downloads there and ask again with ``If-None-Match``; a cached file is checked against the
      manifest like a new one.
    - ``public_key``: the operator's Ed25519 key (raw 32 bytes or base64url) to verify the manifest's signature. A
      manifest without a signature is hash-checked only (``signature == "absent"``) when no key is given; when a key is
      given, an unsigned manifest is refused (note ``signature_missing``, hosts ``unverified``). ``require_signature=True`` refuses
      every shard unless the signature is verified.
    - ``changes``: also read the seven-day changes file and mark your hosts that changed in that window.

    A shard that does not match the manifest is not used (its hosts get ``status == "unverified"``). Hosts that are not
    in the bundle get ``status == "not_in_bundle"`` and a note to use a live check; private, local and malformed names
    are not looked up.
    """
    if not isinstance(pad_to, int) or isinstance(pad_to, bool) or not 1 <= pad_to <= SHARD_COUNT:
        raise ValueError("pad_to must be an integer from 1 to %d" % SHARD_COUNT)
    moment = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    result = Digest()
    extra_skip = os.environ.get("AHN_CLIENT_SKIP_HOSTS", "")
    disabled = os.environ.get("AHN_CLIENT_DISABLE") == "1"
    records: List[dict] = []
    wanted: Dict[str, List[dict]] = {}
    for item in hosts:
        name = normalize_host(item)
        if name is None:
            records.append({"host": None, "input": str(item)[:80], "status": "invalid"})
        elif skip_host(name, extra_skip) or disabled:
            records.append({"host": name, "status": "skipped"})
        else:
            record = {"host": name, "shard": shard_id(name), "status": "unavailable", "advice": None}
            records.append(record)
            wanted.setdefault(record["shard"], []).append(record)
    result.hosts = records
    if not wanted:
        return result

    picker = rng or random.SystemRandom()
    others = [format(index, "02x") for index in range(SHARD_COUNT) if format(index, "02x") not in wanted]
    fill = max(0, pad_to - len(wanted))
    if len(wanted) > pad_to:
        result.notes.append("more_shards_than_pad_to")
    fetcher = _Fetcher(base_url, cache_dir, timeout)
    try:
        plan = sorted(list(wanted) + fetcher.padding(set(wanted), others, fill, picker))
    except _PaddingUnreadable:
        result.notes.append("padding_unreadable")
        return result
    result.shards_requested = len(plan)

    try:
        manifest_body = fetcher.get("/v1/bundle/shards/manifest.json", "manifest")
        manifest = _json(manifest_body, MANIFEST_SCHEMA)
        entries = {entry["id"]: entry for entry in manifest["shards"] if isinstance(entry, dict) and isinstance(entry.get("id"), str)}
    except (_NoFile, KeyError, TypeError):
        result.notes.append("manifest_unavailable")
        return result

    key = _public_key(public_key)
    if manifest.get("signature") is None:
        result.signature = "absent"
    elif key is None:
        result.signature = "unchecked"
    else:
        result.signature = "verified" if verify_envelope(manifest, key) else "invalid"
    if key is not None and manifest.get("signature") is None:
        # A key was given, so an unsigned manifest is refused too (otherwise stripping the signature defeats the check).
        result.notes.append("signature_missing")
        for record in records:
            if record.get("status") == "unavailable":
                record["status"] = "unverified"
        return result
    if result.signature == "invalid" or (require_signature and result.signature != "verified"):
        result.notes.append("signature_" + ("invalid" if result.signature == "invalid" else "not_verified"))
        for record in records:
            if record.get("status") == "unavailable":
                record["status"] = "unverified"
        return result
    result.generated_at, result.valid_until = manifest.get("generated_at"), manifest.get("valid_until")
    expires = _parse_time(result.valid_until)
    result.stale = bool(expires and moment > expires)

    for sid in plan:
        try:
            body = fetcher.get("/v1/bundle/shards/%s.json" % sid, "shard-" + sid)
            entry = entries.get(sid)
            if entry is None or hashlib.sha256(body).hexdigest() != entry.get("sha256") or len(body) != entry.get("bytes"):
                raise _NoFile("checksum")
            shard = _json(body, SHARD_SCHEMA)
            if shard.get("shard") != sid or not isinstance(shard.get("hosts"), dict):
                raise _NoFile("wrong_shard")
        except _NoFile as error:
            if sid in wanted:
                status = "unverified" if str(error) in ("checksum", "wrong_shard", "unexpected_schema", "unreadable") else "unavailable"
                for record in wanted[sid]:
                    record["status"] = status
            continue
        for record in wanted.get(sid, []):
            sections = shard["hosts"].get(record["host"])
            if isinstance(sections, dict) and sections:
                record["status"] = "in_bundle"
                for section, field_name in SECTIONS.items():
                    if isinstance(sections.get(section), dict):
                        record[field_name] = sections[section]
            else:
                record["status"] = "not_in_bundle"
                record["advice"] = LIVE_CHECK

    if changes and isinstance(manifest.get("changes"), dict):
        _mark_changes(fetcher, manifest["changes"], records, result, moment)
    return result


def _mark_changes(fetcher: _Fetcher, described: dict, records: List[dict], result: Digest, moment: datetime) -> None:
    try:
        body = fetcher.get("/v1/bundle/changes.json", "changes")
        if hashlib.sha256(body).hexdigest() != described.get("sha256"):
            raise _NoFile("checksum")
        document = _json(body, CHANGES_SCHEMA)
        listed = document.get("changes")
        if listed is not None and not isinstance(listed, list):
            raise _NoFile("unreadable")
        rows = [row for row in listed or [] if isinstance(row, dict)]
    except _NoFile:
        result.notes.append("changes_unavailable")
        return
    result.changes_covers_since = document.get("covers_since")
    earliest = (moment - timedelta(days=CHANGE_DAYS)).date().isoformat()
    for record in records:
        if record.get("status") not in ("in_bundle", "not_in_bundle"):
            continue
        record["recent_changes"] = [
            {"section": row.get("section"), "changed_on": row.get("changed_on"), "removed": bool(row.get("removed"))}
            for row in rows if row.get("host") == record["host"] and isinstance(row.get("changed_on"), str) and row["changed_on"] >= earliest]


# --- Markdown card -------------------------------------------------------------------------------------------------

_TOKEN = re.compile(r"[a-z][a-z0-9_]{0,23}")
_AGENT = re.compile(r"[A-Za-z][A-Za-z0-9-]{1,31}")
_URL = re.compile(r"https://[A-Za-z0-9.-]{1,253}(?:[/?#][A-Za-z0-9._~!$&'()*+,;=:@%/?#-]{0,200})?")
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
_STAMP = re.compile(r"\d{4}-\d{2}-\d{2}T[0-9:.]{5,16}Z")
_NAME_STRIP = re.compile(r"[^\w .&+'-]")


def _token(value) -> str:
    return value if isinstance(value, str) and _TOKEN.fullmatch(value) else "unknown"


def _url(value) -> Optional[str]:
    return value if isinstance(value, str) and len(value) <= 240 and _URL.fullmatch(value) else None


def _name(value) -> str:
    """A curated product or provider name: letters, digits and a few marks, at most 5 words and 40 characters, else none."""
    words = _NAME_STRIP.sub("", value).split() if isinstance(value, str) else []
    name = " ".join(words)
    return name if 0 < len(words) <= 5 and len(name) <= 40 else ""


def _access_line(access: dict) -> str:
    parts = ["robots " + _token(access.get("robots")), "llms.txt " + _token(access.get("llms_txt"))]
    if isinstance(access.get("root_allowed"), bool):
        parts.append("root allowed" if access["root_allowed"] else "root disallowed")
    delay = access.get("crawl_delay")
    if isinstance(delay, (int, float)) and not isinstance(delay, bool) and 0 < delay <= 3600:
        parts.append("crawl-delay %gs" % delay)
    blocked = sorted(token for token in access.get("agents_blocked") or [] if isinstance(token, str) and _AGENT.fullmatch(token))
    if blocked:
        parts.append("agents blocked: " + ", ".join(blocked[:12]))
    signals = access.get("content_signals")
    if isinstance(signals, dict):
        shown = ["%s=%s" % (key, _token(signals[key])) for key in ("search", "ai_input", "ai_train") if key in signals]
        if shown:
            parts.append("content signals " + " ".join(shown))
    if access.get("ai_input_declared_no") is True:
        parts.append("declares no AI input")
    if _DATE.fullmatch(str(access.get("read_on") or "")):
        parts.append("read " + access["read_on"])
    return "; ".join(parts)


def _links(record: dict, labels) -> str:
    return "; ".join("%s `%s`" % (label, link) for key, label in labels if (link := _url(record.get(key))))


def digest_markdown(result: Union[Digest, dict]) -> str:
    """A Markdown card of a ``local_digest`` result for your own notes.

    Only fixed words, host names, short enum words, https URLs and curated names (cut to 60 characters, markup characters
    removed) are written. No other text from a site or from the files is copied, so a card is safe to keep in a memory file.
    """
    data = result.to_dict() if isinstance(result, Digest) else result
    lines = ["# Regular sites: offline bundle check", ""]
    meta = ["signature: " + {"verified": "verified", "absent": "absent (hash-checked only)", "unchecked": "present, not checked (no key)",
                             "invalid": "INVALID, shards not used"}.get(data.get("signature"), "unknown")]
    if isinstance(data.get("generated_at"), str) and _STAMP.fullmatch(data["generated_at"]):
        meta.append("bundle built " + data["generated_at"])
    if data.get("stale") is True:
        meta.append("OLDER THAN ITS valid_until: refresh")
    lines.append(" · ".join(meta))
    since = str(data.get("changes_covers_since") or "")[:10]
    if _DATE.fullmatch(since):
        lines.append("Changes are known since %s." % since)
    lines.append("")
    for record in data.get("hosts") or []:
        host, status = record.get("host"), record.get("status")
        if not isinstance(host, str):
            lines += ["## (not a hostname)", "- skipped", ""]
            continue
        lines.append("## " + host)
        if status == "in_bundle":
            if isinstance(record.get("access"), dict):
                lines.append("- access: " + _access_line(record["access"]))
            official = record.get("official")
            if isinstance(official, dict):
                label = _name(official.get("name"))
                links = _links(official, (("api_docs", "API docs"), ("developer_portal", "portal"), ("key_signup_url", "key signup"),
                                          ("openapi_url", "OpenAPI"), ("rate_limits_url", "rate limits"), ("terms_url", "terms")))
                text = "; ".join(part for part in (label, links) if part)
                if text:
                    lines.append("- official path: " + text)
            status_source = record.get("provider_status")
            if isinstance(status_source, dict):
                label = _name(status_source.get("name"))
                links = _links(status_source, (("page", "page"), ("api", "api")))
                text = "; ".join(part for part in (label, links) if part)
                if text:
                    lines.append("- provider status: " + text)
        elif status == "not_in_bundle":
            lines.append("- not in the bundle: use a live check (`ahn.check` or the `check_incident` tool) if a step fails")
        elif status == "unverified":
            lines.append("- shard failed verification and was not used")
        elif status == "skipped":
            lines.append("- private or local name: not looked up")
        else:
            lines.append("- shard unavailable: try again later")
        changes = [row for row in record.get("recent_changes") or [] if isinstance(row, dict)]
        if changes:
            lines.append("- changed in the last 7 days: " + ", ".join(
                "%s%s (%s)" % (_token(row.get("section")), " removed" if row.get("removed") else "",
                               row["changed_on"] if _DATE.fullmatch(str(row.get("changed_on"))) else "?") for row in changes[:6]))
        lines.append("")
    return "\n".join(line for index, line in enumerate(lines) if line or index == 0 or lines[index - 1]).rstrip() + "\n"
