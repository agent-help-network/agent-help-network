"""D59: get_profile stores the standard-file reads with the robots read they belong to (dedicated ahn_test only)."""

from __future__ import annotations

import os

import pytest

TEST_URL = os.environ.get("AHN_TEST_DATABASE_URL", "")
if not TEST_URL:
    pytest.skip("AHN_TEST_DATABASE_URL is required", allow_module_level=True)

from datetime import datetime, timedelta, timezone  # noqa: E402

from ahn import access_profile, site_declared  # noqa: E402
from ahn.db import connect, migrate  # noqa: E402
from ahn.models import AccessProfile  # noqa: E402
from ahn.settings import Settings  # noqa: E402

CATALOG = b'{"linkset": [{"anchor": "https://example.com/api", "service-doc": [{"href": "https://example.com/docs"}]}]}'


@pytest.fixture
def settings():
    settings = Settings(TEST_URL, "test-pepper-must-be-at-least-thirty-two-chars", public_mode=True)
    with connect(settings) as conn:
        if conn.info.dbname != "ahn_test":
            raise RuntimeError("Refusing to reset a database other than ahn_test")
    migrate(settings)
    with connect(settings) as conn:
        conn.execute("TRUNCATE host_access_profile CASCADE")
    return settings


def _reader(calls, robots=b"User-agent: *\nAllow: /\n"):
    def read(host, path, **_kw):
        calls.append(path)
        if path == "/robots.txt":
            return ("ok", robots, "http_200")
        if path == site_declared.API_CATALOG_PATH:
            return ("ok", CATALOG, "http_200")
        return ("absent", None, "http_404")
    return read


def test_get_profile_stores_the_site_declared_read_with_its_robots_read(settings):
    now = datetime.now(timezone.utc).replace(microsecond=0)
    calls: list[str] = []
    profile = access_profile.get_profile(settings, "example.com", now=now, reader=_reader(calls))
    assert calls == ["/robots.txt", "/llms.txt", site_declared.API_CATALOG_PATH, site_declared.TDMREP_PATH,
                     site_declared.AI_CATALOG_PATH]
    shown = profile["site_declared"]
    assert shown["api_catalog"]["status"] == "declared" and shown["api_catalog"]["apis"][0]["service_doc"] == ["https://example.com/docs"]
    assert shown["tdm"] == {"status": "none_declared", "reason": "absent", "source_url": "https://example.com/.well-known/tdmrep.json"}
    AccessProfile(**profile, note="n")
    with connect(settings) as conn:
        row = conn.execute("SELECT site_declared FROM host_access_profile WHERE site_host='example.com'").fetchone()
    assert row["site_declared"]["version"] == site_declared.VERSION
    assert "linkset" not in str(row["site_declared"])  # parsed fields only, never the file
    # The cache answers from the same row without reading again.
    again: list[str] = []
    cached = access_profile.get_profile(settings, "example.com", now=now + timedelta(minutes=5), reader=_reader(again))
    assert again == [] and cached["site_declared"]["api_catalog"]["status"] == "declared"


def test_a_failed_robots_read_keeps_the_previous_site_declared_and_a_new_read_never_shows_an_old_one(settings):
    now = datetime.now(timezone.utc).replace(microsecond=0)
    access_profile.get_profile(settings, "example.com", now=now, reader=_reader([]))
    failed = access_profile.get_profile(settings, "example.com", now=now + timedelta(hours=25),
                                        reader=lambda host, path, **_kw: ("error", None, "connect_timeout"))
    assert failed["site_declared"]["api_catalog"]["status"] == "declared" and failed["site_declared"]["freshness"] == "stale"
    # A robots.txt that now disallows the files: nothing is requested and the old result is not shown as current.
    calls: list[str] = []
    later = access_profile.get_profile(settings, "example.com", now=now + timedelta(hours=50),
                                       reader=_reader(calls, robots=b"User-agent: *\nDisallow: /.well-known/\n"))
    assert calls == ["/robots.txt", "/llms.txt"]
    assert later["site_declared"]["api_catalog"] == {"status": "not_read", "reason": "robots_disallowed",
                                                     "source_url": "https://example.com/.well-known/api-catalog"}


def test_the_update_only_lands_on_the_read_it_belongs_to(settings):
    now = datetime.now(timezone.utc).replace(microsecond=0)
    access_profile.get_profile(settings, "example.com", now=now, reader=_reader([]))
    declared = {"read_host": "example.com", "hop": 0, "headers": [],
                "gates": {path: (True, None) for path in site_declared.paths()}}
    older = now - timedelta(hours=1)
    assert access_profile._store_site_declared(settings, "example.com", declared, _reader([]), older) is None
    with connect(settings) as conn:
        row = conn.execute("SELECT site_declared FROM host_access_profile WHERE site_host='example.com'").fetchone()
    assert datetime.fromisoformat(row["site_declared"]["read_at"]) == now
