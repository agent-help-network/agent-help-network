"""MCP tool ``find_task_route`` (D117). Registered by ahn.mcp_adapter only while AHN_TASK_ROUTES=1.

Read-only: it reads the curated table in code through ahn.task_routes.lookup (no database, no request to any site). The first
text block is the answer's summary_line; the second is the answer as JSON, the same text the SDK builds for a model result,
and the only place a URL appears. This module does not import the adapter (the adapter imports it).
"""

from __future__ import annotations

from typing import Annotated, Any

import pydantic_core
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import CallToolResult, TextContent, ToolAnnotations
from pydantic import Field, WithJsonSchema

from .task_routes import TaskKind, TaskRouteInputError, TaskRoutesResponse, lookup, parse_query

SITE_HOST_DESCRIPTION = ("The public hostname of the site whose task routes you want, lowercase, for example example.com. "
                         "Only the host: no scheme, URL path, query, port or credentials.")
TASK_KIND_DESCRIPTION = ("Optional: one kind of customer task to look up, from the list. Leave it out to get the records for every "
                         "task kind of the site.")
DESCRIPTION = ("Look up recorded task routes for a public hostname: for a kind of customer task (a refund, a cancellation, a booking "
               "and so on), the common obstacles and the routes the site's own documents name. Call it when you start a task of "
               "several steps on a site and do not know which document, channel or entry point it needs. The answer is a list of "
               "records, not a guarantee and not advice; an empty list does not mean there is no route. Sends only the hostname "
               "and an optional task kind, and changes nothing. Read summary_line first.")


def _task_kind_schema() -> dict:
    return {"type": "string", "enum": [item.value for item in TaskKind], "description": TASK_KIND_DESCRIPTION}


def task_route_result(answer: TaskRoutesResponse) -> CallToolResult:
    """Two text blocks (summary_line, then the JSON answer) and structuredContent equal to the REST answer."""
    content = [TextContent(type="text", text=answer.summary_line),
               TextContent(type="text", text=pydantic_core.to_json(answer, indent=2).decode())]
    return CallToolResult(content=content, structured_content=answer.model_dump(mode="json"))


def register(mcp) -> None:
    @mcp.tool(
        description=DESCRIPTION,
        annotations=ToolAnnotations(title="Find task routes", read_only_hint=True, destructive_hint=False, idempotent_hint=True,
                                    open_world_hint=False),
        structured_output=True,
    )
    async def find_task_route(site_host: Annotated[str, Field(description=SITE_HOST_DESCRIPTION)],
                              task_kind: Annotated[Any, WithJsonSchema(_task_kind_schema())] = None) -> Annotated[CallToolResult, TaskRoutesResponse]:
        try:  # the same reading and the same error codes as GET /v1/task-routes
            host, kind = parse_query(site_host, task_kind)
        except TaskRouteInputError as error:
            raise ToolError(error.code) from None
        return task_route_result(lookup(host, kind))
