"""Pure checks on the curated official-path table; no network (live checks: scripts/verify_official_paths.py)."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from urllib.parse import urlsplit

from ahn.models import valid_host
from ahn.official_paths import OFFICIAL_ACCESS, OFFICIAL_PATHS, official_paths
from ahn.service import PUBLIC_SUFFIXES_2

ROOT = Path(__file__).resolve().parents[1]


def _verifier():
    spec = importlib.util.spec_from_file_location("verify_official_paths", ROOT / "scripts" / "verify_official_paths.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


VERIFIER = _verifier()


def _urls(entry: dict) -> list[str]:
    listed = [entry[field] for field in ("api_docs", "developer_portal") if entry[field] is not None]
    return listed + [match.rstrip(".") for match in VERIFIER.NOTE_URL.findall(entry["note"] or "")]


def test_table_has_at_least_300_hosts():
    assert len(OFFICIAL_PATHS) >= 300


def test_every_key_is_a_lowercase_apex_host_that_passes_valid_host():
    bad = []
    for host in OFFICIAL_PATHS:
        try:
            assert valid_host(host) == host
        except (AssertionError, ValueError):
            bad.append(host)
            continue
        # A public suffix (gov.uk, go.kr) as a key would claim every unrelated host beneath it.
        if host in PUBLIC_SUFFIXES_2 or host.startswith("www."):
            bad.append(host)
    assert bad == []


# Public-suffix-list entries independent of PUBLIC_SUFFIXES_2, so the key check above cannot pass by
# leaving the same gap on both sides.
KNOWN_PUBLIC_SUFFIXES = {"police.uk", "nhs.uk", "sch.uk", "mod.uk", "ltd.uk", "plc.uk", "net.uk", "gov.uk", "ac.uk", "co.uk",
                         "org.uk", "go.kr", "co.kr", "or.kr", "go.jp", "co.jp", "gov.au", "com.au", "gov.br", "com.br"}


def test_no_table_key_is_a_known_public_suffix_and_police_uk_is_covered():
    assert OFFICIAL_PATHS.keys().isdisjoint(KNOWN_PUBLIC_SUFFIXES)
    assert "police.uk" in PUBLIC_SUFFIXES_2
    assert "data.police.uk" in OFFICIAL_PATHS and VERIFIER.registrable("data.police.uk") == "data.police.uk"
    assert official_paths("police.uk") is None
    assert official_paths("api.data.police.uk") == OFFICIAL_PATHS["data.police.uk"]


def test_every_entry_has_a_name_and_some_content():
    for host, entry in OFFICIAL_PATHS.items():
        assert set(entry) == {"name", "api_docs", "developer_portal", "note"}, host
        assert isinstance(entry["name"], str) and entry["name"].strip() == entry["name"] and entry["name"], host
        assert entry["api_docs"] or entry["developer_portal"] or entry["note"], host
        assert entry["note"] is None or (isinstance(entry["note"], str) and entry["note"].strip()), host


def test_every_url_is_https_and_parses_to_a_public_host():
    bad = []
    for host, entry in OFFICIAL_PATHS.items():
        for url in _urls(entry):
            parts = urlsplit(url)
            try:
                assert parts.scheme == "https" and url.startswith("https://") and not any(ch.isspace() for ch in url)
                assert parts.hostname and valid_host(parts.hostname) == parts.netloc and parts.path.startswith("/")
            except (AssertionError, ValueError):
                bad.append(f"{host}: {url}")
    assert bad == []


def test_cross_domain_urls_are_declared_publisher_domains():
    undeclared = []
    for host, entry in OFFICIAL_PATHS.items():
        declared = {VERIFIER.registrable(host)} | VERIFIER.PUBLISHER_DOMAINS.get(host, set())
        undeclared += [f"{host}: {url}" for url in _urls(entry) if VERIFIER.registrable(urlsplit(url).hostname) not in declared]
    assert undeclared == []
    assert set(VERIFIER.PUBLISHER_DOMAINS) <= set(OFFICIAL_PATHS)
    assert all(VERIFIER.PUBLISHER_DOMAINS.values())


def test_official_paths_resolves_subdomains_to_the_apex_entry():
    assert official_paths("api.tfl.gov.uk") == OFFICIAL_PATHS["tfl.gov.uk"]
    assert official_paths("WWW.Census.Gov.") == OFFICIAL_PATHS["census.gov"]
    assert official_paths("developer.uscis.gov") == OFFICIAL_PATHS["uscis.gov"]
    hit = official_paths("docs.github.com")
    assert hit.pop("access") == OFFICIAL_ACCESS["github.com"]  # the curated access bundle rides along (tests/test_official_access.py)
    assert hit == OFFICIAL_PATHS["github.com"] and hit is not OFFICIAL_PATHS["github.com"]
    # Uncurated hosts under a public suffix stay unanswered rather than inheriting a sibling's entry.
    assert official_paths("unlisted-council.gov.uk") is None
    assert official_paths("unlisted.go.kr") is None
