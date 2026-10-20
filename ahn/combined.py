"""One-port REST, bilingual web, and streamable HTTP MCP service for hosting."""

from __future__ import annotations

import os
from contextlib import asynccontextmanager

from mcp.server.transport_security import TransportSecuritySettings

from . import background_reads
from .main import create_app
from .mcp_adapter import mcp


port = int(os.environ.get("PORT", "10000"))
os.environ.setdefault("AHN_API_BASE", f"http://127.0.0.1:{port}")
public_hosts = [host.strip() for host in os.environ.get("AHN_MCP_PUBLIC_HOSTS", "").split(",") if host.strip()]
render_host = os.environ.get("RENDER_EXTERNAL_HOSTNAME", "").strip()
if render_host:
    public_hosts.append(render_host)
allowed_hosts = [f"127.0.0.1:{port}", f"localhost:{port}", *public_hosts]
mcp_app = mcp.streamable_http_app(
    streamable_http_path="/mcp",
    stateless_http=True,
    json_response=True,
    transport_security=TransportSecuritySettings(
        allowed_hosts=allowed_hosts,
        allowed_origins=["https://chatgpt.com"],
    ),
)


@asynccontextmanager
async def lifespan(app):
    # The background reader fills the robots.txt/llms.txt and status caches that lookups answer from;
    # AHN_BACKGROUND_READS=0 leaves that to the review cron alone.
    started = background_reads.start_worker(app.state.settings)
    try:
        async with mcp_app.router.lifespan_context(mcp_app):
            yield
    finally:
        if started:
            background_reads.stop_worker()


app = create_app(lifespan=lifespan)
app.mount("/", mcp_app)
