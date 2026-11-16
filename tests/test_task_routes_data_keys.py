"""A dict literal in the curated table must not repeat a string key: Python keeps the last one and the earlier records vanish silently. No database, no network."""

from __future__ import annotations

import ast
from pathlib import Path

DATA = Path(__file__).resolve().parents[1] / "ahn" / "task_routes_data.py"


def duplicate_keys(source: str) -> list[str]:
    found = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Dict):
            seen = set()
            for key in node.keys:
                if isinstance(key, ast.Constant) and isinstance(key.value, str):
                    if key.value in seen:
                        found.append(f"line {key.lineno}: {key.value}")
                    seen.add(key.value)
    return found


def test_the_curated_table_repeats_no_string_key():
    assert duplicate_keys(DATA.read_text(encoding="utf-8")) == []


def test_the_check_sees_a_repeated_key():
    assert duplicate_keys('X = {"a.com": [1], "b.com": [2], "a.com": [3]}') == ["line 1: a.com"]
    assert duplicate_keys('X = {"a.com": {"a.com": 1}}') == []  # nested dicts are separate scopes
