"""Ed25519 signature check (RFC 8032) in plain Python, so the client keeps its no-dependency promise.

Only verification, only for the few signatures a manifest carries; it is not constant time and never handles a secret.
``canonical_json`` is the RFC 8785 (JCS) form the service signs, for the values a manifest holds (strings, integers,
booleans, null, lists and objects).
"""

from __future__ import annotations

import base64
import hashlib
import json
from typing import Optional, Tuple

_P = 2 ** 255 - 19
_L = 2 ** 252 + 27742317777372353535851937790883648493
_D = -121665 * pow(121666, _P - 2, _P) % _P
_I = pow(2, (_P - 1) // 4, _P)
Point = Tuple[int, int]


def _inv(value: int) -> int:
    return pow(value, _P - 2, _P)


def _recover_x(y: int, sign: int) -> Optional[int]:
    if y >= _P:
        return None
    x2 = (y * y - 1) * _inv(_D * y * y + 1) % _P
    if x2 == 0:
        return None if sign else 0
    x = pow(x2, (_P + 3) // 8, _P)
    if (x * x - x2) % _P:
        x = x * _I % _P
    if (x * x - x2) % _P:
        return None
    return _P - x if x & 1 != sign else x


def _add(a: Point, b: Point) -> Point:
    t = _D * a[0] * b[0] * a[1] * b[1] % _P
    return ((a[0] * b[1] + b[0] * a[1]) * _inv(1 + t) % _P, (a[1] * b[1] + a[0] * b[0]) * _inv(1 - t) % _P)


def _mul(scalar: int, point: Point) -> Point:
    result: Point = (0, 1)
    while scalar:
        if scalar & 1:
            result = _add(result, point)
        point = _add(point, point)
        scalar >>= 1
    return result


def _decode(data: bytes) -> Optional[Point]:
    value = int.from_bytes(data, "little")
    y, sign = value & ((1 << 255) - 1), value >> 255
    x = _recover_x(y, sign)
    return None if x is None else (x, y)


_BASE_Y = 4 * _inv(5) % _P
_BASE: Point = (_recover_x(_BASE_Y, 0) or 0, _BASE_Y)


def verify(public_key: bytes, message: bytes, signature: bytes) -> bool:
    """True when ``signature`` (64 bytes) is a valid Ed25519 signature of ``message`` under the 32-byte ``public_key``."""
    try:
        if len(public_key) != 32 or len(signature) != 64:
            return False
        a, r = _decode(public_key), _decode(signature[:32])
        s = int.from_bytes(signature[32:], "little")
        if a is None or r is None or s >= _L:
            return False
        h = int.from_bytes(hashlib.sha512(signature[:32] + public_key + message).digest(), "little") % _L
        return _mul(s, _BASE) == _add(r, _mul(h, a))
    except Exception:
        return False


def b64url_decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def canonical_json(value) -> bytes:
    """RFC 8785 text of a JSON value; raises TypeError for anything else (a float is not a manifest value)."""
    def text(item) -> str:
        if item is None:
            return "null"
        if item is True:
            return "true"
        if item is False:
            return "false"
        if isinstance(item, int):
            return str(item)
        if isinstance(item, str):
            return json.dumps(item, ensure_ascii=False)
        if isinstance(item, list):
            return "[" + ",".join(text(entry) for entry in item) + "]"
        if isinstance(item, dict):
            pairs = sorted(item.items(), key=lambda pair: pair[0].encode("utf-16-be"))
            return "{" + ",".join(json.dumps(key, ensure_ascii=False) + ":" + text(entry) for key, entry in pairs) + "}"
        raise TypeError(type(item).__name__)

    return text(value).encode("utf-8")


def verify_envelope(record: dict, public_key: bytes) -> bool:
    """The service's envelope signature: Ed25519 over the canonical form of the record without its ``signature`` member."""
    try:
        signature = record.get("signature")
        if not isinstance(signature, dict) or signature.get("alg") != "ed25519" or not isinstance(signature.get("sig"), str):
            return False
        body = canonical_json({key: value for key, value in record.items() if key != "signature"})
        return verify(public_key, body, b64url_decode(signature["sig"]))
    except Exception:
        return False
