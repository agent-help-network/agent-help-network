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


LINE = "example-shop.com refund_request deadline_or_window"


def test_deadline_without_text_is_listed():
    adv = _adv(_rec("deadline_or_window"))
    assert adv["deadline_no_text"] == [LINE]
    assert adv["deadline_no_time"] == [] and adv["fee_no_text"] == [] and adv["open_ended"] == []


def test_deadline_text_without_time_is_listed():
    adv = _adv(_rec("deadline_or_window", "the original purchaser may return it"))
    assert adv["deadline_no_time"] == [LINE] and adv["deadline_no_text"] == []


def test_deadline_text_cut_in_a_list_is_listed_twice():
    adv = _adv(_rec("deadline_or_window", "Applies to ReviveRX 5-in-1 with GHK-Cu,"))
    assert adv["deadline_no_time"] == [LINE]
    assert adv["open_ended"] == [LINE]


def test_fee_without_text_is_listed():
    adv = _adv(_rec("fee_required", kind="return_or_exchange"))
    assert adv["fee_no_text"] == ["example-shop.com return_or_exchange fee_required"]


def test_time_quantity_is_not_listed():
    for text in ("within 30 days of delivery.", "a 30-day money-back guarantee", "no more than three (3) business days",
                 "up to 1 year before it expires", "at least 24 hours before renewal"):
        adv = _adv(_rec("deadline_or_window", text))
        assert not any(adv.values()), text


def test_non_time_numbers_do_not_count_as_a_quantity():
    adv = _adv(_rec("deadline_or_window", "one of the following (2) items at 100% value."))
    assert adv["deadline_no_time"] == [LINE]


def test_open_ended_endings_ignore_trailing_spaces():
    for tail in (",", "(", ":", ";", ",  "):
        assert _adv(_rec("none", f"see the list{tail}"))["open_ended"] == ["example-shop.com refund_request none"], tail
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
