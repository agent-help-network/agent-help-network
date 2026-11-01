"""Public board list size and order (no database needed): up to 100 lines each, newest update first whatever the grade."""

from __future__ import annotations

from datetime import datetime, timezone

from ahn import public_board, signal_history

NOW = datetime(2026, 10, 24, 3, 30, tzinfo=timezone.utc)


def _board(provider_candidates):
    return {"candidates": provider_candidates, "provider_candidates": provider_candidates, "truncated": False, "days": 7,
            "coverage": {"registered_feeds_curated": 1, "target_hosts": 1}, "p_episodes": 0, "held_now": False,
            "provider_excluded_now": dict.fromkeys(signal_history.PROVIDER_EXCLUSIONS, 0),
            "a_episodes": 0, "a_matched_feed": 0, "a_ahead_of_feed": 0, "a_likely_false_positive": 0}


def _line(host, indicator, updated="2026-10-24T03:00:00Z"):
    return {"site_host": host, "ahn_check": None,
            "provider": {"indicator": indicator, "source_name": "S", "source_url": "https://status.example/",
                         "updated_at": updated}}


def test_the_board_lists_up_to_100_lines_and_flags_more():
    assert public_board.LIST_MAX == 100
    for count, cut in ((21, False), (100, False), (101, True)):
        lines = [_line(f"h{i:03d}.example", "minor") for i in range(count)]
        body = public_board.snapshot(_board(lines), ahn_check=False, now=NOW)
        assert len(body["problems"]) == min(count, 100) and body["truncated"] is cut
    maint = [_line(f"m{i:03d}.example", "maintenance") for i in range(101)]
    body = public_board.snapshot(_board(maint), ahn_check=False, now=NOW)
    assert len(body["maintenance"]) == 100 and body["truncated"] is True


def test_newest_provider_update_comes_first_whatever_the_grade_then_host_name():
    entries = [_line("a.example", "minor", "2026-10-24T01:00:00Z"),
               _line("z.example", "minor", "2026-10-24T03:00:00Z"),
               _line("b.example", "minor", "2026-10-24T03:00:00Z"),
               _line("c.example", "major", "2026-10-23T01:00:00Z"),
               _line("d.example", None, "2026-10-24T02:00:00Z"),
               _line("e.example", "critical", "2026-10-20T01:00:00Z")]
    entries[4]["provider"]["source_kind"] = "history_atom"
    body = public_board.snapshot(_board(entries), ahn_check=False, now=NOW)
    assert [line["site_host"] for line in body["problems"]] == [
        "b.example", "z.example", "d.example", "a.example", "c.example", "e.example"]
    assert body["problems"][-1]["provider"]["indicator"] == "critical"  # the grade is still reported


def test_a_lower_grade_that_is_newer_ranks_above_a_higher_grade():
    entries = [_line("eclipse.org", "major", "2026-10-20T01:00:00Z"), _line("gcore.com", "minor", "2026-10-24T03:00:00Z")]
    assert [e["site_host"] for e in signal_history.board_order(entries)] == ["gcore.com", "eclipse.org"]


def test_owner_and_public_boards_share_one_order_function():
    check = lambda host, read, since: {"site_host": host, "provider": None,
                                       "ahn_check": {"kind": "x", "last_read_at": read, "since": since}}
    entries = [_line("gcore.com", "minor", "2026-10-24T03:00:00Z"), _line("eclipse.org", "major", "2026-10-20T01:00:00Z"),
               _line("n.example", "major", "2026-10-24T02:00:00Z"),
               check("o.example", "2026-10-24T02:30:00Z", "2026-10-01T00:00:00Z"),
               check("p.example", None, "2026-10-24T02:45:00Z"),
               {"site_host": "q.example", "provider": None, "ahn_check": None}]
    entries.append(_line("m.example", "major", None))
    entries[-1]["provider"].pop("updated_at")
    expected = ["gcore.com", "p.example", "o.example", "n.example", "eclipse.org", "m.example", "q.example"]
    assert [e["site_host"] for e in signal_history.board_order(entries)] == expected
    assert [e["site_host"] for e in public_board._ordered(entries)] == expected
