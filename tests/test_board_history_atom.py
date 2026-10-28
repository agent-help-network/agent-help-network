"""D57: an open incident read from a page's Atom history (D52) on the public board and in the lookup. Pure: no database
(the end-to-end checks are in test_public_board_db.py and test_status_atom_db.py).

The board must never say more than the lookup (D53): a board line always implies a lookup wait. D57 keeps that by
letting both read the same predicate, triage.history_incident, through triage.provider_wait_reason.
"""

from __future__ import annotations

import itertools
from datetime import datetime, timedelta, timezone

import pytest

from ahn import public_board, signal_history, triage

NOW = datetime(2026, 10, 27, 15, 0, tzinfo=timezone.utc)


def atom(incidents=1, updated=NOW - timedelta(minutes=30), *, blind=None, freshness="fresh", maintenances=None) -> dict:
    status = {"source_name": "GitHub Status", "source_url": "https://www.githubstatus.com/", "indicator": None,
              "source_updated_at": None, "fetched_at": NOW, "freshness": freshness, "source_kind": "history_atom"}
    if incidents is not None:
        status["active_incidents"] = incidents
    if updated is not None:
        status["incident_updated_at"] = updated
    if blind is not None:
        status["window_blind"] = blind
    if maintenances is not None:
        status["active_maintenances"] = maintenances
    return status


@pytest.mark.parametrize("provider, expected", [
    (atom(), None),                                                         # listed
    (atom(incidents=3, maintenances=2), None),                              # maintenances neither help nor hurt
    (atom(updated=NOW - timedelta(days=7)), None),                          # exactly a week: still listed (as D53)
    (atom(updated=NOW - timedelta(days=7, seconds=1)), "stale_incidents"),  # just past the week
    (atom(updated=NOW - timedelta(days=48)), "stale_incidents"),            # mongodb's 48-day incident (2026-10-22 sample)
    (atom(updated=None), "no_update_time"),                                 # an open incident without <updated>
    (atom(updated=NOW + timedelta(minutes=30)), None),                      # clock skew within FUTURE_SLACK
    (atom(updated=NOW + timedelta(hours=2)), "no_update_time"),             # further ahead: no time
    (atom(updated="not a time"), "no_update_time"),
    (atom(blind=True), "window_blind"),                                     # the feed may be missing incidents
    (atom(blind=True, updated=None), "window_blind"),                       # blind is said first
    (atom(blind=False), None),
    (atom(incidents=True), "not_a_wait"),                                   # not a whole number
    (atom(incidents="1"), "not_a_wait"),
])
def test_board_reasons_for_an_atom_reading(provider, expected):
    assert signal_history.board_provider(provider, NOW) == expected
    if expected is not None:
        assert expected in signal_history.PROVIDER_EXCLUSIONS


def test_a_board_line_from_the_atom_history_always_implies_a_lookup_wait_and_back():
    """D53's invariant over every Atom shape: listed implies the lookup waits. For an Atom reading D57 makes it
    two-way: the board lists exactly what the lookup waits on (no D53 time rule is stricter here, the predicate is one).
    The board reads only fresh statements (signal_history._provider_now), so a stale or pending one is never listed."""
    waited = 0
    for incidents, updated, blind, freshness, maintenances in itertools.product(
            (None, 0, 1, 2, True, 1.5, "1"),
            (NOW - timedelta(hours=1), NOW - timedelta(days=7), NOW - timedelta(days=8), NOW + timedelta(minutes=59),
             NOW + timedelta(hours=1, minutes=1), None, "2026-10-27T14:00:00Z"),
            (None, False, True, 1),
            ("fresh", "stale", "pending"),
            (None, 0, 3)):
        provider = atom(incidents, updated, blind=blind, freshness=freshness, maintenances=maintenances)
        waits = triage.provider_wait_reason(provider, NOW) is not None
        rec = triage.recommendation(None, provider, None, None, now=NOW)
        listed = freshness == "fresh" and signal_history.board_provider(provider, NOW) is None
        assert listed == waits == (rec["action"] == "wait_provider_incident") == triage.history_incident(provider, NOW), provider
        if waits:
            waited += 1
            assert rec == {"action": "wait_provider_incident", "confidence": "low",
                           "reasons": ["provider_scope", "provider_history_incident"]}
            assert type(incidents) is int and incidents >= 1 and blind in (None, False) and freshness == "fresh"
        else:
            assert "provider_history_incident" not in rec["reasons"]
    assert waited


def test_the_rows_without_an_open_incident_read_as_unknown_not_healthy():
    for provider in (atom(incidents=None, updated=None), atom(incidents=0, updated=None)):
        assert triage.provider_wait_reason(provider, NOW) is None
        codes = [check["code"] for check in triage.next_checks(None, provider, "no_reports", now=NOW)]
        assert "provider_unknown" in codes and "provider_clear" not in codes and "provider_scope" not in codes


def test_the_regional_flag_never_reaches_an_atom_wait():
    # The feed names no components; a stray regional flag must not turn the wait into a "some regions" answer.
    rec = triage.recommendation(None, atom(), None, None, provider_regional=True, now=NOW)
    assert rec["reasons"] == ["provider_scope", "provider_history_incident"] and rec["confidence"] == "low"
    codes = [c["code"] for c in triage.next_checks(None, atom(), "no_reports", provider_partial=True, now=NOW)]
    assert "provider_scope_partial" not in codes and codes.count("provider_history_incident") == 1


def test_both_languages_explain_the_new_code():
    assert "provider_history_incident" in triage.CHECKS_KO
    assert "history_atom" in triage.CHECKS["provider_history_incident"]
    assert "history_atom" in triage.CHECKS_KO["provider_history_incident"]
    assert f"{triage.STALE_INCIDENT_DAYS}일" in triage.CHECKS_KO["provider_history_incident"]
    assert f"{triage.STALE_INCIDENT_DAYS} days" in triage.CHECKS["provider_history_incident"]


def _shape(provider_line):
    return {"candidates": [provider_line], "provider_candidates": [provider_line], "truncated": False, "days": 7,
            "coverage": {"registered_feeds_curated": 1, "target_hosts": 1}, "p_episodes": 0, "held_now": False,
            "provider_excluded_now": dict.fromkeys(signal_history.PROVIDER_EXCLUSIONS, 0),
            "a_episodes": 0, "a_matched_feed": 0, "a_ahead_of_feed": 0, "a_likely_false_positive": 0}


def test_the_public_line_says_no_level_instead_of_making_one_up():
    line = {"site_host": "github.com", "ahn_check": None,
            "provider": {"indicator": None, "source_kind": "history_atom", "active_incidents": 2, "basis": None,
                         "source_name": "GitHub Status", "source_url": "https://www.githubstatus.com/",
                         "fetched_at": "2026-10-27T14:55:00Z", "source_updated_at": None, "updated_at": "2026-10-27T14:30:00Z"}}
    body = public_board.snapshot(_shape(line), ahn_check=False, now=NOW)
    assert body["maintenance"] == [] and [item["site_host"] for item in body["problems"]] == ["github.com"]
    provider = body["problems"][0]["provider"]
    assert provider == {"indicator": None, "source_kind": "history_atom", "active_incidents": 2,
                        "updated_at": "2026-10-27T14:30:00Z", "source_name": "GitHub Status",
                        "source_url": "https://www.githubstatus.com/", "fetched_at": "2026-10-27T14:55:00Z"}
    assert "window_blind" in body["stats_7d"]["provider_left_out_now"]
    assert "history_atom" in body["legend"]["provider"]


def test_a_line_from_a_status_api_keeps_its_shape():
    line = {"site_host": "zapier.com", "ahn_check": None,
            "provider": {"indicator": "major", "basis": "incident", "source_name": "Zapier Status",
                         "source_url": "https://status.zapier.com/", "fetched_at": "2026-10-27T14:55:00Z",
                         "source_updated_at": "2026-10-27T14:40:00Z", "updated_at": "2026-10-27T14:40:00Z"}}
    provider = public_board.snapshot(_shape(line), ahn_check=False, now=NOW)["problems"][0]["provider"]
    assert "source_kind" not in provider and "active_incidents" not in provider and provider["indicator"] == "major"


def test_the_code_is_in_the_model_the_contracts_the_readmes_and_the_plugin():
    from pathlib import Path

    from ahn.models import NextCheck

    root = Path(__file__).resolve().parents[1]
    code = "provider_history_incident"
    assert code in NextCheck.model_fields["code"].annotation.__args__
    en = (root / "docs" / "api-contract.en.md").read_text(encoding="utf-8")
    ko = (root / "docs" / "api-contract.md").read_text(encoding="utf-8")
    assert f"| `{code}` |" in en and f"`{code}`" in ko and "모두 35개 코드" in ko
    for name in ("README.md", "README.ko.md", "ahn/llms.txt", "plugins/claude-code/ahn/README.md"):
        assert code in (root / name).read_text(encoding="utf-8"), name
    assert "| D57 (2026-10-27" in (root / "docs" / "decisions.md").read_text(encoding="utf-8")
