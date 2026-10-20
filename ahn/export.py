"""Export v1: stored facts as JSON Lines envelopes, one per line, for the operator.

``python -m ahn.cli export --kind access_profile|status|observation [--since ISO] [--out FILE]``

This is an operator tool, not a public endpoint. Reports follow the same rules as the public
answer: only published, non-synthetic, unwithdrawn observations, ``observed_at`` cut to the hour,
and no reporter identity. When ``AHN_EXPORT_SIGNING_KEY`` names an Ed25519 private key (PEM), each
envelope is signed over its RFC 8785 (JCS) canonical form without the ``signature`` member;
otherwise ``signature`` is null. Nothing here contacts the network.
"""

from __future__ import annotations

import base64
import hashlib
import json
import math
import os
import re
from datetime import datetime, timedelta, timezone

from .provenance import operator_id

SCHEMA_VERSION = 1
KINDS = ("access_profile", "status", "observation")
EXPIRES = {"access_profile": timedelta(hours=24), "status": timedelta(days=7), "observation": timedelta(days=7)}
_KEY_ID_RE = re.compile(r"^[a-z0-9](?:[a-z0-9.-]{0,62}[a-z0-9])?:\d{4}-(?:0[1-9]|1[0-2])$")


# --- RFC 8785 JSON Canonicalization Scheme -----------------------------------------------------

def _jcs_number(value: float | int) -> str:
    """ECMAScript Number serialization (RFC 8785 section 3.2.2.3)."""
    if isinstance(value, int) and abs(value) <= 2**53:
        return str(value)
    value = float(value)
    if not math.isfinite(value):
        raise ValueError("JCS cannot represent NaN or Infinity")
    if value == 0:
        return "0"
    sign = "-" if value < 0 else ""
    text = repr(abs(value))  # shortest round-trip digits, the same digits ECMAScript chooses
    mantissa, _, exponent = text.partition("e")
    whole, _, fraction = mantissa.partition(".")
    digits = (whole + fraction).lstrip("0")
    # value = 0.digits * 10**point
    point = len(whole) + int(exponent or 0) - (len(whole + fraction) - len((whole + fraction).lstrip("0")))
    digits = digits.rstrip("0") or "0"
    k = len(digits)
    if k <= point <= 21:
        out = digits + "0" * (point - k)
    elif 0 < point <= 21:
        out = digits[:point] + "." + digits[point:]
    elif -6 < point <= 0:
        out = "0." + "0" * (-point) + digits
    else:
        exp = point - 1
        out = (digits[0] + ("." + digits[1:] if k > 1 else "")) + "e" + ("+" if exp >= 0 else "-") + str(abs(exp))
    return sign + out


_ESCAPES = {'"': '\\"', "\\": "\\\\", "\b": "\\b", "\f": "\\f", "\n": "\\n", "\r": "\\r", "\t": "\\t"}


def _jcs_string(value: str) -> str:
    out = []
    for char in value:
        if char in _ESCAPES:
            out.append(_ESCAPES[char])
        elif ord(char) < 0x20:
            out.append(f"\\u{ord(char):04x}")
        else:
            out.append(char)
    return '"' + "".join(out) + '"'


def _utf16_key(text: str) -> list[int]:
    data = text.encode("utf-16-be", "surrogatepass")
    return [int.from_bytes(data[index:index + 2], "big") for index in range(0, len(data), 2)]


def canonicalize(value) -> str:
    """RFC 8785 canonical JSON text of a JSON-compatible value (dict keys must be strings)."""
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, (int, float)):
        return _jcs_number(value)
    if isinstance(value, str):
        return _jcs_string(value)
    if isinstance(value, (list, tuple)):
        return "[" + ",".join(canonicalize(item) for item in value) + "]"
    if isinstance(value, dict):
        if not all(isinstance(key, str) for key in value):
            raise TypeError("JCS object keys must be strings")
        items = sorted(value.items(), key=lambda item: _utf16_key(item[0]))
        return "{" + ",".join(_jcs_string(key) + ":" + canonicalize(item) for key, item in items) + "}"
    raise TypeError(f"not a JSON value: {type(value).__name__}")


def canonical_bytes(value) -> bytes:
    return canonicalize(value).encode("utf-8")


# --- Envelopes ----------------------------------------------------------------------------------

def iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def hour(value: datetime) -> datetime:
    return value.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)


def envelope(kind: str, *, site_host: str, observed_at: datetime, received_at: datetime | None, payload: dict,
             origin_operator: str | None = None, vantage: dict | None = None, reader_version: str | None = None,
             source_url: str | None = None, content_sha256: str | None = None, record_suffix: str | None = None) -> dict:
    """One export record (schema_version 1) with signature still null."""
    if kind not in KINDS:
        raise ValueError("unknown export kind")
    operator = origin_operator or operator_id()
    observed = iso(observed_at)
    record_id = f"ahn:{operator}:{kind}:{site_host}:{observed}" + (f":{record_suffix}" if record_suffix else "")
    return {
        "schema_version": SCHEMA_VERSION, "kind": kind, "record_id": record_id,
        "origin_operator": operator, "vantage": vantage, "reader_version": reader_version,
        "observed_at": observed, "received_at": iso(received_at),
        "expires_at": iso(observed_at + EXPIRES[kind]),
        "source_url": source_url, "content_sha256": content_sha256,
        "payload": payload, "signature": None,
    }


def access_profile_envelope(row: dict) -> dict:
    read_host = row["read_host"] or row["site_host"]
    return envelope(
        "access_profile", site_host=row["site_host"], observed_at=row["fetched_at"], received_at=row["fetched_at"],
        origin_operator=row.get("origin_operator"), vantage=row.get("vantage"), reader_version=row.get("reader_version"),
        source_url=f"https://{read_host}/robots.txt", content_sha256=row.get("robots_sha256"),
        payload={
            "site_host": row["site_host"], "read_host": read_host,
            "robots_status": row["robots_status"], "robots": row["robots"], "robots_detail": row["robots_detail"],
            "llms_txt_status": row["llms_txt_status"], "llms_detail": row["llms_detail"],
            "llms_txt_url": f"https://{read_host}/llms.txt", "llms_sha256": row.get("llms_sha256"),
        },
    )


def status_envelope(row: dict) -> dict:
    return envelope(
        "status", site_host=row["site_host"], observed_at=row["fetched_at"], received_at=row["fetched_at"],
        origin_operator=row.get("origin_operator"), vantage=row.get("vantage"), reader_version=row.get("reader_version"),
        source_url=row["api_url"], content_sha256=row.get("body_sha256"),
        payload={
            "site_host": row["site_host"], "source_name": row["source_name"], "status_page_url": row["status_page_url"],
            "indicator": row["indicator"], "source_updated_at": iso(row["source_updated_at"]),
            "curated_by": row.get("curated_by"), "evidence": row.get("evidence"), "evidence_url": row.get("evidence_url"),
        },
    )


def observation_envelope(row: dict) -> dict:
    """The public fields of one published report, times cut to the hour, no reporter identity.

    Several reports for one host can share an hour, so the record id ends with a short digest of
    the operator's own observation id (the id itself is never exported).
    """
    operator = row.get("origin_operator") or operator_id()
    own_id = str(row.get("origin_observation_id") or row["id"])
    suffix = hashlib.sha256(f"{operator}:{own_id}".encode()).hexdigest()[:16]
    observed = hour(row["observed_at"])
    return envelope(
        "observation", site_host=row["site_host"], observed_at=observed, received_at=hour(row["received_at"]),
        origin_operator=operator, record_suffix=suffix,
        payload={
            "site_host": row["site_host"], "observed_at": iso(observed), "task_step": row["task_step"],
            "error_class": row["error_class"], "attempt_actor": row["attempt_actor"], "attempt_env": row["attempt_env"],
            "outcome": row["outcome"], "received_channel": row.get("received_channel"),
        },
    )


# --- Signing ------------------------------------------------------------------------------------

def load_signing_key(path: str):
    """An Ed25519 private key from a PEM file (PKCS#8, unencrypted)."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    with open(path, "rb") as handle:
        key = serialization.load_pem_private_key(handle.read(), password=None)
    if not isinstance(key, Ed25519PrivateKey):
        raise ValueError("AHN_EXPORT_SIGNING_KEY must be an Ed25519 private key")
    return key


def key_id(now: datetime | None = None) -> str:
    """AHN_EXPORT_KEY_ID when it has the form <operator>:<YYYY-MM>, else <operator>:<current UTC month>."""
    configured = os.environ.get("AHN_EXPORT_KEY_ID", "").strip()
    if _KEY_ID_RE.fullmatch(configured):
        return configured
    return f"{operator_id()}:{(now or datetime.now(timezone.utc)).strftime('%Y-%m')}"


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def signing_input(record: dict) -> bytes:
    """What is signed: the JCS form of the envelope without its signature member."""
    return canonical_bytes({key: value for key, value in record.items() if key != "signature"})


def sign(record: dict, private_key, kid: str) -> dict:
    signature = private_key.sign(signing_input(record))
    return {**record, "signature": {"alg": "ed25519", "key_id": kid, "sig": _b64url(signature)}}


def verify(record: dict, public_key) -> bool:
    """True when the record's Ed25519 signature matches its content."""
    sig = (record.get("signature") or {}).get("sig")
    if not isinstance(sig, str):
        return False
    try:
        public_key.verify(base64.urlsafe_b64decode(sig + "=" * (-len(sig) % 4)), signing_input(record))
    except Exception:
        return False
    return True


# --- Database rows ------------------------------------------------------------------------------

def _rows(conn, kind: str, since: datetime | None):
    since = since or datetime(1970, 1, 1, tzinfo=timezone.utc)
    if kind == "access_profile":
        return conn.execute(
            """SELECT site_host,fetched_at,robots_status,robots,robots_detail,llms_txt_status,llms_detail,read_host,
                      origin_operator,vantage,reader_version,robots_sha256,llms_sha256
               FROM host_access_profile WHERE fetched_at IS NOT NULL AND fetched_at >= %s ORDER BY fetched_at,site_host""",
            (since,),
        )
    if kind == "status":
        return conn.execute(
            """SELECT s.site_host,s.source_name,s.status_page_url,s.api_url,s.curated_by,s.evidence,s.evidence_url,
                      c.indicator,c.source_updated_at,c.fetched_at,c.origin_operator,c.vantage,c.reader_version,c.body_sha256
               FROM status_cache c JOIN status_sources s ON s.site_host=c.site_host
               WHERE s.enabled=true AND c.fetched_at IS NOT NULL AND c.fetched_at >= %s ORDER BY c.fetched_at,c.site_host""",
            (since,),
        )
    # The public answer's rules: published, real, not withdrawn, on a published incident.
    return conn.execute(
        """SELECT o.id,o.observed_at,o.received_at,o.attempt_actor,o.attempt_env,o.outcome,o.origin_operator,
                  o.origin_observation_id,o.received_channel,i.site_host,i.task_step,i.error_class
           FROM observations o JOIN incidents i ON i.id=o.incident_id
           WHERE o.review_state='published' AND o.synthetic=false AND o.withdrawn_at IS NULL
             AND i.visibility='published' AND o.observed_at >= %s
           ORDER BY o.observed_at,o.id""",
        (since,),
    )


BUILDERS = {"access_profile": access_profile_envelope, "status": status_envelope, "observation": observation_envelope}


def export(settings, kind: str, out, *, since: datetime | None = None, signing_key=None, kid: str | None = None) -> int:
    """Write one JSON line per record of ``kind`` to the text stream ``out``; returns the count."""
    from .db import connect

    if kind not in KINDS:
        raise ValueError("unknown export kind")
    count = 0
    with connect(settings) as conn:
        for row in _rows(conn, kind, since):
            record = BUILDERS[kind](row)
            if signing_key is not None:
                record = sign(record, signing_key, kid or key_id())
            out.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
            count += 1
    return count
