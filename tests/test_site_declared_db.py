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


def test_a_file_with_nul_and_control_characters_is_stored(settings):
    # jsonb rejects U+0000: before the fix the whole UPDATE failed silently and the answer kept saying not_read.
    nul = ('{"specVersion": "1.0", "entries": [{"identifier": "urn:a\\u0000b", "type": "application/mcp-server-card+json",'
           ' "url": "https://example.com/card"}, {"identifier": "urn:ok", "type": "application/mcp-server-card+json",'
           ' "url": "https://example.com/c\\u202e"}]}').encode()
    rules = b'[{"location": "/\\u0000", "tdm-reservation": 1}, {"location": "/", "tdm-reservation": 0}]'

    def read(host, path, **_kw):
        files = {"/robots.txt": b"User-agent: *\nAllow: /\n", site_declared.AI_CATALOG_PATH: nul, site_declared.TDMREP_PATH: rules}
        return ("ok", files[path], "http_200") if path in files else ("absent", None, "http_404")

    now = datetime.now(timezone.utc).replace(microsecond=0)
    shown = access_profile.get_profile(settings, "example.com", now=now, reader=read)["site_declared"]
    assert shown["mcp_discovery"]["servers"] == [{"identifier": None, "card_url": "https://example.com/card"}]
    assert shown["mcp_discovery"]["servers_total"] == 1  # the entry with an unusable URL is skipped
    assert shown["tdm"]["rules"] == [{"location": "/", "tdm_reservation": 0}] and shown["tdm"]["root_reservation"] == 0


def test_unpaired_surrogates_are_dropped_and_the_rest_is_stored(settings):
    # jsonb rejects an unpaired surrogate (a JSON escape such as \ud800) as it rejects U+0000: before the fix the
    # whole UPDATE failed and the host stayed pending for good.
    catalog = ('{"specVersion": "1.0", "entries": [{"identifier": "urn:a\\ud800", "type": "application/mcp-server-card+json",'
               ' "url": "https://example.com/c\\udfff"}, {"identifier": "urn:ok\\u200b", "type": "application/mcp-server-card+json",'
               ' "url": "https://example.com/card"}]}').encode()

    def read(host, path, **_kw):
        files = {"/robots.txt": b"User-agent: *\nAllow: /\n", site_declared.AI_CATALOG_PATH: catalog,
                 site_declared.API_CATALOG_PATH: CATALOG}
        return ("ok", files[path], "http_200") if path in files else ("absent", None, "http_404")

    now = datetime.now(timezone.utc).replace(microsecond=0)
    shown = access_profile.get_profile(settings, "example.com", now=now, reader=read)["site_declared"]
    assert shown["mcp_discovery"]["status"] == "declared"
    assert shown["mcp_discovery"]["servers"] == [{"identifier": None, "card_url": "https://example.com/card"}]
    assert shown["api_catalog"]["status"] == "declared"
    with connect(settings) as conn:
        row = conn.execute("SELECT site_declared FROM host_access_profile WHERE site_host='example.com'").fetchone()
    assert not row["site_declared"].get(site_declared.PENDING)


def test_a_failed_store_is_logged_and_the_answer_says_store_failed(settings, monkeypatch, caplog):
    now = datetime.now(timezone.utc).replace(microsecond=0)

    def broken(*_args, **_kwargs):
        raise ValueError("boom")

    monkeypatch.setattr(site_declared, "read_all", broken)
    with caplog.at_level("WARNING", logger="ahn.access_profile"):
        profile = access_profile.get_profile(settings, "example.com", now=now, reader=_reader([]))
    sections = [profile["site_declared"][key] for key in ("api_catalog", "tdm", "mcp_discovery")]
    assert {(section["status"], section["reason"]) for section in sections} == {("not_read", "store_failed")}
    assert "site_declared_store_failed host=example.com error=ValueError" in caplog.text and "boom" not in caplog.text
    AccessProfile(**profile, note="n")
    # The stored marker replaces the pending one, so a later answer from the cache does not say pending either.
    with connect(settings) as conn:
        row = conn.execute("SELECT site_declared FROM host_access_profile WHERE site_host='example.com'").fetchone()
    assert row["site_declared"].get(site_declared.STORE_FAILED) and not row["site_declared"].get(site_declared.PENDING)
    cached = access_profile.get_profile(settings, "example.com", now=now + timedelta(minutes=5), reader=_reader([]))
    assert cached["site_declared"]["tdm"]["reason"] == "store_failed"


def test_a_refused_robots_txt_stops_the_standard_file_reads(settings):
    calls: list[str] = []

    def refusing(host, path, **_kw):
        calls.append(path)
        return ("refused", None, "http_403")

    now = datetime.now(timezone.utc).replace(microsecond=0)
    shown = access_profile.get_profile(settings, "example.com", now=now, reader=refusing)["site_declared"]
    assert not any(path.startswith("/.well-known/") for path in calls)
    assert shown["api_catalog"]["reason"] == "robots_refused"


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
