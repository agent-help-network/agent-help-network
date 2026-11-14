"""Curated task routes (D117).

For a site and a kind of customer task, an obstacle and a route that the site's OWN documents name (a page that says a
cancellation is taken by phone only, or that a claim needs a particular form). Kept apart from ahn/official_paths.py: nothing
here feeds official_paths(), next_action, customer_url or the verdict.

Keys are apex hosts (ahn.task_routes.route_host); values are lists of _route(...) records. Every value is a closed value of
ahn.task_routes (no quoted text), every official_url follows ahn.task_routes.official_url_problem, and source_url is the
site's own page that states it, read on verified_at with the curation user agent after robots.txt allowed it. An entry is
named only when the page states it outright; a hint is left out.

Checks: python scripts/verify_official_paths.py --task-routes-only. Procedure: scripts/README.md.
"""

from __future__ import annotations

from datetime import date

from .task_routes import period_for

# An entry older than this is left out of answers until it is verified again (same age as consumer paths).
TASK_ROUTE_MAX_AGE_DAYS = 90


def _route(task_kind: str, *, obstacle: str, source_url: str, verified_at: str, routes: tuple[str, ...] | list[str] = (),
           worked: str | None = None, official_url: str | None = None, verification_kind: str | None = None,
           site_requires: str | None = None, document_kind: str | None = None, entry_point_kind: str | None = None) -> dict:
    """A curated record: basis official_doc, outcome not_applicable, source curated_official, period from verified_at."""
    item = {"task_kind": task_kind, "obstacle": obstacle, "routes_tried": list(routes), "basis": "official_doc",
            "outcome": "not_applicable", "source_kind": "curated_official",
            "period": period_for(date.fromisoformat(verified_at)), "source_url": source_url, "verified_at": verified_at}
    optional = {"route_worked": worked, "official_url": official_url, "verification_kind": verification_kind,
                "site_requires": site_requires, "document_kind": document_kind, "entry_point_kind": entry_point_kind}
    item.update({key: value for key, value in optional.items() if value is not None})
    return item


TASK_ROUTES_CURATED: dict[str, list[dict]] = {}
