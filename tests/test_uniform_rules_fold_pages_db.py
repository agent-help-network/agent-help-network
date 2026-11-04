"""D94 over the HTML search page and the public incident detail: both read robots.rules token by token, so the fold (which only
the REST/MCP lookup route applies) must not change what they show. Compared with the fold switched off."""

from __future__ import annotations

import os
import re
import sys
from dataclasses import replace
from pathlib import Path

import pytest

if not os.environ.get("AHN_TEST_DATABASE_URL"):
    pytest.skip("AHN_TEST_DATABASE_URL is required", allow_module_level=True)

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_uniform_rules_fold_db import HOST, ROBOTS_FILES, prepare, serve  # noqa: E402

from fastapi.testclient import TestClient  # noqa: E402

from ahn import service  # noqa: E402
from ahn.main import create_app  # noqa: E402


def text(html):
    html = re.sub(r"<script.*?</script>", "", html, flags=re.S)
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html))


@pytest.mark.parametrize("name", ["star_only_open", "star_blocks_root", "star_disallow_paths"])
@pytest.mark.parametrize("params", [{}, {"error_class": "service_error"}])
def test_search_page_is_the_same_with_the_fold_on_and_off(monkeypatch, name, params):
    settings = prepare(monkeypatch, guide=True)
    serve(monkeypatch, settings, ROBOTS_FILES[name])
    pages = {}
    for label, current in (("on", settings), ("off", replace(settings, robots_rules_fold=False))):
        with TestClient(create_app(current)) as client:
            response = client.get("/en/search", params={"site_host": HOST, **params})
            assert response.status_code == 200
            pages[label] = text(response.text)
    assert "no parseable rules" not in pages["on"] and "Declared policy unknown" not in pages["on"]
    assert "Agent token" in pages["on"]
    assert pages["on"] == pages["off"] or re.sub(r"\d{4}-\d\d-\d\dT[\d:.]+Z?", "", pages["on"]) == re.sub(r"\d{4}-\d\d-\d\dT[\d:.]+Z?", "", pages["off"])


def test_the_lookup_route_folds_but_search_and_detail_callers_do_not(monkeypatch):
    settings = prepare(monkeypatch, guide=True)
    serve(monkeypatch, settings, ROBOTS_FILES["star_only_open"])
    folded = service.check(settings, HOST, "service_error")
    kept = service.check(settings, HOST, "service_error", fold_rules=False)
    assert "rules" not in folded["access_profile"]["robots"] and folded["access_profile"]["robots"]["rules_uniform"]
    assert len(kept["access_profile"]["robots"]["rules"]) > 1 and "rules_uniform" not in kept["access_profile"]["robots"]
