"""Read official public status feeds for hosts that have actually been queried.

Statuspage has no universal hostname-to-page directory. Source mappings below are
verified publisher endpoints, never URLs assembled from a user's hostname.
"""

from __future__ import annotations

import os
import ipaddress
import json
import re
import socket
import ssl
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

import dns.exception
import dns.resolver
import httpx
import urllib3

from .db import connect
from .settings import Settings


class FeedFailure(ValueError):
    """A feed read that ended for a known reason; the label is stored as status_cache.last_error_detail."""

    def __init__(self, label: str):
        super().__init__(label)
        self.label = label


FAILURE_LABELS = frozenset({"too_large", "invalid_body", "dns_failed", "connect_error", "tls_error", "timeout",
                            "read_error", "no_cname", "id_mismatch", "not_public", "error"})
_DNS_HINTS = ("getaddrinfo", "name or service", "nodename nor servname", "name resolution", "no address associated")
_TLS_HINTS = ("ssl", "certificate", "tls")


def failure_label(exc: BaseException) -> str:
    """Short lowercase reason for a failed feed read: ``http_NNN`` or one of FAILURE_LABELS. Never raises."""
    if isinstance(exc, FeedFailure):
        return exc.label
    if isinstance(exc, httpx.HTTPStatusError):
        return f"http_{exc.response.status_code}"
    message = str(exc).lower()
    if isinstance(exc, (urllib3.exceptions.NameResolutionError, socket.gaierror)):
        return "dns_failed"
    if isinstance(exc, (urllib3.exceptions.SSLError, ssl.SSLError)):
        return "tls_error"
    # urllib3's NewConnectionError inherits from its ConnectTimeoutError, so connection failures go before timeouts.
    if isinstance(exc, (httpx.ConnectError, urllib3.exceptions.NewConnectionError, ConnectionError)):
        if any(hint in message for hint in _DNS_HINTS):
            return "dns_failed"
        if any(hint in message for hint in _TLS_HINTS):
            return "tls_error"
        return "connect_error"
    if isinstance(exc, (httpx.TimeoutException, urllib3.exceptions.TimeoutError, TimeoutError)):
        return "timeout"
    if isinstance(exc, (httpx.HTTPError, urllib3.exceptions.ProtocolError)):
        return "read_error"
    if isinstance(exc, (json.JSONDecodeError, ValueError, TypeError, KeyError)):
        return "invalid_body"
    if isinstance(exc, OSError):
        return "connect_error"
    return "error"


@dataclass(frozen=True)
class Source:
    name: str
    page_url: str
    api_url: str
    kind: str = "statuspage"


SOURCES: dict[str, Source] = {
    "github.com": Source("GitHub Status", "https://www.githubstatus.com/", "https://www.githubstatus.com/api/v2/status.json"),
    "github.io": Source("GitHub Status (includes Pages)", "https://www.githubstatus.com/", "https://www.githubstatus.com/api/v2/status.json"),
    "openai.com": Source("OpenAI Status", "https://status.openai.com/", "https://status.openai.com/api/v2/status.json"),
    "chatgpt.com": Source("OpenAI Status", "https://status.openai.com/", "https://status.openai.com/api/v2/status.json"),
    "cloudflare.com": Source("Cloudflare Status", "https://www.cloudflarestatus.com/", "https://www.cloudflarestatus.com/api/v2/status.json"),
    "cloudflare.net": Source("Cloudflare Status", "https://www.cloudflarestatus.com/", "https://www.cloudflarestatus.com/api/v2/status.json"),
    "discord.com": Source("Discord Status", "https://discordstatus.com/", "https://discordstatus.com/api/v2/status.json"),
    "discord.gg": Source("Discord Status", "https://discordstatus.com/", "https://discordstatus.com/api/v2/status.json"),
    "discord.media": Source("Discord Status", "https://discordstatus.com/", "https://discordstatus.com/api/v2/status.json"),
    "discordapp.com": Source("Discord Status", "https://discordstatus.com/", "https://discordstatus.com/api/v2/status.json"),
    "reddit.com": Source("Reddit Status", "https://www.redditstatus.com/", "https://www.redditstatus.com/api/v2/status.json"),
    "twitch.tv": Source("Twitch Status", "https://status.twitch.com/", "https://status.twitch.com/api/v2/status.json"),
    "doctolib.fr": Source("Doctolib Status", "https://status.doctolib.com/", "https://status.doctolib.com/api/v2/status.json"),
    "doctolib.net": Source("Doctolib Status", "https://status.doctolib.com/", "https://status.doctolib.com/api/v2/status.json"),
    "wikimedia.org": Source("Wikimedia Status (major outages)", "https://www.wikimediastatus.net/", "https://www.wikimediastatus.net/api/v2/status.json"),
    "hcaptcha.com": Source("hCaptcha Status", "https://www.hcaptchastatus.com/", "https://www.hcaptchastatus.com/api/v2/status.json"),
    "vercel.app": Source("Vercel Status", "https://www.vercel-status.com/", "https://www.vercel-status.com/api/v2/status.json"),
    "vercel.com": Source("Vercel Status", "https://www.vercel-status.com/", "https://www.vercel-status.com/api/v2/status.json"),
    "akamai.net": Source("Akamai Status", "https://www.akamaistatus.com/", "https://www.akamaistatus.com/api/v2/status.json"),
    "calendly.com": Source("Calendly Status", "https://calendlystatus.com/", "https://calendlystatus.com/api/v2/status.json"),
    "godaddy.com": Source("GoDaddy Status", "https://status.godaddy.com/", "https://status.godaddy.com/api/v2/status.json"),
    "netlify.app": Source("Netlify Status", "https://www.netlifystatus.com/", "https://www.netlifystatus.com/api/v2/status.json"),
    "netlify.com": Source("Netlify Status", "https://www.netlifystatus.com/", "https://www.netlifystatus.com/api/v2/status.json"),
    "bugsnag.com": Source("BugSnag SaaS Status", "https://bugsnag.status.smartbear.com/", "https://bugsnag.status.smartbear.com/api/v2/status.json"),
    "surveymonkey.com": Source("SurveyMonkey Status", "https://surveymonkey.statuspage.io/", "https://surveymonkey.statuspage.io/api/v2/status.json"),
    "dropbox.com": Source("Dropbox Status", "https://status.dropbox.com/", "https://status.dropbox.com/api/v2/status.json"),
    "digitalocean.com": Source("DigitalOcean Status", "https://status.digitalocean.com/", "https://status.digitalocean.com/api/v2/status.json"),
    "shopify.com": Source("Shopify Status", "https://www.shopifystatus.com/", "https://www.shopifystatus.com/api/v2/status.json"),
    "tailscale.com": Source("Tailscale Status", "https://status.tailscale.com/", "https://status.tailscale.com/api/v2/status.json"),
    "render.com": Source("Render Status", "https://status.render.com/", "https://status.render.com/api/v2/status.json"),
    "claude.com": Source("Claude Status", "https://status.claude.com/", "https://status.claude.com/api/v2/status.json"),
    "claude.ai": Source("Claude Status", "https://status.claude.com/", "https://status.claude.com/api/v2/status.json"),
    "gmail.com": Source("Google Workspace Status Dashboard — Gmail", "https://www.google.com/appsstatus/dashboard/", "https://www.google.com/appsstatus/dashboard/incidents.json", "google_gmail"),
    "base.org": Source("Base Status (network and services)", "https://status.base.org/", "https://status.base.org/api/v2/status.json"),
    "doi.org": Source("DOI Foundation Status (resolution and website)", "https://doi.statuspage.io/", "https://doi.statuspage.io/api/v2/status.json"),
    "braze.com": Source("Braze Status", "https://braze.statuspage.io/", "https://braze.statuspage.io/api/v2/status.json"),
    "atlassian.net": Source("Atlassian Cloud Status", "https://status.atlassian.com/", "https://status.atlassian.com/api/v2/status.json"),
}
# Publisher-owned pages for which this service has not verified a compatible
# machine-readable status feed. A link must never be converted to an indicator.
OFFICIAL_STATUS_PAGES: dict[str, tuple[str, str]] = {
    "netflix.com": ("Netflix Service Status", "https://help.netflix.com/en/is-netflix-down"),
    "webex.com": ("Webex Global Service Status", "https://status.webex.com/"),
    "adobe.com": ("Adobe Status", "https://status.adobe.com/"),
    "brave.com": ("Brave Status", "https://status.brave.com/"),
    "cdn77.org": ("CDN77 Status", "https://client.cdn77.com/support/status"),
    "fastly.net": ("Fastly Status", "https://www.fastlystatus.com/"),
    "jetbrains.com": ("JetBrains Services Status", "https://status.jetbrains.com/"),
    "office.com": ("Microsoft 365 Service Health", "https://status.cloud.microsoft/"),
    "office365.com": ("Microsoft 365 Service Health", "https://status.cloud.microsoft/"),
    "okta.com": ("Okta Status", "https://status.okta.com/"),
    "oraclecloud.com": ("Oracle Cloud Infrastructure Status", "https://ocistatus.oraclecloud.com/"),
    "roblox.com": ("Roblox Status", "https://status.roblox.com/"),
    "salesforce.com": ("Salesforce Trust Status", "https://status.salesforce.com/"),
    "sophos.com": ("Sophos Central Status", "https://centralstatus.sophos.com/"),
    "stripe.com": ("Stripe System Status", "https://status.stripe.com/"),
    "trendmicro.com": ("Trend Micro Status", "https://status.trendmicro.com/"),
    "unity3d.com": ("Unity Services Status", "https://status.unity.com/"),
    "xbox.com": ("Xbox Network Status", "https://support.xbox.com/en-US/xbox-live-status"),
    "tradingview.com": ("TradingView Status", "https://status.tradingview.com/"),
    "zendesk.com": ("Zendesk Status", "https://status.zendesk.com/"),
    "hostgator.com.br": ("HostGator Brasil Status", "https://status.hostgator.com.br/"),
    "craigslist.org": ("Craigslist System Status", "https://www.craigslist.org/about/help/system-status"),
    "playstation.net": ("PlayStation Network Status", "https://status.playstation.com/"),
}
GMAIL_PRODUCT_ID = "prPt2Yra2CbGsbEm9cpC"
INDICATORS = frozenset({"none", "minor", "major", "critical", "unknown"})
_SOURCE_LOCKS = {host: threading.Lock() for host in SOURCES}
_STATUSPAGE_CNAME = re.compile(r"^([a-z0-9]{8,32})\.stspg-customer\.com\.$")


def _statuspage_target(site_host: str) -> tuple[str, str] | None:
    """Only accept a first-party status subdomain delegated to Statuspage."""
    status_host = f"status.{site_host}"
    resolver = dns.resolver.Resolver()
    resolver.timeout = 1.0
    resolver.lifetime = 1.5
    try:
        answers = resolver.resolve(status_host, "CNAME")
    except (dns.exception.DNSException, OSError):
        return None
    targets = {str(answer.target).lower() for answer in answers}
    if len(targets) != 1:
        return None
    target = targets.pop()
    match = _STATUSPAGE_CNAME.fullmatch(target)
    return (target.rstrip("."), match.group(1)) if match else None


def _read_discovered_statuspage(site_host: str) -> tuple[Source, str, datetime | None]:
    """Fetch a publisher-owned Statuspage endpoint using a pinned public IP."""
    target = _statuspage_target(site_host)
    if target is None:
        raise FeedFailure("no_cname")
    cname, page_id = target
    status_host = f"status.{site_host}"
    addresses = {item[4][0] for item in socket.getaddrinfo(cname, 443, type=socket.SOCK_STREAM)}
    if not addresses or len(addresses) > 16 or any(not ipaddress.ip_address(ip).is_global for ip in addresses):
        raise FeedFailure("not_public")
    pool = urllib3.HTTPSConnectionPool(
        sorted(addresses)[0], port=443, server_hostname=status_host,
        assert_hostname=status_host, timeout=urllib3.Timeout(connect=2.0, read=3.0), retries=False,
    )
    try:
        response = pool.request(
            "GET", "/api/v2/status.json", headers={"Host": status_host, "Accept": "application/json"},
            redirect=False, preload_content=False,
        )
        try:
            if response.status != 200:
                raise FeedFailure(f"http_{response.status}")
            body = response.read(32_769)
            if len(body) > 32_768:
                raise FeedFailure("too_large")
        finally:
            response.release_conn()
    finally:
        pool.close()
    payload = json.loads(body)
    page = payload.get("page") if isinstance(payload, dict) else None
    if not isinstance(page, dict) or page.get("id") != page_id:
        raise FeedFailure("id_mismatch")
    name = page.get("name")
    if not isinstance(name, str) or not 1 <= len(name) <= 72:
        raise FeedFailure("invalid_body")
    indicator, updated_at = parse_status(payload)
    source = Source(f"{name} Status", f"https://{status_host}/", f"https://{status_host}/api/v2/status.json", "discovered_statuspage")
    return source, indicator, updated_at


def discover_source(site_host: str) -> tuple[Source, str, datetime | None] | None:
    """Bounded best-effort discovery; unknown hosts remain unknown."""
    try:
        return _read_discovered_statuspage(site_host)
    except (ValueError, OSError, urllib3.exceptions.HTTPError, json.JSONDecodeError):
        return None


def parse_status(body: dict) -> tuple[str, datetime | None]:
    """Parse an Atlassian Statuspage page-level status response."""
    if not isinstance(body, dict) or not isinstance(body.get("status"), dict):
        raise ValueError("invalid provider status response")
    indicator = body["status"].get("indicator")
    if indicator not in INDICATORS:
        raise ValueError("unknown provider indicator")
    page = body.get("page")
    if page is not None and not isinstance(page, dict):
        raise ValueError("invalid provider page")
    raw_time = (page or {}).get("updated_at")
    updated_at = None
    if raw_time is not None:
        if not isinstance(raw_time, str):
            raise ValueError("invalid provider update time")
        updated_at = datetime.fromisoformat(raw_time.replace("Z", "+00:00"))
        if updated_at.tzinfo is None:
            raise ValueError("provider time has no timezone")
        updated_at = updated_at.astimezone(timezone.utc)
    return indicator, updated_at


def parse_google_gmail(body: list) -> tuple[str, datetime | None]:
    """Interpret only active Gmail incidents in Google's official history feed."""
    if not isinstance(body, list):
        raise ValueError("invalid Google Workspace incident feed")
    active: list[tuple[str, datetime | None]] = []
    severity = {"SERVICE_INFORMATION": "minor", "SERVICE_DISRUPTION": "major", "SERVICE_OUTAGE": "critical"}
    for incident in body:
        if not isinstance(incident, dict):
            raise ValueError("invalid Google Workspace incident")
        products = incident.get("affected_products")
        if not isinstance(products, list):
            raise ValueError("missing Google Workspace affected products")
        if not any(isinstance(product, dict) and product.get("id") == GMAIL_PRODUCT_ID for product in products):
            continue
        if incident.get("end"):
            continue
        raw_begin = incident.get("begin")
        if not isinstance(raw_begin, str):
            raise ValueError("missing Gmail incident start")
        begin = datetime.fromisoformat(raw_begin.replace("Z", "+00:00"))
        if begin.tzinfo is None:
            raise ValueError("Gmail incident start has no timezone")
        if begin > datetime.now(timezone.utc):
            continue
        impact = incident.get("status_impact")
        if impact not in severity:
            raise ValueError("unknown Gmail incident impact")
        raw_time = incident.get("modified") or incident.get("begin")
        updated_at = None
        if raw_time is not None:
            if not isinstance(raw_time, str):
                raise ValueError("invalid Gmail incident time")
            updated_at = datetime.fromisoformat(raw_time.replace("Z", "+00:00"))
            if updated_at.tzinfo is None:
                raise ValueError("Gmail incident time has no timezone")
        active.append((severity[impact], updated_at))
    if not active:
        return "none", None
    order = {"minor": 0, "major": 1, "critical": 2}
    indicator = max((item[0] for item in active), key=order.__getitem__)
    updated_times = [item[1] for item in active if item[1] is not None]
    return indicator, max(updated_times) if updated_times else None


def register_source(conn, site_host: str, source: Source | None = None) -> bool:
    """Persist a verified mapping only when a user looks up its host."""
    source = source or SOURCES.get(site_host)
    if source is None:
        row = conn.execute(
            "SELECT status_page_url,api_url,enabled FROM status_sources WHERE site_host=%s",
            (site_host,),
        ).fetchone()
        page_url = f"https://status.{site_host}/"
        return bool(row and row["enabled"] and row["status_page_url"] == page_url and row["api_url"] == page_url + "api/v2/status.json")
    row = conn.execute(
        """INSERT INTO status_sources (site_host,source_name,status_page_url,api_url)
           VALUES (%s,%s,%s,%s) ON CONFLICT (site_host) DO UPDATE SET
           source_name=EXCLUDED.source_name,status_page_url=EXCLUDED.status_page_url,
           api_url=EXCLUDED.api_url RETURNING enabled""",
        (site_host, source.name, source.page_url, source.api_url),
    ).fetchone()
    return bool(row["enabled"])


def _read_source(source: Source, client: httpx.Client) -> tuple[str, datetime | None]:
    if source.kind == "discovered_statuspage":
        host = urlparse(source.page_url).hostname
        if not host or not host.startswith("status."):
            raise FeedFailure("error")
        _, indicator, updated_at = _read_discovered_statuspage(host[7:])
        return indicator, updated_at
    # Follow no redirects, stream with a hard byte cap, and never send credentials.
    cap = 1_048_576 if source.kind == "google_gmail" else 32_768
    with client.stream("GET", source.api_url, headers={"Accept": "application/json"}) as response:
        if response.status_code != 200:
            raise FeedFailure(f"http_{response.status_code}")
        data = bytearray()
        for chunk in response.iter_bytes():
            data.extend(chunk)
            if len(data) > cap:
                raise FeedFailure("too_large")
    body = httpx.Response(200, content=bytes(data)).json()
    return parse_google_gmail(body) if source.kind == "google_gmail" else parse_status(body)


def _cached_ok(settings: Settings, site_host: str) -> bool | None:
    with connect(settings) as conn:
        row = conn.execute("SELECT fetched_at,last_error_at FROM status_cache WHERE site_host=%s", (site_host,)).fetchone()
    if row is None:
        return None
    return bool(row["fetched_at"] and (not row["last_error_at"] or row["fetched_at"] > row["last_error_at"]))


def refresh_one(settings: Settings, site_host: str, *, client: httpx.Client | None = None, min_interval: int = 15,
                wait: bool = False) -> bool:
    """Fetch one verified source now; retain prior data as stale on failure.

    Request-path callers leave wait=False: when another thread is already
    fetching this host, they return the cached verdict instead of queueing
    on the per-host lock. Only a host with no cache row yet waits, so the
    first lookup still answers fresh. The cron passes wait=True.
    """
    source = SOURCES.get(site_host)
    if source is None:
        with connect(settings) as conn:
            row = conn.execute(
                "SELECT source_name,status_page_url,api_url FROM status_sources WHERE site_host=%s AND enabled=true",
                (site_host,),
            ).fetchone()
        expected_page = f"https://status.{site_host}/"
        if row and row["status_page_url"] == expected_page and row["api_url"] == expected_page + "api/v2/status.json":
            source = Source(row["source_name"], row["status_page_url"], row["api_url"], "discovered_statuspage")
    if source is None:
        return False
    lock = _SOURCE_LOCKS.setdefault(site_host, threading.Lock())
    if not lock.acquire(blocking=wait):
        cached = _cached_ok(settings, site_host)
        if cached is not None:
            return cached
        lock.acquire()
    try:
        if min_interval:
            with connect(settings) as conn:
                previous = conn.execute(
                    "SELECT fetched_at,last_error_at FROM status_cache WHERE site_host=%s",
                    (site_host,),
                ).fetchone()
            if previous:
                fetched_at, error_at = previous["fetched_at"], previous["last_error_at"]
                latest = max((t for t in (fetched_at, error_at) if t is not None), default=None)
                if latest and latest >= datetime.now(timezone.utc) - timedelta(seconds=min_interval):
                    return bool(fetched_at and (not error_at or fetched_at > error_at))
        own_client = client is None
        client = client or httpx.Client(timeout=4.0, follow_redirects=False, trust_env=False)
        try:
            indicator, updated_at = _read_source(source, client)
            with connect(settings) as conn:
                conn.execute(
                    """INSERT INTO status_cache (site_host,indicator,source_updated_at,fetched_at,last_error_at,last_error_detail)
                       VALUES (%s,%s,%s,now(),NULL,NULL) ON CONFLICT (site_host) DO UPDATE SET
                       indicator=EXCLUDED.indicator,source_updated_at=EXCLUDED.source_updated_at,
                       fetched_at=EXCLUDED.fetched_at,last_error_at=NULL,last_error_detail=NULL""",
                    (site_host, indicator, updated_at),
                )
            return True
        except (httpx.HTTPError, urllib3.exceptions.HTTPError, OSError, ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
            # Keep why the read failed: a bare "stale" would show a 403 from the feed and a timeout as the same word.
            with connect(settings) as conn:
                conn.execute(
                    """INSERT INTO status_cache (site_host,indicator,fetched_at,last_error_at,last_error_detail)
                       VALUES (%s,'unknown',NULL,now(),%s) ON CONFLICT (site_host) DO UPDATE SET
                       last_error_at=now(),last_error_detail=EXCLUDED.last_error_detail""",
                    (site_host, failure_label(exc)),
                )
            return False
        finally:
            if own_client:
                client.close()
    finally:
        lock.release()


def refresh_batch(settings: Settings, *, client: httpx.Client | None = None, limit: int = 20) -> dict:
    """Periodically refresh only sources registered by an actual lookup."""
    if not 1 <= limit <= 20:
        raise ValueError("limit must be 1-20")
    with connect(settings) as conn:
        rows = conn.execute(
            """SELECT s.site_host FROM status_sources s
               JOIN queried_hosts q ON q.site_host=s.site_host
               LEFT JOIN status_cache c ON c.site_host=s.site_host
               WHERE s.enabled=true AND
                 COALESCE(GREATEST(c.fetched_at,c.last_error_at),'-infinity'::timestamptz)
                   < now() - interval '1 hour'
               ORDER BY GREATEST(c.fetched_at,c.last_error_at) NULLS FIRST,s.site_host LIMIT %s""",
            (limit,),
        ).fetchall()
    result = {"checked": 0, "updated": 0, "failed": 0}
    for row in rows:
        result["checked"] += 1
        if refresh_one(settings, row["site_host"], client=client, wait=True):
            result["updated"] += 1
        else:
            result["failed"] += 1
    return result


def main() -> None:
    database_url = os.environ.get("AHN_DATABASE_URL", "")
    if not database_url:
        raise RuntimeError("AHN_DATABASE_URL is required")
    settings = Settings(database_url, "unused-by-status-refresh")
    result = refresh_batch(settings)
    print(f"status_refresh checked={result['checked']} updated={result['updated']} failed={result['failed']}")


if __name__ == "__main__":
    main()
