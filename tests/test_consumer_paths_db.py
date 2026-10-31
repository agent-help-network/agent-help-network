"""D61 end to end against ahn_test: the REST answer and the search page carry the site's own customer path.

No real host is read (tests/conftest.py stubs every background read)."""

from __future__ import annotations

import os
from datetime import date, datetime, timezone

import pytest

TEST_URL = os.environ.get("AHN_TEST_DATABASE_URL", "")
if not TEST_URL:
    pytest.skip("AHN_TEST_DATABASE_URL is required", allow_module_level=True)

from fastapi.testclient import TestClient  # noqa: E402

from ahn import background_reads  # noqa: E402
from ahn import official_paths as official_paths_module  # noqa: E402
from ahn.db import connect, migrate  # noqa: E402
from ahn.main import create_app  # noqa: E402
from ahn.official_paths import CONSUMER_PATHS, OFFICIAL_PATHS  # noqa: E402
from ahn.response_guide import CONSUMER_SEE_TAIL  # noqa: E402
from ahn.settings import Settings  # noqa: E402

HOST = "walgreens.com"


@pytest.fixture
def db():
    settings = Settings(TEST_URL, "test-pepper-must-be-at-least-thirty-two-chars", public_mode=True)
    with connect(settings) as conn:
        if conn.info.dbname != "ahn_test":
            raise RuntimeError("Refusing to reset a database other than ahn_test")
    migrate(settings)
    with connect(settings) as conn:
        conn.execute("TRUNCATE lookup_metrics, request_metrics, mcp_check_metrics, status_cache, status_sources, queried_hosts, "
                     "host_access_profile, background_reads RESTART IDENTITY")
    return settings


@pytest.fixture(autouse=True)
def no_worker(monkeypatch):
    monkeypatch.setattr(background_reads, "worker_running", lambda: False)


@pytest.fixture(autouse=True)
def verified_day(monkeypatch):
    """The service's today is the day the entry was verified, so these tests do not expire 90 days after it."""
    day = date.fromisoformat(CONSUMER_PATHS[HOST]["verified_at"])

    class Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(day.year, day.month, day.day, 12, 0, tzinfo=tz or timezone.utc)
    monkeypatch.setattr(official_paths_module, "datetime", Frozen)


def test_the_rest_answer_names_both_paths_and_keeps_the_verdict(db):
    with TestClient(create_app(db)) as client:
        body = client.get("/v1/incidents/check", params={"site_host": HOST, "error_class": "site_access"}).json()
        plain = client.get("/v1/incidents/check", params={"site_host": "github.com", "error_class": "site_access"}).json()
    assert body["official_paths"]["consumer"]["paths"] == CONSUMER_PATHS[HOST]["paths"]
    assert body["recommendation"]["action"] == "use_official_path"
    assert body["next_action"]["do"] == "use_official_path"
    assert body["next_action"]["url"] == OFFICIAL_PATHS[HOST]["developer_portal"]
    assert body["next_action"]["customer_url"] == CONSUMER_PATHS[HOST]["paths"][0]["url"]
    assert body["summary_line"].endswith(CONSUMER_SEE_TAIL) and len(body["summary_line"]) <= 200
    # A developer host without a consumer entry answers as before: no new keys.
    assert "consumer" not in plain["official_paths"] and "customer_url" not in plain["next_action"]
    assert not plain["summary_line"].endswith(CONSUMER_SEE_TAIL)


@pytest.mark.parametrize("lang, lead", [("en", "For a task in your own account"), ("ko", "내 계정으로 하는 일이라면")])
def test_the_search_page_links_the_customer_path(db, lang, lead):
    with TestClient(create_app(db)) as client:
        page = client.get(f"/{lang}/search", params={"site_host": HOST}).text
        other = client.get(f"/{lang}/search", params={"site_host": "github.com"}).text
    assert lead in page
    for item in CONSUMER_PATHS[HOST]["paths"]:
        assert f'href="{item["url"]}"' in page
    assert lead not in other
