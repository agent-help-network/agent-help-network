"""Changes in a public host's declared access policy, as short codes (D75).

When AHN re-reads a host's robots.txt, llms.txt and standard files (``access_profile.get_profile``), or checks its home page
(``site_probe.measure``), the new reading is compared with the value stored just before. A field that differs gets one row in
``host_access_changes``: the host, the time, the field, and the old and new short code. The first reading of a host writes
nothing (there is nothing to compare), and a field that the new reading could not determine is not compared.

What is kept: a fixed list of fields (``PLAIN_FIELDS`` and ``robots_verdict:<token>`` for AHN's own token and the governing
token of each vendor, D48) and codes of at most 24 characters. Never the file, a path, a header value or a hostname other
than the host itself. A failure to write is logged and never reaches the read that called it.

Read by ``cause`` (the hints ``robots_now_disallows``, ``terms_signal_changed`` and ``new_bot_defense``, D73/D75) and by the
offline bundle (changes of the last 30 days of curated hosts). Nothing here changes a ``recommendation``. Kept 90 days
(``platform_metrics.prune_analytics``; the privacy policy states the same number).
"""

from __future__ import annotations

import logging
import re
from datetime import datetime

LOG = logging.getLogger(__name__)

RETENTION_DAYS = 90
BUNDLE_DAYS = 30
OUR_TOKEN = "AgentHelpNetwork"          # the user-agent token of AHN's own reads; its verdict is the stored home_gate
VERDICT_PREFIX = "robots_verdict:"
PLAIN_FIELDS = ("robots_status", "llms_presence", "tdm_root", "site_declared_api", "site_declared_reservation",
                "home_outcome", "bot_vendor")
SOURCES = ("read", "home_check")
ALLOW, BLOCK = "allow", "block"
RESERVED = "reserved"
NO_VENDOR = "none"
# Home-page outcomes that say nothing about a policy: a passing failure, or no measurement. They are never compared.
TRANSIENT_HOME = frozenset({"unreachable", "server_error", "not_measured"})
_CODE = re.compile(r"^[a-z0-9_.-]{1,24}$")
_FIELD = re.compile(r"^robots_verdict:[A-Za-z0-9._-]{1,40}$")


def verdict_tokens() -> tuple[str, ...]:
    """AHN's own token, then the governing token(s) of each vendor in the order of AGENT_VENDORS (D48)."""
    from .access_profile import AGENT_VENDORS, GOVERNING_TOKENS

    tokens = [OUR_TOKEN]
    for vendor in AGENT_VENDORS:
        tokens += [token for token in GOVERNING_TOKENS[vendor] if token not in tokens]
    return tuple(tokens)


def valid_field(field: str) -> bool:
    return field in PLAIN_FIELDS or bool(_FIELD.fullmatch(field))


def _code(value) -> str | None:
    """A stored value as a short code, or None when it is not one (then the field is not compared)."""
    text = str(value).strip().lower() if value is not None else ""
    return text if _CODE.fullmatch(text) else None


# --- Codes of one reading ---------------------------------------------------------------------

def profile_codes(robots_status, robots, llms_txt_status, home_gate) -> dict[str, str]:
    """The comparable codes of one robots.txt / llms.txt reading. ``robots`` is the parsed summary (or None), ``home_gate`` the
    stored verdict for "/" (AHN's own token). A field whose reading is not a definitive answer is left out."""
    out: dict[str, str] = {}
    if robots_status not in (None, "error", "pending") and (code := _code(robots_status)):
        out["robots_status"] = code
    if llms_txt_status in ("present", "absent"):
        out["llms_presence"] = llms_txt_status
    if isinstance(home_gate, dict):
        if home_gate.get("allowed"):
            out[VERDICT_PREFIX + OUR_TOKEN] = ALLOW
        elif home_gate.get("reason") == "robots_disallowed":
            out[VERDICT_PREFIX + OUR_TOKEN] = BLOCK
    rules = {rule.get("agent"): rule for rule in robots.get("rules") or [] if isinstance(rule, dict)} \
        if isinstance(robots, dict) else {}
    for token in verdict_tokens()[1:]:
        if robots_status == "absent":
            out[VERDICT_PREFIX + token] = ALLOW      # no robots.txt: nothing is disallowed
        elif token in rules and isinstance(rules[token].get("root_allowed"), bool):
            out[VERDICT_PREFIX + token] = ALLOW if rules[token]["root_allowed"] else BLOCK
    return out


def declared_codes(site_declared) -> dict[str, str]:
    """The comparable codes of the standard-file reading (``access_profile.site_declared`` as stored). A marker for a read
    that is pending or failed to store gives nothing; a file that was not read leaves its fields out."""
    from . import site_declared as sd

    if not isinstance(site_declared, dict) or site_declared.get("version") != sd.VERSION \
            or site_declared.get(sd.PENDING) or site_declared.get(sd.STORE_FAILED):
        return {}
    out: dict[str, str] = {}
    catalog = site_declared.get("api_catalog")
    status = catalog.get("status") if isinstance(catalog, dict) else None
    if status in ("declared", "none_declared"):
        out["site_declared_api"] = "declared" if status == "declared" else "none"
    tdm = site_declared.get("tdm")
    tdm = tdm if isinstance(tdm, dict) else {}
    header_reserved = any(isinstance(item, dict) and item.get("tdm_reservation") == 1
                          for item in site_declared.get("tdm_headers") or [])
    if tdm.get("status") == "declared":
        root = tdm.get("root_reservation")
        out["tdm_root"] = RESERVED if root == 1 or (root is None and header_reserved) else "open" if root == 0 else "unset"
        out["site_declared_reservation"] = RESERVED if tdm.get("any_reserved") or header_reserved else "none"
    elif header_reserved:
        out["tdm_root"] = out["site_declared_reservation"] = RESERVED   # a header is a declaration even where the file is missing
    elif tdm.get("status") == "none_declared":
        out["tdm_root"] = out["site_declared_reservation"] = "none"
    return out


def home_codes(outcome, bot_vendor) -> dict[str, str]:
    """The comparable codes of one measured home-page check; a failure, a limit or a skipped check gives nothing."""
    if outcome is None or outcome in TRANSIENT_HOME:
        return {}
    out = {}
    if code := _code(outcome):
        out["home_outcome"] = code
    out["bot_vendor"] = (_code(bot_vendor) if bot_vendor else None) or NO_VENDOR
    return out


def changes(before: dict[str, str] | None, after: dict[str, str]) -> list[tuple[str, str, str]]:
    """(field, from, to) of each field present in both readings with a different code, in field order."""
    if not before:
        return []
    return [(field, before[field], after[field]) for field in sorted(after) if field in before and before[field] != after[field]]


# --- Writing ----------------------------------------------------------------------------------

def record(conn, site_host: str, rows: list[tuple[str, str, str]], at: datetime, source: str) -> int:
    """One row per changed field, in a savepoint; the number written. Never raises."""
    rows = [row for row in rows if valid_field(row[0]) and _CODE.fullmatch(row[1]) and _CODE.fullmatch(row[2])]
    if not rows or source not in SOURCES:
        return 0
    try:
        with conn.transaction():
            conn.execute(
                """INSERT INTO host_access_changes (site_host,at,field,from_code,to_code,source)
                   SELECT %s,%s,f,a,b,%s FROM unnest(%s::text[],%s::text[],%s::text[]) AS t(f,a,b)""",
                (site_host, at, source, [r[0] for r in rows], [r[1] for r in rows], [r[2] for r in rows]))
        return len(rows)
    except Exception as exc:  # the history is best effort; the read and its cache row stand
        LOG.warning("host_access_changes_write_failed error=%s", type(exc).__name__)
        return 0


def snapshot(conn, site_host: str) -> dict | None:
    """The codes of the stored reading, taken just before a new one replaces it; None for a host never read (the first
    reading records nothing) or on any error. Never raises."""
    try:
        with conn.transaction():
            row = conn.execute(
                "SELECT fetched_at,robots_status,robots,llms_txt_status,site_declared,home_gate FROM host_access_profile WHERE site_host=%s",
                (site_host,)).fetchone()
        if row is None or row["fetched_at"] is None:
            return None
        return {"profile": profile_codes(row["robots_status"], row["robots"], row["llms_txt_status"], row["home_gate"]),
                "declared": declared_codes(row["site_declared"])}
    except Exception as exc:
        LOG.warning("host_access_changes_snapshot_failed error=%s", type(exc).__name__)
        return None


def record_profile(conn, site_host: str, before: dict | None, profile: dict, at: datetime) -> int:
    """Compare a fresh ``build_profile`` result with the snapshot taken before it was stored. Never raises."""
    if before is None:
        return 0
    try:
        after = profile_codes(profile.get("robots_status"), profile.get("robots"), profile.get("llms_txt_status"),
                              profile.get("home_gate"))
        return record(conn, site_host, changes(before["profile"], after), at, "read")
    except Exception as exc:
        LOG.warning("host_access_changes_compare_failed error=%s", type(exc).__name__)
        return 0


def record_declared(conn, site_host: str, before: dict | None, stored: dict, at: datetime) -> int:
    """Compare the stored standard-file reading with the one that preceded it (``snapshot(...)["declared"]``)."""
    try:
        return record(conn, site_host, changes(before, declared_codes(stored)), at, "read")
    except Exception as exc:
        LOG.warning("host_access_changes_compare_failed error=%s", type(exc).__name__)
        return 0


def record_home_check(conn, site_host: str, previous, row: dict, at: datetime) -> int:
    """Compare one stored home-page check (``site_probe._store`` row) with the check it replaces. Never raises."""
    if not previous:
        return 0
    try:
        before = home_codes(previous["outcome"], previous["bot_vendor"])
        after = home_codes(row["outcome"], row["bot_vendor"])
        return record(conn, site_host, changes(before, after), at, "home_check")
    except Exception as exc:
        LOG.warning("host_access_changes_compare_failed error=%s", type(exc).__name__)
        return 0
