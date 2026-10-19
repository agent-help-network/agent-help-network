"""Shape of the curated status mappings. No database, no network."""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path
from urllib.parse import urlsplit

from ahn.models import valid_host
from ahn.service import PUBLIC_SUFFIXES_2, aggregate_eligible_host
from ahn.status_refresh import OFFICIAL_STATUS_PAGES, SOURCES

HOST = re.compile(r"^(?=.{4,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,24}$")
_ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("verify_official_paths", _ROOT / "scripts" / "verify_official_paths.py")
_verify = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_verify)
SHARED_HOSTING_SUFFIXES = _verify.PRIVATE_SUFFIXES
# Platforms whose own status page answers for every customer site under their suffix (a deliberate, older mapping).
PLATFORM_KEYS = frozenset({"github.io", "netlify.app", "vercel.app"})


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
        if source.kind == "statuspage":
            assert source.api_url == source.page_url + "api/v2/status.json", host


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
