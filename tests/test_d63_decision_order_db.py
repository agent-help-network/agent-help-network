"""D63 through the service against ahn_test: a failure signal splits the answer, a test header does not."""

from __future__ import annotations

import os

import pytest

TEST_URL = os.environ.get("AHN_TEST_DATABASE_URL", "")
if not TEST_URL:
    pytest.skip("AHN_TEST_DATABASE_URL is required", allow_module_level=True)

from fastapi.testclient import TestClient  # noqa: E402

from ahn import background_reads  # noqa: E402
from ahn.db import connect, migrate  # noqa: E402
from ahn.main import create_app  # noqa: E402
from ahn.settings import Settings  # noqa: E402


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


def test_t30_a_test_header_does_not_change_the_answer_and_a_failure_signal_is_read(db):
    with TestClient(create_app(db)) as client:
        base = {"site_host": "github.com"}
        plain = client.get("/v1/incidents/check", params=base).json()
        flagged = client.get("/v1/incidents/check", params=base, headers={"X-AHN-Test": "1"}).json()
        failed = client.get("/v1/incidents/check", params={**base, "failure_stage": "unknown"}).json()
    assert flagged["recommendation"] == plain["recommendation"] and flagged["next_action"] == plain["next_action"]
    # The host was not read yet, so the robots layer is pending in all three; the failure signal only adds its check.
    codes = [c["code"] for c in failed["next_checks"]]
    assert "locate_failure_stage" in codes
    assert "locate_failure_stage" not in [c["code"] for c in plain["next_checks"]]
