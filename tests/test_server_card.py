"""MCP Server Card at /.well-known/mcp/server-card.json: static, derived from server.json, no stored data."""

import json
import os
import re
from pathlib import Path

from fastapi.testclient import TestClient

os.environ.setdefault("AHN_DATABASE_URL", "postgresql://invalid/unused")
os.environ.setdefault("AHN_KEY_PEPPER", "x" * 32)

from ahn import server_card
from ahn.main import create_app
from ahn.settings import Settings

PATH = "/.well-known/mcp/server-card.json"
MANIFEST = json.loads((Path(__file__).resolve().parents[1] / "server.json").read_text(encoding="utf-8"))
# Top-level members of the Server Card schema (experimental-ext-server-card schema.ts).
SCHEMA_MEMBERS = {"$schema", "name", "version", "description", "title", "websiteUrl", "repository", "icons", "remotes", "_meta"}


def _client(public=True):
    return TestClient(create_app(Settings("postgresql://invalid/unused", "x" * 32, public_mode=public)))


def test_card_is_served_as_cacheable_static_json():
    response = _client().get(PATH)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    assert response.headers["cache-control"] == "public, max-age=3600"
    assert response.headers["access-control-allow-origin"] == "*"
    assert response.json() == json.loads(response.content)


def test_card_matches_server_json_so_there_is_one_source():
    card = _client().get(PATH).json()
    for key in ("name", "version", "description", "title", "websiteUrl", "repository", "remotes"):
        assert card[key] == MANIFEST[key], key
    assert card["remotes"] == [{"type": "streamable-http", "url": "https://agenthelpnetwork.com/mcp"}]
    assert card["name"] == "com.agenthelpnetwork/agent-help-network"


def test_card_stays_inside_the_server_card_schema():
    card = _client().get(PATH).json()
    assert set(card) <= SCHEMA_MEMBERS
    assert card["$schema"] == "https://static.modelcontextprotocol.io/schemas/v1/server-card.schema.json"
    assert re.fullmatch(r"[a-zA-Z0-9.-]+/[a-zA-Z0-9._-]+", card["name"])
    assert 1 <= len(card["description"]) <= 100
    assert len(card["version"]) <= 255
    # The schema excludes primitives and capabilities; tools are listed at runtime, never in the card.
    for forbidden in ("tools", "capabilities", "resources", "prompts", "serverInfo", "transport", "protocolVersion"):
        assert forbidden not in card


def test_card_carries_no_secret_or_private_value():
    text = _client().get(PATH).text
    for marker in ("localhost", "127.0.0.1", "pepper", "password", "token", "postgres"):
        assert marker not in text.lower()


def test_card_supports_conditional_requests():
    with _client() as client:
        first = client.get(PATH)
        etag = first.headers["etag"]
        again = client.get(PATH, headers={"If-None-Match": etag})
        assert again.status_code == 304 and again.content == b""
        assert again.headers["etag"] == etag
        assert client.get(PATH, headers={"If-None-Match": '"other"'}).status_code == 200


def test_card_is_not_served_by_the_local_build():
    assert _client(public=False).get(PATH).status_code == 404


def test_card_builder_copies_only_known_fields():
    card = server_card.build_card({**MANIFEST, "packages": [{"x": 1}], "unknown": 1})
    assert set(card) <= SCHEMA_MEMBERS
