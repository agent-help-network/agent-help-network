"""Curation advisories for task routes (scripts only): lists, never failures. No database, no network."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

from ahn.task_routes_data import TASK_ROUTES_CURATED

ROOT = Path(__file__).resolve().parents[1]


def _load():
    spec = importlib.util.spec_from_file_location("verify_official_paths", ROOT / "scripts" / "verify_official_paths.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("verify_official_paths", module)
    spec.loader.exec_module(module)
    return module


verify = _load()


def _rec(obstacle: str, text: str | None = None, kind: str = "refund_request") -> dict:
    record = {"task_kind": kind, "obstacle": obstacle, "routes_tried": [], "basis": "official_doc", "outcome": "not_applicable",
              "source_kind": "curated_official", "period": "2026-Q4", "source_url": "https://www.example-shop.com/help",
              "verified_at": "2026-11-14", "official_url": "https://www.example-shop.com/help"}
    if text is not None:
        record["condition_text"] = text
    return record


def _adv(*records: dict) -> dict[str, list[str]]:
    return verify.task_routes_curation_advisories({"example-shop.com": list(records)})


def _line(number: int, text: str | None = None, obstacle: str = "deadline_or_window", kind: str = "refund_request") -> str:
    shown = "-" if not text else (text if len(text) <= 40 else text[:40].rstrip() + "...")
    return f'example-shop.com #{number} {kind} {obstacle} "{shown}"'


LINE = _line(1)


def test_deadline_without_text_is_listed():
    adv = _adv(_rec("deadline_or_window"))
    assert adv["deadline_no_text"] == [LINE]
    assert adv["deadline_no_time"] == [] and adv["fee_no_text"] == [] and adv["open_ended"] == []


def test_deadline_text_without_time_is_listed():
    text = "the original purchaser may return it"
    adv = _adv(_rec("deadline_or_window", text))
    assert adv["deadline_no_time"] == [_line(1, text)] and adv["deadline_no_text"] == []


def test_deadline_text_cut_in_a_list_is_listed_twice():
    text = "Applies to ReviveRX 5-in-1 with GHK-Cu,"
    adv = _adv(_rec("deadline_or_window", text))
    assert adv["deadline_no_time"] == [_line(1, text)]
    assert adv["open_ended"] == [_line(1, text)]


def test_fee_without_text_is_listed():
    adv = _adv(_rec("fee_required", kind="return_or_exchange"))
    assert adv["fee_no_text"] == [_line(1, None, "fee_required", "return_or_exchange")]


def test_time_quantity_is_not_listed():
    for text in ("within 30 days of delivery.", "a 30-day money-back guarantee", "no more than three (3) business days",
                 "up to 1 year before it expires", "at least 24 hours before renewal", "within fourteen (14) days of delivery",
                 "within 30 consecutive days", "within 48hrs of the order", "within 48 hrs of the order",
                 "must be returned within (30) days"):
        adv = _adv(_rec("deadline_or_window", text))
        assert not any(adv.values()), text


def test_non_time_numbers_do_not_count_as_a_quantity():
    text = "one of the following (2) items at 100% value."
    adv = _adv(_rec("deadline_or_window", text))
    assert adv["deadline_no_time"] == [_line(1, text)]


def test_line_numbers_count_records_of_the_key_from_one():
    adv = _adv(_rec("fee_required", "A fee applies.", kind="return_or_exchange"), _rec("deadline_or_window"))
    assert adv["deadline_no_text"] == [_line(2)]


def test_same_task_with_a_time_is_marked_on_the_no_time_line():
    first, second = "within 30 days of delivery.", "the original purchaser may return it"
    note = " (another record for this task states a time)"
    adv = _adv(_rec("deadline_or_window", first), _rec("deadline_or_window", second))
    assert adv["deadline_no_time"] == [_line(2, second) + note]
    other = _adv(_rec("deadline_or_window", first, kind="cancel_subscription"), _rec("deadline_or_window", second))
    assert other["deadline_no_time"] == [_line(2, second)]


def test_open_ended_endings_ignore_trailing_spaces():
    for tail in (",", "(", ":", ";", ",  "):
        assert _adv(_rec("none", f"see the list{tail}"))["open_ended"] == [_line(1, f"see the list{tail}".strip(), "none")], tail
    assert _adv(_rec("none", "a whole sentence."))["open_ended"] == []


def test_advisories_never_count_as_table_problems():
    table = {"example-shop.com": [_rec("deadline_or_window"), _rec("deadline_or_window", "the original purchaser,"),
                                  _rec("fee_required")]}
    assert any(verify.task_routes_curation_advisories(table).values())
    assert verify.task_routes_table_problems(table) == []


def test_real_table_has_advisories_and_no_failures():
    assert verify.task_routes_table_problems(TASK_ROUTES_CURATED) == []
    adv = verify.task_routes_curation_advisories(TASK_ROUTES_CURATED)
    assert set(adv) == {"deadline_no_text", "deadline_no_time", "fee_no_text", "open_ended"}
    assert sum(len(lines) for lines in adv.values()) > 0


def test_printing_advisories_does_not_change_the_result():
    out: list[str] = []
    table = {"example-shop.com": [_rec("deadline_or_window")]}
    verify.print_curation_advisories(table, out=out.append)
    assert out[0] == "advisories (not failures):"
    assert any(LINE in line for line in out)
    assert verify.task_routes_table_problems(table) == []


def test_task_routes_check_exits_zero_with_advisories_only(monkeypatch, capsys):
    """check_task_routes with the page reading stubbed out: the real table has advisories and the exit code is still 0."""

    class _Fetcher:
        requests = 0

    class _Checker:
        help_robots: dict = {}
        stopped: dict = {}
        pages: dict = {}

        def __init__(self, fetcher, out=print, help_pages=False):
            pass

        def run(self, table, keys):
            return []

    monkeypatch.setattr(verify, "Fetcher", _Fetcher)
    monkeypatch.setattr(verify, "ConsumerCheck", _Checker)
    monkeypatch.setattr(verify, "task_route_check_pages", lambda keys, exceptions=None: {})
    assert verify.check_task_routes(None) == 0
    out = capsys.readouterr().out
    assert "advisories (not failures):" in out
    assert "deadline_no_text: " in out and "rule problems: 0" in out
