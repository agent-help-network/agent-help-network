from __future__ import annotations

import hashlib
import hmac
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import NamedTuple
from uuid import UUID, uuid4

from . import adoption, background_reads, feedback, feed_robots, gov_registry, lookup_signals, status_scope
from .access_profile import cached_profile, pending_profile
from .db import connect, get_reporter_id
from .lookup_signals import LookupSignal
from .models import ObservationIn, OutcomeIn
from .official_paths import official_paths
from .provenance import operator_id
from .settings import Settings
from .status_refresh import ACTIVE_ITEM_TIME_FEEDS, OFFICIAL_STATUS_PAGES, SOURCES, register_source
from . import response_guide
from .triage import next_checks, recommendation


LOG = logging.getLogger(__name__)
PUBLIC_REPORTER_ID = UUID("b2d045dc-9791-4a27-9db4-3e469c63f57f")
PUBLIC_TASK_STEPS = {"site_open", "login", "human_verification", "service_use", "other"}
PUBLIC_DAILY_CAP = 100
PUBLIC_HOST_DAILY_CAP = 5
PUBLIC_AGGREGATE_TLDS = {
    "com", "org", "net", "gov", "edu", "io", "ai", "dev", "app", "co", "me", "us", "tv", "gg", "fm", "ly", "to", "sh", "cc",
    "info", "biz", "xyz", "tech", "cloud", "site", "online", "store", "shop", "news", "blog", "media", "eu", "uk", "de",
    "fr", "jp", "kr", "ca", "au", "in", "br", "es", "it", "nl", "se", "ch", "ru", "cn", "tw", "hk", "sg", "nz", "ie", "be",
    "at", "pl", "mx", "ar", "za", "no", "dk", "fi", "pt", "cz", "il", "tr", "id", "th", "vn", "ph", "my",
    # Added 2026-10-19 for curated hosts that were silently left unanswered (notion.so, who.int, mastodon.social,
    # linktr.ee, deepmind.google, cloud.microsoft, ...). A two-label name under any of these is its registrant's apex;
    # deeper names stay ineligible as everywhere else. Spam-heavy TLDs with no curated host are still left out.
    "int", "so", "social", "markets", "ee", "pro", "one", "mobi", "gl", "ms", "mp", "page",
    "google", "goog", "microsoft", "apple", "aws",
}
# Registrable apexes under two-label public suffixes (bbc.co.uk, naver.co.kr); no full PSL dependency.
PUBLIC_SUFFIXES_2 = {
    "co.uk", "org.uk", "ac.uk", "gov.uk", "me.uk", "police.uk", "co.kr", "or.kr", "go.kr", "ne.kr", "ac.kr", "co.jp", "ne.jp", "or.jp",
    "go.jp", "ac.jp", "com.au", "net.au", "org.au", "gov.au", "edu.au", "com.br", "org.br", "gov.br", "co.in", "net.in",
    "org.in", "gov.in", "com.tw", "com.cn", "net.cn", "org.cn", "gov.cn", "com.hk", "com.sg", "com.mx", "com.ar", "co.za",
    "co.nz", "org.nz", "com.tr", "co.il", "com.my", "co.th", "com.vn", "com.ph", "co.id", "com.pl", "com.es", "com.pt",
}
# Bounded registry of looked-up apex hosts; idle rows are pruned by platform_metrics.prune_analytics.
QUERIED_HOST_CAP = 10000
ACCESS_PROFILE_NOTE = ("Declared crawler policy read from the host's own robots.txt and llms.txt. It is not an access guarantee: "
                       "user-directed agents may be treated differently by CDNs, and a permissive file does not prove a page is reachable.")


def aggregate_eligible_host(site_host: str) -> bool:
    """Keep account/tenant subdomains out of lookup telemetry, public totals and policy reads."""
    labels = site_host.split(".")
    if labels[0].startswith("xn--"):
        return False
    if len(labels) == 2:
        return labels[1] in PUBLIC_AGGREGATE_TLDS
    return len(labels) == 3 and ".".join(labels[1:]) in PUBLIC_SUFFIXES_2


def policy_host(site_host: str) -> str:
    """The host whose declared policy, provider status, official paths and lookup counts answer for site_host.

    A single leading "www." label in front of an eligible apex names the same site as that apex, so
    www.nytimes.com is answered from nytimes.com. Every other host, including other subdomains and
    "www." in front of a non-eligible name, is returned unchanged. A "www" that is itself the
    registrable label (www.co.uk) is an eligible apex already and is never cut down to a public
    suffix. Reports stay keyed by site_host.
    """
    if site_host.startswith("www.") and not aggregate_eligible_host(site_host):
        apex = site_host[4:]
        if aggregate_eligible_host(apex):
            return apex
    return site_host


class ServiceError(Exception):
    def __init__(self, code: str, status: int):
        self.code = code
        self.status = status


def _request_hash(kind: str, parent_id: UUID | None, data: ObservationIn | OutcomeIn) -> str:
    # Hash the caller's stated fields. Server-generated timestamps/defaults must
    # not turn an otherwise identical retry with a stable key into a conflict.
    body = {"kind": kind, "parent_id": str(parent_id) if parent_id else None, "data": data.model_dump(mode="json", exclude_unset=True)}
    return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _withdrawal_token(settings: Settings, observation_id: UUID, idempotency_key: UUID) -> str:
    message = f"withdraw:{observation_id}:{idempotency_key}".encode()
    return hmac.new(settings.key_pepper.encode(), message, hashlib.sha256).hexdigest()


RECEIVED_CHANNELS = frozenset({"rest", "mcp", "web"})


def submit(settings: Settings, key: str, data: ObservationIn | OutcomeIn, parent_id: UUID | None = None, *,
           channel: str = "rest"):
    if settings.test_mode and (not data.synthetic or (isinstance(data, ObservationIn) and not data.site_host.endswith(".test"))):
        raise ServiceError("synthetic_test_only", 403)
    public_intake = settings.public_mode and settings.reporting_enabled and not settings.test_mode and not key and parent_id is None and isinstance(data, ObservationIn)
    if public_intake and (data.synthetic or data.site_host.endswith(".test") or data.task_step not in PUBLIC_TASK_STEPS):
        raise ServiceError("invalid_public_observation", 400)
    if not settings.test_mode and not public_intake:
        raise ServiceError("real_submissions_not_enabled", 503)
    digest = _request_hash("outcome" if parent_id else "observation", parent_id, data)
    with connect(settings) as conn:
        if public_intake:
            flag = conn.execute("SELECT enabled FROM service_flags WHERE name='public_intake' FOR SHARE").fetchone()
            if not flag or not flag["enabled"]:
                raise ServiceError("reporting_paused", 503)
        reporter_id = PUBLIC_REPORTER_ID if public_intake else get_reporter_id(conn, key, settings)
        if reporter_id is None:
            raise ServiceError("invalid_api_key", 401)
        # Serializes retries from one reporter without making an unlocked SELECT race.
        if conn.execute("SELECT id FROM reporters WHERE id = %s FOR UPDATE", (reporter_id,)).fetchone() is None:
            raise ServiceError("reporter_unavailable", 503)
        existing = conn.execute(
            "SELECT s.request_hash, s.observation_id, o.incident_id, o.review_state, o.synthetic FROM report_submissions s JOIN observations o ON o.id=s.observation_id WHERE s.reporter_id=%s AND s.idempotency_key=%s",
            (reporter_id, data.idempotency_key),
        ).fetchone()
        if existing:
            if existing["request_hash"] != digest:
                raise ServiceError("idempotency_conflict", 409)
            token = _withdrawal_token(settings, existing["observation_id"], data.idempotency_key) if public_intake else None
            return _receipt(existing["observation_id"], existing["incident_id"], existing["review_state"], existing["synthetic"], token), False
        if public_intake:
            global_count = conn.execute(
                "INSERT INTO public_intake_daily (day,reports) VALUES (CURRENT_DATE,1) ON CONFLICT (day) DO UPDATE SET reports=public_intake_daily.reports+1 WHERE public_intake_daily.reports < %s RETURNING reports",
                (PUBLIC_DAILY_CAP,),
            ).fetchone()
            # www.<apex> shares its apex's daily slots, so splitting reports across the two
            # names cannot double the cap; the observation itself keeps its exact site_host.
            host_count = conn.execute(
                "INSERT INTO public_intake_host_daily (day,site_host,reports) VALUES (CURRENT_DATE,%s,1) ON CONFLICT (day,site_host) DO UPDATE SET reports=public_intake_host_daily.reports+1 WHERE public_intake_host_daily.reports < %s RETURNING reports",
                (policy_host(data.site_host), PUBLIC_HOST_DAILY_CAP),
            ).fetchone() if global_count else None
            if not global_count or not host_count:
                raise ServiceError("report_rate_limited", 429)
        else:
            recent_count = conn.execute(
                "SELECT count(*) AS n FROM observations WHERE reporter_id=%s AND received_at >= now() - interval '1 hour'",
                (reporter_id,),
            ).fetchone()["n"]
            if recent_count >= settings.max_reports_per_hour:
                raise ServiceError("report_rate_limited", 429)
        if parent_id:
            parent = conn.execute(
                "SELECT o.incident_id, o.observed_at, o.synthetic, i.site_host FROM observations o JOIN incidents i ON i.id=o.incident_id WHERE o.id=%s AND o.reporter_id=%s AND o.withdrawn_at IS NULL",
                (parent_id, reporter_id),
            ).fetchone()
            if not parent:
                raise ServiceError("not_found", 404)
            if data.observed_at < parent["observed_at"] or data.synthetic != parent["synthetic"]:
                raise ServiceError("invalid_outcome_sequence", 400)
            if settings.test_mode and not parent["site_host"].endswith(".test"):
                raise ServiceError("synthetic_test_only", 403)
            incident_id = parent["incident_id"]
        else:
            incident_id = conn.execute(
                "INSERT INTO incidents (site_host, task_step, error_class) VALUES (%s,%s,%s) ON CONFLICT (site_host,task_step,error_class) DO UPDATE SET site_host=EXCLUDED.site_host RETURNING id",
                (data.site_host, data.task_step, data.error_class.value),
            ).fetchone()["id"]
        # Provenance: this operator received the report under its own id, through this interface.
        observation_id = uuid4()
        row = conn.execute(
            """INSERT INTO observations
            (id,incident_id,reporter_id,observed_at,agent_family,attempt_actor,attempt_env,outcome,challenge_vendor,challenge_kind,synthetic,supersedes_id,
             origin_operator,origin_observation_id,received_channel)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id,review_state""",
            (observation_id, incident_id, reporter_id, data.observed_at, data.agent_family.value, data.attempt_actor.value,
             data.attempt_env.value, data.outcome.value, data.challenge_vendor.value, data.challenge_kind.value,
             data.synthetic, parent_id, operator_id(), observation_id, channel if channel in RECEIVED_CHANNELS else "rest"),
        ).fetchone()
        conn.execute(
            "INSERT INTO report_submissions (reporter_id,idempotency_key,request_hash,observation_id) VALUES (%s,%s,%s,%s)",
            (reporter_id, data.idempotency_key, digest, row["id"]),
        )
        conn.execute("INSERT INTO audit_events (event_type,record_id) VALUES (%s,%s)", ("observation_created", row["id"]))
        token = _withdrawal_token(settings, row["id"], data.idempotency_key) if public_intake else None
        return _receipt(row["id"], incident_id, row["review_state"], data.synthetic, token), True


def _receipt(observation_id, incident_id, review_state, synthetic, withdrawal_token=None):
    result = {"observation_id": str(observation_id), "incident_id": str(incident_id), "review_state": review_state, "synthetic": synthetic}
    if withdrawal_token:
        result["withdrawal_token"] = withdrawal_token
    return result


def withdraw_public(settings: Settings, observation_id: UUID, token: str):
    if not settings.public_mode or len(token) != 64:
        raise ServiceError("not_found", 404)
    with connect(settings) as conn:
        row = conn.execute(
            "SELECT idempotency_key FROM report_submissions WHERE reporter_id=%s AND observation_id=%s",
            (PUBLIC_REPORTER_ID, observation_id),
        ).fetchone()
        if not row or not hmac.compare_digest(token, _withdrawal_token(settings, observation_id, row["idempotency_key"])):
            raise ServiceError("not_found", 404)
        updated = conn.execute(
            "UPDATE observations SET withdrawn_at=now() WHERE id=%s AND withdrawn_at IS NULL RETURNING id",
            (observation_id,),
        ).fetchone()
        if updated:
            conn.execute("INSERT INTO audit_events (event_type,record_id) VALUES (%s,%s)", ("observation_withdrawn", observation_id))
    return {"withdrawn": True}


def withdraw(settings: Settings, key: str, observation_id: UUID):
    with connect(settings) as conn:
        reporter_id = get_reporter_id(conn, key, settings)
        if reporter_id is None:
            raise ServiceError("invalid_api_key", 401)
        row = conn.execute(
            "UPDATE observations SET withdrawn_at=now() WHERE id=%s AND reporter_id=%s AND withdrawn_at IS NULL RETURNING id",
            (observation_id, reporter_id),
        ).fetchone()
        if not row:
            exists = conn.execute("SELECT id FROM observations WHERE id=%s AND reporter_id=%s", (observation_id, reporter_id)).fetchone()
            if not exists:
                raise ServiceError("not_found", 404)
            return {"withdrawn": True}
        conn.execute("INSERT INTO audit_events (event_type,record_id) VALUES (%s,%s)", ("observation_withdrawn", observation_id))
    return {"withdrawn": True}


def _record_lookup(conn, settings: Settings, host: str, error_class: str | None, *, public: bool) -> tuple[bool, bool, bool]:
    """Register the host, count the lookup and gate discovery inside a savepoint; never raises.

    Returns (registered, mapped_source, discovery_due). Only local database writes happen here:
    no third-party host is contacted on the lookup path, and a failed write is rolled back without
    failing the lookup.
    """
    registered = mapped_source = discovery_due = False
    eligible = aggregate_eligible_host(host)
    try:
        with conn.transaction():
            if public and (eligible or host in SOURCES):
                # Record demand only for public apex hosts. A bounded registry prevents
                # arbitrary tenant names in hostnames from becoming permanent public data.
                existing = conn.execute(
                    "UPDATE queried_hosts SET last_seen_at=now() WHERE site_host=%s RETURNING site_host",
                    (host,),
                ).fetchone()
                if existing or host in SOURCES or conn.execute("SELECT count(*) AS n FROM queried_hosts").fetchone()["n"] < QUERIED_HOST_CAP:
                    if not existing:
                        conn.execute(
                            "INSERT INTO queried_hosts (site_host) VALUES (%s) ON CONFLICT (site_host) DO UPDATE SET last_seen_at=now()",
                            (host,),
                        )
                    registered = True
                    mapped_source = register_source(conn, host)
                    if not mapped_source and host not in SOURCES:
                        # At most one discovery attempt per host per day. The worker records the attempt
                        # time (last_discovery_at) when it actually runs the discovery.
                        discovery_due = bool(conn.execute(
                            """SELECT 1 AS due FROM queried_hosts
                               WHERE site_host=%s AND (last_discovery_at IS NULL OR last_discovery_at < now() - interval '1 day')
                               AND NOT EXISTS (SELECT 1 FROM status_sources WHERE site_host=%s AND enabled=true)""",
                            (host, host),
                        ).fetchone())
            if settings.collect_lookup_metrics and eligible and (not public or registered):
                conn.execute(
                    "INSERT INTO lookup_metrics (day,site_host,error_class,checks) VALUES (CURRENT_DATE,%s,%s,1) ON CONFLICT (day,site_host,error_class) DO UPDATE SET checks=lookup_metrics.checks+1",
                    (host, error_class or "unspecified"),
                )
    except Exception:
        # Registration and metrics are bookkeeping: a failure must not turn a lookup into an error.
        LOG.warning("lookup_record_unavailable")
        return False, False, False
    return registered, mapped_source, discovery_due


def _record_signal(conn, settings: Settings, host: str, error_class: str | None, failure_stage: str | None,
                   signal: LookupSignal, action: str, *, public: bool, registered: bool, now: datetime,
                   result: dict | None = None) -> tuple | None:
    """Count the lookup's optional codes per host and hour inside a savepoint; never raises (D47).

    Same gate as lookup_metrics (eligible apex host, inside the lookup register in public mode, metrics
    switch on), minus example hosts. Database writes only; nothing here reaches the answer.

    The answered lookup also goes into the daily adoption counts (D49, ahn/adoption.py) under the metrics switch alone:
    it holds no host, so it does not need the host gate. Here it is only prepared (a read, run first because it asks
    whether this source already has a digest row today); the caller writes the returned row (adoption.write) as the
    last statement before a commit, so the one hot daily row is never locked for longer than that statement.
    """
    if not settings.collect_lookup_metrics:
        return None
    gated = aggregate_eligible_host(host) and (not public or registered)
    pending = None
    if result is not None:
        pending = _prepare_adoption(conn, settings, host, signal, result, now, count_source=bool(gated))
    if not gated:
        return pending
    if host in lookup_signals.EXAMPLE_HOSTS:
        return pending
    try:
        with conn.transaction():
            # Never wait on a table lock for the sake of a count: give up after 200 ms (the setting ends with the savepoint).
            conn.execute("SET LOCAL lock_timeout = '200ms'")
            lookup_signals.record(conn, settings.key_pepper, host, error_class, failure_stage, signal, action, now)
            conn.execute("SET LOCAL lock_timeout = DEFAULT")
    except Exception:
        # Shadow bookkeeping: a failure (for example a migration not applied yet) must not touch the lookup.
        LOG.warning("lookup_signal_unavailable")
    return pending


def _prepare_adoption(conn, settings: Settings, host: str, signal: LookupSignal, result: dict, now: datetime, *,
                      count_source: bool) -> tuple | None:
    """The adoption row for this answered lookup (a lock-free read in a savepoint); never raises (D49)."""
    try:
        with conn.transaction():
            return adoption.prepare(conn, settings.key_pepper, host, signal, result, now, count_source=count_source)
    except Exception:
        adoption._count_drop()
        LOG.warning("lookup_adoption_unavailable")
        return None


def _queue_reads(conn, jobs: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """Queue background reads inside a savepoint; never raises. Returns the jobs now in the queue."""
    if not jobs:
        return []
    try:
        with conn.transaction():
            return background_reads.enqueue(conn, jobs)
    except Exception:
        LOG.warning("background_reads_enqueue_unavailable")
        return []


def _retry_after(conn, host: str) -> int | None:
    """When to ask again for a queued profile read, or None (never fails the lookup)."""
    try:
        with conn.transaction():
            return background_reads.estimate_wait(conn, "access_profile", host)
    except Exception:
        LOG.warning("background_reads_estimate_unavailable")
        return None


def _provider_scope(row: dict) -> dict:
    """The stored scope of a Statuspage feed as provider_status fields (D43); a field that was not read is left out.

    source_time (D54) is "active_items" when the row's feed address is one whose update time is defined by the
    incidents and maintenances it lists as active (status_refresh.ACTIVE_ITEM_TIME_FEEDS: Instatus, Google Gmail), and
    left out otherwise, including when the address is not known to be one."""
    out: dict = {}
    if row.get("api_url") in ACTIVE_ITEM_TIME_FEEDS:
        out["source_time"] = "active_items"
    if row.get("active_incidents") is not None:
        out["active_incidents"] = row["active_incidents"]
        out["max_incident_impact"] = row["max_incident_impact"]
        if row.get("incident_updated_at") is not None:
            out["incident_updated_at"] = row["incident_updated_at"].astimezone(timezone.utc)
    if row.get("active_maintenances") is not None:
        out["active_maintenances"] = row["active_maintenances"]
    basis = status_scope.basis(row.get("indicator"), row.get("active_incidents"), row.get("active_maintenances"))
    if basis:
        out["basis"] = basis
    if row.get("affected"):
        out["affected"] = list(row["affected"])[: status_scope.MAX_AFFECTED]
    return out


def _atom_scope(row: dict) -> dict:
    """provider_status fields of a reading taken from a page's Atom history (D52): only what the feed says. No indicator,
    no basis, no impact: a count of zero is left out (nothing listed is unknown, not healthy)."""
    out: dict = {"source_kind": "history_atom"}
    if row.get("active_incidents"):
        out["active_incidents"] = row["active_incidents"]
        if row.get("incident_updated_at") is not None:
            out["incident_updated_at"] = row["incident_updated_at"].astimezone(timezone.utc)
    if row.get("active_maintenances") is not None:
        out["active_maintenances"] = row["active_maintenances"]
    if row.get("window_blind"):
        out["window_blind"] = True
    return out


_FEED_SOURCE_SQL = """SELECT s.source_name,s.status_page_url,s.api_url,c.indicator,c.source_updated_at,
                          c.fetched_at,c.last_error_at,c.last_error_detail,c.failures,
                          c.active_incidents,c.max_incident_impact,c.incident_updated_at,
                          c.active_maintenances,c.affected,c.affected_regional,c.feed_source,c.window_blind
                   FROM status_sources s LEFT JOIN status_cache c ON c.site_host=s.site_host
                   WHERE s.site_host=%s AND s.enabled=true"""


class FeedView(NamedTuple):
    """What the provider layer of an answer reads from the stored rows (see feed_view)."""
    row: dict | None              # the stored source row, shown or not
    provider_status: dict | None  # the statement to show; None when there is no source or it may not be shown
    partial: bool
    regional: bool
    queue_decision: bool          # a robots decision (or the Atom history) is still to be read for this feed
    blocked_page: tuple | None    # (name, url) of a page whose feed its own robots.txt does not allow: a link, never a reading


def feed_view(conn, host: str, now: datetime) -> FeedView:
    """The provider layer of an answer, from database rows only: no request, no queue insert, no write. check() and the
    home page's real-answer card both read the feed through this one function, so they cannot disagree about it."""
    source = conn.execute(_FEED_SOURCE_SQL, (host,)).fetchone()
    if not source:
        return FeedView(None, None, False, False, False, None)
    # D50: a feed is shown only while its host's robots.txt allows it (database read, no request). With no
    # decision yet only a host on the interim list (read by hand on 2026-10-22) keeps its statement on
    # show, and the read that decides is queued; a host whose robots.txt disallows it, or could not be
    # read, shows none and links to the status page; so does a host with no decision yet that is not on the
    # list (a link, never a reading).
    feed_host = feed_robots.host_and_path(source["api_url"])[0]
    decision = feed_robots.feed_state(conn, source["api_url"])
    if not feed_robots.shown(decision, feed_host):
        # D52: where the API is disallowed and the page's Atom history is allowed, the open announcements
        # it lists are shown (information only) once that feed has been read.
        atom = decision == "disallowed" and feed_robots.history_allowed(conn, source["api_url"])
        if not (atom and source["feed_source"] == "history_atom"):
            blocked_page = (source["source_name"], source["status_page_url"])  # a link only; the feed is not read
            return FeedView(source, None, False, False, decision == "undecided" or atom, blocked_page)
    fetched_at = source["fetched_at"]
    # The age of the last successful statement decides; a later failed read only adds error_detail (D46).
    freshness, failed_later = background_reads.feed_freshness(
        fetched_at, source["last_error_at"], now, source["last_error_detail"])
    from_atom = source["feed_source"] == "history_atom"
    provider_status = {
        "source_name": source["source_name"], "source_url": source["status_page_url"],
        "indicator": None if from_atom else source["indicator"], "source_updated_at": source["source_updated_at"],
        "fetched_at": fetched_at, "freshness": freshness,
        "error_detail": source["last_error_detail"] if failed_later else None,
    }
    provider_status.update(_atom_scope(source) if from_atom else _provider_scope(source))
    partial = status_scope.scope_partial(
        source["indicator"], source["active_incidents"], source["max_incident_impact"], source["affected_regional"])
    return FeedView(source, provider_status, partial, source["affected_regional"] is True, False, None)


def check(settings: Settings, site_host: str, error_class: str | None = None, task_step: str | None = None,
          window_hours: int = 24, failure_stage: str | None = None, signal: LookupSignal | None = None,
          agent_vendor: str | None = None, issue_feedback: bool = False):
    """Answer a lookup from the database alone.

    ``issue_feedback`` is off unless the caller asks: only the counted lookup route (GET /v1/incidents/check, which the
    MCP check_incident tool calls) passes True, and that route sends the answer with ``Cache-Control: no-store``. Any
    other caller (the search page, the incident detail) never gets a one-time ``feedback_token`` (D58).

    Nothing on this path contacts the target host or a status provider: reads that are missing or
    out of date are queued (ahn/background_reads.py) and the cached value is returned as it is,
    marked ``pending`` or ``stale``.

    ``signal`` carries the optional lookup codes and the caller's source (D47). It is only written down,
    in a savepoint after the answer is built; it never changes the answer.

    ``agent_vendor`` (D48) is an accepted value (triage.accepted_vendor) or None. It only chooses the recommendation
    and is never stored; AHN_VENDOR_AWARE_BLOCK=0 ignores it.
    """
    test_query = settings.test_mode and site_host.endswith(".test")
    public = settings.public_mode and not test_query
    where = ["i.site_host=%s", "o.withdrawn_at IS NULL"]
    params: list = [site_host]
    if test_query:
        where.extend(["o.synthetic=true", "o.review_state<>'hidden'"])
    else:
        where.extend(["o.synthetic=false", "o.review_state='published'", "i.visibility='published'"])
    if error_class:
        where.append("i.error_class=%s")
        params.append(error_class)
    if task_step:
        where.append("i.task_step=%s")
        params.append(task_step)
    where.append("o.observed_at >= %s")
    params.append(datetime.now(timezone.utc) - timedelta(days=7))
    sql = f"SELECT o.observed_at,o.attempt_actor,o.attempt_env,o.outcome,o.reporter_id,i.error_class,i.task_step FROM observations o JOIN incidents i ON i.id=o.incident_id WHERE {' AND '.join(where)} ORDER BY o.observed_at DESC LIMIT 500"
    # Reports are keyed by the exact host; every host-level layer below
    # (registry, provider status, lookup counts, declared policy, official paths)
    # answers for the site, so www.<apex> reads and counts as <apex>.
    host = policy_host(site_host)
    eligible = aggregate_eligible_host(host)
    registered = mapped_source = False
    now = datetime.now(timezone.utc)
    provider_status = None
    robots_blocked_page = None  # (name, url) of a status page whose feed its own robots.txt does not allow (D50)
    provider_partial = False
    provider_regional = False
    provider_discovery_attempted_at = None
    lookup_activity = None
    # Layer 1: the host's own declared policy; layer 2: curated official paths.
    # Both are independent of reports, so a lookup answers before any observation exists.
    access_profile = None
    profile_not_read = False
    profile_due = False
    host_facts = None
    jobs: list[tuple[str, str]] = []
    official = official_paths(host) if public else None
    # One connection: the bookkeeping writes (committed at once, so their row locks are not held for
    # the rest of the lookup), the reads the answer needs, and the queue inserts. Database round trips only.
    retry_after = None
    queued: list[tuple[str, str]] = []
    count_row = None
    with connect(settings) as conn:
        if (public and (eligible or host in SOURCES)) or (not settings.public_mode and settings.collect_lookup_metrics and eligible):
            registered, mapped_source, discovery_due = _record_lookup(conn, settings, host, error_class, public=public)
            conn.commit()
            if discovery_due:
                jobs.append(("status_discovery", host))
        rows = conn.execute(sql, params).fetchall()
        if public:
            discovery = conn.execute(
                "SELECT last_discovery_at FROM queried_hosts WHERE site_host=%s",
                (host,),
            ).fetchone()
            if discovery:
                provider_discovery_attempted_at = discovery["last_discovery_at"]
            feed = feed_view(conn, host, now)
            provider_status, provider_partial, provider_regional = feed.provider_status, feed.partial, feed.regional
            robots_blocked_page = feed.blocked_page
            if feed.row and mapped_source and (provider_status is not None or feed.queue_decision):
                # Stale-while-revalidate: answer with the cached statement; once it would read as
                # stale by age (or was never read), refresh it behind the lookup, not again within
                # FEED_RETRY_MINUTES of the last attempt. An active indicator is due after
                # FEED_RETRY_MINUTES already, and a failing feed backs off (the cron uses the same
                # rule, background_reads.feed_next_due), so a resolved problem stops reading as
                # active within about that long. Curated feeds are also read by the cron (D44).
                due = background_reads.feed_next_due(feed.row, timedelta(hours=background_reads.FEED_STALE_HOURS))
                if due is None or due <= now:
                    jobs.append(("status_feed", host))
            if eligible:
                volume = conn.execute(
                    """SELECT COALESCE(sum(checks) FILTER (WHERE day >= CURRENT_DATE - 6),0) AS recent,
                              COALESCE(sum(checks) FILTER (WHERE day BETWEEN CURRENT_DATE - 13 AND CURRENT_DATE - 7),0) AS previous
                       FROM lookup_metrics WHERE site_host=%s AND day >= CURRENT_DATE - 13""",
                    (host,),
                ).fetchone() or {"recent": 0, "previous": 0}
                recent, previous = volume["recent"], volume["previous"]
                if recent >= 1:
                    lookup_activity = {
                        "period_days": 7,
                        "volume_band": "50+" if recent >= 50 else "10-49" if recent >= 10 else "5-9" if recent >= 5 else "1-4",
                        "trend": "increased" if previous >= 10 and recent >= 20 and recent >= previous * 2 else None,
                        "unit": "requests_not_agents",
                    }
                try:
                    with conn.transaction():
                        access_profile, profile_due = cached_profile(conn, host, now=now)
                except Exception:
                    # The declared-policy layer must never turn a lookup into an error.
                    LOG.warning("access_profile_cache_unavailable")
                    access_profile, profile_due = None, False
                else:
                    # Only hosts inside the bounded lookup register (QUERIED_HOST_CAP) get a read queued.
                    if profile_due and registered:
                        jobs.append(("access_profile", host))
                if gov_registry.enabled():
                    # Registry facts (a government list, a government-only suffix) are database reads like the
                    # layers above; a failure leaves the field out instead of failing the lookup.
                    try:
                        with conn.transaction():
                            host_facts = gov_registry.host_facts(conn, host)
                    except Exception:
                        LOG.warning("host_facts_unavailable")
                        host_facts = None
            else:
                # Subdomain or tenant host: say so instead of leaving the layer silently empty.
                profile_not_read = True
        queued = _queue_reads(conn, jobs)
        if ("access_profile", host) in queued:
            retry_after = _retry_after(conn, host)
        conn.commit()  # the queued rows are visible before the worker is woken
        if queued:
            background_reads.wake()
        if access_profile is None and profile_due:
            # First lookup of this host: nothing is cached yet. retry_after_seconds only when a read is queued.
            access_profile = pending_profile(host, retry_after)
        elif access_profile is not None and retry_after is not None and access_profile["freshness"] == "pending":
            # Only failed attempts so far, and the retry is due: a read is queued again.
            access_profile = {**access_profile, "retry_after_seconds": retry_after}
        if access_profile is not None:
            access_profile = {**access_profile, "note": ACCESS_PROFILE_NOTE}
        recent = [row for row in rows if row["observed_at"] >= now - timedelta(hours=window_hours)]
        stale = bool(rows) and not recent
        selected = recent if recent else (rows[:20] if stale else [])
        failures = sum(row["outcome"] in {"blocked", "error"} for row in selected)
        successes = sum(row["outcome"] == "passed" for row in selected)
        sources = len({row["reporter_id"] for row in selected})
        if stale:
            level, code, summary = "stale_reports", "OLDER_REPORTS_STATUS_UNKNOWN", "Older reports exist, but current site status is unknown."
        elif not recent:
            level, code, summary = "no_reports", "NO_MATCHING_REPORTS_STATUS_UNKNOWN", "No matching reports in the 7-day data horizon. The site's current status is unknown."
        elif failures and successes and max(row["observed_at"] for row in recent if row["outcome"] == "passed") > min(row["observed_at"] for row in recent if row["outcome"] in {"blocked", "error"}):
            level, code, summary = "later_success_observed", "LATER_SUCCESS_NOT_GENERAL_RECOVERY", "A later successful attempt was reported. This does not establish general recovery."
        elif sources == 1:
            level, code, summary = "single_observation", "ONE_SOURCE_UNCONFIRMED", "One source reported attempts. This is unconfirmed by another source."
        else:
            level, code, summary = "multiple_observations", "MULTIPLE_SOURCES_INDEPENDENCE_UNKNOWN", "Multiple sources reported attempts. Their independence is not verified."
        observations = []
        for row in selected[:20]:
            observed_at = row["observed_at"]
            if not test_query:
                observed_at = observed_at.replace(minute=0, second=0, microsecond=0)
            observations.append({
                "observed_at": observed_at.isoformat().replace("+00:00", "Z"),
                "attempt_actor": row["attempt_actor"], "attempt_env": row["attempt_env"],
                "outcome": row["outcome"], "error_class": row["error_class"],
                "task_step": row["task_step"], "synthetic": test_query,
            })
        official_page = ((OFFICIAL_STATUS_PAGES.get(host) or robots_blocked_page)
                         if settings.public_mode and not test_query and provider_status is None else None)
        result = {
            "site_host": site_host, "generated_at": now.isoformat().replace("+00:00", "Z"),
            "synthetic": test_query, "window_hours": window_hours, "data_horizon_hours": 168,
            "current_site_status": "unknown", "evidence_level": level,
            "summary_code": code, "summary": summary, "failures": failures,
            "successes": successes, "reporting_sources": sources, "observations": observations,
            "provider_status": provider_status,
            "provider_source_state": "feed_connected" if provider_status else "official_page_only" if official_page else "not_verified_by_current_methods",
            "provider_discovery_attempted_at": provider_discovery_attempted_at,
            "official_status_page": {"source_name": official_page[0], "source_url": official_page[1]} if official_page else None,
            "lookup_activity": lookup_activity,
            "access_profile": access_profile,
            "official_paths": official,
            "recommendation": recommendation(error_class, provider_status, access_profile, official, profile_not_read=profile_not_read,
                                             provider_regional=provider_regional, now=now, agent_vendor=agent_vendor,
                                             vendor_aware=settings.vendor_aware_block, failure_stage=failure_stage,
                                             proceed_without_failure=settings.proceed_without_failure),
            "failure_stage": failure_stage,
            "next_checks": next_checks(error_class, provider_status, level, failure_stage, access_profile, official,
                                       profile_not_read=profile_not_read, host_facts=host_facts,
                                       provider_partial=provider_partial, now=now, agent_vendor=agent_vendor,
                                       vendor_aware=settings.vendor_aware_block,
                                       proceed_without_failure=settings.proceed_without_failure),
        }
        if host_facts is not None:
            result["host_facts"] = host_facts
        if settings.response_guide:
            # D51: computed from the finished answer, after everything above; it changes none of it. A failure here
            # leaves the three keys out: the answer is still the one before D51 and the lookup record below still runs.
            try:
                result.update(response_guide.guide(result))
            except Exception:
                LOG.warning("response_guide_unavailable")
        if (issue_feedback and settings.lookup_feedback and settings.collect_lookup_metrics and error_class and not test_query
                and eligible and host not in lookup_signals.EXAMPLE_HOSTS and (not public or registered)):
            # D58: added last, after the answer is decided; a signed value, so nothing is written here. A lookup that is not
            # counted (operator network, X-AHN-Test) still gets a token so the flow can be tried, but its feedback is not counted.
            try:
                result["feedback_token"] = feedback.feedback_field(settings.key_pepper, host, result["recommendation"]["action"],
                                                             signal.client_kind if signal is not None else "rest_other",
                                                             signal is not None, now)
            except Exception:
                LOG.warning("feedback_token_unavailable")
        if signal is not None:
            count_row = _record_signal(conn, settings, host, error_class, failure_stage, signal,
                                          result["recommendation"]["action"], public=public, registered=registered,
                                          now=now, result=result)
            # Its own short transaction on this connection: one upsert, committed at once, so the daily row's lock is not
            # held for the rest of the request.
            adoption.write(conn, count_row)
            conn.commit()
    return result

HOME_EXAMPLE_HOST = "github.com"


def home_example(settings: Settings, site_host: str = HOME_EXAMPLE_HOST) -> dict | None:
    """The home page's "real answer" card: the answer a lookup of one curated host gives from what is already stored.

    Database SELECTs only. It queues no read, records no lookup, signal or adoption row and contacts no host, so a home
    page view never makes a new external read. Returns None (the card is left out) when nothing usable is cached: no
    public mode, no response guide, or a host never read or still pending. The provider layer comes from feed_view,
    the function check() uses (D50 gate, D52 Atom history), so the card is the answer a lookup gives. Nothing here is a
    fixed value: every field comes from the cache rows.
    """
    if not (settings.public_mode and settings.response_guide):
        return None
    now = datetime.now(timezone.utc)
    official = official_paths(site_host)
    with connect(settings) as conn:
        access_profile, _due = cached_profile(conn, site_host, now=now)
        if access_profile is None or access_profile["freshness"] == "pending" or access_profile["fetched_at"] is None:
            return None
        feed = feed_view(conn, site_host, now)
    provider_status, provider_regional = feed.provider_status, feed.regional
    official_page = (OFFICIAL_STATUS_PAGES.get(site_host) or feed.blocked_page) if provider_status is None else None
    verdict = recommendation(None, provider_status, access_profile, official, profile_not_read=False,
                             provider_regional=provider_regional, now=now, agent_vendor=None,
                             vendor_aware=settings.vendor_aware_block,
                             proceed_without_failure=settings.proceed_without_failure)
    answer = response_guide.guide({"site_host": site_host, "recommendation": verdict, "access_profile": access_profile,
                                   "provider_status": provider_status, "official_paths": official,
                                   "official_status_page": ({"source_name": official_page[0], "source_url": official_page[1]}
                                                            if official_page else None)})
    action = answer["next_action"]
    status = provider_status["indicator"] if provider_status and provider_status.get("indicator") else None
    return {
        "site_host": site_host,
        "summary_line": answer["summary_line"],
        "do": action["do"],
        "url": (action.get("url") or "").removeprefix("https://").removeprefix("http://").rstrip("/") or None,
        "confidence": verdict.get("confidence"),
        "robots_status": access_profile["robots_status"],
        "llms_txt_status": access_profile["llms_txt_status"],
        "status": status if provider_status else None,
        # Named only when a statement is shown; an Atom reading has no indicator, only the open announcements it lists.
        "provider_name": provider_status["source_name"] if provider_status else None,
        "open_incidents": provider_status.get("active_incidents") or 0 if provider_status else 0,
        "read_on": access_profile["fetched_at"].astimezone(timezone.utc).strftime("%Y-%m-%d"),
    }


def public_incidents(settings: Settings, site_host: str | None = None, error_class: str | None = None, limit: int = 20):
    where = ["i.visibility='published'", "o.review_state='published'", "o.synthetic=false", "o.withdrawn_at IS NULL", "o.observed_at >= now() - interval '7 days'"]
    params: list = []
    if site_host:
        where.append("i.site_host=%s")
        params.append(site_host)
    if error_class:
        where.append("i.error_class=%s")
        params.append(error_class)
    params.append(limit)
    sql = f"SELECT i.id,i.site_host,i.task_step,i.error_class,count(*) AS observations,max(o.observed_at) AS latest_at FROM incidents i JOIN observations o ON o.incident_id=i.id WHERE {' AND '.join(where)} GROUP BY i.id ORDER BY latest_at DESC LIMIT %s"
    with connect(settings) as conn:
        rows = conn.execute(sql, params).fetchall()
    return [{**row, "id": str(row["id"]), "latest_at": row["latest_at"].replace(minute=0, second=0, microsecond=0).isoformat()} for row in rows]


def public_incident(settings: Settings, incident_id: UUID):
    with connect(settings) as conn:
        row = conn.execute("SELECT id,site_host,task_step,error_class FROM incidents WHERE id=%s AND visibility='published'", (incident_id,)).fetchone()
    if not row:
        raise ServiceError("not_found", 404)
    result = check(settings, row["site_host"], row["error_class"], row["task_step"], 168)
    if result["evidence_level"] == "no_reports":
        raise ServiceError("not_found", 404)
    return {"id": str(row["id"]), "site_host": row["site_host"], "task_step": row["task_step"], "error_class": row["error_class"], **result}
