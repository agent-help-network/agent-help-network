"""The terms page keeps the independence notice, the recommendation limit and the governing law."""

from __future__ import annotations

import re

from fastapi.testclient import TestClient

from ahn.settings import Settings
from ahn.main import create_app


def _page(lang: str, name: str) -> str:
    settings = Settings("postgresql://invalid/unused", "x" * 32, public_mode=True)
    response = TestClient(create_app(settings)).get(f"/{lang}/{name}")  # no `with`: lifespan (database) never runs
    assert response.status_code == 200
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", response.text))


def test_terms_state_independence_in_both_languages():
    assert "independent project" in _page("en", "terms")
    assert "독립 프로젝트" in _page("ko", "terms")


def test_terms_limit_the_recommendation_field_in_both_languages():
    en, ko = _page("en", "terms"), _page("ko", "terms")
    assert "recommendation field is an automated summary" in en and "not legal advice" in en
    assert "법률 자문이 아니고" in ko


def test_terms_governing_law_is_florida_in_both_languages():
    en, ko = _page("en", "terms"), _page("ko", "terms")
    assert "laws of the State of Florida" in en
    assert "플로리다주" in ko


def test_policy_has_site_owner_exclusion_in_both_languages():
    assert "control a public hostname" in _page("en", "policy")
    assert "공개 호스트명을 관리하는 소유자" in _page("ko", "policy")
