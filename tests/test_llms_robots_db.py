"""D56: a transport-failed robots read keeps the previous profile row, llms.txt status included (dedicated ahn_test only)."""

from __future__ import annotations

import os

import pytest

TEST_URL = os.environ.get("AHN_TEST_DATABASE_URL", "")
if not TEST_URL:
    pytest.skip("AHN_TEST_DATABASE_URL is required", allow_module_level=True)

from datetime import datetime, timedelta, timezone  # noqa: E402

from ahn import access_profile  # noqa: E402
from ahn.db import connect, migrate  # noqa: E402
from ahn.settings import Settings  # noqa: E402


def test_a_failed_robots_read_does_not_overwrite_the_previous_llms_status():
    settings = Settings(TEST_URL, "test-pepper-must-be-at-least-thirty-two-chars", public_mode=True)
    with connect(settings) as conn:
        if conn.info.dbname != "ahn_test":
            raise RuntimeError("Refusing to reset a database other than ahn_test")
    migrate(settings)
    with connect(settings) as conn:
        conn.execute("TRUNCATE host_access_profile CASCADE")
    now = datetime.now(timezone.utc)
    ok = lambda host, path, **_kw: ("ok", b"User-agent: *\nAllow: /\n", "http_200") if path == "/robots.txt" else ("ok", b"# x\n", "http_200")  # noqa: E731
    assert access_profile.get_profile(settings, "example.com", now=now, reader=ok)["llms_txt_status"] == "present"
    fail = lambda host, path, **_kw: ("error", None, "timeout")  # noqa: E731
    later = now + timedelta(hours=2)
    kept = access_profile.get_profile(settings, "example.com", now=later, reader=fail)
    assert kept["llms_txt_status"] == "present" and kept["robots_status"] == "ok"
    with connect(settings) as conn:
        row = conn.execute("SELECT llms_txt_status, robots_status FROM host_access_profile WHERE site_host='example.com'").fetchone()
    assert (row["llms_txt_status"], row["robots_status"]) == ("present", "ok")
