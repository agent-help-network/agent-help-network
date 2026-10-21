"""Signal history for the "Site problems right now" board (docs/decisions.md D41). Recording and owner view only.

Two signals, never merged into one verdict and never mixed into observations, evidence_level,
reporting_sources, current_site_status or the recommendation:

* P, provider: a change in the indicator of a connected official status feed, recorded where
  ``status_refresh.refresh_one`` stores a successful read.
* A, AHN check: the transport outcome of a robots.txt read that already happens (after a lookup, or the
  weekly seed-list read), recorded where ``access_profile.get_profile`` stores the result. A refused read
  (401, 403, 429, 503) is never a failure here (D27). When the apex fails but its www alias answers (a
  refusal, a redirect, a web page), the read counts as that answer, not as a failure.

Recording adds no request to any site: it is one statement next to the write the read already makes (the
event row, plus the day's lookup-or-seed counter of that host), inside a savepoint, so a failed history
write never changes or stops the read. Only curated hosts are recorded (``board_hosts``): the curated part
of the seed list, the official-paths table and the status mappings. Hosts that were only looked up, hosts
linked only by Statuspage discovery, and hosts that appear only in the seed list's Tranco block are not.

The rollup (``rollup``) runs in the 15-minute cleanup cron. It judges each closed 15-minute window once
(``hold_decision``; a row that arrives later for a judged window follows that judgement), feeds the reads
through a per-host state machine (``outage_episodes``) and adds daily totals. A-signal states: ok ->
suspect (one counted failure, an open episode with confirmed_at NULL) -> down (``Thresholds.n_fail``
consecutive failures spanning at least ``t_min``) -> ended by ``n_ok`` consecutive answers, or at once by a
refusal (or another non-failure) after a no-response or TLS period: the host answered, so it is not
"not responding" any more, but a refusal is never counted as a recovery either. A read in a held window
changes nothing.
"""

from __future__ import annotations

import functools
import logging
import os
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from statistics import median

from psycopg.types.json import Jsonb

LOG = logging.getLogger(__name__)

# Retention (platform_metrics.prune_analytics carries the same numbers; tests/test_policy_coverage.py checks them).
RAW_RETENTION_DAYS = 14
ROLLUP_RETENTION_DAYS = 90
PUBLIC_WINDOW_HOURS = 24  # what a later public page may show; nothing is public in this version

WINDOW = timedelta(minutes=15)
CLOSE_GRACE = timedelta(minutes=5)   # a window is judged once it has been closed this long
MAX_EVENTS_PER_RUN = 20_000
HOLD_MIN_HOSTS = 5                   # simultaneous failures: more than max(5 hosts, 20% of the window's reads)
HOLD_SHARE = 0.20
CLUSTER_MIN_HOSTS = 5                # 5 or more hosts with dns_* (our resolver?) or tls_error (our CA bundle?)
INFRA_LOOKBACK = timedelta(minutes=60)
MATCH_MARGIN = timedelta(minutes=60)  # an A period "matches" a provider period within this margin
BASELINE_DAYS = 30
UNSTABLE_FAILURE_RATE = 0.10
MIN_BASELINE_READS = 20              # the usual-failure-rate exclusion needs at least this many reads in 30 days
STALE_AFTER = timedelta(hours=48)    # a confirmed period with no read for this long is not a current candidate
FEED_FRESH = timedelta(hours=2)      # the lookup answer calls a provider value older than this stale
INTERMITTENT_WINDOW = timedelta(hours=6)
INTERMITTENT_PERIODS = 3
CANDIDATE_LIST_MAX = 40
_LOCK_KEY = 81260531024

RESPONDING_STATUSES = frozenset({"ok", "absent", "redirected", "invalid", "too_large"})
_DETAIL_OUTCOMES = {
    "dns_failed": "no_response:dns", "dns_timeout": "no_response:dns",
    "connect_error": "no_response:connect", "connect_timeout": "no_response:connect",
    "read_timeout": "no_response:timeout", "read_error": "no_response:timeout",
    "tls_error": "tls_error",
}
FAILURE_OUTCOMES = frozenset({"no_response:dns", "no_response:connect", "no_response:timeout", "tls_error", "server_error"})
NEUTRAL_OUTCOMES = frozenset({"refused", "other"})  # neither a failure nor a recovery
# Period kinds that an answer of any sort ends: the host did answer, so "not responding" / "TLS error" is over.
# A server_error period stays open through a refusal: a 503 can be the same server trouble.
ANSWER_ENDS_KINDS = frozenset({"no_response", "tls_error"})
PROVIDER_PROBLEMS = frozenset({"minor", "major", "critical", "maintenance"})
PROVIDER_INCIDENTS = frozenset({"minor", "major", "critical"})
# Shared infrastructure whose own published incident can explain many hosts failing at once (design 7.1).
# Only hosts with a connected feed produce P events; the others are listed so a later mapping counts at once.
INFRA_HOSTS = frozenset({"cloudflare.com", "cloudflare.net", "akamai.com", "akamai.net", "fastly.com", "fastly.net",
                         "amazonaws.com", "cloud.google.com"})


@dataclass(frozen=True)
class Thresholds:
    n_fail: int = 2
    t_min: timedelta = timedelta(minutes=45)
    n_ok: int = 1


# Stage 1 reads a failed host again after one hour (access_profile.ERROR_RETRY); stage 3 (15-minute reads, not
# built) would use 3 / 30 minutes / 2. AHN_BOARD_STAGE picks one; anything else is stage 1.
STAGES = {"1": Thresholds(), "3": Thresholds(n_fail=3, t_min=timedelta(minutes=30), n_ok=2)}


def thresholds_from_env() -> Thresholds:
    return STAGES.get(os.environ.get("AHN_BOARD_STAGE", "1").strip(), STAGES["1"])


# --- What is recorded ------------------------------------------------------------------------------

@functools.lru_cache(maxsize=1)
def board_hosts() -> frozenset[str]:
    """Curated hosts: curated seed part (above the Tranco block), official paths, status mappings."""
    from .bundle import BundleError, bundle_seed_hosts
    from .official_paths import OFFICIAL_ACCESS, OFFICIAL_PATHS
    from .status_refresh import OFFICIAL_STATUS_PAGES, SOURCES

    try:
        seeds = set(bundle_seed_hosts())
    except BundleError:  # the seed list lost its Tranco marker: record nothing from it rather than guess
        LOG.warning("signal_history_seed_marker_missing")
        seeds = set()
    return frozenset(seeds | set(OFFICIAL_PATHS) | set(OFFICIAL_ACCESS) | set(SOURCES) | set(OFFICIAL_STATUS_PAGES))


def classify(robots_status: str | None, detail: str | None) -> str | None:
    """The A outcome of one robots.txt read (design 2.2); None when the read is not recorded at all."""
    if robots_status in RESPONDING_STATUSES:
        return "responding"
    if robots_status == "refused":
        return "refused"
    if robots_status != "error" or detail == "invalid_input":
        return None  # not_public, pending, or a name the reader refused to try
    if detail in _DETAIL_OUTCOMES:
        return _DETAIL_OUTCOMES[detail]
    if detail and detail.startswith("http_") and detail[5:].isdigit():
        code = int(detail[5:])
        # 503 is a refusal (REFUSAL_STATUSES) and never reaches here; any other 5xx is a server error.
        return "server_error" if 500 <= code <= 599 and code != 503 else "responding"
    return "other"


def read_outcome(site_host: str, profile: dict) -> tuple[str, str | None, str, str] | None:
    """(robots_status, detail, read_host, outcome) of one read as the history records it, or None.

    The apex result decides, except when the apex failed and the www alias (already tried by build_profile)
    answered in some way: a refusal, a redirect, a web page or another HTTP answer. Then the host answered,
    and the www attempt is what is recorded. No request is made here.
    """
    status, detail = profile.get("robots_status"), profile.get("robots_detail")
    outcome = classify(status, detail)
    if outcome is None:
        return None
    www = profile.get("www_attempt")
    if outcome in FAILURE_OUTCOMES and www:
        alt = classify(www.get("robots_status"), www.get("robots_detail"))
        if alt in ("responding", "refused"):
            return www["robots_status"], www.get("robots_detail"), f"www.{site_host}", alt
    return status, detail, profile.get("read_host") or site_host, outcome


def _savepoint(conn, write) -> bool:
    """Run one history write in a savepoint; a failure is logged and never reaches the read that called it."""
    try:
        with conn.transaction():
            return bool(write())
    except Exception as exc:  # the history is best effort; the read and its cache row stand
        LOG.warning("signal_history_write_failed error=%s", type(exc).__name__)
        return False


def _curated(site_host: str) -> bool:
    try:
        return site_host in board_hosts()
    except Exception as exc:  # never let the scope check break the read that called it
        LOG.warning("signal_history_scope_unavailable error=%s", type(exc).__name__)
        return False


def record_read(conn, site_host: str, profile: dict, read_at: datetime, trigger: str) -> bool:
    """One event row for a robots.txt read of a curated host, and +1 on that host's daily lookup or seed counter.

    One statement. Whether the read followed a lookup is kept only as that daily count, never on the event.
    True when written. Never raises.
    """
    if not _curated(site_host):
        return False
    try:
        found = read_outcome(site_host, profile)
    except Exception:
        found = None
    if found is None:
        return False
    status, detail, read_host, outcome = found
    seed = 1 if trigger == "seed" else 0

    def write():
        from .provenance import operator_id, vantage

        conn.execute(
            """WITH event AS (
                   INSERT INTO robots_read_events (site_host,read_at,read_host,robots_status,detail,a_outcome,
                                                   origin_operator,vantage)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s) RETURNING site_host,read_at)
               INSERT INTO robots_read_daily (site_host,day,lookup_reads,seed_reads)
               SELECT site_host,(read_at AT TIME ZONE 'UTC')::date,%s,%s FROM event
               ON CONFLICT (site_host,day) DO UPDATE SET lookup_reads=robots_read_daily.lookup_reads+EXCLUDED.lookup_reads,
                 seed_reads=robots_read_daily.seed_reads+EXCLUDED.seed_reads""",
            (site_host, read_at, read_host, status, str(detail)[:32] if detail else None, outcome,
             operator_id(), Jsonb(vantage()), 1 - seed, seed),
        )
        return True

    return _savepoint(conn, write)


def record_provider_status(conn, site_host: str, indicator: str, source_updated_at: datetime | None) -> bool:
    """One row when a curated host's provider indicator differs from the last recorded one; True when written. Never raises."""
    if not _curated(site_host):
        return False

    def write():
        return conn.execute(
            """INSERT INTO provider_status_events (site_host,at,from_indicator,to_indicator,source_updated_at)
               SELECT %s,now(),last.indicator,%s,%s
               FROM (SELECT (SELECT to_indicator FROM provider_status_events WHERE site_host=%s
                             ORDER BY at DESC,id DESC LIMIT 1) AS indicator) last
               WHERE last.indicator IS DISTINCT FROM %s""",
            (site_host, indicator, source_updated_at, site_host, indicator),
        ).rowcount

    return _savepoint(conn, write)


# --- Rollup: windows, holds, state machine, daily totals --------------------------------------------

def window_start(at: datetime) -> datetime:
    epoch = int(at.timestamp())
    return datetime.fromtimestamp(epoch - epoch % int(WINDOW.total_seconds()), timezone.utc)


def hold_decision(events: list[dict], infra_reported: bool, baseline: set[str] | None = None) -> tuple[bool, str | None]:
    """(held, reason) for one closed 15-minute window of reads.

    Held when many unrelated hosts fail together, or failures cluster on dns_* or tls_error: both point
    at our own network, resolver or CA bundle more than at the sites. Only hosts in ``baseline`` (hosts that
    have answered a read in the last 30 days) are counted, failures and reads alike: names that never answer
    (dead domains in the seed list) must not hold a window and freeze a real failure. ``None`` counts every
    host. A published incident of a shared infrastructure provider in the same period explains it instead,
    so the window is not held and is marked ``infra_provider_reported`` (a real CDN outage must not be erased).
    """
    counted = events if baseline is None else [e for e in events if e["site_host"] in baseline]
    failing = {e["site_host"] for e in counted if e["a_outcome"] in FAILURE_OUTCOMES}
    dns = {e["site_host"] for e in counted if e["a_outcome"] == "no_response:dns"}
    tls = {e["site_host"] for e in counted if e["a_outcome"] == "tls_error"}
    reason = None
    if len(failing) > max(HOLD_MIN_HOSTS, HOLD_SHARE * len(counted)):
        reason = "simultaneous_failures"
    elif len(dns) >= CLUSTER_MIN_HOSTS:
        reason = "dns_cluster"
    elif len(tls) >= CLUSTER_MIN_HOSTS:
        reason = "tls_cluster"
    if reason is None:
        return False, None
    if infra_reported:
        return False, "infra_provider_reported"
    return True, reason


def _kind(outcome: str) -> str:
    return "no_response" if outcome.startswith("no_response") else outcome


def _apply_provider_events(conn, now: datetime, limit: int) -> int:
    rows = conn.execute(
        "SELECT id,site_host,at,to_indicator FROM provider_status_events WHERE applied_at IS NULL ORDER BY at,id LIMIT %s",
        (limit,),
    ).fetchall()
    if not rows:
        return 0
    hosts = sorted({row["site_host"] for row in rows})
    open_by = {row["site_host"]: dict(row) for row in conn.execute(
        "SELECT id,site_host,kind FROM outage_episodes WHERE signal='P' AND ended_at IS NULL AND site_host=ANY(%s)",
        (hosts,)).fetchall()}
    for row in rows:
        host, to, at = row["site_host"], row["to_indicator"], row["at"]
        current = open_by.get(host)
        if to == "unknown" or (current and current["kind"] == to):
            continue
        if current:
            conn.execute("UPDATE outage_episodes SET ended_at=%s,last_event_at=%s,ended_reason='provider_changed' WHERE id=%s",
                         (at, at, current["id"]))
            del open_by[host]
        if to in PROVIDER_PROBLEMS:
            new_id = conn.execute(
                """INSERT INTO outage_episodes (site_host,signal,kind,first_fail_at,confirmed_at,last_event_at,feed_connected)
                   VALUES (%s,'P',%s,%s,%s,%s,true) RETURNING id""",
                (host, to, at, at, at),
            ).fetchone()["id"]
            open_by[host] = {"id": new_id, "site_host": host, "kind": to}
    conn.execute("UPDATE provider_status_events SET applied_at=%s WHERE id=ANY(%s)", (now, [row["id"] for row in rows]))
    return len(rows)


def _infra_periods(conn, start: datetime, end: datetime) -> list[tuple[datetime, datetime | None]]:
    rows = conn.execute(
        """SELECT first_fail_at,ended_at FROM outage_episodes
           WHERE signal='P' AND site_host=ANY(%s) AND kind=ANY(%s)
             AND first_fail_at < %s AND (ended_at IS NULL OR ended_at > %s)""",
        (sorted(INFRA_HOSTS), sorted(PROVIDER_INCIDENTS), end, start - INFRA_LOOKBACK),
    ).fetchall()
    return [(row["first_fail_at"], row["ended_at"]) for row in rows]


def _registered_feed_hosts(conn) -> set[str]:
    """Hosts whose feed refresh_batch actually reads now: an enabled mapping registered by a lookup (D22)."""
    return {row["site_host"] for row in conn.execute(
        "SELECT s.site_host FROM status_sources s JOIN queried_hosts q ON q.site_host=s.site_host WHERE s.enabled=true"
    ).fetchall()}


def _answered_before(conn, hosts: list[str], first: datetime) -> tuple[set[str], dict[str, datetime]]:
    """Hosts with a counted answer in the daily totals of the 30 days before ``first``, and each host's first
    answering read in the raw events (which also holds the reads of this batch)."""
    since = first - timedelta(days=BASELINE_DAYS)
    daily = {row["site_host"] for row in conn.execute(
        "SELECT DISTINCT site_host FROM robots_read_daily WHERE site_host=ANY(%s) AND day >= %s AND responding > 0",
        (hosts, since.date())).fetchall()}
    raw = {row["site_host"]: row["at"] for row in conn.execute(
        """SELECT site_host,min(read_at) AS at FROM robots_read_events
           WHERE site_host=ANY(%s) AND a_outcome='responding' AND read_at >= %s GROUP BY site_host""",
        (hosts, since)).fetchall()}
    return daily, raw


def _prior_decisions(conn, first: datetime, final: datetime) -> dict[datetime, tuple[bool, str | None]]:
    """Judgements already made for windows of this batch (rows that arrived after their window was judged)."""
    rows = conn.execute(
        """SELECT floor(extract(epoch FROM read_at) / %s)::bigint AS w,bool_or(held) AS held,max(hold_reason) AS reason
           FROM robots_read_events WHERE applied_at IS NOT NULL AND read_at >= %s AND read_at < %s GROUP BY 1""",
        (int(WINDOW.total_seconds()), first, final)).fetchall()
    seconds = int(WINDOW.total_seconds())
    return {datetime.fromtimestamp(row["w"] * seconds, timezone.utc): (bool(row["held"]), row["reason"]) for row in rows}


class _Episodes:
    """Open A episodes of the hosts in one batch, written through on every change."""

    def __init__(self, conn, hosts: list[str], feeds: set[str], thresholds: Thresholds, counts: dict):
        self.conn, self.feeds, self.t, self.counts = conn, feeds, thresholds, counts
        self.open = {row["site_host"]: dict(row) for row in conn.execute(
            """SELECT id,site_host,kind,first_fail_at,confirmed_at,reads,ok_streak,held_overlap
               FROM outage_episodes WHERE signal='A' AND ended_at IS NULL AND site_host=ANY(%s)""",
            (hosts,)).fetchall()}

    def held(self, host: str, at: datetime) -> None:
        ep = self.open.get(host)
        if ep:
            ep["held_overlap"] += 1
            self.conn.execute("UPDATE outage_episodes SET held_overlap=%s,last_event_at=%s WHERE id=%s",
                              (ep["held_overlap"], at, ep["id"]))

    def fail(self, host: str, at: datetime, outcome: str) -> None:
        ep = self.open.get(host)
        if ep is None:
            row = self.conn.execute(
                """INSERT INTO outage_episodes (site_host,signal,kind,first_fail_at,last_event_at,reads,feed_connected)
                   VALUES (%s,'A',%s,%s,%s,1,%s) RETURNING id""",
                (host, _kind(outcome), at, at, host in self.feeds),
            ).fetchone()
            ep = {"id": row["id"], "site_host": host, "kind": _kind(outcome), "first_fail_at": at, "confirmed_at": None,
                  "reads": 1, "ok_streak": 0, "held_overlap": 0}
            self.open[host] = ep
            self.counts["opened"] += 1
        else:
            ep["reads"] += 1
            ep["ok_streak"] = 0
        if ep["confirmed_at"] is None and ep["reads"] >= self.t.n_fail and at - ep["first_fail_at"] >= self.t.t_min:
            ep["confirmed_at"] = at
            self.counts["confirmed"] += 1
        self.conn.execute("UPDATE outage_episodes SET reads=%s,ok_streak=0,confirmed_at=%s,last_event_at=%s WHERE id=%s",
                          (ep["reads"], ep["confirmed_at"], at, ep["id"]))

    def _end(self, ep: dict, at: datetime, reason: str) -> None:
        self.conn.execute("UPDATE outage_episodes SET ok_streak=%s,ended_at=%s,last_event_at=%s,ended_reason=%s WHERE id=%s",
                          (ep["ok_streak"], at, at, reason, ep["id"]))
        del self.open[ep["site_host"]]
        self.counts["ended"] += 1

    def respond(self, host: str, at: datetime) -> None:
        ep = self.open.get(host)
        if ep is None:
            return
        ep["ok_streak"] += 1
        # A suspect clears on one answer; a confirmed period needs n_ok answers in a row.
        if ep["confirmed_at"] is None or ep["ok_streak"] >= self.t.n_ok:
            self._end(ep, at, "answered")
        else:
            self.conn.execute("UPDATE outage_episodes SET ok_streak=%s,last_event_at=%s WHERE id=%s",
                              (ep["ok_streak"], at, ep["id"]))

    def other_answer(self, host: str, at: datetime, outcome: str) -> None:
        """A refusal (or another non-failure) ends a no-response or TLS period: the host answered. Not a recovery."""
        ep = self.open.get(host)
        if ep is None or ep["kind"] not in ANSWER_ENDS_KINDS:
            return
        self._end(ep, at, "answered_refused" if outcome == "refused" else "answered_other")


def _apply_read_events(conn, now: datetime, thresholds: Thresholds, limit: int, counts: dict) -> None:
    closed_before = window_start(now - CLOSE_GRACE)
    rows = conn.execute(
        """SELECT id,site_host,read_at,a_outcome FROM robots_read_events
           WHERE applied_at IS NULL AND read_at < %s ORDER BY read_at,id LIMIT %s""",
        (closed_before, limit + 1),
    ).fetchall()
    if not rows:
        return
    if len(rows) > limit:
        # Judge whole windows only: leave the last, possibly partial window for the next run.
        rows = rows[:limit]
        last = window_start(rows[-1]["read_at"])
        rows = [row for row in rows if window_start(row["read_at"]) < last] or rows
    windows: dict[datetime, list[dict]] = {}
    for row in rows:
        windows.setdefault(window_start(row["read_at"]), []).append(row)
    first, final = min(windows), max(windows) + WINDOW
    hosts = sorted({row["site_host"] for row in rows})
    infra = _infra_periods(conn, first, final)
    prior = _prior_decisions(conn, first, final)
    daily_answered, first_answer = _answered_before(conn, hosts, first)
    decisions: dict[datetime, tuple[bool, str | None]] = {}
    for start, events in windows.items():
        end = start + WINDOW
        if start in prior:  # late rows of a judged window follow its judgement
            decisions[start] = prior[start]
            continue
        baseline = {e["site_host"] for e in events
                    if e["site_host"] in daily_answered or (e["site_host"] in first_answer and first_answer[e["site_host"]] < end)}
        reported = any(begin < end and (stop is None or stop > start - INFRA_LOOKBACK) for begin, stop in infra)
        decisions[start] = hold_decision(events, reported, baseline)
        if decisions[start][0]:
            counts["held_windows"] += 1
    episodes = _Episodes(conn, hosts, _registered_feed_hosts(conn), thresholds, counts)
    daily: dict[tuple[str, date], list[int]] = {}
    for row in rows:
        host, at, outcome = row["site_host"], row["read_at"], row["a_outcome"]
        held = decisions[window_start(at)][0]
        totals = daily.setdefault((host, at.astimezone(timezone.utc).date()), [0, 0, 0, 0, 0])
        totals[0] += 1
        if held:
            totals[4] += 1
            episodes.held(host, at)
        elif outcome in FAILURE_OUTCOMES:
            totals[2] += 1
            episodes.fail(host, at, outcome)
        elif outcome == "responding":
            totals[1] += 1
            episodes.respond(host, at)
        else:  # refused or other: neither a failure nor a recovery in the totals
            if outcome == "refused":
                totals[3] += 1
            episodes.other_answer(host, at, outcome)
    with conn.cursor() as cur:
        cur.executemany(
            """INSERT INTO robots_read_daily (site_host,day,reads,responding,failed,refused,held) VALUES (%s,%s,%s,%s,%s,%s,%s)
               ON CONFLICT (site_host,day) DO UPDATE SET reads=robots_read_daily.reads+EXCLUDED.reads,
                 responding=robots_read_daily.responding+EXCLUDED.responding,failed=robots_read_daily.failed+EXCLUDED.failed,
                 refused=robots_read_daily.refused+EXCLUDED.refused,held=robots_read_daily.held+EXCLUDED.held""",
            [(host, day, *values) for (host, day), values in sorted(daily.items())],
        )
    conn.execute("UPDATE robots_read_events SET applied_at=%s WHERE id=ANY(%s)", (now, [row["id"] for row in rows]))
    for start, (held, reason) in decisions.items():
        if reason:
            conn.execute("UPDATE robots_read_events SET held=%s,hold_reason=%s WHERE id=ANY(%s)",
                         (held, reason, [row["id"] for row in windows[start]]))
    counts["a_events"] += len(rows)


def _match_episodes(conn, now: datetime) -> None:
    """Link each confirmed A period to the first overlapping provider incident period of the same host."""
    conn.execute(
        """UPDATE outage_episodes a SET matched_episode_id=(
               SELECT p.id FROM outage_episodes p
               WHERE p.signal='P' AND p.site_host=a.site_host AND p.kind=ANY(%s)
                 AND p.first_fail_at <= COALESCE(a.ended_at,%s) + %s
                 AND COALESCE(p.ended_at,'infinity'::timestamptz) >= a.first_fail_at - %s
               ORDER BY p.first_fail_at LIMIT 1)
           WHERE a.signal='A' AND a.confirmed_at IS NOT NULL AND a.matched_episode_id IS NULL
             AND (a.ended_at IS NULL OR a.ended_at > %s)""",
        (sorted(PROVIDER_INCIDENTS), now, MATCH_MARGIN, MATCH_MARGIN, now - timedelta(days=2)),
    )


def rollup(conn, *, now: datetime | None = None, thresholds: Thresholds | None = None,
           limit: int = MAX_EVENTS_PER_RUN) -> dict:
    """Apply recorded events: provider periods first (they can explain a window), then closed read windows."""
    now = now or datetime.now(timezone.utc)
    thresholds = thresholds or thresholds_from_env()
    counts = {"p_events": 0, "a_events": 0, "held_windows": 0, "opened": 0, "confirmed": 0, "ended": 0, "skipped": 0}
    if not conn.execute("SELECT pg_try_advisory_xact_lock(%s) AS ok", (_LOCK_KEY,)).fetchone()["ok"]:
        counts["skipped"] = 1
        return counts
    counts["p_events"] = _apply_provider_events(conn, now, limit)
    _apply_read_events(conn, now, thresholds, limit, counts)
    _match_episodes(conn, now)
    return counts


def run(database_url: str) -> str:
    """The cron step: one rollup in its own transaction; returns the log line (counts only, no host names)."""
    import psycopg
    from psycopg.rows import dict_row

    with psycopg.connect(database_url, row_factory=dict_row) as conn:
        counts = rollup(conn)
    return "signal_history " + " ".join(f"{key}={value}" for key, value in counts.items())


# --- Owner view ------------------------------------------------------------------------------------

def _iso(value):
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z") if isinstance(value, datetime) else value


def _baselines(conn, hosts: list[str], now: datetime) -> dict[str, str]:
    """'shown', 'no_baseline' (never answered in 30 days) or 'unstable' (usual failure rate above 10%,
    judged only with at least MIN_BASELINE_READS counted reads in 30 days)."""
    if not hosts:
        return {}
    since = (now - timedelta(days=BASELINE_DAYS)).date()
    totals = {row["site_host"]: row for row in conn.execute(
        """SELECT site_host,sum(responding)::bigint AS responding,sum(failed)::bigint AS failed,
                  sum(reads - held)::bigint AS counted
           FROM robots_read_daily WHERE site_host=ANY(%s) AND day >= %s GROUP BY site_host""",
        (hosts, since)).fetchall()}
    in_periods = {row["site_host"]: row["reads"] for row in conn.execute(
        """SELECT site_host,sum(reads)::bigint AS reads FROM outage_episodes
           WHERE signal='A' AND confirmed_at IS NOT NULL AND site_host=ANY(%s) AND last_event_at >= %s
           GROUP BY site_host""",
        (hosts, now - timedelta(days=BASELINE_DAYS))).fetchall()}
    result = {}
    for host in hosts:
        row = totals.get(host)
        if not row or not row["responding"]:
            result[host] = "no_baseline"
            continue
        if row["counted"] < MIN_BASELINE_READS:
            result[host] = "shown"  # too few reads to call a usual failure rate
            continue
        down = in_periods.get(host, 0)
        usual_failed = max(0, row["failed"] - down)
        usual_reads = max(1, row["counted"] - down)
        result[host] = "unstable" if usual_failed / usual_reads > UNSTABLE_FAILURE_RATE else "shown"
    return result


def _provider_now(conn, now: datetime) -> dict[str, dict]:
    """Curated hosts whose fresh provider value is a problem (the lookup answer's own freshness rule)."""
    curated = board_hosts()
    rows = conn.execute(
        """SELECT site_host,indicator,fetched_at,source_updated_at FROM status_cache
           WHERE indicator=ANY(%s) AND fetched_at >= %s AND (last_error_at IS NULL OR fetched_at >= last_error_at)""",
        (sorted(PROVIDER_PROBLEMS), now - FEED_FRESH),
    ).fetchall()
    return {row["site_host"]: dict(row) for row in rows if row["site_host"] in curated}


def _infra_feeds(conn, feeds: set[str], now: datetime) -> list[dict]:
    """Whether each shared-infrastructure host that can explain a window has a feed that is registered and read."""
    from .status_refresh import SOURCES

    cache = {row["site_host"]: row for row in conn.execute(
        "SELECT site_host,indicator,fetched_at,last_error_at FROM status_cache WHERE site_host=ANY(%s)",
        (sorted(INFRA_HOSTS),)).fetchall()}
    result = []
    for host in sorted(INFRA_HOSTS):
        row = cache.get(host)
        fetched = row["fetched_at"] if row else None
        result.append({"host": host, "feed": host in SOURCES, "registered": host in feeds,
                       "fresh": bool(fetched and fetched >= now - FEED_FRESH
                                     and (row["last_error_at"] is None or fetched >= row["last_error_at"])),
                       "fetched_at": _iso(fetched), "indicator": row["indicator"] if row and fetched else None})
    return result


def _covered(intervals: list[tuple[datetime, datetime]], at: datetime) -> bool:
    return any(begin <= at < end for begin, end in intervals)


def board_summary(conn, *, now: datetime | None = None, days: int = 7) -> dict:
    """Owner-only numbers for the 3-7 day observation before any public page (design 4.1). Read-only."""
    now = now or datetime.now(timezone.utc)
    since = now - timedelta(days=days)
    since_day = since.date()
    curated = board_hosts()
    feed_hosts = _registered_feed_hosts(conn)

    open_a = conn.execute(
        """SELECT id,site_host,kind,first_fail_at,confirmed_at,last_event_at,reads,held_overlap,feed_connected
           FROM outage_episodes WHERE signal='A' AND ended_at IS NULL ORDER BY first_fail_at""").fetchall()
    confirmed_open = [row for row in open_a if row["confirmed_at"] is not None]
    provider = _provider_now(conn, now)
    window_a = conn.execute(
        """SELECT a.id,a.site_host,a.kind,a.first_fail_at,a.confirmed_at,a.ended_at,a.ended_reason,a.last_event_at,a.reads,
                  a.feed_connected,a.matched_episode_id,p.first_fail_at AS p_started,
                  EXISTS (SELECT 1 FROM outage_episodes o WHERE o.signal='P' AND o.site_host=a.site_host
                          AND o.first_fail_at <= COALESCE(a.ended_at,%s) AND COALESCE(o.ended_at,'infinity'::timestamptz) >= a.first_fail_at)
                    AS any_provider_problem
           FROM outage_episodes a LEFT JOIN outage_episodes p ON p.id=a.matched_episode_id
           WHERE a.signal='A' AND a.confirmed_at IS NOT NULL
             AND a.confirmed_at < %s AND (a.ended_at IS NULL OR a.ended_at >= %s)""",
        (now, now, since)).fetchall()
    window_p = conn.execute(
        """SELECT site_host,kind,first_fail_at,ended_at FROM outage_episodes
           WHERE signal='P' AND first_fail_at < %s AND (ended_at IS NULL OR ended_at >= %s)""",
        (now, since)).fetchall()
    baseline = _baselines(conn, sorted({row["site_host"] for row in confirmed_open} | {row["site_host"] for row in window_a}), now)

    # Current candidates: what a board would list now (design 2.4), one line per host, sources side by side.
    # A confirmed period with no read for STALE_AFTER is counted as stale, not listed: the last word is too old.
    candidates: dict[str, dict] = {}
    excluded = {"no_baseline": 0, "unstable": 0, "stale": 0}
    for row in confirmed_open:
        if now - row["last_event_at"] > STALE_AFTER:
            excluded["stale"] += 1
            continue
        state = baseline.get(row["site_host"], "no_baseline")
        if state != "shown" and not (state == "unstable" and row["site_host"] in provider):
            excluded[state] += 1
            continue
        candidates[row["site_host"]] = {"site_host": row["site_host"], "provider": None,
                                        "ahn_check": {"kind": row["kind"], "since": _iso(row["first_fail_at"]),
                                                      "confirmed_at": _iso(row["confirmed_at"]), "failed_reads": row["reads"],
                                                      "last_read_at": _iso(row["last_event_at"]), "baseline": state}}
    for host, row in provider.items():
        entry = candidates.setdefault(host, {"site_host": host, "provider": None, "ahn_check": None})
        entry["provider"] = {"indicator": row["indicator"], "fetched_at": _iso(row["fetched_at"]),
                             "source_updated_at": _iso(row["source_updated_at"])}
    order = {"critical": 0, "major": 1, "minor": 2, "maintenance": 4}
    listed = sorted(candidates.values(), key=lambda c: (order.get((c["provider"] or {}).get("indicator"), 3), c["site_host"]))

    # Held windows, from the raw events (kept 14 days).
    holds = conn.execute(
        """SELECT hold_reason,held,count(DISTINCT floor(extract(epoch FROM read_at) / %s))::bigint AS windows
           FROM robots_read_events WHERE hold_reason IS NOT NULL AND read_at >= %s GROUP BY hold_reason,held""",
        (int(WINDOW.total_seconds()), since)).fetchall()
    held_by_reason = {row["hold_reason"]: row["windows"] for row in holds if row["held"]}
    explained = sum(row["windows"] for row in holds if not row["held"])
    latest = conn.execute(
        """SELECT bool_or(held) AS held FROM robots_read_events WHERE applied_at IS NOT NULL
             AND read_at >= (SELECT max(read_at) FROM robots_read_events WHERE applied_at IS NOT NULL) - interval '15 minutes'"""
    ).fetchone()

    # Share of 15-minute slots in which the board would have been empty, since recording began. An open period
    # counts only until STALE_AFTER past its last read, as for the current candidates.
    started = conn.execute(
        """SELECT least((SELECT min(read_at) FROM robots_read_events), (SELECT min(at) FROM provider_status_events),
                        (SELECT min(day) FROM robots_read_daily)::timestamp AT TIME ZONE 'UTC') AS t""").fetchone()["t"]
    empty_ratio, slots = None, 0
    if started is not None:
        begin = window_start(max(since, started))
        shown_hosts = {host for host, state in baseline.items() if state == "shown"}
        intervals = [(row["confirmed_at"], row["ended_at"] or min(now, row["last_event_at"] + STALE_AFTER)) for row in window_a
                     if row["site_host"] in shown_hosts or (baseline.get(row["site_host"]) == "unstable" and row["any_provider_problem"])]
        intervals += [(row["first_fail_at"], row["ended_at"] or now) for row in window_p if row["site_host"] in curated]
        empty, at = 0, begin
        while at < now:
            slots += 1
            if not _covered(intervals, at + WINDOW / 2):
                empty += 1
            at += WINDOW
        empty_ratio = round(empty / slots, 4) if slots else None

    # Hosts whose AHN check changed between down and recovered INTERMITTENT_PERIODS times or more in 6 hours.
    flips: dict[str, int] = {}
    for row in conn.execute(
            """SELECT site_host,confirmed_at,ended_at FROM outage_episodes WHERE signal='A' AND confirmed_at IS NOT NULL
               AND (ended_at IS NULL OR ended_at >= %s)""", (now - INTERMITTENT_WINDOW,)).fetchall():
        flips[row["site_host"]] = flips.get(row["site_host"], 0) + sum(
            1 for at in (row["confirmed_at"], row["ended_at"]) if at is not None and at >= now - INTERMITTENT_WINDOW)
    intermittent = sum(1 for count in flips.values() if count >= INTERMITTENT_PERIODS)

    reads = conn.execute(
        """SELECT COALESCE(sum(reads),0)::bigint AS reads,COALESCE(sum(responding),0)::bigint AS responding,
                  COALESCE(sum(failed),0)::bigint AS failed,COALESCE(sum(refused),0)::bigint AS refused,
                  COALESCE(sum(held),0)::bigint AS held,COALESCE(sum(lookup_reads),0)::bigint AS lookup,
                  COALESCE(sum(seed_reads),0)::bigint AS seed,count(DISTINCT site_host) FILTER (WHERE reads > 0)::bigint AS hosts
           FROM robots_read_daily WHERE day >= %s""", (since_day,)).fetchone()
    pending = conn.execute("SELECT count(*)::bigint AS n FROM robots_read_events WHERE applied_at IS NULL").fetchone()["n"]

    episodes_in = [row for row in window_a if row["confirmed_at"] >= since]
    kinds: dict[str, int] = {}
    ended_by: dict[str, int] = {}
    for row in episodes_in:
        kinds[row["kind"]] = kinds.get(row["kind"], 0) + 1
        if row["ended_reason"]:
            ended_by[row["ended_reason"]] = ended_by.get(row["ended_reason"], 0) + 1
    matched = [row for row in episodes_in if row["matched_episode_id"] is not None]
    ahead = [(row["p_started"] - row["confirmed_at"]).total_seconds() / 60 for row in matched
             if row["p_started"] is not None and row["confirmed_at"] < row["p_started"]]
    p_kinds: dict[str, int] = {}
    for row in window_p:
        if row["first_fail_at"] >= since:
            p_kinds[row["kind"]] = p_kinds.get(row["kind"], 0) + 1
    total_reads = int(reads["reads"])
    return {
        "generated_at": _iso(now), "days": days, "stage_thresholds": _thresholds_view(thresholds_from_env()),
        "coverage": {"registered_feeds": len(feed_hosts), "registered_feeds_curated": len(feed_hosts & curated),
                     "target_hosts": len(curated), "recording_hosts": int(reads["hosts"])},
        "infra_feeds": _infra_feeds(conn, feed_hosts, now),
        "candidates_now": len(listed), "candidates": listed[:CANDIDATE_LIST_MAX], "truncated": len(listed) > CANDIDATE_LIST_MAX,
        "candidate_sources": {"ahn_check": sum(1 for c in listed if c["ahn_check"]), "provider": sum(1 for c in listed if c["provider"])},
        "excluded_now": excluded, "stale_now": excluded["stale"],
        "suspects_now": len(open_a) - len(confirmed_open), "intermittent_now": intermittent,
        "held_windows": sum(held_by_reason.values()), "held_by_reason": held_by_reason, "infra_explained_windows": explained,
        "held_now": bool(latest and latest["held"]),
        "empty_ratio": empty_ratio, "empty_ratio_slots": slots,
        "reads": {"total": total_reads, "responding": int(reads["responding"]), "failed": int(reads["failed"]),
                  "refused": int(reads["refused"]), "held": int(reads["held"]),
                  "refused_ratio": round(int(reads["refused"]) / total_reads, 4) if total_reads else None,
                  "failure_ratio": round(int(reads["failed"]) / total_reads, 4) if total_reads else None,
                  "by_trigger": {"lookup": int(reads["lookup"]), "seed": int(reads["seed"])}, "not_yet_applied": int(pending)},
        "a_episodes": len(episodes_in), "a_episodes_by_kind": kinds, "a_ended_by": ended_by,
        "a_feed_connected": sum(1 for row in episodes_in if row["feed_connected"]),
        "a_matched_feed": len(matched),
        "a_ahead_of_feed": len(ahead), "a_ahead_of_feed_median_minutes": round(median(ahead), 1) if ahead else None,
        "a_likely_false_positive": sum(1 for row in episodes_in if row["feed_connected"] and row["ended_at"] is not None
                                       and not row["any_provider_problem"] and row["reads"] <= 2),
        "a_unverifiable": sum(1 for row in episodes_in if not row["feed_connected"]),
        "p_episodes": sum(p_kinds.values()), "p_episodes_by_kind": p_kinds,
        "notes": ["AHN check = AHN's own robots.txt reads from one declared vantage, updated only when a read happens.",
                  "Baseline and the empty ratio use today's baseline for past slots; provider periods ignore feed staleness.",
                  "A confirmed period with no read for 48 hours is counted as stale, not as a candidate.",
                  "Held-window counts come from raw events, kept 14 days; reads by trigger are daily totals."],
    }


def _thresholds_view(t: Thresholds) -> dict:
    return {"n_fail": t.n_fail, "t_min_minutes": int(t.t_min.total_seconds() // 60), "n_ok": t.n_ok}


def owner_summary(settings, days: int = 7) -> dict:
    from .db import connect

    with connect(settings) as conn:
        return board_summary(conn, days=days)
