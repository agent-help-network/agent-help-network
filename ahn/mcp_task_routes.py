"""MCP tools ``find_task_route`` (D117) and ``report_task_route`` (D118). find_task_route is registered by ahn.mcp_adapter only
while AHN_TASK_ROUTES=1; report_task_route only while AHN_TASK_ROUTE_REPORTS=1 as well, with public mode and public reporting on.

find_task_route is read-only: it reads the curated table in code through ahn.task_routes.lookup and, while the write switch is on,
the published stored records (no request to any site). The first
text block is the answer's summary_line; the second is the answer as JSON, the same text the SDK builds for a model result,
and the only place a URL appears. This module does not import the adapter (the adapter imports it).
"""

from __future__ import annotations

from typing import Annotated, Any

import pydantic_core
from mcp.server.mcpserver import Context
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import CallToolResult, TextContent, ToolAnnotations
from pydantic import BaseModel, Field, WithJsonSchema

from .task_routes import TaskKind, TaskRouteInputError, TaskRoutesResponse, lookup, parse_query

# Appended to the server instructions by ahn.mcp_adapter only while the read switch is on (AHN_TASK_ROUTES); with it off the
# instructions are the text they always were.
INSTRUCTIONS_ADDENDUM = ("When you start a customer task of several steps on a site (for example a refund, a cancellation or a document submission) "
                         "and do not know its official document, channel or entry point, call find_task_route once for that task with the public hostname "
                         "(task_kind is optional). Its answer is a list of records, not a guarantee and not advice; an empty list does not mean there is "
                         "no route. The answer does not change when to call check_incident or what check_incident recommends.")
SITE_HOST_DESCRIPTION = ("The public hostname of the site whose task routes you want, lowercase, for example example.com. "
                         "Only the host: no scheme, URL path, query, port or credentials.")
TASK_KIND_DESCRIPTION = ("Optional: one kind of customer task to look up, from the list. Leave it out to get the records for every "
                         "task kind of the site.")
DESCRIPTION = ("Look up recorded task routes for a public hostname: for a kind of customer task (a refund, a cancellation, a booking "
               "and so on), an obstacle and the routes the site's own documents name. Call it when you start a task of "
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


def _settings():
    """The process settings while the write switch is on (the lookup then adds the published stored records, D118), else None.
    A deployment whose settings cannot be read answers from the curated table alone, as before."""
    from .settings import Settings, task_route_reports_env_enabled

    if not task_route_reports_env_enabled():
        return None
    try:
        return Settings.from_env()
    except Exception:
        return None


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
        return task_route_result(lookup(host, kind, settings=_settings()))


# --- report_task_route (D118) ----------------------------------------------------------------------------------------------
REPORT_DESCRIPTION = ("Report one task route you went through on a public site: the kind of task, the obstacle, the routes you tried "
                      "and the one that worked, and how it ended. Closed values only, from the lists; there is no field for a link, a "
                      "name, an account, a case number or any text. Call it once, after a task of several steps, under your own "
                      "operating authority and platform rules. Reports start private and are reviewed before any is shown. Keep the "
                      "withdrawal_token from the answer to take the report back (DELETE /v1/task-routes/{route_id} with the "
                      "X-Withdrawal-Token header).")
REPORT_SITE_HOST_DESCRIPTION = ("The public hostname of the site the task was on, for example example.com. Only the host: no URL path, "
                                "query, port or credentials (a pasted https:// scheme, capital letters or a trailing dot are read as "
                                "the host). The report is kept under the site's root domain; an account or tenant subdomain, or a "
                                "public suffix such as co.uk or github.io, is refused.")
REPORT_FIELD_DESCRIPTION = ("The task route: site_host, task_kind, obstacle, basis and outcome are required; the finer fields are each "
                            "allowed only under the obstacle they explain (verification_kind under verification_step, site_requires "
                            "under site_requires_info_from_person, document_kind under the three document obstacles, "
                            "entry_point_kind under wrong_entry_point). route_worked is one of routes_tried or none. idempotency_key "
                            "(a UUID) makes a retry safe.")


def _enum_schema(enum, *, drop=()) -> dict:
    return {"type": "string", "enum": [item.value for item in enum if item.value not in drop]}


def _report_schema() -> dict:
    """The input schema of report_task_route: TaskRouteIn written out without references, without the curated-only values."""
    from .task_routes import (DocumentKind, EntryPointKind, RouteBasis, RouteDuration, RouteKind, RouteOutcome, SiteRequires,
                              TASK_ROUTE_MAX_TRIED, TaskObstacle, VerificationKind)

    properties = {
        "site_host": {"type": "string", "description": REPORT_SITE_HOST_DESCRIPTION},
        "task_kind": _enum_schema(TaskKind), "obstacle": _enum_schema(TaskObstacle),
        "verification_kind": _enum_schema(VerificationKind), "site_requires": _enum_schema(SiteRequires),
        "document_kind": _enum_schema(DocumentKind), "entry_point_kind": _enum_schema(EntryPointKind),
        "routes_tried": {"type": "array", "items": _enum_schema(RouteKind), "maxItems": TASK_ROUTE_MAX_TRIED, "uniqueItems": True},
        "route_worked": {"type": "string", "enum": [item.value for item in RouteKind] + ["none"]},
        "basis": _enum_schema(RouteBasis, drop=("official_doc",)), "outcome": _enum_schema(RouteOutcome, drop=("not_applicable",)),
        "duration": _enum_schema(RouteDuration), "idempotency_key": {"type": "string", "format": "uuid"},
    }
    return {"type": "object", "properties": properties, "required": ["site_host", "task_kind", "obstacle", "basis", "outcome"],
            "additionalProperties": False, "description": REPORT_FIELD_DESCRIPTION}


class TaskRouteReceipt(BaseModel):
    route_id: str
    review_state: str
    withdrawal_token: str


def register_reports(mcp, call_api, outer_headers=None) -> None:
    """Register report_task_route. The adapter calls this only while the write switch, public mode and public reporting are on.
    The report is checked here with the same model as POST /v1/task-routes and sent to it; a refusal is its error code alone.
    ``outer_headers`` (the adapter's outer_request_headers) passes the caller's address on, so the per-client report limit of
    POST /v1/task-routes applies to a report sent over MCP as well."""
    from pydantic import ValidationError

    from .task_routes import TaskRouteIn

    @mcp.tool(
        description=REPORT_DESCRIPTION,
        annotations=ToolAnnotations(title="Report a task route", read_only_hint=False, destructive_hint=False, idempotent_hint=False,
                                    open_world_hint=True),
        structured_output=True,
    )
    async def report_task_route(report: Annotated[Any, WithJsonSchema(_report_schema())],
                                ctx: Context | None = None) -> TaskRouteReceipt:
        if not isinstance(report, dict):
            raise ToolError("invalid_task_route")
        try:  # the same model as the REST path; the error never repeats what was sent
            data = TaskRouteIn.model_validate(report)
        except ValidationError:
            raise ToolError("invalid_task_route") from None
        payload = data.model_dump(mode="json", exclude_none=True)
        extra = outer_headers(ctx) if outer_headers is not None else None
        return TaskRouteReceipt(**await call_api("POST", "/v1/task-routes", payload=payload, write=True, extra_headers=extra))
