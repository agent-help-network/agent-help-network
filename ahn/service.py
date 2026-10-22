from __future__ import annotations

import hashlib
import hmac
import json
import logging
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

from . import background_reads, gov_registry, lookup_signals, status_scope
from .access_profile import cached_profile, pending_profile
from .db import connect, get_reporter_id
from .lookup_signals import LookupSignal
from .models import ObservationIn, OutcomeIn
from .official_paths import official_paths
from .provenance import operator_id
from .settings import Settings
from .status_refresh import OFFICIAL_STATUS_PAGES, SOURCES, register_source
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
                   signal: LookupSignal, action: str, *, public: bool, registered: bool, now: datetime) -> None:
    """Count the lookup's optional codes per host and hour inside a savepoint; never raises (D47).

    Same gate as lookup_metrics (eligible apex host, inside the lookup register in public mode, metrics
    switch on), minus example hosts. Database writes only; nothing here reaches the answer.
    """
    if not (settings.collect_lookup_metrics and aggregate_eligible_host(host) and (not public or registered)):
        return
    if host in lookup_signals.EXAMPLE_HOSTS:
        return
    try:
        with conn.transaction():
            # Never wait on a table lock for the sake of a count: give up after 200 ms (the setting ends with the savepoint).
            conn.execute("SET LOCAL lock_timeout = '200ms'")
            lookup_signals.record(conn, settings.key_pepper, host, error_class, failure_stage, signal, action, now)
            conn.execute("SET LOCAL lock_timeout = DEFAULT")
    except Exception:
        # Shadow bookkeeping: a failure (for example a migration not applied yet) must not touch the lookup.
        LOG.warning("lookup_signal_unavailable")


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
    """The stored scope of a Statuspage feed as provider_status fields (D43); a field that was not read is left out."""
    out: dict = {}
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


def check(settings: Settings, site_host: str, error_class: str | None = None, task_step: str | None = None,
          window_hours: int = 24, failure_stage: str | None = None, signal: LookupSignal | None = None,
          agent_vendor: str | None = None):
    """Answer a lookup from the database alone.

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
            source = conn.execute(
                """SELECT s.source_name,s.status_page_url,c.indicator,c.source_updated_at,
                          c.fetched_at,c.last_error_at,c.last_error_detail,c.failures,
                          c.active_incidents,c.max_incident_impact,c.incident_updated_at,
                          c.active_maintenances,c.affected,c.affected_regional
                   FROM status_sources s LEFT JOIN status_cache c ON c.site_host=s.site_host
                   WHERE s.site_host=%s AND s.enabled=true""",
                (host,),
            ).fetchone()
            if source:
                fetched_at = source["fetched_at"]
                # The age of the last successful statement decides; a later failed read only adds error_detail (D46).
                freshness, failed_later = background_reads.feed_freshness(
                    fetched_at, source["last_error_at"], now, source["last_error_detail"])
                provider_status = {
                    "source_name": source["source_name"], "source_url": source["status_page_url"],
                    "indicator": source["indicator"], "source_updated_at": source["source_updated_at"],
                    "fetched_at": fetched_at, "freshness": freshness,
                    "error_detail": source["last_error_detail"] if failed_later else None,
                }
                provider_status.update(_provider_scope(source))
                provider_partial = status_scope.scope_partial(
                    source["indicator"], source["active_incidents"], source["max_incident_impact"], source["affected_regional"])
                provider_regional = source["affected_regional"] is True
                if mapped_source:
                    # Stale-while-revalidate: answer with the cached statement; once it would read as
                    # stale by age (or was never read), refresh it behind the lookup, not again within
                    # FEED_RETRY_MINUTES of the last attempt. An active indicator is due after
                    # FEED_RETRY_MINUTES already, and a failing feed backs off (the cron uses the same
                    # rule, background_reads.feed_next_due), so a resolved problem stops reading as
                    # active within about that long. Curated feeds are also read by the cron (D44).
                    due = background_reads.feed_next_due(source, timedelta(hours=background_reads.FEED_STALE_HOURS))
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
        official_page = OFFICIAL_STATUS_PAGES.get(host) if settings.public_mode and not test_query and provider_status is None else None
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
                                             vendor_aware=settings.vendor_aware_block),
            "failure_stage": failure_stage,
            "next_checks": next_checks(error_class, provider_status, level, failure_stage, access_profile, official,
                                       profile_not_read=profile_not_read, host_facts=host_facts,
                                       provider_partial=provider_partial, now=now, agent_vendor=agent_vendor,
                                       vendor_aware=settings.vendor_aware_block),
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
        if signal is not None:
            _record_signal(conn, settings, host, error_class, failure_stage, signal, result["recommendation"]["action"],
                           public=public, registered=registered, now=now)
    return result


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
