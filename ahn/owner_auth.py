"""Small read-only owner dashboard gate. Never put credentials in URLs."""

from __future__ import annotations

import base64
import binascii
import hmac
from ipaddress import ip_address


# Owner-only routes. They share the rate-limit exemption for the allowed network and stay out of traffic metrics.
OWNER_PATHS = frozenset({"/owner", "/owner/hosts"})


def valid_owner_ip(header: str, allowed_ips: tuple[str, ...]) -> bool:
    """Render's edge overwrites CF-Connecting-IP for public web requests."""
    if not allowed_ips:
        return False
    try:
        address = ip_address(header.strip()).compressed
    except ValueError:
        return False
    return address in allowed_ips


def valid_owner_authorization(header: str, password: str) -> bool:
    if not password or not header.startswith("Basic ") or len(header) > 512:
        return False
    try:
        decoded = base64.b64decode(header[6:], validate=True).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError):
        return False
    username, separator, submitted = decoded.partition(":")
    return bool(separator) and hmac.compare_digest(username.encode("utf-8"), b"owner") and hmac.compare_digest(submitted.encode("utf-8"), password.encode("utf-8"))


OWNER_HEADERS = {
    "Cache-Control": "private, no-store",
    "X-Frame-Options": "DENY",
    "X-Content-Type-Options": "nosniff",
    "X-Robots-Tag": "noindex, nofollow",
    "Referrer-Policy": "no-referrer",
}
