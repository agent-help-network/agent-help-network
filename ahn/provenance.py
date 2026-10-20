"""Who read a stored fact, from where, and with which reader.

Every robots.txt/llms.txt read, status-feed read and report row carries the operator that made or
received it (``AHN_OPERATOR_ID``), the declared vantage point of the reads (``AHN_VANTAGE``, JSON)
and the reader version. Nothing here touches the network or the database.
"""

from __future__ import annotations

import hashlib
import json
import os
import re

DEFAULT_OPERATOR_ID = "agenthelpnetwork.com"
DEFAULT_VANTAGE = {"kind": "datacenter", "region": "US", "asn": None, "declared": True}
_OPERATOR_RE = re.compile(r"^[a-z0-9](?:[a-z0-9.-]{0,62}[a-z0-9])?$")
_VANTAGE_KINDS = {"datacenter", "residential", "mobile", "unknown"}


def operator_id() -> str:
    """The operator's public identifier: a lowercase hostname-like name, else the default."""
    value = os.environ.get("AHN_OPERATOR_ID", "").strip().lower()
    return value if _OPERATOR_RE.fullmatch(value) else DEFAULT_OPERATOR_ID


def vantage() -> dict:
    """The declared vantage point of this service's reads; an invalid AHN_VANTAGE falls back to the default."""
    raw = os.environ.get("AHN_VANTAGE", "").strip()
    if not raw:
        return dict(DEFAULT_VANTAGE)
    try:
        value = json.loads(raw)
    except ValueError:
        return dict(DEFAULT_VANTAGE)
    if not isinstance(value, dict) or value.get("kind") not in _VANTAGE_KINDS:
        return dict(DEFAULT_VANTAGE)
    region = value.get("region")
    asn = value.get("asn")
    return {
        "kind": value["kind"],
        "region": region if isinstance(region, str) and re.fullmatch(r"[A-Z]{2}(?:-[A-Z0-9]{1,3})?", region) else None,
        "asn": asn if isinstance(asn, int) and not isinstance(asn, bool) and 0 < asn < 2**32 else None,
        "declared": True,
    }


def sha256_hex(body: bytes | None) -> str | None:
    """SHA-256 of the bytes as received, or None when no body was kept (absent, over the cap, failed)."""
    return hashlib.sha256(body).hexdigest() if isinstance(body, (bytes, bytearray)) else None
