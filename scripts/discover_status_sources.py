"""Stage 1 of the status-source pipeline: find and verify publisher status pages for a list of hosts.

Curation aid; not run in CI and never imported by the service. Nothing it writes is applied to
ahn/status_refresh.py automatically: a curator reads final.csv and edits the mapping by hand.

    python scripts/discover_status_sources.py --hosts-file work/hosts.txt --batch batch-1
    python scripts/discover_status_sources.py --hosts-file work/prior/status-excluded.csv --batch retry-0
    python scripts/discover_status_sources.py --candidates-csv work/found.csv --batch retry-1   # verify addresses found by hand
    python scripts/discover_status_sources.py --classify --batch classify-0     # which service runs each linked page

Per host (a plain list, or a CSV with a ``host`` column):

1. Skip hosts that already have a mapping (SOURCES / OFFICIAL_STATUS_PAGES) or that are not
   ``aggregate_eligible_host`` / not the apex form that ``policy_host`` answers for.
2. Candidates: (a) ``status.<apex>`` when its DNS CNAME points at a known status service,
   (b) status links found on the host's own OFFICIAL_PATHS documentation pages, (c) the Statusphere list when
   ``--statusphere FILE`` is given (skipped, and said so in the summary, when the file is absent),
   (d) common name rules (``status.<apex>``, ``status.<name>.com``, ``<name>status.com``, ...). A candidate whose
   name does not resolve to a public address is never contacted.
3. Verification of a candidate: ``/api/v2/status.json`` that ``parse_status()`` accepts = ``feed``; otherwise a 200 page
   whose title or host reads like a status page = ``link``; 401/403/429/202, a bot wall or an unreadable page =
   ``blocked`` (unverified); anything else is dropped.
4. Ownership evidence, decided by rule: A = the status host shares the publisher's registrable domain (shared hosting
   suffixes such as github.io or statuspage.io never count); B = the publisher's own ``status.<apex>`` redirects to it;
   C = a documentation page from OFFICIAL_PATHS links to it (the page URL is recorded). A candidate with none of the
   three, and every blocked one, is written as ``needs_review`` for the Sonnet stage. A blocked page still gets C when
   a publisher document names it, or A when its publisher-domain host is delegated by DNS to a known status product.
   ``--candidates-csv`` (host,candidate[,evidence_url]) runs the same verification on addresses found by hand: an
   evidence_url must be on the publisher's own domain and must mention the candidate's host.
   ``--classify`` names the product behind every page in OFFICIAL_STATUS_PAGES (DNS target, header, page text, or the
   product's documented public JSON) and notes whether the Statuspage-format endpoint already parses.

Etiquette (same as scripts/verify_official_paths.py): at most one HTTPS request per second overall, 10 s timeout,
the curation User-Agent, redirects followed by hand (https only, every hop checked for a public address and rate
limited). Only status hosts and publisher documentation pages already in OFFICIAL_PATHS are read; the site's own
front page is never probed and no robots.txt is fetched.

Output under work/status-discovery/<batch>/: candidates.csv (every candidate and what was seen), final.csv (same
columns as the first status batch: host,name,kind,page,api,indicator,evidence,evidence_url), excluded.csv
(host,candidate,reason,disposition where disposition is "excluded" or "needs_review"), summary.json, and cache.json
so an interrupted run resumes without repeating requests.
"""

from __future__ import annotations

import argparse
import csv
from concurrent.futures import ThreadPoolExecutor
import html.parser
import importlib.util
import ipaddress
import json
import re
import socket
import sys
import time
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import dns.exception
import dns.resolver
import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ahn.official_paths import OFFICIAL_PATHS  # noqa: E402
from ahn.service import PUBLIC_SUFFIXES_2, aggregate_eligible_host, policy_host  # noqa: E402
from ahn.status_refresh import OFFICIAL_STATUS_PAGES, SOURCES, parse_status  # noqa: E402

_spec = importlib.util.spec_from_file_location("verify_official_paths", ROOT / "scripts" / "verify_official_paths.py")
_verify = importlib.util.module_from_spec(_spec)
sys.modules.setdefault("verify_official_paths", _verify)
_spec.loader.exec_module(_verify)  # PUBLISHER_DOMAINS, PRIVATE_SUFFIXES and the title/bot-wall patterns live there
PUBLISHER_DOMAINS = _verify.PUBLISHER_DOMAINS
BOT_WALL, NOT_FOUND, SITE_DOWN, SIGN_IN, NOTE_URL = _verify.BOT_WALL, _verify.NOT_FOUND, _verify.SITE_DOWN, _verify.SIGN_IN, _verify.NOTE_URL

USER_AGENT = _verify.USER_AGENT
MIN_INTERVAL = 1.0
TIMEOUT = 10.0
MAX_HOPS = 5
JSON_CAP = 32_768
TITLE_CAP = 65_536
DOC_CAP = 786_432
FINGERPRINT_CAP = 262_144
WORK = ROOT / "work" / "status-discovery"
JSON_PATHS = {"Instatus": "/summary.json", "Better Stack": "/index.json"}  # public JSON of products this script can recognise

# Domains whose subdomains belong to many different owners: sharing one proves nothing about ownership.
SHARED_HOSTING = frozenset(_verify.PRIVATE_SUFFIXES | {
    "statuspage.io", "stspg-customer.com", "instatus.com", "betteruptime.com", "betterstack.com", "statuspal.io",
    "hostedstatus.com", "freshstatus.io", "uptimerobot.com", "status.io", "sorryapp.com", "hund.io", "onlineornot.com",
    "readme.io", "notion.site", "herokuapp.com", "azurewebsites.net", "cloudfront.net", "pages.github.com",
})

# Third-party status products, identified by the CNAME target (suffix), a response header or a page marker.
STATUS_SERVICES: dict[str, dict[str, tuple[str, ...]]] = {
    "Atlassian Statuspage": {"dns": ("stspg-customer.com", "statuspage.io"), "headers": ("x-statuspage-version",),
                              "body": ("atlassian statuspage", "powered by statuspage", "statuspage.io/embed")},
    "Instatus": {"dns": ("instatus.com",), "headers": (), "body": ("powered by instatus", "instatus.com")},
    "Better Stack": {"dns": ("betteruptime.com", "betterstack.com"), "headers": (),
                     "body": ("powered by better stack", "betteruptime.com", "betterstack.com")},
    "incident.io": {"dns": ("incident.io",), "headers": (), "body": ("powered by incident.io", "incident.io/")},
    "Status.io": {"dns": ("status.io",), "headers": (), "body": ("status.io", "statuspage by status.io")},
    "Statuspal": {"dns": ("statuspal.io",), "headers": (), "body": ("statuspal.io", "powered by statuspal")},
    "Uptime Robot": {"dns": ("uptimerobot.com",), "headers": (), "body": ("uptimerobot.com",)},
    "Freshstatus": {"dns": ("freshstatus.io",), "headers": (), "body": ("freshstatus.io", "powered by freshstatus")},
    "Hund": {"dns": ("hund.io",), "headers": (), "body": ("hund.io",)},
    "StatusCast": {"dns": ("statuscast.com",), "headers": (), "body": ("statuscast",)},
    "Sorry": {"dns": ("sorryapp.com",), "headers": (), "body": ("sorryapp.com",)},
    "OnlineOrNot": {"dns": ("onlineornot.com",), "headers": (), "body": ("onlineornot.com",)},
    "PagerDuty Status": {"dns": ("pagerduty.com",), "headers": (), "body": ("status page by pagerduty",)},
    "Cachet": {"dns": (), "headers": (), "body": ("powered by cachet", "cachethq")},
    "Uptime Kuma": {"dns": (), "headers": (), "body": ("uptime kuma",)},
    "Uptime.com": {"dns": ("uptime.com",), "headers": (), "body": ("| uptime.com", "uptime.com status")},
    "Pingdom": {"dns": ("pingdom.com",), "headers": (), "body": ("pingdom public reports", "pingdom.com")},
}
# Statuspage / vendor product hosts that are never a customer's status page.
NOT_A_CUSTOMER_PAGE = frozenset({
    "statuspage.io", "www.statuspage.io", "developer.statuspage.io", "support.atlassian.com", "manage.statuspage.io",
    "metastatuspage.com", "status.io", "www.status.io", "instatus.com", "www.instatus.com", "betteruptime.com",
    "betterstack.com", "www.betterstack.com", "incident.io", "www.incident.io", "status.incident.io",
})
# Third-party aggregators and social sites: a link there is never the publisher's own status page.
NOT_A_PUBLISHER_PAGE = frozenset({
    "statusgator.com", "isitdownrightnow.com", "downdetector.com", "updownradar.com", "uptime.com", "fruits.co",
    "twitter.com", "x.com", "facebook.com", "linkedin.com", "youtube.com", "github.com", "medium.com", "reddit.com",
    "discord.com", "slack.com", "stackoverflow.com", "wikipedia.org", "google.com", "apple.com",
})
STATUS_LINK_TEXT = re.compile(r"^(?:(?:api|system|service|platform|network|cloud|developer|site|server|operational|current)\s+)?status"
                              r"(?:\s+(?:page|dashboard))?$|^(?:service|system)\s+health$|^uptime$", re.IGNORECASE)
STATUS_TITLE = re.compile(r"\b(status|statut|estado|статус|состояние|incidents?|uptime|operational|outages?|health|availability)\b|障害|稼働|상태", re.IGNORECASE)
TITLE = re.compile(rb"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)


# ---------------------------------------------------------------------------------------------------------------
# Pure helpers (covered by tests/test_discover_status_sources.py)

def registrable(host: str) -> str:
    labels = host.lower().rstrip(".").split(".")
    if len(labels) >= 3 and (".".join(labels[-2:]) in PUBLIC_SUFFIXES_2 or ".".join(labels[-2:]) in _verify.PRIVATE_SUFFIXES):
        return ".".join(labels[-3:])
    return ".".join(labels[-2:])


def is_shared_hosting(host: str) -> bool:
    host = host.lower().rstrip(".")
    return any(host == domain or host.endswith("." + domain) for domain in SHARED_HOSTING)


def name_label(site_host: str) -> str:
    """The brand label of a site host: perplexity.ai -> perplexity, bbc.co.uk -> bbc."""
    return registrable(site_host).split(".")[0]


def normalise_host(raw: str) -> str:
    raw = raw.strip().lower()
    if "://" in raw:
        raw = urlsplit(raw).hostname or ""
    return raw.split("/")[0].rstrip(".")


def read_hosts(path: Path) -> list[str]:
    """Hosts from a plain list (``#`` comments allowed) or from the ``host`` column of a CSV; order kept, duplicates dropped."""
    if path.suffix.lower() == ".csv":
        with path.open(encoding="utf-8", newline="") as stream:
            raw = [row["host"] for row in csv.DictReader(stream)]
    else:
        raw = [line.split("#")[0] for line in path.read_text(encoding="utf-8").splitlines()]
    seen: dict[str, None] = {}
    for item in raw:
        host = normalise_host(item)
        if host:
            seen.setdefault(host, None)
    return list(seen)


def mapped_hosts() -> set[str]:
    return set(SOURCES) | set(OFFICIAL_STATUS_PAGES)


def skip_reason(site_host: str, mapped: set[str]) -> str | None:
    """Why a host is not worth discovering for, or None."""
    if site_host in mapped:
        return "already_mapped"
    if not aggregate_eligible_host(site_host):
        return "not_aggregate_eligible"
    if policy_host(site_host) != site_host:
        return "folds_onto_apex"
    return None


def service_for_cname(target: str) -> str | None:
    """Name of the known status product a DNS target (or host) belongs to."""
    target = target.lower().rstrip(".")
    for service, markers in STATUS_SERVICES.items():
        if any(target == suffix or target.endswith("." + suffix) for suffix in markers["dns"]):
            return service
    return None


def candidate_urls(site_host: str) -> list[tuple[str, str]]:
    """(host, origin) name-rule candidates for a site: first-party status subdomain, then common vendor-style names."""
    name, reg = name_label(site_host), registrable(site_host)
    rules = [
        (f"status.{reg}", "status_subdomain"),
        (f"status.{name}.com", "name_rule"),
        (f"{name}status.com", "name_rule"),
        (f"{name}-status.com", "name_rule"),
        (f"{name}.statuspage.io", "name_rule"),
    ]
    out: dict[str, str] = {}
    for host, origin in rules:
        if host != site_host:
            out.setdefault(host, origin)
    return [(host, origin) for host, origin in out.items()]


class _Links(html.parser.HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[tuple[str, str]] = []
        self._href: str | None = None
        self._text: list[str] = []

    def flush(self) -> None:
        if self._href is not None:
            self.links.append((self._href, " ".join("".join(self._text).split())))
            self._href = None

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self.flush()  # an unclosed <a> ends where the next one begins
            self._href = dict(attrs).get("href")
            self._text = []

    def handle_data(self, data):
        if self._href is not None:
            self._text.append(data)

    def handle_endtag(self, tag):
        if tag == "a":
            self.flush()


def extract_links(page_html: str, base_url: str) -> list[tuple[str, str]]:
    parser = _Links()
    try:
        parser.feed(page_html)
        parser.close()
        parser.flush()
    except Exception:  # malformed markup must not stop a run; whatever was parsed so far still counts
        pass
    out = []
    for href, text in parser.links:
        url = urljoin(base_url, href.strip())
        if urlsplit(url).scheme == "https":
            out.append((url.split("#")[0], text))
    return out


def status_links(page_html: str, base_url: str) -> list[str]:
    """https URLs on a documentation page that read like a link to the publisher's status page."""
    base_host = urlsplit(base_url).hostname or ""
    found: dict[str, None] = {}
    for url, text in extract_links(page_html, base_url):
        parts = urlsplit(url)
        host = (parts.hostname or "").lower()
        if not host or parts.port not in (None, 443) or parts.username:
            continue
        if host in NOT_A_CUSTOMER_PAGE or any(host == d or host.endswith("." + d) for d in NOT_A_PUBLISHER_PAGE):
            continue
        by_host = any(re.search(r"status", label) for label in host.split(".")[:-1]) and host != base_host
        by_text = bool(STATUS_LINK_TEXT.match(text.strip(" :."))) and host != base_host
        if by_host or by_text:
            found.setdefault(f"https://{host}{parts.path if parts.path not in ('', '/') else '/'}", None)
    return list(found)


STRONG_TITLE = re.compile(r"\b(?:system|service|api|platform|network|server|site|cloud)\s+(?:status|health)\b|all systems|operational|\bincidents?\b|\boutages?\b|uptime", re.IGNORECASE)
STATUS_PATH_SEGMENT = re.compile(r"(?:[a-z]+[-_])?status(?:[-_]page)?")


def looks_like_status_page(title: str, final_url: str, *, site_host: str = "", cname_service: str = "", supplied: bool = False) -> bool:
    """Whether a fetched page is a status page rather than a home page, a parked name or a user-profile 'status'.

    A page that landed on the publisher's own site (a redirect from status.<apex> to the main site) must say outright that it is a
    system/service status or health page. Elsewhere a status word in the title is enough, or a status-named host delegated by DNS to a
    known status product. A hand-supplied address is also accepted on a status path segment."""
    parts = urlsplit(final_url)
    host = (parts.hostname or "").lower()
    if host in NOT_A_CUSTOMER_PAGE or host.endswith(".atlassian.com") and host != "status.atlassian.com" or host == "atlassian.com":
        return False  # an unclaimed custom domain lands on the vendor's own marketing or help pages
    if BOT_WALL.search(title) or NOT_FOUND.search(title) or SITE_DOWN.search(title) or not title:
        return False
    status_host = any("status" in label for label in host.split(".")[:-1])
    if site_host and registrable(host) == registrable(site_host) and not status_host:
        return bool(STRONG_TITLE.search(title)) and not re.search(r"/(?:profile|user|users|u|members?)/", parts.path.lower() + "/")
    if STATUS_TITLE.search(title):
        return True
    if status_host and cname_service:
        return True
    return supplied and any(STATUS_PATH_SEGMENT.fullmatch(segment) for segment in parts.path.lower().split("/"))


def docs_publisher_domains(site_host: str, listed_url: str) -> set[str]:
    return {registrable(site_host), registrable(urlsplit(listed_url).hostname or "")} | PUBLISHER_DOMAINS.get(site_host, set())


def judge_evidence(site_host: str, status_host: str, *, via_own_status: bool = False, doc_pages: list[str] | None = None) -> tuple[str, str, str]:
    """Ownership rule for a status host: ("A"|"B"|"C"|"needs_review", detail, evidence_url)."""
    status_host = status_host.lower()
    if registrable(status_host) == registrable(site_host) and not is_shared_hosting(status_host):
        return "A", "status page is on the publisher's registered domain", ""
    if via_own_status and not is_shared_hosting(site_host):
        return "B", f"the publisher's own status.{registrable(site_host)} redirects to this page", ""
    if doc_pages:
        return "C", "the publisher's own documentation links to this page", doc_pages[0]
    return "needs_review", "no registered-domain match, no own-subdomain redirect and no documentation link", ""


def title_of(body: bytes) -> str:
    found = TITLE.search(body[:TITLE_CAP])
    return " ".join(found.group(1).decode("utf-8", "replace").split())[:120] if found else ""


def fingerprint(cnames: list[str], headers: dict[str, str], body: str) -> tuple[str, str]:
    """(service, how) for a status page from its DNS chain, response headers and page text; ("self_built_or_unknown", "") if none match."""
    for target in cnames:
        service = service_for_cname(target)
        if service:
            return service, f"dns:{target.rstrip('.')}"
    lowered = {key.lower(): value for key, value in headers.items()}
    text = body.lower()
    for service, markers in STATUS_SERVICES.items():
        for header in markers["headers"]:
            if header in lowered:
                return service, f"header:{header}"
    for service, markers in STATUS_SERVICES.items():
        for needle in markers["body"]:
            if needle in text:
                return service, f"marker:{needle}"
    return "self_built_or_unknown", ""


def recognise_json(path: str, body: bytes) -> str | None:
    """Service whose public JSON answer this is: Instatus ``/summary.json`` or Better Stack ``/index.json``; None otherwise."""
    try:
        data = json.loads(body)
    except (ValueError, RecursionError):
        return None
    if not isinstance(data, dict):
        return None
    if path == "/summary.json" and isinstance(data.get("page"), dict) and data["page"].get("status") in {"UP", "HASISSUES", "UNDERMAINTENANCE"}:
        return "Instatus"
    attributes = data.get("data", {}).get("attributes") if isinstance(data.get("data"), dict) else None
    if path == "/index.json" and isinstance(attributes, dict) and data["data"].get("type") == "status_page" and "aggregate_state" in attributes:
        return "Better Stack"
    return None


# ---------------------------------------------------------------------------------------------------------------
# Network

class Net:
    """One request per second overall, manual redirects, public addresses only, https only."""

    def __init__(self, min_interval: float = MIN_INTERVAL, max_requests: int | None = None) -> None:
        self.client = httpx.Client(
            http2=False, follow_redirects=False, timeout=httpx.Timeout(TIMEOUT), trust_env=False,
            headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8", "Accept-Language": "en"},
        )
        self.min_interval, self.max_requests = min_interval, max_requests
        self.last, self.requests = 0.0, 0
        self._public: dict[str, bool] = {}
        self._cname: dict[str, list[str]] = {}

    def public_host(self, host: str) -> bool:
        if host not in self._public:
            try:
                addresses = {item[4][0] for item in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)}
                self._public[host] = bool(addresses) and len(addresses) <= 16 and all(ipaddress.ip_address(ip).is_global for ip in addresses)
            except (OSError, ValueError):
                self._public[host] = False
        return self._public[host]

    def cname_chain(self, host: str) -> list[str]:
        """Targets of the CNAME chain for host ([] when none or unresolvable). DNS only; no HTTP."""
        if host not in self._cname:
            chain, current = [], host
            for _ in range(6):
                try:
                    answers = dns.resolver.resolve(current, "CNAME", lifetime=3)
                except (dns.exception.DNSException, OSError):
                    break
                current = str(next(iter(answers)).target).lower()
                chain.append(current)
            self._cname[host] = chain
        return self._cname[host]

    def _get(self, url: str, cap: int, want_body: bool) -> tuple[int, str | None, bytes, dict[str, str]]:
        if self.max_requests is not None and self.requests >= self.max_requests:
            raise RuntimeError("request budget exhausted")
        wait = self.min_interval - (time.monotonic() - self.last)
        if wait > 0:
            time.sleep(wait)
        self.last = time.monotonic()
        self.requests += 1
        with self.client.stream("GET", url) as response:
            body = b""
            if response.status_code == 200 and want_body:
                for chunk in response.iter_bytes():
                    body += chunk
                    if len(body) >= cap:
                        break
            return response.status_code, response.headers.get("location"), body, dict(response.headers)

    def fetch(self, url: str, *, cap: int = TITLE_CAP) -> dict:
        """GET with hand-followed redirects. Keys: status, final_url, chain (hosts visited), body, headers, error."""
        current, chain, retried = url, [urlsplit(url).hostname or ""], False
        result = {"status": None, "final_url": url, "chain": chain, "body": b"", "headers": {}, "error": ""}
        for _ in range(MAX_HOPS + 2):
            parts = urlsplit(current)
            host = parts.hostname or ""
            if parts.scheme != "https" or parts.port not in (None, 443) or parts.username or not self.public_host(host):
                result["error"] = "non_public_or_non_https_hop"
                return result
            try:
                status, location, body, headers = self._get(current, cap, True)
            except httpx.HTTPError as exc:
                if not retried:
                    retried = True
                    continue
                result["error"] = type(exc).__name__
                return result
            if status in {429, 500, 502, 503, 504} and not retried:
                retried = True
                continue
            result.update(status=status, final_url=current, body=body, headers=headers)
            if status in {301, 302, 303, 307, 308} and location and len(chain) <= MAX_HOPS:
                current = urljoin(current, location)
                chain.append(urlsplit(current).hostname or "")
                continue
            return result
        result["error"] = "too_many_redirects"
        return result


# ---------------------------------------------------------------------------------------------------------------
# Candidate verification

@dataclass
class Probe:
    host: str
    origin: str
    kind: str = "none"              # feed | link | blocked | none
    page: str = ""
    api: str = ""
    indicator: str = ""
    title: str = ""
    page_name: str = ""
    final_host: str = ""
    chain: list[str] = field(default_factory=list)
    cname_service: str = ""
    note: str = ""


def probe_candidate(net: Net, host: str, origin: str, path: str = "/", site_host: str = "") -> Probe:
    """Verify one candidate. A candidate with a path other than "/" is a specific page: only that page is read."""
    probe = Probe(host=host, origin=origin)
    if not net.public_host(host):
        probe.note = "no_public_dns"
        return probe
    chain = net.cname_chain(host)
    probe.cname_service = next((service_for_cname(target) for target in chain if service_for_cname(target)), "") or ""
    api = net.fetch(f"https://{host}/api/v2/status.json", cap=JSON_CAP) if path == "/" else {"status": None, "chain": [host]}
    probe.chain = api["chain"]
    if api["status"] == 200:
        final = urlsplit(api["final_url"])
        try:
            body = json.loads(api["body"])
            parse_status(body)
            page = body.get("page") if isinstance(body, dict) else None
            if final.path.endswith("/api/v2/status.json") and isinstance(page, dict):
                prefix = final.path[: -len("api/v2/status.json")]
                probe.kind, probe.final_host = "feed", final.hostname or ""
                probe.page, probe.api = f"https://{final.hostname}{prefix}", f"https://{final.hostname}{prefix}api/v2/status.json"
                probe.indicator = body["status"]["indicator"]
                probe.page_name = str(page.get("name", ""))[:100]
                return probe
        except (ValueError, TypeError, KeyError, RecursionError):
            pass
    page = net.fetch(f"https://{host}{path}")
    probe.chain = page["chain"]
    final = urlsplit(page["final_url"])
    probe.final_host = final.hostname or ""
    if page["error"]:
        probe.note = f"unreachable:{page['error']}"
        return probe
    if page["status"] in {202, 401, 403, 429}:
        probe.kind, probe.note, probe.page = "blocked", f"blocked:{page['status']}", f"https://{probe.final_host}{final.path or '/'}"
        return probe
    if page["status"] != 200:
        probe.note = f"status:{page['status']}"
        return probe
    probe.title = title_of(page["body"])
    if "challenge" in final.path.lower() or probe.final_host.startswith("unblock.") or BOT_WALL.search(probe.title):
        probe.kind, probe.note, probe.page = "blocked", "blocked:challenge", f"https://{probe.final_host}{final.path or '/'}"
    elif SIGN_IN.search(final.path):
        probe.note = "sign_in_page"
    elif final.path.rstrip("/").endswith("/inactive"):
        probe.note = "inactive_statuspage_placeholder"  # Statuspage's answer for a subdomain nobody runs
    elif looks_like_status_page(probe.title, page["final_url"], site_host=site_host, cname_service=probe.cname_service, supplied=origin == "supplied"):
        probe.kind = "link"
        probe.page = f"https://{probe.final_host}{final.path or '/'}"
        if path == "/" and probe.final_host != host and final.path in {"", "/"}:
            # A redirect from the candidate to another host often drops the path, so the feed check above missed it: ask the landing host.
            landed = net.fetch(f"https://{probe.final_host}/api/v2/status.json", cap=JSON_CAP)
            try:
                if landed["status"] == 200 and urlsplit(landed["final_url"]).path == "/api/v2/status.json":
                    body = json.loads(landed["body"])
                    parse_status(body)
                    probe.kind, probe.page = "feed", f"https://{probe.final_host}/"
                    probe.api, probe.indicator = f"https://{probe.final_host}/api/v2/status.json", body["status"]["indicator"]
                    probe.page_name = str((body.get("page") or {}).get("name", ""))[:100]
            except (ValueError, TypeError, KeyError, RecursionError):
                pass
    else:
        probe.note = "not_a_status_page"  # includes status.<apex> redirecting to the publisher's home page
    return probe


def docs_status_links(net: Net, site_host: str, cache: dict) -> dict[str, list[str]]:
    """status link host -> [doc page URLs that link to it], from this host's OFFICIAL_PATHS pages."""
    entry = OFFICIAL_PATHS.get(site_host)
    if not entry:
        return {}
    urls = [u for u in (entry["api_docs"], entry["developer_portal"]) if u]
    urls += [m.rstrip(".") for m in NOTE_URL.findall(entry["note"] or "")]
    out: dict[str, list[str]] = {}
    for url in dict.fromkeys(urls):
        if url not in cache:
            page = net.fetch(url, cap=DOC_CAP)
            final = urlsplit(page["final_url"])
            owned = (page["status"] == 200 and not page["error"] and final.scheme == "https"
                     and registrable(final.hostname or "") in docs_publisher_domains(site_host, url)
                     and not BOT_WALL.search(title_of(page["body"])))
            cache[url] = status_links(page["body"].decode("utf-8", "replace"), page["final_url"]) if owned else []
        for link in cache[url]:
            out.setdefault(urlsplit(link).hostname or "", []).append(url)
    return out


def display_name(site_host: str, probe: Probe) -> str:
    base = (OFFICIAL_PATHS.get(site_host) or {}).get("name") or probe.page_name or name_label(site_host).title()
    return base if base.lower().endswith("status") else f"{base} Status"


def scope_flag(site_host: str, probe: Probe) -> str:
    """Raised when the page's own name does not contain the site's brand label (multi-product or renamed pages)."""
    label = re.sub(r"[^a-z0-9]", "", name_label(site_host))
    seen = re.sub(r"[^a-z0-9]", "", f"{probe.page_name} {probe.title} {probe.final_host}".lower())
    return "" if label and label in seen else "page_name_differs"


def rank(probe: Probe, verdict: str) -> tuple:
    return (verdict in {"A", "B", "C"}, probe.kind == "feed", probe.kind == "link", probe.kind == "blocked")


def publisher_domains(site_host: str) -> set[str]:
    return {registrable(site_host)} | PUBLISHER_DOMAINS.get(site_host, set())


def evidence_page_hosts(net: Net, site_host: str, evidence_url: str, cache: dict) -> set[str]:
    """Hosts linked from a supplied evidence page, provided that page is itself on the publisher's own domain."""
    key = f"evidence:{site_host}:{evidence_url}"
    if key not in cache:
        page = net.fetch(evidence_url, cap=DOC_CAP)
        final = urlsplit(page["final_url"])
        owned = (page["status"] == 200 and not page["error"] and final.scheme == "https"
                 and registrable(final.hostname or "") in publisher_domains(site_host)
                 and not BOT_WALL.search(title_of(page["body"])))
        text = page["body"].decode("utf-8", "replace") if owned else ""
        mentioned = {m.group(1).lower() for m in re.finditer(r"https://([a-z0-9][a-z0-9.-]*[a-z0-9])", text, re.IGNORECASE)}
        cache[key] = sorted(({urlsplit(url).hostname or "" for url, _ in extract_links(text, page["final_url"])} | mentioned) - {""})
    return set(cache[key])


def discover_host(net: Net, site_host: str, statusphere: dict[str, list[str]], docs_cache: dict,
                  supplied: list[tuple[str, str]] | None = None) -> dict:
    """Candidates for one host. ``supplied`` = [(candidate URL, evidence page URL or "")] found by hand: with it only those are probed."""
    directed = supplied is not None
    docs = {} if directed else docs_status_links(net, site_host, docs_cache)
    candidates: dict[tuple[str, str], str] = {}  # (host, path) -> origin
    if directed:
        for url, _ in supplied:
            parts = urlsplit(url)
            candidates.setdefault((parts.hostname or "", parts.path or "/"), "supplied")
        for _, evidence_url in supplied:
            if evidence_url:
                for link_host in evidence_page_hosts(net, site_host, evidence_url, docs_cache):
                    docs.setdefault(link_host, []).append(evidence_url)
    else:
        for host, origin in candidate_urls(site_host):
            if origin == "status_subdomain" and any(service_for_cname(target) for target in net.cname_chain(host)):
                origin = "dns_cname_status_service"
            candidates.setdefault((host, "/"), origin)
        for host in docs:
            candidates.setdefault((host, "/"), "docs_link")
        for host in statusphere.get(re.sub(r"[^a-z0-9]", "", name_label(site_host)), []):
            candidates.setdefault((host, "/"), "statusphere")
    probes: list[tuple[Probe, tuple[str, str, str]]] = []
    for (host, path), origin in candidates.items():
        probe = probe_candidate(net, host, origin, path, site_host)
        final_host = probe.final_host or host
        doc_pages = list(dict.fromkeys(docs.get(host, []) + docs.get(final_host, [])))
        own = f"status.{registrable(site_host)}"
        via_own = probe.chain[:1] == [own] and registrable(final_host) != registrable(site_host) and probe.kind in {"feed", "link"}
        if probe.kind in {"feed", "link"}:
            judged = judge_evidence(site_host, final_host, via_own_status=via_own, doc_pages=doc_pages)
        elif probe.kind == "blocked":
            judged = judge_evidence(site_host, final_host, doc_pages=doc_pages)
            if doc_pages:  # the publisher's own page names it; the refusal only stops us reading what it says
                judged = ("C", f"the publisher's own documentation links to this page; the page itself refused the automated check ({probe.note})", doc_pages[0])
            elif judged[0] == "A" and probe.cname_service:  # the page refused us, but DNS says the publisher's own host is delegated to a status product
                judged = ("A", f"{judged[1]}; fetch refused ({probe.note}) but DNS delegates {host} to {probe.cname_service}", "")
            elif judged[0] == "A":
                judged = ("needs_review", f"on the publisher's domain but fetch refused ({probe.note}) and no status-product DNS delegation", "")
        else:
            judged = ("needs_review", probe.note, "")
        probes.append((probe, judged))
    return {"site_host": site_host, "probes": probes}

def recheck(probe: Probe, site_host: str) -> Probe:
    """Apply the current page rule to a probe read from an older cache, so rule fixes never need new requests."""
    if probe.kind == "link" and probe.title and not looks_like_status_page(probe.title, probe.page, site_host=site_host,
                                                                          cname_service=probe.cname_service, supplied=probe.origin == "supplied"):
        return replace(probe, kind="none", page="", note="not_a_status_page")
    return probe


def decide(site_host: str, probes: list[tuple[Probe, tuple[str, str, str]]]) -> tuple[str, Probe | None, tuple[str, str, str] | None, str]:
    """(disposition, best probe, its evidence, reason) with disposition final | needs_review | excluded."""
    live = [(p, j) for p, j in probes if p.kind in {"feed", "link", "blocked"}]
    if not live:
        notes = " | ".join(f"https://{p.host}/ {p.note or p.kind}" for p, _ in probes[:6])
        return "excluded", None, None, f"no status page found: {notes}"
    best_probe, best_judged = max(live, key=lambda item: rank(item[0], item[1][0]))
    if best_judged[0] in {"A", "B", "C"}:
        return "final", best_probe, best_judged, ""
    if best_probe.kind == "blocked":
        return "needs_review", best_probe, best_judged, f"status page refused the automated check ({best_probe.note}); ownership and content unverified"
    return "needs_review", best_probe, best_judged, f"{best_probe.kind} found on {best_probe.final_host} but {best_judged[1]}"


# ---------------------------------------------------------------------------------------------------------------
# Output

FINAL_FIELDS = ["host", "name", "kind", "page", "api", "indicator", "evidence", "evidence_url", "scope_flag"]
EXCLUDED_FIELDS = ["host", "candidate", "reason", "disposition"]
CANDIDATE_FIELDS = ["host", "candidate_host", "origin", "kind", "page", "api", "indicator", "title", "final_host",
                    "redirect_chain", "cname_service", "evidence", "evidence_detail", "evidence_url", "note"]


def write_csv(path: Path, fields: list[str], rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def statusphere_labels(url_host: str, name: str) -> set[str]:
    """Brand labels a Statusphere entry can be matched to: its listed name and the brand part of its address."""
    labels = {re.sub(r"[^a-z0-9]", "", name.lower())}
    brand = url_host.split(".")[0] if is_shared_hosting(url_host) else name_label(url_host)
    labels.add(re.sub(r"[^a-z0-9]", "", brand.lower()))
    return {label for label in labels if len(label) >= 3}


def load_statusphere(path: Path | None) -> tuple[dict[str, list[str]], str]:
    """Brand label -> status hosts from Statusphere's list (https://github.com/metoro-io/statusphere, MIT; the Go
    source ``status_pages.go`` or a JSON list of URLs). (empty, reason) when no file is given or it is missing.
    A listing is only a candidate: ownership still has to come from rule A, B or C."""
    if path is None:
        return {}, "skipped: no --statusphere file given"
    if not path.exists():
        return {}, f"skipped: {path} not found"
    text = path.read_text(encoding="utf-8")
    if path.suffix == ".go":
        entries = re.findall(r'URL:\s*"([^"]+)"\s*,\s*Name:\s*"([^"]*)"', text)
    else:
        data = json.loads(text)
        entries = [(url, "") for url in (data.values() if isinstance(data, dict) else data)]
    out: dict[str, list[str]] = {}
    for url, name in entries:
        host = urlsplit(url if "://" in url else f"https://{url}").hostname
        if host and public_name(host):
            for label in statusphere_labels(host, name):
                if host not in out.setdefault(label, []):
                    out[label].append(host)
    return out, f"loaded {len(entries)} entries, {len(out)} brand labels"


def public_name(host: str) -> bool:
    return "." in host and not host.endswith(".local")


def prewarm_dns(net: Net, hosts: list[str], workers: int = 16) -> None:
    """Resolve every name-rule candidate in parallel first, so the one-request-per-second HTTP loop never waits on DNS.

    DNS answers only; no HTTP request is made here, so the request rate toward status hosts is unchanged.
    """
    names = {candidate for host in hosts for candidate, _ in candidate_urls(host)}
    with ThreadPoolExecutor(max_workers=workers) as pool:
        list(pool.map(net.public_host, names))
        list(pool.map(net.cname_chain, [name for name in names if net.public_host(name)]))


def run_discovery(args) -> int:
    batch_dir = WORK / args.batch
    batch_dir.mkdir(parents=True, exist_ok=True)
    cache_path = batch_dir / "cache.json"
    cache = json.loads(cache_path.read_text(encoding="utf-8")) if cache_path.exists() else {"hosts": {}, "docs": {}}
    statusphere, statusphere_note = load_statusphere(args.statusphere)
    supplied: dict[str, list[tuple[str, str]]] = {}
    if args.candidates_csv:
        with args.candidates_csv.open(encoding="utf-8", newline="") as stream:
            for row in csv.DictReader(stream):
                supplied.setdefault(normalise_host(row["host"]), []).append((row["candidate"].strip(), (row.get("evidence_url") or "").strip()))
        hosts = list(supplied)
    else:
        hosts = read_hosts(args.hosts_file)
    mapped = mapped_hosts()
    net = Net(args.min_interval, args.max_requests)
    skipped: dict[str, list[str]] = {}
    todo = []
    for host in hosts:
        reason = skip_reason(host, mapped)
        (skipped.setdefault(reason, []).append(host) if reason else todo.append(host))
    prewarm_dns(net, [host for host in todo if host not in cache["hosts"]])
    final_rows, excluded_rows, candidate_rows = [], [], []
    counts = {"final": 0, "needs_review": 0, "excluded": 0}
    evidence_counts: dict[str, int] = {}
    started = time.monotonic()
    deadline = next_clock_time(args.stop_at) if args.stop_at else None
    state = {"complete": False, "stopped": ""}

    def flush() -> None:
        """Everything done so far goes to disk: a run that is killed loses at most the last few hosts, and a rerun resumes from cache.json."""
        cache_path.write_text(json.dumps(cache), encoding="utf-8")
        write_csv(batch_dir / "candidates.csv", CANDIDATE_FIELDS, candidate_rows)
        write_csv(batch_dir / "final.csv", FINAL_FIELDS, final_rows)
        write_csv(batch_dir / "excluded.csv", EXCLUDED_FIELDS, excluded_rows)
        summary = {
            "batch": args.batch, "written_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "user_agent": USER_AGENT,
            "complete": state["complete"], "stopped": state["stopped"], "hosts_in": len(hosts),
            "skipped": {reason: len(items) for reason, items in skipped.items()}, "to_process": len(todo), "processed": sum(counts.values()),
            **counts, "evidence": evidence_counts, "requests": net.requests, "statusphere": statusphere_note,
            "seconds": round(time.monotonic() - started),
        }
        (batch_dir / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")

    try:
        for index, host in enumerate(todo, 1):
            if host in cache["hosts"]:
                probes = [(recheck(Probe(**p), host), tuple(j)) for p, j in cache["hosts"][host]]
            else:
                if deadline and datetime.now() >= deadline:
                    state["stopped"] = f"stop time {args.stop_at} reached"
                    print(f"stopping: {state['stopped']}", flush=True)
                    break
                try:
                    probes = discover_host(net, host, statusphere, cache["docs"], supplied.get(host) if args.candidates_csv else None)["probes"]
                except RuntimeError as exc:  # request budget
                    state["stopped"] = str(exc)
                    print(f"stopping: {exc}", flush=True)
                    break
                cache["hosts"][host] = [[asdict(p), list(j)] for p, j in probes]
            disposition, best, judged, reason = decide(host, probes)
            for probe, j in probes:
                candidate_rows.append({"host": host, "candidate_host": probe.host, "origin": probe.origin, "kind": probe.kind, "page": probe.page,
                                       "api": probe.api, "indicator": probe.indicator, "title": probe.title, "final_host": probe.final_host,
                                       "redirect_chain": ">".join(probe.chain), "cname_service": probe.cname_service,
                                       "evidence": j[0] if probe.kind in {"feed", "link"} else "", "evidence_detail": j[1],
                                       "evidence_url": j[2], "note": probe.note})
            counts[disposition] += 1
            if disposition == "final":
                evidence_counts[judged[0]] = evidence_counts.get(judged[0], 0) + 1
                final_rows.append({"host": host, "name": display_name(host, best), "kind": "feed" if best.kind == "feed" else "page",
                                   "page": best.page, "api": best.api, "indicator": best.indicator,
                                   "evidence": f"{judged[0]}:{judged[1]}", "evidence_url": judged[2], "scope_flag": scope_flag(host, best)})
            else:
                excluded_rows.append({"host": host, "candidate": (best.page or f"https://{best.host}/") if best else "", "reason": reason, "disposition": disposition})
            print(f"[{index}/{len(todo)}] {host:<28} {disposition:<12} {(judged[0] if judged else '-'):<13} {best.page if best and best.page else ''}", flush=True)
            if index % 10 == 0:
                flush()
        else:
            state["complete"] = True
    finally:
        flush()
    print(json.dumps(json.loads((batch_dir / "summary.json").read_text(encoding="utf-8")), indent=2))
    return 0


def next_clock_time(clock: str) -> datetime:
    """The next local occurrence of HH:MM (so --stop-at 07:30 started at night means the coming morning)."""
    hour, minute = (int(part) for part in clock.split(":"))
    now = datetime.now()
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    return target if target > now else target + timedelta(days=1)


def run_classify(args) -> int:
    """Which status product runs each page in OFFICIAL_STATUS_PAGES (or --pages-csv with host,page columns)."""
    batch_dir = WORK / args.batch
    batch_dir.mkdir(parents=True, exist_ok=True)
    pairs = {host: url for host, (_, url) in OFFICIAL_STATUS_PAGES.items()}
    if args.pages_csv:
        with args.pages_csv.open(encoding="utf-8", newline="") as stream:
            pairs.update({row["host"]: row["page"] for row in csv.DictReader(stream) if row.get("page")})
    net = Net(args.min_interval, args.max_requests)
    by_url: dict[str, dict] = {}
    for url in dict.fromkeys(pairs.values()):
        host = urlsplit(url).hostname or ""
        chain = net.cname_chain(host)
        page = net.fetch(url, cap=FINGERPRINT_CAP)
        final_host = urlsplit(page["final_url"]).hostname or host
        chain = list(dict.fromkeys(chain + (net.cname_chain(final_host) if final_host != host else [])))
        body = page["body"].decode("utf-8", "replace") if page["status"] == 200 else ""
        service, how = fingerprint(chain, page["headers"], body)
        feed_note = ""
        if service in {"Atlassian Statuspage", "incident.io"}:  # incident.io pages also answer in Statuspage's format: check the standard endpoint again
            api = net.fetch(f"https://{final_host}/api/v2/status.json", cap=JSON_CAP)
            try:
                parse_status(json.loads(api["body"]))
                feed_note = "status.json parses: can become a feed with the existing parser"
            except (ValueError, TypeError, RecursionError):
                feed_note = f"status.json not usable (HTTP {api['status'] or api['error']})"
        if service in JSON_PATHS:  # does the product's documented public JSON answer on this very page?
            reply = net.fetch(f"https://{final_host}{JSON_PATHS[service]}", cap=FINGERPRINT_CAP)  # Better Stack's index.json carries history and is large
            usable = reply["status"] == 200 and recognise_json(JSON_PATHS[service], reply["body"]) == service
            feed_note = f"{JSON_PATHS[service]} answers with the documented shape" if usable else f"{JSON_PATHS[service]} not usable (HTTP {reply['status'] or reply['error']})"
        if service == "self_built_or_unknown" and page["status"] == 200:
            # No marker in DNS or page: ask for the two public JSON shapes that other products serve, on the page's own host.
            for json_path in ("/summary.json", "/index.json"):
                probe = net.fetch(f"https://{final_host}{json_path}", cap=JSON_CAP)
                found = recognise_json(json_path, probe["body"]) if probe["status"] == 200 and urlsplit(probe["final_url"]).hostname == final_host else None
                if found:
                    service, how = found, f"json:{json_path}"
                    break
        if page["status"] != 200 and not how:
            service = f"unclassified:{page['error'] or page['status']}"
        by_url[url] = {"page": url, "final_url": page["final_url"], "http": page["status"] or page["error"], "service": service, "how": how,
                       "title": title_of(page["body"]) if page["body"] else "", "feed": feed_note}
        print(f"{service:<24} {url} ({how or page['status'] or page['error']})", flush=True)
    rows = [{"host": h, **by_url[u]} for h, u in pairs.items()]
    write_csv(batch_dir / "classified.csv", ["host", "page", "final_url", "http", "service", "how", "feed", "title"], rows)
    tally: dict[str, int] = {}
    for row in rows:
        tally[row["service"]] = tally.get(row["service"], 0) + 1
    print(json.dumps(dict(sorted(tally.items(), key=lambda kv: -kv[1])), indent=2))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--hosts-file", type=Path, help="plain host list or CSV with a host column")
    parser.add_argument("--batch", required=True, help="output folder name under work/status-discovery/")
    parser.add_argument("--candidates-csv", type=Path, help="host,candidate[,evidence_url] rows found by hand: only those candidates are probed, and an evidence_url on the publisher's own domain that links the candidate gives C")
    parser.add_argument("--statusphere", type=Path, help="local Statusphere export (optional; skipped when absent)")
    parser.add_argument("--classify", action="store_true", help="classify the provider of every linked status page instead of discovering")
    parser.add_argument("--pages-csv", type=Path, help="with --classify: extra host,page rows to classify")
    parser.add_argument("--min-interval", type=float, default=MIN_INTERVAL, help="seconds between requests (never below the default in a real run)")
    parser.add_argument("--stop-at", help="local HH:MM at which to stop starting new hosts (progress is saved; a rerun resumes)")
    parser.add_argument("--max-requests", type=int, help="stop after this many HTTP requests")
    args = parser.parse_args()
    if args.min_interval < MIN_INTERVAL:
        parser.error("--min-interval below 1 second is not allowed")
    if args.classify:
        return run_classify(args)
    if not args.hosts_file and not args.candidates_csv:
        parser.error("--hosts-file or --candidates-csv is required unless --classify")
    return run_discovery(args)


if __name__ == "__main__":
    raise SystemExit(main())
