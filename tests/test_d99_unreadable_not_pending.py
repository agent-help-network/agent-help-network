"""D99: an unreadable-robots host shows ahn_check as not_measured (robots_unreadable), not pending with a wait.

No database: a stand-in connection and stubs. The recommendation, summary_line, next_action and cause are decided before
site_probe.for_lookup runs, so the comparison here is of the ahn_check object only; the DB test file checks the whole answer."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from ahn import settings as settings_module
from ahn import site_probe
from ahn.models import AhnCheck
from ahn.settings import Settings, parse_d99_switch

NOW = datetime(2026, 11, 5, tzinfo=timezone.utc)
HOST = "unread-example.org"


class Cursor:
    def fetchone(self):
        return None


class Conn:
    def execute(self, *_a, **_k):
        return Cursor()

    def commit(self):
        pass

    def rollback(self):
        pass


def unreadable_profile(**over):
    profile = {"robots_status": "error", "fetched_at": None, "freshness": "pending", "llms_txt_status": "pending"}
    profile.update(over)
    return profile


@pytest.fixture
def probe(monkeypatch):
    queued = []
    monkeypatch.setenv("AHN_SITE_PROBE", "1")
    monkeypatch.setattr(site_probe, "_lookup_wanted", lambda *_a, **_k: True)
    monkeypatch.setattr(site_probe, "_queue", lambda _conn, host: queued.append(host))
    return queued


def settings(on=True):
    return Settings("postgresql://unused", "x" * 32, d99_unreadable_not_pending=on)


def look(profile, on=True):
    return site_probe.for_lookup(settings(on), Conn(), HOST, NOW, access_profile=profile)


def test_unreadable_robots_shape_is_not_measured_with_no_wait(probe):
    shown = look(unreadable_profile())
    assert shown["status"] == "not_measured" and shown["not_measured_reason"] == "robots_unreadable"
    assert "retry_after_seconds" not in shown
    AhnCheck(**shown)
    assert probe == [HOST]          # the queueing is left as it was


def test_switch_off_is_the_answer_before_d99(probe):
    shown = look(unreadable_profile(), on=False)
    assert shown == site_probe.pending_view(site_probe.PENDING_RETRY_SECONDS) and shown["retry_after_seconds"] == 60


@pytest.mark.parametrize("profile", [
    unreadable_profile(retry_after_seconds=60),                 # a read is queued/due: still pending
    unreadable_profile(retry_after_seconds=3600),
    unreadable_profile(fetched_at=NOW),                         # a read once succeeded
    unreadable_profile(robots_status="pending"),                # first lookup, nothing read yet
    unreadable_profile(robots_status="refused"),
    unreadable_profile(robots_status="ok", freshness="stale"),
    None,
    {},
])
def test_every_other_profile_is_unchanged(probe, profile):
    for on in (True, False):
        expected = site_probe.pending_view(site_probe._profile_retry(profile))
        assert look(profile, on=on) == expected


def test_a_stored_row_wins(probe):
    stored = {"outcome": "not_measured", "not_measured_reason": "backoff", "probed_at": NOW, "fingerprint_version": site_probe.FINGERPRINT_VERSION,
              "vantage": None, "next_due_at": datetime(2000, 1, 1, tzinfo=timezone.utc)}

    class RowConn(Conn):
        def execute(self, *_a, **_k):
            class C:
                def fetchone(self_inner):
                    return stored
            return C()
    for on in (True, False):
        shown = site_probe.for_lookup(settings(on), RowConn(), HOST, NOW, access_profile=unreadable_profile())
        assert shown["status"] == "not_measured" and shown["not_measured_reason"] == "backoff"


def test_fresh_profile_keeps_the_synchronous_measurement_path(probe, monkeypatch):
    called = []
    monkeypatch.setattr(site_probe, "sync_measure", lambda *_a, **_k: called.append(1) or (None, "timeout"))
    shown = look({"robots_status": "ok", "freshness": "fresh", "fetched_at": NOW})
    assert called == [1] and shown["status"] == "pending"


@pytest.mark.parametrize("raw,expected", [("", True), ("1", True), (" 1 ", True), ("0", False), ("true", False), ("off", False)])
def test_switch_parser(raw, expected, monkeypatch):
    monkeypatch.setattr(settings_module, "_D99_WARNED", False)
    assert parse_d99_switch(raw) is expected


def test_unknown_value_warns_once_without_the_value(monkeypatch, caplog):
    monkeypatch.setattr(settings_module, "_D99_WARNED", False)
    with caplog.at_level("WARNING"):
        parse_d99_switch("secretish")
        parse_d99_switch("secretish")
    assert [r.getMessage() for r in caplog.records] == ["d99_switch_unknown_value"]


def test_from_env_reads_the_switch(monkeypatch):
    monkeypatch.setenv("AHN_DATABASE_URL", "postgresql://unused")
    monkeypatch.setenv("AHN_KEY_PEPPER", "x" * 32)
    monkeypatch.delenv("AHN_D99_UNREADABLE_NOT_PENDING", raising=False)
    assert Settings.from_env().d99_unreadable_not_pending is True
    monkeypatch.setenv("AHN_D99_UNREADABLE_NOT_PENDING", "0")
    assert Settings.from_env().d99_unreadable_not_pending is False
