"""Shape of the curated status mappings. No database, no network."""

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import urlsplit

from ahn.official_paths import OFFICIAL_PATHS
from ahn.status_refresh import OFFICIAL_STATUS_PAGES, SOURCES

HOST = re.compile(r"^(?=.{4,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,24}$")
SEED_HOSTS = {
    line.strip() for line in (Path(__file__).resolve().parents[1] / "ahn" / "seed_hosts.txt").read_text(encoding="utf-8").splitlines()
    if line.strip() and not line.startswith("#")
}


def test_keys_are_plain_lowercase_apex_style_hosts():
    for host in [*SOURCES, *OFFICIAL_STATUS_PAGES]:
        assert HOST.fullmatch(host), host
        assert not host.startswith("www."), host  # policy_host() already folds www.<apex> onto <apex>


def test_every_key_is_a_curated_host():
    for host in [*SOURCES, *OFFICIAL_STATUS_PAGES]:
        assert host in OFFICIAL_PATHS or host in SEED_HOSTS, host


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
