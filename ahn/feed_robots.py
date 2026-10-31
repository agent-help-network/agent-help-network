"""Robots.txt decisions for status-feed hosts (D50).

AgentHelpNetwork asks agents to follow a host's robots.txt, so it follows the robots.txt of every status host it reads
a feed from. Before a feed path is requested, the host's own /robots.txt is read (at most once a day per host), parsed
under RFC 9309 with the same grouping code as the declared-policy layer (access_profile.split_groups, never
urllib.robotparser) and the answer is stored per path the service would request. A path the file disallows is not
requested. The file itself is never stored, only its SHA-256, the deciding rule line and the answer.

How a robots.txt read ends (RFC 9309 section 2.3.1):
  ok           200 with a file: the rules decide, per path.
  absent       404 or 410: no rules, every path allowed.
  unavailable  another 4xx (401 and 403 included) or a web page served for /robots.txt: no usable rules, every path
               allowed ("Unavailable": the crawler may access any resource). 429 is not that: a host asking us to slow
               down is "unknown" below.
  error        5xx, 429, a timeout, a DNS, TLS or connection failure, a redirect we do not follow, a file over the cap:
               "complete disallow" (section 2.3.1.4). Nothing is requested from the host until the file can be read, and
               the read is tried again after an hour. A failed read is never taken as permission.
               One exception, RFC 9309 section 2.4: when a host's last read was an `ok` less than STALE_OK_MAX ago, that
               file, which the host really declared, keeps deciding while the file cannot be read (``error_at`` records
               the failed attempt and holds the next try back by an hour). After STALE_OK_MAX the host is disallowed as
               above. A host with no earlier `ok` read gets no such grace.

Nothing here runs on the lookup path except ``feed_state``, a database read.
"""

from __future__ import annotations

import functools
import hashlib
import re
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse

from psycopg.types.json import Jsonb

from .access_profile import ROBOTS_CAP, USER_AGENT, _path_matches, fetch_public_file, split_groups
from .db import as_utc, connect
from .provenance import sha256_hex

PARSER_VERSION = "ahn-feed-robots/1; RFC 9309, longest match, Allow wins a tie"
PRODUCT_TOKEN = "agenthelpnetwork"  # the product token of USER_AGENT, as split_groups lowercases group names
assert USER_AGENT.split("/", 1)[0].lower() == PRODUCT_TOKEN
DECIDED_TTL = timedelta(hours=24)   # a cached robots.txt answer lasts this long (RFC 9309 section 2.4), plus the host's slack
SLACK_MAX_SECONDS = 2 * 3600        # each host's answer lasts 0-2 hours longer, fixed per host, so decisions do not expire together
STALE_OK_MAX = timedelta(hours=48)  # an `ok` read keeps deciding this long while the file cannot be read
ERROR_TTL = timedelta(hours=1)      # a read that failed is tried again after this long (access_profile.ERROR_RETRY)
RULE_CHARS = 80                     # characters of the deciding rule line that are stored
STATUSES = ("ok", "absent", "unavailable", "error")
_PATH_RE = re.compile(r"^/[\x21-\x7e]{0,300}$")

INTERIM_FILE = Path(__file__).with_name("feed_robots_allowed.txt")
HISTORY_PATH = "/history.atom"      # a Statuspage page's public Atom history (D52): decided with the feed's other paths
STATUS_JSON_PATH = "/api/v2/status.json"


@functools.lru_cache(maxsize=1)
def interim_hosts() -> frozenset[str]:
    """The 121 feed hosts whose robots.txt was read by hand on 2026-10-22 and allows the feed path (the interim stop,
    D49 stage 0). Used only to keep an answer on show for a host that has no stored decision yet (see ``shown``)."""
    lines = INTERIM_FILE.read_text(encoding="utf-8").splitlines()
    return frozenset(line.strip().lower() for line in lines if line.strip() and not line.lstrip().startswith("#"))


def host_and_path(url: str) -> tuple[str, str]:
    """The lowercase host and the path (with its query, as robots.txt rules match it) of a feed address."""
    parsed = urlparse(url)
    path = (parsed.path or "/") + (f"?{parsed.query}" if parsed.query else "")
    return (parsed.hostname or "").lower(), path


@dataclass(frozen=True)
class Decision:
    """What one host's robots.txt says about the paths we would request."""

    host: str
    robots_status: str                 # one of STATUSES
    detail: str | None                 # short label of how the read ended, e.g. http_503, read_timeout
    checked_at: datetime
    decisions: dict                    # path -> {"allowed": bool, "rule": str | None, "group": str | None}
    sha256: str | None = None
    parser_version: str = PARSER_VERSION
    read_now: bool = False             # this call made the request (the caller paces the request that follows)
    error_at: datetime | None = None   # a later read failed while this `ok` answer kept deciding (see the module note)
    error_detail: str | None = None

    def allows(self, path: str) -> bool:
        """A path with no stored answer is not allowed."""
        answer = self.decisions.get(path)
        return bool(answer and answer.get("allowed"))

    def ttl(self) -> timedelta:
        return ERROR_TTL if self.robots_status == "error" else DECIDED_TTL + slack(self.host)

    def expires_at(self) -> datetime:
        """When the next read is due. After a failed read that left an `ok` answer in force, an hour after the failure."""
        # Count in UTC: a stored timestamp can arrive in a DST zone, where "+ 24 hours" would move the wall clock.
        due = as_utc(self.checked_at) + self.ttl()
        return max(due, as_utc(self.error_at) + ERROR_TTL) if self.error_at else due

    def current(self, paths, now: datetime) -> bool:
        """Usable without a new read: same parser, every wanted path answered, inside the cache window (and, while a
        failed read is being ridden out, inside STALE_OK_MAX of the last `ok` read)."""
        if self.parser_version != PARSER_VERSION or not all(path in self.decisions for path in paths):
            return False
        if self.error_at and now - self.checked_at >= STALE_OK_MAX:
            return False
        return now < self.expires_at()


def slack(host: str) -> timedelta:
    """The 0-2 hour slack of one host's answer: fixed by the host name, spread evenly over hosts."""
    return timedelta(seconds=int(hashlib.sha256(host.encode()).hexdigest()[:8], 16) % (SLACK_MAX_SECONDS + 1))


def _rule_text(kind: str, pattern: str) -> str:
    return f"{kind}: {pattern}"[:RULE_CHARS]


def _decide_path(groups: list[dict], path: str) -> dict:
    """RFC 9309 section 2.2.2 and 2.2.3 for one path: the groups naming our product token, else the `*` groups; the
    longest matching rule wins and Allow wins a tie; no matching rule means allowed."""
    named = [group for group in groups if PRODUCT_TOKEN in group["agents"]]
    chosen, label = (named, "agenthelpnetwork") if named else ([group for group in groups if "*" in group["agents"]], "*")
    best_len, best = -1, None
    for group in chosen:
        for kind, pattern in group["rules"]:
            if not _path_matches(pattern, path):
                continue
            if len(pattern) > best_len or (len(pattern) == best_len and kind == "allow"):
                best_len, best = len(pattern), (kind, pattern)
    if best is None:
        return {"allowed": True, "rule": None, "group": label if chosen else None}
    return {"allowed": best[0] == "allow", "rule": _rule_text(*best), "group": label}


def decide_paths(body: bytes | str, paths) -> dict:
    """Pure. The answer for each path. Two groupings are read (access_profile.split_groups, D37's default and the
    strict RFC 9309 / Google one); where they differ the stricter answer stands, since a reader that is unsure
    what a file says should not read."""
    default, _ = split_groups(body)
    strict, _ = split_groups(body, google_style=True)
    result = {}
    for path in paths:
        a, b = _decide_path(default, path), _decide_path(strict, path)
        result[path] = a if not a["allowed"] else b
    return result


def classify(status: str, detail: str | None) -> tuple[str, bool]:
    """(robots_status, readable) of one fetch_public_file result that carried no usable body (everything but ok)."""
    code = int(detail[5:]) if detail and re.fullmatch(r"http_\d{3}", detail) else None
    if status == "absent":
        return "absent", True
    if status == "invalid":  # a web page for /robots.txt: a soft 404, no rules
        return "unavailable", True
    if code is not None and 400 <= code <= 499 and code != 429:
        return "unavailable", True
    return "error", False


def build(host: str, fetched: tuple, paths, now: datetime) -> Decision:
    """Pure. A Decision from the result of fetch_public_file(host, "/robots.txt", ...)."""
    status, body, detail = (tuple(fetched) + (None,))[:3]
    paths = tuple(paths)
    if status == "ok" and body is not None:
        return Decision(host, "ok", detail, now, decide_paths(body, paths), sha256_hex(body))
    robots_status, readable = classify(status, detail)
    answer = {"allowed": readable, "rule": None, "group": None}
    return Decision(host, robots_status, (detail or status)[:32], now, {path: dict(answer) for path in paths})


_COLUMNS = "feed_host,checked_at,robots_status,robots_detail,robots_sha256,parser_version,decisions,error_at,error_detail"


def _decision(row) -> Decision:
    return Decision(row["feed_host"], row["robots_status"], row["robots_detail"], row["checked_at"], dict(row["decisions"]),
                    row["robots_sha256"], row["parser_version"], False, row["error_at"], row["error_detail"])


def load(conn, host: str) -> Decision | None:
    row = conn.execute(f"SELECT {_COLUMNS} FROM feed_robots WHERE feed_host=%s", (host,)).fetchone()
    return _decision(row) if row else None


def load_all(conn) -> dict[str, Decision]:
    return {row["feed_host"]: _decision(row) for row in conn.execute(f"SELECT {_COLUMNS} FROM feed_robots").fetchall()}


def store(conn, decision: Decision) -> None:
    conn.execute(
        f"""INSERT INTO feed_robots ({_COLUMNS}) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (feed_host) DO UPDATE SET checked_at=EXCLUDED.checked_at,robots_status=EXCLUDED.robots_status,
            robots_detail=EXCLUDED.robots_detail,robots_sha256=EXCLUDED.robots_sha256,
            parser_version=EXCLUDED.parser_version,decisions=EXCLUDED.decisions,
            error_at=EXCLUDED.error_at,error_detail=EXCLUDED.error_detail
            WHERE feed_robots.checked_at <= EXCLUDED.checked_at""",  # a slower reader never overwrites a newer answer
        (decision.host, decision.checked_at, decision.robots_status, decision.detail, decision.sha256,
         decision.parser_version, Jsonb(decision.decisions), decision.error_at, decision.error_detail),
    )


class Gate:
    """The one check before any status-feed request. ``decide(host, paths)`` returns the stored decision while it is
    current and otherwise reads the host's /robots.txt once, stores the answer and returns it. The read is the same
    one access_profile makes (AgentHelpNetwork user agent, pinned public IP, 256 KB cap, per-operation and whole-body
    time limits, at most the apex-to-www redirect) and never raises; ``fetch`` replaces it in tests."""

    def __init__(self, settings, *, fetch=None, clock=None):
        self.settings = settings
        self.fetch = fetch or fetch_public_file
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def decide(self, host: str, paths) -> Decision:
        host = host.strip().rstrip(".").lower()
        paths = tuple(dict.fromkeys(paths))
        now = self.clock()
        invalid = not host or not paths or not all(_PATH_RE.match(p) for p in paths)
        if invalid:
            return Decision(host, "error", "invalid_input", now, {p: {"allowed": False, "rule": None, "group": None} for p in paths})
        with connect(self.settings) as conn:
            previous = load(conn, host)
        if previous is not None and previous.current(paths, now):
            return previous
        covered = previous is not None and previous.parser_version == PARSER_VERSION
        # A path the earlier answer covered stays covered (a host serving two feeds is read once a day, not once per feed).
        wanted = tuple(dict.fromkeys(paths + tuple(previous.decisions))) if covered else paths
        fetched = self.fetch(host, "/robots.txt", cap_bytes=ROBOTS_CAP)
        decision = build(host, fetched, wanted, now)
        if (decision.robots_status == "error" and covered and previous.robots_status == "ok"
                and now - previous.checked_at < STALE_OK_MAX and all(path in previous.decisions for path in paths)):
            # RFC 9309 section 2.4: the file cannot be read right now; the one the host declared less than STALE_OK_MAX
            # ago keeps deciding, and the failed attempt holds the next try back by an hour. Only an `ok` read earns
            # this: a host never read, or last read as missing, is not given the benefit of the doubt.
            decision = replace(previous, error_at=now, error_detail=decision.detail)
        with connect(self.settings) as conn:
            store(conn, decision)
        return replace(decision, read_now=True)


def feed_state(conn, api_url: str) -> str:
    """Database only. What the stored decision says about one feed address:
    ``allowed``, ``disallowed`` (the file says no), ``unknown`` (the file could not be read) or ``undecided`` (no
    decision stored for that host or path yet). Age is not checked: a lapsed answer stays on show until the next read."""
    host, path = host_and_path(api_url)
    decision = load(conn, host) if host else None
    return _state(decision, path)


def _state(decision: Decision | None, path: str) -> str:
    if decision is None or path not in decision.decisions:
        return "undecided"
    if decision.allows(path):
        return "allowed"
    return "unknown" if decision.robots_status == "error" else "disallowed"


def history_path(api_url: str) -> str | None:
    """The Atom history path of a Statuspage feed address (D52), or None when the address is not a page's own
    /api/v2/status.json (another platform, or a page served under a path prefix)."""
    return HISTORY_PATH if urlparse(api_url).path == STATUS_JSON_PATH else None


def history_allowed(conn, api_url: str) -> bool:
    """Database only. Whether the stored decision for this feed's host allows the page's Atom history."""
    path = history_path(api_url)
    host, _ = host_and_path(api_url)
    return bool(path and host and _state(load(conn, host), path) == "allowed")


def shown(state: str, host: str) -> bool:
    """Whether an answer may show a feed's statement. A host with no decision yet keeps showing it only when it is on
    the interim list (read by hand on 2026-10-22); a decision, once stored, replaces the list."""
    return state == "allowed" or (state == "undecided" and host.lower() in interim_hosts())


def counts(conn) -> dict:
    """Owner board: registered feed addresses by what their host's robots.txt says. ``unknown`` includes feeds with no
    decision yet and hosts whose robots.txt could not be read."""
    decisions = load_all(conn)
    result = {"allowed": 0, "disallowed": 0, "unknown": 0}
    for row in conn.execute("SELECT DISTINCT api_url FROM status_sources WHERE enabled=true").fetchall():
        host, path = host_and_path(row["api_url"])
        state = _state(decisions.get(host), path)
        result["allowed" if state == "allowed" else "disallowed" if state == "disallowed" else "unknown"] += 1
    return result
