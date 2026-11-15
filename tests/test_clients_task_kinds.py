"""The task kinds the langchain-ahn tool offers are the service's. The package does not import ahn, so the list is read
from its source text here; the main repository's test run does not need langchain installed."""

from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "clients" / "python" / "src"))

from ahn.task_routes import TaskKind  # noqa: E402
from ahn_client import _task_routes  # noqa: E402
TOOLS = ROOT / "clients" / "langchain-ahn" / "src" / "langchain_ahn" / "tools.py"


def listed_kinds() -> tuple:
    tree = ast.parse(TOOLS.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(getattr(t, "id", None) == "TASK_KINDS" for t in node.targets):
            return ast.literal_eval(node.value)
    raise AssertionError("TASK_KINDS not found in langchain_ahn/tools.py")


def test_langchain_task_kinds_equal_the_server_enum():
    kinds = listed_kinds()
    assert len(kinds) == len(set(kinds)) == 24
    assert set(kinds) == {kind.value for kind in TaskKind}


def test_ahn_client_task_kinds_equal_the_server_enum_and_the_langchain_list():
    client = _task_routes.TASK_KINDS
    assert len(client) == len(set(client)) == 24
    assert set(client) == {kind.value for kind in TaskKind} == set(listed_kinds())


def test_langchain_package_does_not_import_ahn():
    tree = ast.parse(TOOLS.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and not node.level:
            names = [node.module or ""]
        else:
            continue
        assert not any(n == "ahn" or n.startswith("ahn.") for n in names)
