"""Live re-verification of the curated official-path table (ahn/official_paths.py).

Read-only curation aid; not run in CI. Each distinct URL in the table (api_docs, developer_portal and
any https URL inside a note) gets one HTTPS GET with a 10 s timeout. Redirects are followed by hand so
every hop is rate limited (at most one request per second overall) and the final hostname can be checked
against the publisher: it must share the registrable domain of the table key, of the listed URL, or of a
domain declared in PUBLISHER_DOMAINS below. Nothing else is fetched (no robots.txt, no crawling).

A URL passes only with final status 200, an https final URL, a publisher-owned final host, no redirect to
a sign-in page or bot challenge and no "not found" page title. Every hop must resolve only to public (global)
addresses. Transport errors, 429 and 5xx get one retry.
Results are "ok", "blocked:*" (401/403/429/WAF 202 or a challenge page: the publisher refused this automated
check, which is not evidence that the page is gone) or another failure code.

Access bundles (OFFICIAL_ACCESS) are checked the same way, URL by URL, with three additions: the pure rules
in access_problems() (shape, auth methods, quotes of at most 200 characters, publisher-owned domains, and for
github.com / raw.githubusercontent.com the publisher's declared organisation), each quote and each MCP
endpoint must appear verbatim in the visible text of its page (whitespace and typographic quotes folded), and
openapi_url must start like an OpenAPI or Swagger document. MCP endpoints themselves are never requested.
Exit code 1 when any URL is not ok, a quote or endpoint is not found, or a rule fails.

    python scripts/verify_official_paths.py                 # whole table and every access bundle
    python scripts/verify_official_paths.py --hosts a.com,b.org --json work/official-paths.json
    python scripts/verify_official_paths.py --access-only --hosts stripe.com   # one access bundle
"""

from __future__ import annotations

import argparse
import html
import ipaddress
import json
import re
import socket
import sys
import time
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.parse import unquote, urljoin, urlsplit

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ahn.models import valid_host  # noqa: E402
from ahn.official_paths import (ACCESS_AUTH_METHODS, ACCESS_MAX_AGE_DAYS, ACCESS_QUOTE_MAX, OFFICIAL_ACCESS,  # noqa: E402
                                OFFICIAL_PATHS, access_is_current)
from ahn.service import PUBLIC_SUFFIXES_2  # noqa: E402

USER_AGENT = "AgentHelpNetwork-curation/0.1 (+https://agenthelpnetwork.com/en/policy)"
MIN_INTERVAL = 1.0
TIMEOUT = 10.0
MAX_HOPS = 5
TITLE_CAP = 65_536
# Shared hosting suffixes: a.github.io and b.github.io are different owners.
PRIVATE_SUFFIXES = frozenset({"github.io", "gitbook.io", "readthedocs.io", "netlify.app", "vercel.app", "pages.dev"})
# Other domains that belong to the same publisher as the table key (docs hosts, parent companies,
# the publisher's own organisation on GitHub). Every cross-domain URL in the table must be declared here.
PUBLISHER_DOMAINS: dict[str, set[str]] = {
    "bitbucket.org": {"atlassian.com"}, "chatgpt.com": {"openai.com"}, "youtube.com": {"google.com"},
    "twitter.com": {"x.com"}, "stackoverflow.com": {"stackapps.com", "stackexchange.com"},
    "stackexchange.com": {"stackapps.com"}, "wikipedia.org": {"mediawiki.org", "wikimedia.org"}, "wikimedia.org": {"mediawiki.org"},
    "amazon.com": {"developer-docs.amazon"},  # Amazon's .amazon brand TLD
    "amazonaws.com": {"amazon.com", "aws.com"}, "shopify.com": {"shopify.dev"}, "slack.com": {"slack.dev"},
    "whatsapp.com": {"facebook.com"}, "notion.so": {"notion.com"}, "atlassian.net": {"atlassian.com"},
    "trello.com": {"atlassian.com"}, "azure.com": {"microsoft.com"}, "linkedin.com": {"microsoft.com"},
    "instagram.com": {"facebook.com"}, "sendgrid.com": {"twilio.com"},
    "ycombinator.com": {"github.com"},  # official HackerNews organisation repository
    "npmjs.com": {"github.com"},  # npm is part of GitHub; registry docs live in the npm organisation
    "steampowered.com": {"steamcommunity.com", "steamgames.com"},
    "medium.com": {"github.com"},  # official Medium organisation repository
    "dev.to": {"forem.com"},
    "bybit.com": {"bybit-exchange.github.io"},  # Bybit's own GitHub Pages site
    "polygon.io": {"massive.com"},  # Polygon.io rebranded as Massive
    "line.me": {"line.biz"}, "expedia.com": {"expediagroup.com"}, "sam.gov": {"gsa.gov"},
    "regulations.gov": {"gsa.gov"}, "medicare.gov": {"cms.gov"}, "service.gov.uk": {"api.gov.uk", "hmrc.gov.uk"},
    "gov.kr": {"data.go.kr"}, "klm.com": {"airfranceklm.com"}, "airfrance.com": {"airfranceklm.com"},
    "hotels.com": {"expediagroup.com"}, "vrbo.com": {"expediagroup.com"}, "ubereats.com": {"uber.com"},
    "venmo.com": {"paypal.com"}, "braintreepayments.com": {"paypal.com"}, "mercadopago.com": {"mercadopago.com.ar"},
    "livenation.com": {"ticketmaster.com"}, "mercadolibre.com": {"mercadolibre.com.ar"},
    "woocommerce.com": {"woocommerce.github.io"},  # WooCommerce's own GitHub Pages site
    "deliveroo.co.uk": {"deliveroo.com"}, "threads.net": {"facebook.com"}, "threads.com": {"facebook.com"},
    "bsky.app": {"bsky.network"},  # Bluesky's protocol services domain
    "mastodon.social": {"joinmastodon.org"}, "snapchat.com": {"snap.com"}, "blogger.com": {"google.com"},
    "blogspot.com": {"google.com"}, "daum.net": {"kakao.com"}, "blizzard.com": {"battle.net"},
    "steamcommunity.com": {"steamgames.com"}, "apnews.com": {"ap.org"}, "bing.com": {"microsoft.com"},
    "gmail.com": {"google.com"}, "outlook.com": {"microsoft.com"}, "live.com": {"microsoft.com"},
    "office.com": {"microsoft.com"}, "sharepoint.com": {"microsoft.com"}, "microsoftonline.com": {"microsoft.com"},
    "icloud.com": {"apple.com"}, "ncloud.com": {"ncloud-docs.com"}, "linode.com": {"akamai.com"},
    "hetzner.com": {"hetzner.cloud"}, "terraform.io": {"hashicorp.com"},
    "launchpad.net": {"ubuntu.com"},  # Canonical
    "readthedocs.org": {"readthedocs.com"}, "adobe.io": {"adobe.com"}, "canva.com": {"canva.dev"},
    "crates.io": {"rust-lang.org"}, "nuget.org": {"microsoft.com"}, "maven.org": {"sonatype.org"},
    "1password.com": {"1password.dev"}, "springer.com": {"springernature.com"}, "nature.com": {"springernature.com"},
    "sciencedirect.com": {"elsevier.com"}, "google-analytics.com": {"google.com"}, "lg.com": {"lge.com"},
    "xiaomi.com": {"mi.com"}, "qq.com": {"tencent.com"}, "wechat.com": {"qq.com"}, "anthropic.com": {"claude.com"}, "claude.com": {"anthropic.com"},
    "claude.ai": {"claude.com", "anthropic.com"}, "deepmind.google": {"google.dev"}, "llama.com": {"meta.ai"},
    "moonshot.ai": {"kimi.ai"}, "youtu.be": {"google.com"}, "wa.me": {"facebook.com"}, "t.me": {"telegram.org"},
    "discord.gg": {"discord.com"}, "bit.ly": {"bitly.com"},
    "mychart.com": {"epic.com"},
    "googleapis.com": {"google.com"},
    "google.de": {"google.com"},
    "google.co.uk": {"google.com"},
    "google.com.hk": {"google.com"},
    "google.com.br": {"google.com"},
    "office365.com": {"microsoft.com"},
    "visualstudio.com": {"microsoft.com"},
    "amazonalexa.com": {"amazon.com"},
    "zoom.com": {"zoom.us"},
    "docker.io": {"docker.com"},
    "cloudflare-dns.com": {"cloudflare.com"},
    "oraclecloud.com": {"oracle.com"},
    "tencent.com": {"tencentcloud.com"},
    "meraki.com": {"cisco.com"},
    "opendns.com": {"cisco.com"},
    "paloaltonetworks.com": {"pan.dev"},
    "merriam-webster.com": {"dictionaryapi.com"},
    "amazon.co.uk": {"amazon.com", "developer-docs.amazon"},
    "amazon.de": {"amazon.com", "developer-docs.amazon"},
    "amazon.fr": {"amazon.com", "developer-docs.amazon"},
    "amazon.es": {"amazon.com", "developer-docs.amazon"},
    "amazon.it": {"amazon.com", "developer-docs.amazon"},
    "amazon.ca": {"amazon.com", "developer-docs.amazon"},
    "amazon.in": {"amazon.com", "developer-docs.amazon"},
    "amazon.co.jp": {"amazon.com", "developer-docs.amazon"},
    "amazon.com.br": {"amazon.com", "developer-docs.amazon"},
    "amazon.com.au": {"amazon.com", "developer-docs.amazon"},
    "amazon.co.za": {"amazon.com", "developer-docs.amazon"},
    "yandexcloud.net": {"yandex.cloud"},
    "sberbank.ru": {"sber.ru"},
    "bitrix24.ru": {"bitrix24.com"},
    "mercadolibre.com.ar": {"mercadolibre.com"},
    "supercell.com": {"brawlstars.com", "clashofclans.com"},
    "mega.co.nz": {"mega.io"},
    "ok.ru": {"apiok.ru"},
    "ya.ru": {"yandex.ru"},
    "vkontakte.ru": {"vk.com"},
    "no-ip.com": {"noip.com"},
    "vk.ru": {"vk.com"},
    "otto.de": {"otto.market"},
    "shopee.com.br": {"shopee.com"},
    "shopee.co.id": {"shopee.com"},
    "biomedcentral.com": {"springernature.com"},  # BMC is part of Springer Nature
    "metacpan.org": {"github.com"},  # MetaCPAN's own GitHub organisation
    "npmjs.org": {"github.com", "npmjs.com"},  # npm registry docs live in the npm GitHub organisation
    "golang.org": {"go.dev"},  # Go project moved to go.dev
    "pypa.io": {"python.org"},  # Python Packaging Authority specifications are hosted at packaging.python.org
    "ghcr.io": {"github.com"},  # GitHub Packages documentation
    "leagueoflegends.com": {"riotgames.com"},  # Riot Games developer portal
    "skyscanner.com": {"skyscanner.net"},  # Skyscanner's partner developer domain
    "orbitz.com": {"expediagroup.com"},
    "travelocity.com": {"expediagroup.com"},
    "shein.com": {"sheincorp.com"},  # SHEIN corporate open platform domain
    "fedoraproject.org": {"fedora-infra.github.io"},  # Fedora Infrastructure Pages site that bodhi.fedoraproject.org/docs redirects to
    "wayfair.com": {"wayfair.io"},  # Wayfair developer site that developer.wayfair.com redirects to
    "api.aws": {"amazon.com", "aws.com"},  # .aws is Amazon's brand TLD; documentation lives on docs.aws.amazon.com
    "bahn.de": {"deutschebahn.com"},  # Deutsche Bahn API Marketplace
    "censusreporter.org": {"github.com"},  # Census Reporter's own repository
    "dartlang.org": {"dart.dev"},  # legacy domain of dart.dev
    "datadoghq.eu": {"datadoghq.com"},
    "eventbrite.co.uk": {"eventbrite.com"}, "eventbrite.com.au": {"eventbrite.com"},
    "aliexpress.us": {"aliexpress.com"}, "cheaptickets.com": {"expediagroup.com"}, "fb.com": {"facebook.com"},
    "google.ca": {"google.com"}, "google.co.in": {"google.com"},
    "freepik.com": {"magnific.com"},  # Freepik-owned Magnific; docs.freepik.com redirects to docs.magnific.com
    "greenhouse.com": {"greenhouse.io"}, "admin.ch": {"opendata.swiss"},  # Swiss federal open data portal
    "senate.gov": {"lda.gov"},  # lda.senate.gov redirects to lda.gov, the Lobbying Disclosure Act site of the Senate Secretary and House Clerk
    "metmuseum.org": {"metmuseum.github.io"},  # The Met's own GitHub Pages site for its Collection API
    "ourworldindata.org": {"owid.io"},  # Our World in Data's documentation domain for its ETL and API
    "nsf.gov": {"research.gov"},  # NSF operates Research.gov, which hosts the NSF Awards API reference
    "springeropen.com": {"springernature.com"},  # SpringerOpen is part of Springer Nature
    "superuser.com": {"stackapps.com", "stackexchange.com"},  # Stack Exchange network site
    "sncf-connect.com": {"sncf.com"},  # SNCF Connect is the SNCF group's booking service; open data lives on data.sncf.com
    "stubhub.ca": {"stubhub.com"}, "shopee.ph": {"shopee.com"},  # regional hosts of platforms listed above
    "sktelecom.com": {"sk.com"},  # SK Open API portal (openapi.sk.com) run by SK Telecom
    # Korean public bodies: the dataset page on the Public Data Portal (data.go.kr) is the body's own listing,
    # published by the named organisation (checked against the portal's dataset metadata).
    "airkorea.or.kr": {"data.go.kr"},  # dataset 15073861, published by Korea Environment Corporation (operator of AirKorea)
    "airport.kr": {"data.go.kr"},  # dataset 15140153, published by Incheon International Airport Corporation
    "bokjiro.go.kr": {"data.go.kr"},  # dataset 15090532, published by Korea Social Security Information Service (operator of Bokjiro)
    "hira.or.kr": {"data.go.kr"},  # dataset 15001698, published by Health Insurance Review and Assessment Service
    "hometax.go.kr": {"data.go.kr"}, "nts.go.kr": {"data.go.kr"},  # dataset 15081808, published by the National Tax Service (operator of Hometax)
    "mfds.go.kr": {"data.go.kr"},  # dataset 15095677, published by Ministry of Food and Drug Safety
    "nps.or.kr": {"data.go.kr"},  # dataset 3046071, published by National Pension Service
    "police.go.kr": {"data.go.kr"},  # dataset 15000799, published by Korean National Police Agency
    "korail.com": {"data.go.kr"}, "letskorail.com": {"data.go.kr"},  # dataset 15153852, published by Korea Railroad Corporation (Korail)
    "minwon.go.kr": {"data.go.kr"},  # former Minwon24 address of Government24, run by the Ministry of the Interior and Safety, which also runs the portal
    # Domains used only by access bundles (OFFICIAL_ACCESS)
    "github.com": {"githubcopilot.com"},  # GitHub's hosted MCP server, documented on docs.github.com
    "reddit.com": {"redditinc.com"},  # Reddit, Inc.'s policy site, which publishes the Data API Terms
}
# Code hosts where one domain serves many owners. An access-bundle URL on one of them must also sit in the
# publisher's own organisation (first path segment), declared here per table key with how ownership was checked.
GITHUB_CONTENT_HOSTS = frozenset({"github.com", "raw.githubusercontent.com"})
# Other names that also serve many owners' content and so get the same organisation rule (www. and gist. are not the publisher's).
GITHUB_ALIAS_HOSTS = frozenset({"www.github.com", "gist.github.com", "gist.githubusercontent.com"})
PUBLISHER_GITHUB_ORGS: dict[str, set[str]] = {
    "github.com": {"github"},  # docs.github.com links github/rest-api-description as the REST API's OpenAPI description
    "stripe.com": {"stripe"},  # docs.stripe.com/sdks links the official SDKs in this organisation
    "openai.com": {"openai"}, "chatgpt.com": {"openai"},  # developers.openai.com quickstart links the SDKs in this organisation
    "cloudflare.com": {"cloudflare"},  # developers.cloudflare.com links cloudflare-docs and its MCP servers in this organisation
    "asana.com": {"asana"},  # developers.asana.com/reference links raw.githubusercontent.com/Asana/openapi as the OpenAPI definition
    "figma.com": {"figma"},  # developers.figma.com/docs/rest-api links github.com/figma/rest-api-spec as the OpenAPI specification
    "netlify.com": {"netlify"},  # docs.netlify.com get-started-with-api links github.com/netlify/open-api as the OpenAPI specification
    "plaid.com": {"plaid"},  # plaid.com/docs/api links github.com/plaid/plaid-openapi as the OpenAPI specification
    "ngrok.com": {"ngrok"},  # ngrok.com/docs/llms.txt lists raw.githubusercontent.com/ngrok/ngrok-openapi as the OpenAPI specification
}
TITLE = re.compile(rb"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)
NOT_FOUND = re.compile(r"\b(404|not found|page not found|does not exist|no longer available)\b", re.IGNORECASE)
BOT_WALL = re.compile(r"pardon our interruption|just a moment\.\.\.|attention required|access denied|are you a robot|robot check|"
                      r"security check|captcha", re.IGNORECASE)
SITE_DOWN = re.compile(r"site ?down|temporarily unavailable", re.IGNORECASE)
SIGN_IN = re.compile(r"/(log-?in|sign-?in|sign_in|sso|oauth2?/authorize|authorize)(/|$)", re.IGNORECASE)
NOTE_URL = re.compile(r"https://[^\s)>,;\"']+")

ACCESS_FIELDS = ("auth", "key_signup_url", "free_tier", "rate_limits_url", "sandbox_url", "openapi_url", "official_mcp",
                 "feeds", "llms_full_url", "docs_repo", "data_export_url", "terms", "verified_at")
ACCESS_URL_FIELDS = ("key_signup_url", "rate_limits_url", "sandbox_url", "openapi_url", "llms_full_url", "docs_repo",
                     "data_export_url")
ACCESS_FEEDS_MAX = 3
ACCESS_URL_MAX = 300  # the longest URL an access bundle may carry (the model enforces the same limit)
BODY_CAP = 20_000_000  # whole OpenAPI documents (several MB) and long terms pages
FEED_MARK = re.compile(rb"<(?:rss|feed|rdf:RDF)[\s>]", re.IGNORECASE)  # a feed document starts with one of these elements
OPENAPI_MARK = re.compile(rb"[\"']?(?:openapi|swagger)[\"']?\s*:\s*[\"']?[23]\.")
_TYPOGRAPHY = str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"', " ": " ", "–": "-",
                             "—": "-", "​": "", "‍": "", "﻿": ""})
_INVISIBLE = re.compile(r"(?is)<(script|style|noscript|svg|template)\b[^>]*>.*?</\1\s*>")


def registrable(host: str) -> str:
    labels = host.lower().rstrip(".").split(".")
    if len(labels) >= 3 and (".".join(labels[-2:]) in PUBLIC_SUFFIXES_2 or ".".join(labels[-2:]) in PRIVATE_SUFFIXES):
        return ".".join(labels[-3:])
    return ".".join(labels[-2:])


# --- Access bundles: pure checks (also run by tests/test_official_access.py) ---------------------------------------

def publisher_owned(host: str, url: str) -> bool:
    """The URL is on the key's registrable domain or a declared publisher domain; on a shared code host
    (github.com, raw.githubusercontent.com) it must instead sit in the publisher's declared organisation."""
    parts = urlsplit(url)
    name = (parts.hostname or "").lower()
    if name in GITHUB_CONTENT_HOSTS | GITHUB_ALIAS_HOSTS or name.endswith(".githubusercontent.com"):
        org = parts.path.strip("/").split("/", 1)[0].lower()
        return bool(org) and org in PUBLISHER_GITHUB_ORGS.get(host, set())
    return registrable(name) in {registrable(host)} | PUBLISHER_DOMAINS.get(host, set())


def _url_problem(host: str, url) -> str | None:
    if not isinstance(url, str):
        return "not a string"
    parts = urlsplit(url)
    if parts.scheme != "https" or not url.startswith("https://") or any(ch.isspace() for ch in url):
        return f"not a plain https URL: {url!r}"
    try:
        if parts.port is not None or parts.username or parts.password or valid_host(parts.hostname or "") != parts.netloc:
            return f"not a public hostname: {url}"
    except ValueError:
        return f"not a public hostname: {url}"
    path = parts.path
    for _ in range(3):  # percent-decoding until stable, so %2e%2e and %252e%252e are caught too
        path = unquote(path)
    if any(segment in (".", "..") for segment in path.replace("\\", "/").split("/")):
        return f"path has a . or .. segment: {url}"
    if not publisher_owned(host, url):
        return f"not on a publisher-owned domain or declared GitHub organisation: {url}"
    return None


def _quote_problem(quote) -> str | None:
    if not isinstance(quote, str) or not quote or quote != quote.strip() or "\n" in quote:
        return "quote must be a non-empty single-line string without outer spaces"
    if len(quote) > ACCESS_QUOTE_MAX:
        return f"quote is {len(quote)} characters, over {ACCESS_QUOTE_MAX}"
    return None


def _object_problems(value, keys: set[str]) -> list[str]:
    if not isinstance(value, dict) or set(value) != keys:
        return [f"must be an object with exactly {sorted(keys)}"]
    return []


def access_problems(host: str, access, today: date | None = None) -> list[str]:
    """Every rule an access bundle must meet, without the network. An empty list means it passes."""
    if not isinstance(access, dict):
        return [f"{host}: access must be an object"]
    problems: list[str] = []

    def add(field: str, problem: str | None) -> None:
        if problem:
            problems.append(f"{host}: access.{field}: {problem}")

    for key in set(access) - set(ACCESS_FIELDS):
        add(key, "unknown field")
    if not set(access) & (set(ACCESS_FIELDS) - {"verified_at"}):
        add("*", "a bundle needs at least one field besides verified_at")
    for field, value in access.items():
        if value is None or value == [] or value == {}:
            add(field, "leave an unknown field out instead of null or empty")
    verified = access.get("verified_at")
    try:
        verified_on = date.fromisoformat(verified) if isinstance(verified, str) and len(verified) == 10 else None
    except ValueError:
        verified_on = None
    if verified_on is None:
        add("verified_at", "must be an ISO date YYYY-MM-DD")
    elif verified_on > (today or datetime.now(timezone.utc).date()):
        add("verified_at", "is in the future")
    auth = access.get("auth")
    if auth is not None:
        if not isinstance(auth, list):
            add("auth", "must be a list")
        else:
            methods = []
            for item in auth:
                shape = _object_problems(item, {"method", "source_url"})
                if shape:
                    add("auth", shape[0])
                    continue
                if item["method"] not in ACCESS_AUTH_METHODS:
                    add("auth", f"unknown method {item['method']!r}; allowed: {', '.join(ACCESS_AUTH_METHODS)}")
                methods.append(item["method"])
                add("auth", _url_problem(host, item["source_url"]))
            if len(methods) != len(set(methods)):
                add("auth", "a method is listed twice")
    for field in ACCESS_URL_FIELDS:
        if access.get(field) is not None:
            add(field, _url_problem(host, access[field]))
    for field in ("feeds", "llms_full_url", "docs_repo", "data_export_url"):
        for url in ([access[field]] if isinstance(access.get(field), str) else access.get(field) or []):
            if isinstance(url, str) and len(url) > ACCESS_URL_MAX:
                add(field, f"URL is {len(url)} characters, over {ACCESS_URL_MAX}")
    feeds = access.get("feeds")
    if feeds is not None:
        if not isinstance(feeds, list):
            add("feeds", "must be a list")
        else:
            if len(feeds) > ACCESS_FEEDS_MAX:
                add("feeds", f"at most {ACCESS_FEEDS_MAX} feeds")
            for url in feeds:
                add("feeds", _url_problem(host, url))
            if len(feeds) != len(set(feeds)):
                add("feeds", "a feed is listed twice")
    if access.get("free_tier") is not None:
        shape = _object_problems(access["free_tier"], {"quote", "source_url"})
        add("free_tier", shape[0] if shape else _quote_problem(access["free_tier"]["quote"]))
        if not shape:
            add("free_tier", _url_problem(host, access["free_tier"]["source_url"]))
    if access.get("terms") is not None:
        shape = _object_problems(access["terms"], {"url", "quote"})
        add("terms", shape[0] if shape else _quote_problem(access["terms"]["quote"]))
        if not shape:
            add("terms", _url_problem(host, access["terms"]["url"]))
    mcp = access.get("official_mcp")
    if mcp is not None:
        if not isinstance(mcp, list):
            add("official_mcp", "must be a list")
        else:
            endpoints = []
            for item in mcp:
                shape = _object_problems(item, {"endpoint", "doc_url"})
                if shape:
                    add("official_mcp", shape[0])
                    continue
                endpoints.append(item["endpoint"])
                add("official_mcp", _url_problem(host, item["endpoint"]))
                add("official_mcp", _url_problem(host, item["doc_url"]))
            if len(endpoints) != len(set(endpoints)):
                add("official_mcp", "an endpoint is listed twice")
    return problems


def access_urls(access: dict) -> list[tuple[str, str]]:
    """(field, url) for every page of an access bundle that is fetched. MCP endpoints are not fetched: they are
    checked as text on their documentation page instead."""
    found = [(f"access.auth.{item['method']}", item["source_url"]) for item in access.get("auth") or []]
    found += [(f"access.{field}", access[field]) for field in ACCESS_URL_FIELDS if access.get(field)]
    found += [("access.feeds", url) for url in access.get("feeds") or []]
    if access.get("free_tier"):
        found.append(("access.free_tier", access["free_tier"]["source_url"]))
    found += [("access.official_mcp", item["doc_url"]) for item in access.get("official_mcp") or []]
    if access.get("terms"):
        found.append(("access.terms", access["terms"]["url"]))
    return found


def access_needles(access: dict) -> list[tuple[str, str, str]]:
    """(field, page URL, text) that must appear verbatim on that page: the two quotes and each MCP endpoint."""
    needles = []
    if access.get("free_tier"):
        needles.append(("access.free_tier", access["free_tier"]["source_url"], access["free_tier"]["quote"]))
    if access.get("terms"):
        needles.append(("access.terms", access["terms"]["url"], access["terms"]["quote"]))
    needles += [("access.official_mcp", item["doc_url"], item["endpoint"]) for item in access.get("official_mcp") or []]
    return needles


def page_text(body: bytes) -> str:
    """Visible text of an HTML page (scripts and styles dropped, tags removed, entities decoded)."""
    text = _INVISIBLE.sub(" ", body.decode("utf-8", "replace"))
    return html.unescape(re.sub(r"(?s)<[^>]+>", " ", text))


def _squash(text: str) -> str:
    # Typographic quotes and dashes are folded and all whitespace is dropped, so markup between inline
    # elements (<strong>Build</strong>, ...) cannot hide a quote. Letters and punctuation must match exactly.
    return "".join(text.translate(_TYPOGRAPHY).split())


def text_contains(page: str, needle: str) -> bool:
    return bool(needle.strip()) and _squash(needle) in _squash(page)


# --- Live checks ----------------------------------------------------------------------------------------------------

def table_urls(hosts: set[str] | None = None, *, paths: bool = True, access: bool = True) -> dict[str, list[tuple[str, str]]]:
    """URL -> [(host, field)] for every URL in the table and its access bundles (optionally limited to some hosts)."""
    urls: dict[str, list[tuple[str, str]]] = {}
    for host, entry in OFFICIAL_PATHS.items():
        if hosts and host not in hosts:
            continue
        if paths:
            for field in ("api_docs", "developer_portal"):
                if entry[field]:
                    urls.setdefault(entry[field], []).append((host, field))
            for match in NOTE_URL.findall(entry["note"] or ""):
                urls.setdefault(match.rstrip("."), []).append((host, "note"))
        if access and host in OFFICIAL_ACCESS:
            for field, url in access_urls(OFFICIAL_ACCESS[host]):
                if (host, field) not in urls.get(url, []):
                    urls.setdefault(url, []).append((host, field))
    return urls


class Fetcher:
    def __init__(self) -> None:
        self.client = httpx.Client(
            http2=False, follow_redirects=False, timeout=httpx.Timeout(TIMEOUT),
            headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml,*/*;q=0.8", "Accept-Language": "en"},
        )
        self.last = 0.0
        self.requests = 0
        self._public: dict[str, bool] = {}

    def public_host(self, host: str) -> bool:
        """Every address the name resolves to is a public (global) IP; looked up once per host."""
        if host not in self._public:
            try:
                addresses = {item[4][0] for item in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)}
                self._public[host] = bool(addresses) and len(addresses) <= 16 and all(
                    ipaddress.ip_address(ip.split("%", 1)[0]).is_global for ip in addresses)
            except (OSError, ValueError):
                self._public[host] = False
        return self._public[host]

    def _get(self, url: str, want_body: bool) -> tuple[int, str | None, str, bytes]:
        wait = MIN_INTERVAL - (time.monotonic() - self.last)
        if wait > 0:
            time.sleep(wait)
        self.last = time.monotonic()
        self.requests += 1
        with self.client.stream("GET", url) as response:
            location = response.headers.get("location")
            title = ""
            body = bytearray()
            if response.status_code == 200:
                for chunk in response.iter_bytes():
                    body.extend(chunk)
                    if len(body) >= BODY_CAP or (not want_body and (len(body) >= TITLE_CAP or b"</title>" in body.lower())):
                        break
                found = TITLE.search(body[:TITLE_CAP])
                if found:
                    title = " ".join(found.group(1).decode("utf-8", "replace").split())[:120]
            return response.status_code, location, title, bytes(body)

    def check(self, url: str, allowed: set[str], *, orgs: set[str] | None = None, want_body: bool = False) -> tuple[dict, bytes]:
        """One URL, redirects followed by hand. `orgs` (access-bundle URLs) also requires a final URL on a shared
        code host to sit in one of those organisations. With want_body the page body (up to BODY_CAP) is kept."""
        current, hops, status, title, error, body = url, 0, None, "", None, b""
        retried = False
        while True:
            hostname = urlsplit(current).hostname or ""
            if urlsplit(current).scheme != "https":
                error = "not_https"
                break
            if not self.public_host(hostname):
                error = "not_public_address"
                break
            try:
                status, location, title, body = self._get(current, want_body)
            except httpx.HTTPError as exc:
                if not retried:
                    retried = True
                    continue
                error = type(exc).__name__
                break
            if status in {429, 500, 502, 503, 504} and not retried:
                retried = True
                continue
            if status in {301, 302, 303, 307, 308} and location and hops < MAX_HOPS:
                current, hops = urljoin(current, location), hops + 1
                continue
            break
        final = urlsplit(current)
        final_host = (final.hostname or "").lower()
        if orgs is not None and final_host in GITHUB_CONTENT_HOSTS:
            owned = final.path.strip("/").split("/", 1)[0].lower() in orgs
        else:
            owned = registrable(final_host) in allowed
        result = "ok"
        if error in {"not_https", "not_public_address"}:
            result = error
        elif error:
            result = f"error:{error}"
        elif status in {202, 401, 403, 429}:
            result = f"blocked:{status}"
        elif status != 200:
            result = f"status:{status}"
        elif not owned:
            result = f"foreign_host:{final.hostname}"
        elif "challenge" in final.path.lower() or final_host.startswith("unblock.") or BOT_WALL.search(title):
            result = "blocked:challenge"
        elif current != url and SIGN_IN.search(final.path):
            result = "sign_in_page"
        elif NOT_FOUND.search(title) or SITE_DOWN.search(title):
            result = "not_found_title"
        row = {"url": url, "final_url": current, "status": status, "hops": hops, "title": title, "result": result}
        return row, (body if result == "ok" else b"")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--hosts", help="comma-separated table keys to check (default: all)")
    parser.add_argument("--access-only", action="store_true", help="check only access bundles (URLs, quotes, MCP endpoints)")
    parser.add_argument("--json", type=Path, help="write per-URL and per-host results to this file")
    args = parser.parse_args()
    hosts = {h.strip() for h in args.hosts.split(",") if h.strip()} if args.hosts else None
    access_hosts = [host for host in OFFICIAL_ACCESS if not hosts or host in hosts]
    problems = [line for host in access_hosts for line in access_problems(host, OFFICIAL_ACCESS[host])]
    problems += [f"{host}: access bundle for a host that is not in OFFICIAL_PATHS" for host in access_hosts if host not in OFFICIAL_PATHS]
    urls = table_urls(hosts, paths=not args.access_only)
    needles: dict[str, list[tuple[str, str, str]]] = {}
    for host in access_hosts:
        for field, url, text in access_needles(OFFICIAL_ACCESS[host]):
            needles.setdefault(url, []).append((host, field, text))
    fetcher = Fetcher()
    results: dict[str, dict] = {}
    needle_rows: list[dict] = []
    undeclared: list[str] = []
    for index, (url, users) in enumerate(urls.items(), 1):
        allowed = {registrable(urlsplit(url).hostname or "")}
        orgs: set[str] | None = None
        for host, field in users:
            declared = {registrable(host)} | PUBLISHER_DOMAINS.get(host, set())
            if field.startswith("access."):
                orgs = (orgs or set()) | PUBLISHER_GITHUB_ORGS.get(host, set())
            elif registrable(urlsplit(url).hostname or "") not in declared:
                undeclared.append(f"{host}: {url}")
            allowed |= declared
        is_openapi = any(field == "access.openapi_url" for _, field in users)
        is_feed = any(field == "access.feeds" for _, field in users)
        results[url], body = fetcher.check(url, allowed, orgs=orgs, want_body=url in needles or is_openapi or is_feed)
        row = results[url]
        if is_openapi and row["result"] == "ok" and not OPENAPI_MARK.search(body):
            row["result"] = "not_openapi"
        if is_feed and row["result"] == "ok" and not FEED_MARK.search(body[:4096]):
            row["result"] = "not_a_feed"
        moved = f" -> {row['final_url']}" if row["final_url"] != url else ""
        print(f"[{index}/{len(urls)}] {row['result']:<14} {row['status']!s:<4} {url}{moved}", flush=True)
        text = page_text(body) if url in needles and row["result"] == "ok" else ""
        for host, field, needle in needles.get(url, []):
            found = "found" if text_contains(text, needle) else "not_found" if row["result"] == "ok" else f"unverified:{row['result']}"
            needle_rows.append({"host": host, "field": field, "page": url, "text": needle, "result": found})
    per_host: dict[str, dict] = {}
    for url, users in urls.items():
        for host, field in users:
            item = per_host.setdefault(host, {"ok": True, "urls": [], "needles": []})
            item["urls"].append({"field": field, **results[url]})
            item["ok"] &= results[url]["result"] == "ok"
    for row in needle_rows:
        item = per_host.setdefault(row["host"], {"ok": True, "urls": [], "needles": []})
        item["needles"].append(row)
        item["ok"] &= row["result"] == "found"
    failed = [url for url, row in results.items() if row["result"] != "ok"]
    blocked = [url for url in failed if results[url]["result"].startswith("blocked:")]
    missing = [row for row in needle_rows if row["result"] != "found"]
    print()
    print(f"{'host':<28} {'field':<22} {'result':<22} url")
    for host, item in sorted(per_host.items()):
        for row in item["urls"]:
            if row["result"] != "ok" or row["final_url"] != row["url"]:
                print(f"{host:<28} {row['field']:<22} {row['result']:<22} {row['url']} -> {row['final_url']}")
    for row in missing:
        print(f"{row['host']:<28} {row['field']:<22} text {row['result']:<17} {row['text'][:60]!r} on {row['page']}")
    for line in undeclared:
        print(f"undeclared publisher domain: {line}")
    for line in problems:
        print(f"access rule: {line}")
    for host in access_hosts:
        if not access_is_current(OFFICIAL_ACCESS[host]):
            print(f"stale: {host} access verified_at {OFFICIAL_ACCESS[host]['verified_at']} is over {ACCESS_MAX_AGE_DAYS} days old "
                  "and is left out of answers until re-verified")
    hosts_ok = sum(item["ok"] for item in per_host.values())
    print(f"\nhosts: {len(per_host)} checked, {hosts_ok} all-ok, {len(per_host) - hosts_ok} with a failure; "
          f"urls: {len(results)} distinct, {len(results) - len(failed)} ok, {len(blocked)} blocked, {len(failed) - len(blocked)} failed; "
          f"quotes and MCP endpoints: {len(needle_rows)} checked, {len(needle_rows) - len(missing)} found; "
          f"requests: {fetcher.requests}")
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps({
            "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "user_agent": USER_AGENT, "hosts": per_host, "undeclared": undeclared, "access_problems": problems,
        }, indent=2, ensure_ascii=False), encoding="utf-8")
    return 1 if failed or undeclared or problems or missing else 0


if __name__ == "__main__":
    raise SystemExit(main())
