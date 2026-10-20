"""Shape of the curated status mappings. No database, no network."""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path
from urllib.parse import urlsplit

from ahn.models import valid_host
from ahn.service import PUBLIC_SUFFIXES_2, aggregate_eligible_host
from ahn.status_refresh import FEED_KINDS, OFFICIAL_STATUS_PAGES, SOURCES

HOST = re.compile(r"^(?=.{4,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,24}$")
_ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("verify_official_paths", _ROOT / "scripts" / "verify_official_paths.py")
_verify = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_verify)
SHARED_HOSTING_SUFFIXES = _verify.PRIVATE_SUFFIXES
# Platforms whose own status page answers for every customer site under their suffix (a deliberate, older mapping).
PLATFORM_KEYS = frozenset({"github.io", "netlify.app", "vercel.app"})
# Where each feed kind's machine-readable answer lives relative to the status page.
API_PATH_BY_KIND = {
    "statuspage": "api/v2/status.json",  # Atlassian Statuspage and the compatible incident.io endpoint
    "instatus": "summary.json",
    "betterstack": "index.json",
    "google_gmail": "incidents.json",  # https://www.google.com/appsstatus/dashboard/incidents.json
}


def test_keys_are_plain_lowercase_apex_style_hosts():
    for host in [*SOURCES, *OFFICIAL_STATUS_PAGES]:
        assert HOST.fullmatch(host), host
        assert not host.startswith("www."), host  # policy_host() already folds www.<apex> onto <apex>


def test_every_key_is_an_eligible_public_host():
    # The mapping itself is the curated list, so the check is structural: a key must be a host the service would look up as an apex.
    for host in [*SOURCES, *OFFICIAL_STATUS_PAGES]:
        assert valid_host(host), host
        assert host not in PUBLIC_SUFFIXES_2, host  # a public suffix such as co.uk is nobody's host
        assert host not in SHARED_HOSTING_SUFFIXES or host in PLATFORM_KEYS, host  # a shared-hosting suffix is many owners' host
        assert aggregate_eligible_host(host), host


def test_a_host_has_a_feed_or_a_link_never_both():
    assert not set(SOURCES) & set(OFFICIAL_STATUS_PAGES)


def test_feed_sources_point_at_the_page_status_json():
    for host, source in SOURCES.items():
        page, api = urlsplit(source.page_url), urlsplit(source.api_url)
        assert source.name.strip() == source.name and source.name, host
        assert page.scheme == api.scheme == "https", host
        assert page.hostname == api.hostname and not page.query and not page.fragment, host
        assert source.page_url.endswith("/"), host
        assert source.kind in API_PATH_BY_KIND, host  # a new kind needs its own rule here and a parser in FEED_KINDS
        assert source.kind in FEED_KINDS, host
        assert source.api_url == source.page_url + API_PATH_BY_KIND[source.kind], host


def test_official_page_links_are_https_and_named():
    for host, (name, url) in OFFICIAL_STATUS_PAGES.items():
        parts = urlsplit(url)
        assert name.strip() == name and name, host
        assert parts.scheme == "https" and parts.hostname and not parts.fragment, host
        assert not parts.username and not parts.password, host


def test_no_mapping_points_at_a_placeholder_or_an_aggregator():
    urls = [source.page_url for source in SOURCES.values()] + [url for _, url in OFFICIAL_STATUS_PAGES.values()]
    for url in urls:
        parts = urlsplit(url)
        assert not parts.path.rstrip("/").endswith("/inactive"), url  # Statuspage's answer for a subdomain nobody runs
        assert not any((parts.hostname or "").endswith(domain) for domain in ("statusgator.com", "downdetector.com", "fruits.co")), url


# Hosts whose status page was read once on 2026-10-19 and moved from a link to a feed.
INSTATUS_HOSTS = {"bsky.app", "exa.ai", "fastmail.com", "joinmastodon.org", "kagi.com", "mastodon.social", "mollie.com",
                  "tiingo.com", "todoist.com"}
BETTERSTACK_HOSTS = {"brave.com", "firecrawl.dev", "huggingface.co", "openalex.org", "together.ai"}
# Hosts moved from a link to a feed by the 2026-10-20 re-verification (the page's public feed parsed with the existing parser).
REVERIFIED_INSTATUS_HOSTS = {
    "stacker.com",  # agent-demand collection, 2026-10-20: feed parsed on the first check
    "1rpc.io", "appfolio.com", "aternos.org", "cloudez.io", "code.org", "decodo.com", "discogs.com", "doofinder.com",
    "fetlife.com", "flashbots.net", "freeconvert.com", "gamma.app", "growthbook.io", "mediavine.com", "narvar.com",
    "optable.co", "padlet.com", "polymarket.com", "wistia.com",
}
REVERIFIED_BETTERSTACK_HOSTS = {
    "dublincore.org", "nirvanahq.com",  # agent-demand collection, 2026-10-20: feed parsed on the first check
    "apollo.io", "atom.com", "bitdrift.io", "drpc.org", "eclipse.org", "elegantthemes.com", "feedly.com",
    "framer.com", "freeipapi.com", "higgsfield.ai", "linkvertise.com", "modrinth.com", "nordvpn.com", "plausible.io",
    "raspberrypi.org", "signupgenius.com", "sportskeeda.com", "studocu.com", "time.com", "trezor.io", "userway.org",
    "wordfence.com",
}
STATUSPAGE_COMPATIBLE_HOSTS = {"duffel.com", "ghost.org", "easypost.com", "rubygems.org", "sinch.com"}


def test_moved_hosts_use_the_parser_of_the_service_that_runs_their_page():
    assert {host for host, source in SOURCES.items() if source.kind == "instatus"} == INSTATUS_HOSTS | REVERIFIED_INSTATUS_HOSTS
    assert {host for host, source in SOURCES.items() if source.kind == "betterstack"} == BETTERSTACK_HOSTS | REVERIFIED_BETTERSTACK_HOSTS
    for host in STATUSPAGE_COMPATIBLE_HOSTS:
        assert SOURCES[host].kind == "statuspage", host
    assert not (INSTATUS_HOSTS | BETTERSTACK_HOSTS | STATUSPAGE_COMPATIBLE_HOSTS | REVERIFIED_INSTATUS_HOSTS | REVERIFIED_BETTERSTACK_HOSTS) & set(OFFICIAL_STATUS_PAGES)


def test_datadog_stays_a_link_because_its_feed_covers_one_region_only():
    assert "datadoghq.com" in OFFICIAL_STATUS_PAGES and "datadoghq.com" not in SOURCES
