"""The task-route write path (D118): a public report of a task route, its withdrawal, review, and the stored records the lookup reads.

A report carries closed values only (ahn.task_routes.TaskRouteIn): no free text, no link. An answer shows no time finer than the
reporting period; the row keeps received_at, reviewed_at and withdrawn_at for review, retention and withdrawal, never shown.
The server sets source_kind (external_agent), period (period_for), sensitive (is_sensitive) and review_state; a report is kept
under the site's root domain (route_host), and a tenant or account subdomain is refused. Idempotency, the daily caps and the
withdrawal token follow the observation intake (ahn.service.submit) on tables of their own (migration 042), with caps of their own.
Nothing here is read by check(): the verdict of a lookup does not depend on these tables.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

from .db import connect, connect_url
from .service import ServiceError
from .settings import Settings
from .task_routes import (TaskRouteIn, government_suffix, is_sensitive, period_for, route_host)

# Daily caps on public task-route reports, separate from the observation caps (ahn.service.PUBLIC_DAILY_CAP and
# PUBLIC_HOST_DAILY_CAP). A site is its root domain, so www.example.com and example.com share their slots.
TASK_ROUTE_DAILY_CAP = 100
TASK_ROUTE_SITE_DAILY_CAP = 5
TASK_ROUTE_SITE_KIND_DAILY_CAP = 3
# While True, review_task_routes publishes nothing: every report waits for an operator (cli.py task-route publish).
TASK_ROUTE_MANUAL_REVIEW = True
TASK_ROUTE_MIN_REVIEW_AGE = timedelta(minutes=10)
TASK_ROUTE_SITE_WINDOW = timedelta(hours=24)
# A withdrawn report is left out of every read (a cached answer may still show it for up to five minutes, the max-age of
# GET /v1/task-routes); the row itself goes after this grace (as for observations).
TASK_ROUTE_WITHDRAWN_GRACE_DAYS = 30
# The daily intake counters are kept this many days (today and the 7 before); platform_metrics.prune_analytics deletes older ones.
TASK_ROUTE_INTAKE_DAYS = 8
# One report at a time: the idempotency check and the cap counters cannot race (a fixed key of its own).
_INTAKE_LOCK = 81260531118
_REVIEW_LOCK = 81260531119
STORED_COLUMNS = ("site_host", "task_kind", "obstacle", "verification_kind", "site_requires", "document_kind", "entry_point_kind",
                  "routes_tried", "route_worked", "basis", "outcome", "duration", "period", "source_kind", "official_url")


def _request_hash(data: TaskRouteIn) -> str:
    """The caller's stated fields, so an identical retry with the same key is a replay and not a conflict."""
    body = {"kind": "task_route", "data": data.model_dump(mode="json", exclude_unset=True)}
    return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _withdrawal_token(settings: Settings, route_id: UUID, idempotency_key: UUID) -> str:
    message = f"withdraw-task-route:{route_id}:{idempotency_key}".encode()
    return hmac.new(settings.key_pepper.encode(), message, hashlib.sha256).hexdigest()


def _receipt(route_id, review_state: str, token: str) -> dict:
    return {"route_id": str(route_id), "review_state": review_state, "withdrawal_token": token}


def _count(conn, site_host: str, task_kind: str, cap: int) -> bool:
    """Take one slot of today's counter for this scope; False when the scope is full (the counter is left as it was)."""
    row = conn.execute(
        """INSERT INTO task_route_intake_daily (day,site_host,task_kind,reports) VALUES (CURRENT_DATE,%s,%s,1)
           ON CONFLICT (day,site_host,task_kind) DO UPDATE SET reports=task_route_intake_daily.reports+1
           WHERE task_route_intake_daily.reports < %s RETURNING reports""",
        (site_host, task_kind, cap),
    ).fetchone()
    return row is not None


def submit_task_route(settings: Settings, data: TaskRouteIn, *, today=None) -> tuple[dict, bool]:
    """Store one public report. Returns (receipt, created): created is False for a replay of the same key and body.

    ServiceError codes carry no input: real_submissions_not_enabled 503, site_host_not_accepted 422, reporting_paused 503,
    idempotency_conflict 409, report_rate_limited 429."""
    from .settings import task_route_reports_served

    if not task_route_reports_served(settings):
        raise ServiceError("real_submissions_not_enabled", 503)
    site = route_host(data.site_host)
    if site is None:  # an account or tenant subdomain (aggregate_eligible_host), or a name that is not a public site
        raise ServiceError("site_host_not_accepted", 422)
    digest = _request_hash(data)
    with connect(settings) as conn:
        flag = conn.execute("SELECT enabled FROM service_flags WHERE name='public_intake' FOR SHARE").fetchone()
        if not flag or not flag["enabled"]:
            raise ServiceError("reporting_paused", 503)
        conn.execute("SELECT pg_advisory_xact_lock(%s)", (_INTAKE_LOCK,))
        existing = conn.execute(
            """SELECT s.request_hash,s.route_id,r.review_state FROM task_route_submissions s JOIN task_routes r ON r.id=s.route_id
               WHERE s.idempotency_key=%s""",
            (data.idempotency_key,),
        ).fetchone()
        if existing:
            if existing["request_hash"] != digest:
                raise ServiceError("idempotency_conflict", 409)
            token = _withdrawal_token(settings, existing["route_id"], data.idempotency_key)
            return _receipt(existing["route_id"], existing["review_state"], token), False
        kind = data.task_kind.value
        # All three scopes or none: a refused report rolls the transaction back, so it takes no slot.
        if not (_count(conn, "", "", TASK_ROUTE_DAILY_CAP) and _count(conn, site, "", TASK_ROUTE_SITE_DAILY_CAP)
                and _count(conn, site, kind, TASK_ROUTE_SITE_KIND_DAILY_CAP)):
            raise ServiceError("report_rate_limited", 429)
        day = today or datetime.now(timezone.utc).date()
        sensitive = is_sensitive(data.task_kind, site_requires=data.site_requires, document_kind=data.document_kind,
                                 entry_point_kind=data.entry_point_kind, government_host=government_suffix(site))
        route_id = uuid4()
        values = data.model_dump(mode="json")
        row = conn.execute(
            """INSERT INTO task_routes (id,site_host,task_kind,obstacle,verification_kind,site_requires,document_kind,entry_point_kind,
                   routes_tried,route_worked,basis,outcome,duration,period,source_kind,sensitive,review_state)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'external_agent',%s,'pending') RETURNING id,review_state""",
            (route_id, site, kind, values["obstacle"], values.get("verification_kind"), values.get("site_requires"),
             values.get("document_kind"), values.get("entry_point_kind"), list(values.get("routes_tried") or []),
             values.get("route_worked"), values["basis"], values["outcome"], values.get("duration"), period_for(day), sensitive),
        ).fetchone()
        conn.execute("INSERT INTO task_route_submissions (idempotency_key,request_hash,route_id) VALUES (%s,%s,%s)",
                     (data.idempotency_key, digest, row["id"]))
        conn.execute("INSERT INTO audit_events (event_type,record_id) VALUES (%s,%s)", ("task_route_created", row["id"]))
        token = _withdrawal_token(settings, row["id"], data.idempotency_key)
        return _receipt(row["id"], row["review_state"], token), True


def withdraw_task_route(settings: Settings, route_id: UUID, token: str) -> dict:
    """Withdraw a public report with its token. Every failure is the same 404 not_found, so the answer does not tell whether a
    record exists. Works while intake is paused: a reporter can always take a report back."""
    from .settings import task_route_reports_served

    if not task_route_reports_served(settings) or not isinstance(token, str) or len(token) != 64:
        raise ServiceError("not_found", 404)
    with connect(settings) as conn:
        row = conn.execute("SELECT idempotency_key FROM task_route_submissions WHERE route_id=%s", (route_id,)).fetchone()
        if not row or not hmac.compare_digest(token, _withdrawal_token(settings, route_id, row["idempotency_key"])):
            raise ServiceError("not_found", 404)
        updated = conn.execute("UPDATE task_routes SET withdrawn_at=now() WHERE id=%s AND withdrawn_at IS NULL RETURNING id",
                               (route_id,)).fetchone()
        if updated:
            conn.execute("INSERT INTO audit_events (event_type,record_id) VALUES (%s,%s)", ("task_route_withdrawn", route_id))
    return {"withdrawn": True}


def published_rows(settings: Settings, hosts: list[str]) -> list[dict]:
    """The published, not withdrawn stored records under any of ``hosts`` (ahn.task_routes.site_records picks the nearest)."""
    if not hosts:
        return []
    with connect(settings) as conn:
        return conn.execute(
            f"""SELECT {",".join(STORED_COLUMNS)} FROM task_routes
                WHERE site_host = ANY(%s) AND review_state='published' AND withdrawn_at IS NULL
                ORDER BY period DESC, received_at DESC, id LIMIT 200""",
            (hosts,),
        ).fetchall()


def all_published_rows(settings: Settings, limit: int = 2000) -> list[dict]:
    """Every published, not withdrawn stored record, newest first, at most ``limit`` (for the task-route list page)."""
    with connect(settings) as conn:
        return conn.execute(
            f"""SELECT {",".join(STORED_COLUMNS)} FROM task_routes WHERE review_state='published' AND withdrawn_at IS NULL
                ORDER BY period DESC, received_at DESC, id LIMIT %s""",
            (limit,),
        ).fetchall()


def _decision(conn, row: dict, now: datetime) -> str:
    """publish, or the reason a report is held for an operator. Used only while TASK_ROUTE_MANUAL_REVIEW is False."""
    if row["sensitive"]:
        return "sensitive"
    burst = conn.execute(
        """SELECT count(*) AS n FROM task_routes WHERE site_host=%s AND source_kind='external_agent' AND received_at >= %s""",
        (row["site_host"], now - TASK_ROUTE_SITE_WINDOW),
    ).fetchone()["n"]
    if burst > 2:
        return "site_burst"
    recent = conn.execute(
        """SELECT count(*) AS n FROM task_routes WHERE site_host=%s AND task_kind=%s AND source_kind='external_agent'
           AND review_state='published' AND withdrawn_at IS NULL AND reviewed_at >= %s""",
        (row["site_host"], row["task_kind"], now - TASK_ROUTE_SITE_WINDOW),
    ).fetchone()["n"]
    if recent:
        return "recent_site_publication"
    return "publish"


def review_task_routes(database_url: str, *, now: datetime | None = None, limit: int = 100) -> dict[str, int]:
    """Review pending public reports once. While TASK_ROUTE_MANUAL_REVIEW is True every report is held for an operator
    (review_reason manual_review; it stays pending and is never shown). Nothing is published while public_publication is paused."""
    if not 1 <= limit <= 500:
        raise ValueError("limit must be 1-500")
    now = now or datetime.now(timezone.utc)
    result = {"published": 0, "held": 0}
    from psycopg.rows import dict_row

    with connect_url(database_url, row_factory=dict_row) as conn:
        if not conn.execute("SELECT pg_try_advisory_xact_lock(%s) AS ok", (_REVIEW_LOCK,)).fetchone()["ok"]:
            return result
        publication = conn.execute("SELECT enabled FROM service_flags WHERE name='public_publication' FOR SHARE").fetchone()
        if not publication or not publication["enabled"]:
            return result
        rows = conn.execute(
            """SELECT id,site_host,task_kind,sensitive,received_at FROM task_routes
               WHERE review_state='pending' AND review_reason IS NULL AND withdrawn_at IS NULL AND source_kind='external_agent'
                 AND received_at <= %s
               ORDER BY received_at,id LIMIT %s FOR UPDATE SKIP LOCKED""",
            (now - TASK_ROUTE_MIN_REVIEW_AGE, limit),
        ).fetchall()
        for row in rows:
            decision = "manual_review" if TASK_ROUTE_MANUAL_REVIEW else _decision(conn, row, now)
            if decision == "publish":
                conn.execute("UPDATE task_routes SET review_state='published',review_reason='auto_unverified',reviewed_at=%s WHERE id=%s",
                             (now, row["id"]))
                conn.execute("INSERT INTO audit_events (event_type,record_id) VALUES ('task_route_auto_published',%s)", (row["id"],))
                result["published"] += 1
            else:
                conn.execute("UPDATE task_routes SET review_reason=%s,reviewed_at=%s WHERE id=%s", (decision, now, row["id"]))
                conn.execute("INSERT INTO audit_events (event_type,record_id) VALUES ('task_route_auto_held',%s)", (row["id"],))
                result["held"] += 1
    return result


def prune_task_route_reports(conn, retention_days: int) -> int:
    """Delete public reports past the report retention period and withdrawn records past the grace (AHN_REPORT_RETENTION_DAYS,
    as for observations). Does nothing while retention_days is 0. An operator record (operator_panel) is kept until it is
    withdrawn. Returns the number of deleted records; their idempotency rows go with them (ON DELETE CASCADE)."""
    if retention_days <= 0:
        return 0
    return conn.execute(
        """DELETE FROM task_routes WHERE (source_kind='external_agent' AND received_at < now() - make_interval(days => %s))
           OR withdrawn_at < now() - make_interval(days => %s)""",
        (retention_days, TASK_ROUTE_WITHDRAWN_GRACE_DAYS),
    ).rowcount
