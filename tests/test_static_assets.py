"""Large static files are redirected to a pinned CDN copy; small ones are cacheable. No database needed."""

import os
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("AHN_DATABASE_URL", "postgresql://invalid/unused")
os.environ.setdefault("AHN_KEY_PEPPER", "x" * 32)

from ahn.main import create_app
from ahn.settings import Settings
from ahn.static_assets import OFFLOAD_THRESHOLD_BYTES, OFFLOADED

STATIC_DIR = Path(__file__).resolve().parents[1] / "ahn" / "static"
VIDEO = "agent-help-network-review-demo-v3.webm"
PINNED_VIDEO_URL = re.compile(
    r"^https://cdn\.jsdelivr\.net/gh/agent-help-network/agent-help-network@[0-9a-f]{40}/ahn/static/"
    + re.escape(VIDEO) + "$"
)


def client(public_mode=True):
    return TestClient(create_app(Settings("postgresql://invalid/unused", "x" * 32, public_mode=public_mode)))


@pytest.mark.parametrize("public_mode", [True, False])
def test_review_demo_video_redirects_to_commit_pinned_cdn(public_mode):
    with client(public_mode) as c:
        for method in (c.get, c.head):
            response = method(f"/static/{VIDEO}", follow_redirects=False)
            assert response.status_code == 302
            assert PINNED_VIDEO_URL.match(response.headers["location"])
            assert response.headers["cache-control"] == "public, max-age=3600"
            # The redirect itself is tiny; the video bytes are never in this response.
            assert len(response.content) < 1024


def test_redirect_ignores_range_and_query_and_resolves_every_spelling():
    with client() as c:
        ranged = c.get(f"/static/{VIDEO}", headers={"Range": "bytes=0-99"}, follow_redirects=False)
        assert ranged.status_code == 302
        assert c.get(f"/static/{VIDEO}?x=1", follow_redirects=False).status_code == 302
        assert c.get(f"/static/./{VIDEO}", follow_redirects=False).status_code in (302, 404)
        assert c.get(f"/static/{VIDEO.upper()}", follow_redirects=False).status_code in (302, 404)
        assert c.get(f"/static/{VIDEO}/", follow_redirects=False).status_code in (302, 404)


def test_every_offloaded_url_is_pinned_to_a_commit_never_a_branch():
    assert VIDEO in OFFLOADED
    for name, url in OFFLOADED.items():
        assert re.search(r"@[0-9a-f]{40}/ahn/static/" + re.escape(name) + "$", url), url
        assert (STATIC_DIR / name).is_file(), "the file stays in the repository; jsDelivr reads it from there"


def test_no_large_file_is_served_directly():
    large = [p.name for p in STATIC_DIR.iterdir() if p.is_file() and p.stat().st_size > OFFLOAD_THRESHOLD_BYTES]
    assert large, "expected the demo video to exceed the offload threshold"
    assert set(large) <= set(OFFLOADED), f"large static files must be listed in OFFLOADED: {sorted(set(large) - set(OFFLOADED))}"
    with client() as c:
        for name in large:
            assert c.get(f"/static/{name}", follow_redirects=False).status_code == 302


@pytest.mark.parametrize("name", ["site.css", "prompt.js", "report.js"])
def test_small_static_files_are_cacheable_and_revalidate(name):
    with client() as c:
        response = c.get(f"/static/{name}")
        assert response.status_code == 200
        assert response.headers["cache-control"] == "public, max-age=86400"
        assert response.content == (STATIC_DIR / name).read_bytes()
        etag = response.headers["etag"]
        again = c.get(f"/static/{name}", headers={"If-None-Match": etag})
        assert again.status_code == 304
        assert again.headers["cache-control"] == "public, max-age=86400"


def test_missing_static_file_is_not_cached():
    with client() as c:
        response = c.get("/static/nope.css")
        assert response.status_code == 404
        assert "max-age=86400" not in response.headers.get("cache-control", "")


def test_existing_pages_are_unchanged():
    with client() as c:
        home = c.get("/en/")
        assert home.status_code == 200
        assert "/static/site.css?v=" in home.text
        assert "cache-control" not in home.headers
        assert "read-only preview" in c.get("/en/connect").text.lower()
        robots = c.get("/robots.txt")
        assert robots.status_code == 200 and "Disallow: /owner" in robots.text
        assert c.get("/openapi.json").status_code == 200


def test_manifest_url_stays_on_the_service_domain():
    manifest = (Path(__file__).resolve().parents[1] / "plugins" / "agent-help-network" / "plugin.json").read_text(encoding="utf-8")
    assert f"https://agenthelpnetwork.com/static/{VIDEO}" in manifest


def test_redirects_share_the_request_limiter_and_stay_small():
    # The video route used to cost 4 MB per allowed request; now each allowed request is a small redirect,
    # and the shared per-client cap still applies to /static like to every other path.
    with client() as c:
        responses = [c.get(f"/static/{VIDEO}", follow_redirects=False) for _ in range(241)]
        assert {r.status_code for r in responses} <= {302, 429}
        limited = next(r for r in responses if r.status_code == 429)
        assert limited.headers["retry-after"] == "60"
        assert all(len(r.content) < 1024 for r in responses)


def test_phone_nav_is_a_grid_not_a_space_between_wrap():
    """Below 520px the nav wraps into equal columns so a second row lines up with the first."""
    css = (STATIC_DIR / "site.css").read_text(encoding="utf-8")
    blocks = re.findall(r"@media\(max-width:520px\)\{(.*?)\}\}", css + "}")
    nav = "".join(re.findall(r"\.site-header nav\{([^}]*)\}", "".join(b + "}" for b in blocks)))
    assert "display:grid" in nav and "grid-template-columns:repeat(3,minmax(0,1fr))" in nav
    assert "justify-content:normal" in nav and "flex-wrap" not in nav
