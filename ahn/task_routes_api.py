"""Task routes read API (D117): ``GET /v1/task-routes``.

Off by default. With ``AHN_TASK_ROUTES`` unset the path is never registered (404) and nothing here runs. The answer comes from
ahn.task_routes.lookup: the curated table in code, no database and no request to any site (D33). It is a separate layer from
the lookup verdict: nothing here is read by check(), and the verdict does not change when this is on.

Query: ``site_host`` (a public hostname, required) and ``task_kind`` (one value of ahn.task_routes.TaskKind, optional).
Errors are codes only and never echo the input: invalid_site_host (400), invalid_task_kind (400).
"""

from __future__ import annotations

from fastapi import Request
from fastapi.responses import JSONResponse

from .models import normalize_host_input, valid_host
from .task_routes import TaskKind, lookup

PATH = "/v1/task-routes"
CACHE_CONTROL = "public, max-age=300"


def add_routes(app, settings, render=None, lang_text=None) -> None:
    """Register GET /v1/task-routes. Called only while AHN_TASK_ROUTES=1; otherwise the path does not exist (404).

    ``render`` and ``lang_text`` are the page helpers of the app; this step registers no page and ignores them."""
    from .service import ServiceError

    headers = {"Cache-Control": CACHE_CONTROL, "X-Content-Type-Options": "nosniff"}

    @app.get(PATH, include_in_schema=False)
    def task_routes(request: Request, site_host: str = "", task_kind: str | None = None):
        host = normalize_host_input(site_host)
        try:
            valid_host(host)
        except ValueError:
            raise ServiceError("invalid_site_host", 400)
        kind = None
        if task_kind is not None:
            if task_kind not in TaskKind._value2member_map_:
                raise ServiceError("invalid_task_kind", 400)
            kind = TaskKind(task_kind)
        answer = lookup(host, kind)
        return JSONResponse(answer.model_dump(mode="json"), headers=headers)
