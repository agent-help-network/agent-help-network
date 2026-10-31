"""The Korean search page picks a next-check sentence by the English text that was served (web display only).

Five codes have a second English sentence; the Korean page must show the Korean of that sentence, not the plain
CHECKS_KO sentence of the code (which says something else). The API response is not touched.
"""
from __future__ import annotations

import re
from pathlib import Path

import jinja2
import pytest

from ahn import triage
from ahn.triage import (CHECK_TEXT_KO, CHECKS, CHECKS_KO, HISTORY_QUIET_CHECK, HISTORY_QUIET_CHECK_KO, LLMS_TOO_LARGE_CHECK,
                        LLMS_TOO_LARGE_CHECK_KO, MAINTENANCE_CHECK, MAINTENANCE_CHECK_KO, PAGE_STALE_CHECK, PAGE_STALE_CHECK_KO,
                        PREVIOUS_CHECKS, ROBOTS_ABSENT_CHECK, ROBOTS_ABSENT_CHECK_KO)

TEMPLATE = (Path(__file__).resolve().parents[1] / "ahn" / "templates" / "search.html").read_text(encoding="utf-8")
SPECIAL = [
    ("robots_allows_agents", ROBOTS_ABSENT_CHECK, ROBOTS_ABSENT_CHECK_KO),
    ("llms_txt_present", LLMS_TOO_LARGE_CHECK, LLMS_TOO_LARGE_CHECK_KO),
    ("provider_unknown", HISTORY_QUIET_CHECK, HISTORY_QUIET_CHECK_KO),
    ("provider_scope", MAINTENANCE_CHECK, MAINTENANCE_CHECK_KO),
    ("provider_incident_stale", PAGE_STALE_CHECK, PAGE_STALE_CHECK_KO),
]


def korean(item: dict) -> str:
    found = re.search(r"\{\{ item\.text if lang == 'en' else (\(ko_by_text\.get\(.*?\)) \}\}", TEMPLATE)
    assert found
    expression = jinja2.Environment().compile_expression(found.group(1))
    return expression(item=item, ko_by_text=CHECK_TEXT_KO, next_check_ko=CHECKS_KO)


@pytest.mark.parametrize("code,english,ko", SPECIAL)
def test_a_special_sentence_shows_its_own_korean_not_the_plain_one_of_its_code(code, english, ko):
    assert english != CHECKS[code]
    shown = korean({"code": code, "text": english})
    assert shown == ko and shown != CHECKS_KO[code]


@pytest.mark.parametrize("code", sorted(CHECKS))
def test_the_plain_sentence_of_every_code_keeps_the_plain_korean(code):
    assert korean({"code": code, "text": CHECKS[code]}) == CHECKS_KO[code]


@pytest.mark.parametrize("code", sorted(PREVIOUS_CHECKS))
def test_a_previous_wording_falls_back_to_the_code_korean(code):
    assert PREVIOUS_CHECKS[code] not in CHECK_TEXT_KO
    assert korean({"code": code, "text": PREVIOUS_CHECKS[code]}) == CHECKS_KO[code]


def test_an_unknown_sentence_and_code_show_the_english_text():
    assert korean({"code": "no_such_code", "text": "english"}) == "english"


def test_the_table_has_one_korean_per_english_sentence():
    assert all(CHECK_TEXT_KO[CHECKS[c]] == CHECKS_KO[c] for c in CHECKS if CHECKS[c] not in {s[1] for s in SPECIAL})
    assert triage.CHECK_TEXT_KO is CHECK_TEXT_KO
