"""Read-only operator dashboard. Run as a separate loopback-bound ASGI service."""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from .db import connect
from .settings import Settings
from .traffic import OPERATOR_SOURCE

# Display order and labels for the lookup summary. Kinds not listed fall into "other" so the parts always sum to the total.
LOOKUP_KIND_LABELS = (
    ("plugin_hint", "플러그인"), ("ahn_client_hint", "ahn-client"), ("mcp_hint", "MCP"),
    ("browser_hint", "브라우저"), ("unknown", "unknown"),
)


def summarize_lookup_kinds(rows) -> dict:
    """Group (client_type, total, external) rows into the fixed lookup summary. Counts only, nothing identifying."""
    by_kind = {row["client_type"]: (int(row["total"]), int(row["external"])) for row in rows}
    listed = {kind for kind, _ in LOOKUP_KIND_LABELS}
    parts = [{"label": label, "total": by_kind.get(kind, (0, 0))[0], "external": by_kind.get(kind, (0, 0))[1]}
             for kind, label in LOOKUP_KIND_LABELS]
    other = [counts for kind, counts in by_kind.items() if kind not in listed]
    parts.append({"label": "기타", "total": sum(c[0] for c in other), "external": sum(c[1] for c in other)})
    return {"parts": parts, "total": sum(p["total"] for p in parts), "external": sum(p["external"] for p in parts)}


templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent / "templates"))


def _snapshot(settings: Settings) -> dict:
    start = date.today() - timedelta(days=6)
    with connect(settings) as conn:
        demand = conn.execute(
            "SELECT COALESCE(sum(checks),0) AS checks, COALESCE(sum(checks) FILTER (WHERE day=CURRENT_DATE),0) AS today FROM lookup_metrics WHERE day >= %s",
            (start,),
        ).fetchone()
        top_hosts = conn.execute(
            "SELECT site_host,error_class,sum(checks)::bigint AS checks FROM lookup_metrics WHERE day >= %s GROUP BY site_host,error_class ORDER BY checks DESC,site_host LIMIT 12",
            (start,),
        ).fetchall()
        daily = conn.execute(
            "SELECT day,sum(checks)::bigint AS checks FROM lookup_metrics WHERE day >= %s GROUP BY day ORDER BY day",
            (start,),
        ).fetchall()
        mcp_checks = conn.execute(
            """SELECT COALESCE(sum(calls),0) AS successful,
                      COALESCE(sum(calls) FILTER (WHERE evidence_level='no_reports'),0) AS no_reports,
                      COALESCE(sum(calls) FILTER (WHERE provider_signal='active'),0) AS provider_active
               FROM mcp_check_metrics WHERE day >= %s""",
            (start,),
        ).fetchone()
        mcp_results = conn.execute(
            """SELECT evidence_level,provider_signal,sum(calls)::bigint AS calls
               FROM mcp_check_metrics WHERE day >= %s
               GROUP BY evidence_level,provider_signal ORDER BY calls DESC,evidence_level""",
            (start,),
        ).fetchall()
        traffic = conn.execute(
            "SELECT COALESCE(sum(requests),0) AS total, COALESCE(sum(requests) FILTER (WHERE status_class IN ('4xx','5xx')),0) AS errors, COALESCE(sum(requests) FILTER (WHERE status_class='5xx'),0) AS server_errors FROM request_metrics WHERE day >= %s",
            (start,),
        ).fetchone()
        pages = conn.execute(
            """SELECT COALESCE(sum(requests),0) AS total,
                      COALESCE(sum(requests) FILTER (WHERE day=CURRENT_DATE),0) AS today
               FROM request_metrics WHERE day >= %s AND method='GET' AND status_class='2xx'
                 AND route_code IN ('/{lang}/','/{lang}/human','/{lang}/search',
                                    '/{lang}/agents','/{lang}/connect','/{lang}/report',
                                    '/{lang}/policy','/{lang}/incidents/{incident_id}')""",
            (start,),
        ).fetchone()
        bandwidth = conn.execute("SELECT period_start,checked_at,latest_sample_at,usage_gb,state,coverage_gap_since,warning_gb,stop_gb FROM bandwidth_guard_state WHERE name='web'").fetchone()
        # All-time lookup history: the registry's first/last sight per host plus
        # every day of recorded demand, so coverage decisions rest on real demand.
        lookup_history = conn.execute(
            """SELECT site_host,sum(checks)::bigint AS checks,min(day) AS first_day,max(day) AS last_day
               FROM lookup_metrics GROUP BY site_host ORDER BY checks DESC,site_host LIMIT 30"""
        ).fetchall()
        lookup_days = conn.execute(
            "SELECT day,sum(checks)::bigint AS checks,count(DISTINCT site_host)::bigint AS hosts FROM lookup_metrics GROUP BY day ORDER BY day DESC LIMIT 60"
        ).fetchall()
        profile_rows = conn.execute(
            "SELECT robots_status,count(*)::bigint AS hosts FROM host_access_profile GROUP BY robots_status ORDER BY hosts DESC"
        ).fetchall()
        latency = conn.execute(
            "SELECT latency_bucket_ms,sum(requests)::bigint AS requests FROM request_metrics WHERE day >= %s GROUP BY latency_bucket_ms ORDER BY latency_bucket_ms",
            (start,),
        ).fetchall()
        routes = conn.execute(
            "SELECT route_code,method,status_class,sum(requests)::bigint AS requests FROM request_metrics WHERE day >= %s GROUP BY route_code,method,status_class ORDER BY requests DESC,route_code LIMIT 12",
            (start,),
        ).fetchall()
        sources = conn.execute(
            """SELECT source,medium,campaign,referrer,sum(requests)::bigint AS requests
               FROM traffic_attribution_daily WHERE day >= %s AND method='GET' AND status_class='2xx'
                 AND route_code IN ('/{lang}/','/{lang}/human','/{lang}/search',
                                    '/{lang}/agents','/{lang}/connect','/{lang}/report',
                                    '/{lang}/policy','/{lang}/disclaimer','/{lang}/terms',
                                    '/{lang}/support','/{lang}/incidents/{incident_id}')
               GROUP BY source,medium,campaign,referrer
               ORDER BY requests DESC,source LIMIT 20""",
            (start,),
        ).fetchall()
        stage_rows = conn.execute(
            """SELECT day,
                      COALESCE(sum(requests) FILTER (WHERE route_code='/{lang}/' AND method='GET' AND status_class='2xx'),0) AS home,
                      COALESCE(sum(requests) FILTER (WHERE route_code='/{lang}/search' AND method='GET' AND status_class='2xx'),0) AS search,
                      COALESCE(sum(requests) FILTER (WHERE route_code='/v1/incidents/check' AND method='GET' AND status_class='2xx'),0) AS rest_check,
                      COALESCE(sum(requests) FILTER (WHERE source='owner_network' AND route_code IN ('/{lang}/','/{lang}/search','/v1/incidents/check') AND status_class='2xx'),0) AS owner_network
               FROM traffic_attribution_daily WHERE day >= %s GROUP BY day ORDER BY day""",
            (start,),
        ).fetchall()
        mcp_daily = conn.execute(
            "SELECT day,sum(calls)::bigint AS calls FROM mcp_check_metrics WHERE day >= %s GROUP BY day",
            (start,),
        ).fetchall()
        agent_sources = conn.execute(
            """SELECT route_code,source,client_type,sum(requests)::bigint AS requests
               FROM traffic_attribution_daily WHERE day >= %s
                 AND route_code IN ('/mcp','/v1/incidents/check','/v1/incidents',
                                    '/v1/observations','/openapi.json')
               GROUP BY route_code,source,client_type
               ORDER BY requests DESC,route_code LIMIT 20""",
            (start,),
        ).fetchall()
        client_types = conn.execute(
            """SELECT client_type,sum(requests)::bigint AS requests
               FROM traffic_attribution_daily WHERE day >= %s
               GROUP BY client_type ORDER BY requests DESC,client_type""",
            (start,),
        ).fetchall()
        lookup_kind_rows = conn.execute(
            """SELECT client_type,sum(requests)::bigint AS total,
                      COALESCE(sum(requests) FILTER (WHERE source<>%s),0)::bigint AS external
               FROM traffic_attribution_daily
               WHERE day >= %s AND method='GET' AND status_class='2xx' AND route_code='/v1/incidents/check'
               GROUP BY client_type""",
            (OPERATOR_SOURCE, start),
        ).fetchall()
        web_search = conn.execute(
            """SELECT COALESCE(sum(requests),0)::bigint AS total,
                      COALESCE(sum(requests) FILTER (WHERE source<>%s),0)::bigint AS external
               FROM traffic_attribution_daily
               WHERE day >= %s AND method='GET' AND status_class='2xx' AND route_code='/{lang}/search'""",
            (OPERATOR_SOURCE, start),
        ).fetchone()
        client_days = conn.execute(
            "SELECT count(DISTINCT (day,client_digest))::bigint AS n FROM traffic_client_daily WHERE day >= %s",
            (start,),
        ).fetchone()["n"]
        top_clients = conn.execute(
            """SELECT day,left(client_digest,12) AS daily_key,sum(requests)::bigint AS requests
               FROM traffic_client_daily WHERE day >= %s
               GROUP BY day,client_digest ORDER BY requests DESC,day DESC LIMIT 12""",
            (start,),
        ).fetchall()
        render_rows = conn.execute(
            """SELECT DISTINCT ON (metric) metric,sample_at,value,unit,collected_at
               FROM render_metric_samples WHERE sample_at >= now() - interval '4 hours'
               ORDER BY metric,sample_at DESC"""
        ).fetchall()
        database_stats = conn.execute(
            """SELECT pg_database_size(current_database())::bigint AS bytes,
                      (SELECT count(*)::bigint FROM pg_stat_activity
                       WHERE datname=current_database()) AS connections"""
        ).fetchone()
        states = conn.execute(
            "SELECT review_state,synthetic,count(*)::bigint AS observations FROM observations WHERE received_at >= now() - interval '7 days' GROUP BY review_state,synthetic ORDER BY synthetic DESC,review_state"
        ).fetchall()
        withdrawals = conn.execute(
            "SELECT count(*)::bigint AS n FROM observations WHERE withdrawn_at >= now() - interval '7 days'"
        ).fetchone()["n"]
        events = conn.execute(
            "SELECT event_type,count(*)::bigint AS events FROM audit_events WHERE created_at >= now() - interval '7 days' GROUP BY event_type ORDER BY events DESC,event_type"
        ).fetchall()
        pending = conn.execute(
            "SELECT count(*)::bigint AS n FROM observations WHERE review_state='pending' AND synthetic=false AND withdrawn_at IS NULL"
        ).fetchone()["n"]
        held_reasons = conn.execute(
            """SELECT review_reason,count(*)::bigint AS reports FROM observations
               WHERE review_state='pending' AND review_reason IS NOT NULL
                 AND synthetic=false AND withdrawn_at IS NULL
               GROUP BY review_reason ORDER BY reports DESC,review_reason"""
        ).fetchall()
        held_reports = conn.execute(
            """SELECT o.id,i.site_host,i.error_class,o.review_reason,o.received_at
               FROM observations o JOIN incidents i ON i.id=o.incident_id
               WHERE o.review_state='pending' AND o.review_reason IS NOT NULL
                 AND o.synthetic=false AND o.withdrawn_at IS NULL
               ORDER BY o.received_at DESC LIMIT 20"""
        ).fetchall()
    counts = {"pending": 0, "published": 0, "hidden": 0, "synthetic": 0}
    for row in states:
        if row["synthetic"]:
            counts["synthetic"] += row["observations"]
        else:
            counts[row["review_state"]] += row["observations"]
    total_requests = int(traffic["total"])
    error_rate = round(100 * traffic["errors"] / total_requests, 1) if total_requests else None
    max_checks = max((row["checks"] for row in daily), default=0)
    chart = [{"day": (start + timedelta(days=i)).isoformat(), "checks": 0, "pct": 0} for i in range(7)]
    by_day = {row["day"]: row["checks"] for row in daily}
    for row in chart:
        row["checks"] = by_day.get(date.fromisoformat(row["day"]), 0)
        row["pct"] = round(100 * row["checks"] / max_checks) if max_checks else 0
    stages_by_day = {row["day"]: row for row in stage_rows}
    mcp_by_day = {row["day"]: row["calls"] for row in mcp_daily}
    stage_chart = []
    for row in chart:
        day = date.fromisoformat(row["day"])
        data = stages_by_day.get(day)
        stage_chart.append({"day": row["day"], "home": data["home"] if data else 0,
                            "search": data["search"] if data else 0,
                            "rest_check": data["rest_check"] if data else 0,
                            "mcp_check": mcp_by_day.get(day, 0),
                            "owner_network": data["owner_network"] if data else 0})
    p95_bucket = None
    if total_requests:
        target = total_requests * 0.95
        seen = 0
        for row in latency:
            seen += row["requests"]
            if seen >= target:
                p95_bucket = row["latency_bucket_ms"]
                break
    return {
        "demand": demand, "top_hosts": top_hosts, "chart": chart, "stage_chart": stage_chart, "traffic": traffic,
        "mcp_checks": mcp_checks, "mcp_results": mcp_results,
        "pages": pages, "bandwidth": bandwidth, "held_reasons": held_reasons,
        "lookup_history": lookup_history, "lookup_days": lookup_days, "profile_rows": profile_rows,
        "held_reports": held_reports,
        "error_rate": error_rate, "p95_bucket": p95_bucket, "routes": routes,
        "sources": sources, "agent_sources": agent_sources,
        "client_types": client_types, "client_days": client_days,
        "lookup_kinds": summarize_lookup_kinds(lookup_kind_rows), "web_search": web_search,
        "top_clients": top_clients, "render_metrics": {row["metric"]: row for row in render_rows},
        "database_stats": database_stats,
        "counts": counts, "withdrawals": withdrawals, "events": events, "pending": pending,
        "test_mode": settings.test_mode, "lookup_enabled": settings.collect_lookup_metrics,
        "request_enabled": settings.collect_request_metrics,
    }


def create_monitor_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    app = FastAPI(title="Agent Help Network operator monitor", docs_url=None, redoc_url=None, openapi_url=None)

    @app.middleware("http")
    async def local_only(request: Request, call_next):
        if not request.client or request.client.host not in {"127.0.0.1", "::1"}:
            return JSONResponse({"error": "local_only"}, status_code=403)
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-Robots-Tag"] = "noindex, nofollow"
        return response

    @app.get("/", response_class=HTMLResponse)
    def dashboard(request: Request):
        try:
            snapshot = _snapshot(settings)
        except Exception:
            return JSONResponse({"error": "monitor_data_unavailable"}, status_code=503)
        return templates.TemplateResponse(request, "monitor.html", snapshot)

    @app.get("/health")
    def health():
        try:
            with connect(settings) as conn:
                conn.execute("SELECT 1")
        except Exception:
            return JSONResponse({"status": "unavailable"}, status_code=503)
        return {"status": "ok"}

    return app


app = create_monitor_app()
