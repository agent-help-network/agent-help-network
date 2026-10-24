"""The home page's real-answer card against the dedicated ahn_test database: it is built from stored rows only,
writes nothing, queues no read and contacts no host. Every read uses a stub."""

from __future__ import annotations

import os

import pytest

TEST_URL = os.environ.get("AHN_TEST_DATABASE_URL", "")
if not TEST_URL:
    pytest.skip("AHN_TEST_DATABASE_URL is required", allow_module_level=True)

from fastapi.testclient import TestClient  # noqa: E402  (after the skip: ahn.main builds an app from the environment)

from ahn import access_profile, service
from ahn.db import connect, migrate
from ahn.main import create_app
from ahn.settings import Settings

ROBOTS = b"User-agent: Bytespider\nDisallow: /\n\nUser-agent: *\nAllow: /\n"
OFFICIAL = {"name": "GitHub", "api_docs": "https://docs.github.com/en/rest", "developer_portal": "https://docs.github.com/", "note": None}
WRITTEN = ("background_reads", "queried_hosts", "lookup_metrics", "host_access_profile", "status_cache")


def prepare(monkeypatch, **flags):
    settings = Settings(TEST_URL, "test-pepper-must-be-at-least-thirty-two-chars", public_mode=True, **flags)
    with connect(settings) as conn:
        if conn.info.dbname != "ahn_test":
            raise RuntimeError("Refusing to reset a database other than ahn_test")
    migrate(settings)
    with connect(settings) as conn:
        conn.execute("TRUNCATE status_cache, status_sources, queried_hosts, host_access_profile, background_reads, lookup_metrics, "
                     "request_metrics RESTART IDENTITY CASCADE")
    monkeypatch.setattr(service, "cached_profile", access_profile.cached_profile)
    monkeypatch.setattr(service, "official_paths", lambda host: dict(OFFICIAL) if host == "github.com" else None)
    return settings


def counts(settings):
    with connect(settings) as conn:
        return {name: conn.execute(f"SELECT count(*) AS n FROM {name}").fetchone()["n"] for name in WRITTEN}


def seed(settings):
    reader = lambda host, path, **_kw: ("ok", ROBOTS, "http_200") if path == "/robots.txt" else ("absent", None, "http_404")  # noqa: E731
    assert access_profile.get_profile(settings, "github.com", reader=reader)["robots_status"] == "ok"


def refuse_network(monkeypatch):
    def boom(*_a, **_k):
        raise AssertionError("the home page must not read a host")
    monkeypatch.setattr(access_profile, "fetch_public_file", boom)
    monkeypatch.setattr("ahn.background_reads.read_profile_file", boom)


def test_nothing_cached_means_no_card_and_no_write(monkeypatch):
    settings = prepare(monkeypatch)
    refuse_network(monkeypatch)
    before = counts(settings)
    assert service.home_example(settings) is None
    with TestClient(create_app(settings)) as client:
        for lang in ("en", "ko"):
            page = client.get(f"/{lang}/")
            assert page.status_code == 200
            assert "home-live" not in page.text and "Bytespider" not in page.text
    assert counts(settings) == before


def test_cached_host_gives_a_card_from_stored_rows_and_nothing_is_written(monkeypatch):
    settings = prepare(monkeypatch)
    seed(settings)
    refuse_network(monkeypatch)
    before = counts(settings)
    card = service.home_example(settings)
    assert card["site_host"] == "github.com" and card["do"] == "use_official_path"
    assert card["url"] == "docs.github.com/en/rest" and card["confidence"]
    assert card["robots_status"] == "ok" and card["status"] is None and len(card["read_on"]) == 10
    assert card["summary_line"].isascii()
    with TestClient(create_app(settings)) as client:
        en = client.get("/en/").text
        ko = client.get("/ko/").text
    assert 'class="home-live"' in en and "use_official_path" in en and "docs.github.com/en/rest" in en
    assert 'class="home-live"' in ko and "use_official_path" in ko
    assert counts(settings) == before


def test_the_card_is_left_out_when_the_response_guide_is_off_or_a_feed_cannot_show(monkeypatch):
    settings = prepare(monkeypatch)
    seed(settings)
    assert service.home_example(Settings(TEST_URL, "test-pepper-must-be-at-least-thirty-two-chars", public_mode=True,
                                         response_guide=False)) is None
    with connect(settings) as conn:
        conn.execute("INSERT INTO status_sources (site_host,source_name,status_page_url,api_url,enabled) VALUES "
                     "('github.com','GitHub Status','https://www.githubstatus.com','https://www.githubstatus.com/api/v2/status.json',true)")
    monkeypatch.setattr("ahn.feed_robots.feed_state", lambda _conn, _url: "disallowed")
    assert service.home_example(settings) is None
