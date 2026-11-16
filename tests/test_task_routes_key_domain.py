"""A curated task route record must point at its own site: every URL host must be the key or a subdomain of it. No database, no network."""

from __future__ import annotations

import sys
from urllib.parse import urlsplit

import pytest

from ahn import task_routes_data

URL_FIELDS = ("official_url", "source_url")

# (key, URL host) pairs where the operator's own documents live outside the key's host tree. Empty while every
# curated record stays on its key's domain; add one line per pair with the reason.
ALLOWED_CROSS_DOMAIN: set[tuple[str, str]] = set()

mod = sys.modules[__name__]


def key_domain_problems(table):
    problems = []
    for key, records in table.items():
        for record in records:
            for field in URL_FIELDS:
                url = record.get(field)
                if not url:
                    continue
                host = (urlsplit(url).hostname or "").lower()
                key_host = key.lower()
                on_key = host == key_host or host.endswith("." + key_host)
                if not on_key and (key, host) not in ALLOWED_CROSS_DOMAIN:
                    problems.append(f"{key} {field} {host}")
    return problems


def test_curated_urls_stay_on_the_key_domain():
    assert key_domain_problems(task_routes_data.TASK_ROUTES_CURATED) == []


def _table(url):
    return {"calm.com": [{"official_url": "https://www.calm.com/help", "source_url": url}]}


def test_subdomains_of_the_key_pass():
    assert key_domain_problems(_table("https://support.calm.com/terms")) == []


def test_other_domain_is_flagged_and_can_be_allowed_explicitly(monkeypatch):
    table = _table("https://www.example.org/terms")
    assert key_domain_problems(table) == ["calm.com source_url www.example.org"]
    monkeypatch.setattr(mod, "ALLOWED_CROSS_DOMAIN", {("calm.com", "www.example.org")})
    assert key_domain_problems(table) == []
    # the allowance is for that exact pair only
    assert key_domain_problems({"calm.com": [{"official_url": "https://other.example.org/x"}]}) != []


def test_sibling_hosts_under_a_shared_parent_are_flagged():
    # government keys are whole hosts, so a sibling under the same parent is another site
    assert key_domain_problems({"dmv.ca.gov": [{"source_url": "https://edd.ca.gov/x"}]}) == [
        "dmv.ca.gov source_url edd.ca.gov"
    ]
    assert key_domain_problems({"my.uscis.gov": [{"source_url": "https://www.uscis.gov/x"}]}) == [
        "my.uscis.gov source_url www.uscis.gov"
    ]


def test_allowance_is_tied_to_its_key(monkeypatch):
    monkeypatch.setattr(mod, "ALLOWED_CROSS_DOMAIN", {("calm.com", "www.example.org")})
    assert key_domain_problems({"headspace.com": [{"source_url": "https://www.example.org/terms"}]}) == [
        "headspace.com source_url www.example.org"
    ]


def test_lookalike_suffix_is_not_the_key_domain():
    assert key_domain_problems(_table("https://notcalm.com/terms")) != []


def test_curated_check_fails_for_a_moved_record(monkeypatch):
    moved = {"calm.com": [{"official_url": "https://www.example.org/terms"}]}
    monkeypatch.setattr(task_routes_data, "TASK_ROUTES_CURATED", moved)
    with pytest.raises(AssertionError):
        test_curated_urls_stay_on_the_key_domain()
    monkeypatch.setattr(mod, "ALLOWED_CROSS_DOMAIN", {("calm.com", "www.example.org")})
    test_curated_urls_stay_on_the_key_domain()
