"""HEAD on the site's pages answers like GET (same status and headers) with no body. No database, no network."""

import os

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("AHN_DATABASE_URL", "postgresql://invalid/unused")
os.environ.setdefault("AHN_KEY_PEPPER", "x" * 32)

import ahn.main as main
from ahn.main import create_app
from ahn.settings import Settings

PAGES = ["/en/", "/ko/", "/en/policy", "/en/connect", "/en/agents", "/en/terms", "/en/support", "/robots.txt", "/llms.txt",
         "/agent-instructions.txt", "/sitemap.xml", "/static/favicon.svg", "/openapi.json"]
IDENTITY = {"Accept-Encoding": "identity"}
SAME_HEADERS = ("content-type", "content-length", "cache-control", "etag", "location", "vary", "content-encoding")


def client(**kwargs):
    return TestClient(create_app(Settings("postgresql://invalid/unused", "x" * 32, public_mode=True, **kwargs)), follow_redirects=False)


@pytest.mark.parametrize("path", PAGES)
def test_head_has_the_status_and_headers_of_get_and_no_body(path):
    with client() as c:
        got, head = c.get(path, headers=IDENTITY), c.head(path, headers=IDENTITY)
    assert got.status_code == 200, path
    assert head.status_code == got.status_code and head.content == b""
    for name in SAME_HEADERS:
        assert head.headers.get(name) == got.headers.get(name), (path, name)
    assert head.headers["content-length"] == str(len(got.content))


@pytest.mark.parametrize("path,status", [("/xx/", 404), ("/xx/policy", 404), ("/no-such-page", 307),
                                          ("/favicon.ico", 301)])
def test_head_error_and_redirect_statuses_follow_get(path, status):
    with client() as c:
        got, head = c.get(path), c.head(path)
    assert head.status_code == got.status_code and head.content == b""
    if status:
        assert got.status_code == status
    assert head.headers.get("location") == got.headers.get("location")


def test_head_with_gzip_accepted_has_the_status_and_type_of_get_and_no_body():
    with client() as c:
        got = c.get("/en/policy", headers={"Accept-Encoding": "gzip"})
        head = c.head("/en/policy", headers={"Accept-Encoding": "gzip"})
    assert head.status_code == 200 and head.content == b"" and head.headers.get("content-type") == got.headers.get("content-type")


@pytest.mark.parametrize("path", ["/v1/incidents/check?site_host=example.com", "/v1/incidents/00000000-0000-0000-0000-000000000000",
                                  "/en/search?site_host=example.com", "/en/incidents/00000000-0000-0000-0000-000000000000"])
def test_head_never_runs_a_lookup(monkeypatch, path):
    monkeypatch.setattr(main, "check", lambda *a, **k: pytest.fail("a HEAD request must not run a lookup"))
    monkeypatch.setattr(main, "public_incident", lambda *a, **k: pytest.fail("a HEAD request must not run a lookup"))
    with client() as c:
        assert c.head(path).status_code >= 400


@pytest.mark.parametrize("path", ["/owner", "/owner/board.json", "/owner/hosts"])
def test_head_never_opens_an_owner_page(path):
    with client() as c:
        assert c.head(path).status_code == 405


def test_other_methods_are_unchanged():
    with client() as c:
        assert c.post("/en/policy").status_code == 405
        assert c.delete("/robots.txt").status_code == 405


def test_head_is_not_counted_as_a_visit_but_get_still_is(monkeypatch):
    opened = []

    class Stop(Exception):
        pass

    def fake_connect(settings):
        opened.append(1)
        raise Stop  # the metric write swallows any error

    monkeypatch.setattr(main, "connect", fake_connect)
    with client(collect_request_metrics=True) as c:
        c.head("/en/policy")
        assert opened == []
        c.get("/en/policy")
        assert opened == [1]


def test_head_on_health_and_root_follow_get():
    with client() as c:
        for path in ("/", "/health"):
            got, head = c.get(path), c.head(path)
            assert head.status_code == got.status_code and head.content == b"", path
            assert head.headers.get("location") == got.headers.get("location")


def test_head_on_incident_list_runs_the_same_handler_as_get_without_a_lookup(monkeypatch):
    calls = []
    monkeypatch.setattr(main, "public_incidents", lambda *a, **k: calls.append(a) or [])
    monkeypatch.setattr(main, "check", lambda *a, **k: pytest.fail("a HEAD request must not run a lookup"))
    with client() as c:
        got, head = c.get("/v1/incidents"), c.head("/v1/incidents")
    assert got.status_code == head.status_code == 200 and head.content == b""
    assert head.headers["content-length"] == str(len(got.content)) and len(calls) == 2


def test_head_is_not_in_the_openapi_schema():
    with client() as c:
        paths = c.get("/openapi.json").json()["paths"]
    assert all("head" not in ops for ops in paths.values())


HEAD_PATHS = {
    "/", "/.well-known/agentboard-verify/{handle}", "/.well-known/mcp-registry-auth", "/.well-known/mcp/server-card.json",
    "/.well-known/openai-apps-challenge", "/agent-instructions.txt", "/favicon.ico", "/health", "/llms.txt", "/robots.txt", "/sitemap.xml",
    "/v1/incidents", "/v1/platforms", "/{lang}/", "/{lang}/agents", "/{lang}/connect", "/{lang}/contribute", "/{lang}/disclaimer", "/{lang}/human",
    "/{lang}/platforms", "/{lang}/policy", "/{lang}/report", "/{lang}/support", "/{lang}/terms", "/{lang}/why",
}


def test_the_set_of_routes_that_answer_head_is_pinned():
    # A new GET route gets a HEAD copy automatically; this test makes adding one a deliberate choice
    # (add it here, or add it to HEAD_EXCLUDED_PATHS in ahn/main.py if running it has a side effect).
    with client() as c:
        found = {r.path for r in c.app.routes if getattr(r, "methods", None) == {"HEAD"}}
    assert found == HEAD_PATHS


def test_head_is_not_swallowed_by_the_root_mcp_mount_of_the_combined_app():
    from starlette.applications import Starlette

    app = create_app(Settings("postgresql://invalid/unused", "x" * 32, public_mode=True))
    app.mount("/", Starlette())  # as ahn/combined.py does for the MCP app: anything the site routes do not answer falls here and is a 404
    with TestClient(app, follow_redirects=False) as c:
        for path in ("/health", "/", "/v1/incidents"):
            status = c.head(path).status_code
            assert status != 404 and status != 405, path
