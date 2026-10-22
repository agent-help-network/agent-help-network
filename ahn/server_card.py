"""MCP Server Card served at /.well-known/mcp/server-card.json.

The card is derived from the repository's server.json (the MCP Registry manifest), so identity, description,
version and the remote URL have one canonical source. Only fields the Server Card schema defines are copied.
Primitives (tools, resources, prompts) and capabilities are deliberately absent: the schema excludes them and
clients list them at runtime.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

SERVER_JSON = Path(__file__).resolve().parent.parent / "server.json"
CARD_SCHEMA = "https://static.modelcontextprotocol.io/schemas/v1/server-card.schema.json"
CARD_FIELDS = ("name", "version", "description", "title", "websiteUrl", "repository", "remotes")
CACHE_CONTROL = "public, max-age=3600"
CORS_HEADERS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "GET",
    "Access-Control-Allow-Headers": "Content-Type, If-None-Match",
    "Access-Control-Expose-Headers": "ETag",
}


def build_card(manifest: dict) -> dict:
    card = {"$schema": CARD_SCHEMA}
    card.update({key: manifest[key] for key in CARD_FIELDS if key in manifest})
    return card


def load_card() -> tuple[bytes, str]:
    """The card as compact JSON bytes and its strong ETag."""
    manifest = json.loads(SERVER_JSON.read_text(encoding="utf-8"))
    body = (json.dumps(build_card(manifest), indent=2, ensure_ascii=False) + "\n").encode("utf-8")
    return body, '"' + hashlib.sha256(body).hexdigest()[:32] + '"'
