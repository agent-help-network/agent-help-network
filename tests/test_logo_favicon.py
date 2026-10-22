"""Logo and favicon: hand-written SVG served from /static, linked from every page, no external assets."""

import os
import re

from fastapi.testclient import TestClient

os.environ.setdefault("AHN_DATABASE_URL", "postgresql://invalid/unused")
os.environ.setdefault("AHN_KEY_PEPPER", "x" * 32)

from ahn.main import create_app
from ahn.settings import Settings


def _client(public=True):
    return TestClient(create_app(Settings("postgresql://invalid/unused", "x" * 32, public_mode=public)))


def test_logo_and_favicon_are_served_as_svg():
    for public in (True, False):
        client = _client(public)
        for name in ("logo.svg", "favicon.svg"):
            response = client.get(f"/static/{name}")
            assert response.status_code == 200, name
            assert response.headers["content-type"].startswith("image/svg+xml"), name
            assert "viewBox=\"0 0 64 64\"" in response.text
            assert not re.search(r"https?://(?!www\.w3\.org/2000/svg)|<script|<image|<text|font", response.text), name


def test_favicon_ico_redirects_to_the_svg():
    for public in (True, False):
        response = _client(public).get("/favicon.ico", follow_redirects=False)
        assert response.status_code == 301
        assert response.headers["location"] == "/static/favicon.svg"


def test_pages_link_the_favicon_and_show_the_logo_with_alt_text():
    client = _client()
    for path in ("/en/", "/ko/", "/en/human", "/en/policy"):
        html = client.get(path).text
        assert '<link rel="icon" type="image/svg+xml" href="/static/favicon.svg">' in html, path
        assert re.search(r'<img class="brand-logo" src="/static/logo.svg"[^>]*alt="Agent Help Network"', html), path
