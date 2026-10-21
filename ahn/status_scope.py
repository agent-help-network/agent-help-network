"""How far a provider's active status reaches (docs/decisions.md D43).

A Statuspage ``status.json`` indicator is one value for the whole page. Cloudflare's page reads ``major``
for months while every product component is operational and only data-center groups are down; RubyGems
reads ``maintenance`` with no maintenance scheduled because two components were left "under
maintenance" years ago. So when a refresh of an Atlassian Statuspage feed reads an active indicator
(minor or above, or maintenance) we read two more small public lists from the same page; a page that
reads none costs its one status.json request only:

  /api/v2/incidents/unresolved.json            the incidents the provider has not resolved
  /api/v2/scheduled-maintenances/active.json   the maintenances in progress (or being verified)

Each read has the same 32 KiB cap as the feed; a read that fails or is too large only leaves this
information out (the indicator is kept as read). Only counts, an impact enum, the latest incident
update time, at most MAX_AFFECTED names the provider published and one boolean are kept.

Other platforms (incident.io answers ``status.json`` too, but has no such lists) are not asked:
``statuspage_page_id`` tells the two apart from the ``status.json`` body already read.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone

SCOPE_CAP = 32_768  # bytes per list, the same cap as the status.json feed
INCIDENTS_PATH = "incidents/unresolved.json"
MAINTENANCES_PATH = "scheduled-maintenances/active.json"
SCOPE_PATHS = (INCIDENTS_PATH, MAINTENANCES_PATH)
INCIDENT_IMPACTS = ("none", "minor", "major", "critical")
_RANK = {impact: rank for rank, impact in enumerate(INCIDENT_IMPACTS)}
ACTIVE_INDICATORS = frozenset({"minor", "major", "critical", "maintenance"})
BASES = ("incident", "maintenance", "components_only")
MAX_AFFECTED = 3
AFFECTED_CHARS = 80
_RESOLVED = frozenset({"resolved", "postmortem", "completed"})
_MAINTENANCE_ACTIVE = frozenset({"in_progress", "verifying"})
# Atlassian Statuspage page ids are short lowercase base-36 strings (yh6f0r4529hb); incident.io's
# Statuspage-compatible status.json carries a 26-character upper-case ULID and no time_zone.
_PAGE_ID = re.compile(r"[a-z0-9]{8,32}")
_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f  ]+")
_SMALLINT = 32_767

# Statuspage component groups that are regions or points of presence (PoPs), by page id and group id.
# Read on 2026-10-21 from each page's own /api/v2/components.json; the group names are the provider's.
# A group missing here never marks a scope as partial. Product groups are left out on purpose.
REGIONAL_GROUPS: dict[str, dict[str, str]] = {
    # Cloudflare Status: one group per continent, each listing data centers (e.g. "Paris, France - (CDG)").
    "yh6f0r4529hb": {
        "00gpj4s37mz4": "Africa",
        "77867vxkttgw": "Asia",
        "zqxhg7y54vy8": "Europe",
        "91blz4ztt7dm": "Latin America & the Caribbean",
        "m3639x4txd08": "Middle East",
        "4l01sk5cdn5c": "North America",
        "q6qm6fvkst4h": "Oceania",
    },
    # Twilio: delivery through carriers, one component per product and region (e.g. "SMS, Europe").
    "gpkpyklzq55q": {"j8hlnyphbbs0": "CARRIER NETWORK"},
    # RubyGems.org: the CDN's points of presence (e.g. "Fastly London (LCY)").
    "pclby00q90vc": {"zndjv0b4vb9w": "Upstream Provider: Fastly"},
}


def statuspage_page_id(body) -> str | None:
    """The page id when a status.json body is Atlassian Statuspage's own (page.id and page.time_zone), else None."""
    page = body.get("page") if isinstance(body, dict) else None
    if not isinstance(page, dict) or "time_zone" not in page:
        return None
    page_id = page.get("id")
    return page_id if isinstance(page_id, str) and _PAGE_ID.fullmatch(page_id) else None


def _aware(raw) -> datetime:
    if not isinstance(raw, str):
        raise ValueError("invalid time")
    parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("time has no timezone")
    return parsed.astimezone(timezone.utc)


def _items(body, key: str, page_id: str) -> list[dict]:
    if not isinstance(body, dict) or not isinstance(body.get(key), list):
        raise ValueError(f"no {key} list")
    page = body.get("page")
    if not isinstance(page, dict) or page.get("id") != page_id:
        raise ValueError("another page's list")
    if not all(isinstance(item, dict) for item in body[key]):
        raise ValueError(f"invalid {key} item")
    return body[key]


def _entry(item: dict) -> tuple[list[str], list[str | None]]:
    """Names to show for one incident or maintenance, and the group id of each component (None: not a component)."""
    components = item.get("components")
    if components is None:
        components = []
    if not isinstance(components, list):
        raise ValueError("invalid components")
    names: list[str] = []
    groups: list[str | None] = []
    for component in components:
        if isinstance(component, dict) and isinstance(component.get("name"), str):
            names.append(component["name"])
            group = component.get("group_id")
            groups.append(group if isinstance(group, str) else "")
    if not names and isinstance(item.get("name"), str):
        return [item["name"]], [None]
    return names, groups


def parse_incidents(body, page_id: str) -> dict:
    """incidents/unresolved.json -> count, highest impact, latest update and affected names. Raises ValueError."""
    count, impacts, times, entries = 0, [], [], []
    for item in _items(body, "incidents", page_id):
        if item.get("status") in _RESOLVED:
            continue
        impact = item.get("impact")
        if impact not in _RANK:
            raise ValueError("unknown incident impact")
        count += 1
        impacts.append(impact)
        if item.get("updated_at") is not None:
            times.append(_aware(item["updated_at"]))
        entries.append(_entry(item))
    return {"count": count, "max_impact": max(impacts, key=_RANK.__getitem__, default="none"),
            "updated_at": max(times, default=None), "entries": entries}


def parse_maintenances(body, page_id: str) -> dict:
    """scheduled-maintenances/active.json -> count of maintenances in progress and their affected names."""
    entries = [_entry(item) for item in _items(body, "scheduled_maintenances", page_id)
               if item.get("status") in _MAINTENANCE_ACTIVE]
    return {"count": len(entries), "entries": entries}


def _clean(name: str) -> str:
    return _CONTROL.sub(" ", name).strip()[:AFFECTED_CHARS].strip()


def summarize(incidents: dict | None, maintenances: dict | None, page_id: str) -> dict:
    """The stored fields. A list that could not be read leaves its own fields None; names and the regional
    flag need both lists, since names from one list alone would hide the other's."""
    out: dict = {"active_incidents": None, "max_incident_impact": None, "incident_updated_at": None,
                 "active_maintenances": None, "affected": None, "affected_regional": None}
    if incidents is not None:
        out.update(active_incidents=min(incidents["count"], _SMALLINT), max_incident_impact=incidents["max_impact"],
                   incident_updated_at=incidents["updated_at"])
    if maintenances is not None:
        out["active_maintenances"] = min(maintenances["count"], _SMALLINT)
    if incidents is None or maintenances is None:
        return out
    entries = incidents["entries"] + maintenances["entries"]
    names: list[str] = []
    for entry_names, _groups in entries:
        for name in entry_names:
            cleaned = _clean(name)
            if cleaned and cleaned not in names and len(names) < MAX_AFFECTED:
                names.append(cleaned)
    out["affected"] = names or None
    if entries:
        regional = REGIONAL_GROUPS.get(page_id, {})
        out["affected_regional"] = all(groups and all(group in regional for group in groups) for _names, groups in entries)
    return out


def read_scope(fetch, page_id: str) -> dict:
    """Read both lists with fetch(path) -> bytes (which raises on any failure) and summarize them."""
    parts = []
    for path, parse in ((INCIDENTS_PATH, parse_incidents), (MAINTENANCES_PATH, parse_maintenances)):
        try:
            parts.append(parse(json.loads(fetch(path)), page_id))
        except Exception:  # any failure (HTTP status, too_large, timeout, bad JSON) only leaves this list out
            parts.append(None)
    return summarize(parts[0], parts[1], page_id)


def basis(indicator: str | None, active_incidents: int | None, active_maintenances: int | None) -> str | None:
    """What an active indicator rests on: an announced incident, a maintenance in progress, or neither
    (components_only). None when the indicator is not active or a list was not read."""
    if indicator not in ACTIVE_INDICATORS or active_incidents is None:
        return None
    if active_incidents > 0:
        return "incident"
    if active_maintenances is None:
        return None
    return "maintenance" if active_maintenances > 0 else "components_only"


def scope_partial(indicator: str | None, active_incidents: int | None, max_incident_impact: str | None,
                  affected_regional: bool | None) -> bool:
    """True when the announced problem may reach only part of the provider: every affected component is in a
    region or point-of-presence group (REGIONAL_GROUPS), or the worst incident is below the page indicator."""
    if indicator not in ACTIVE_INDICATORS:
        return False
    if affected_regional:
        return True
    return bool(active_incidents and indicator in _RANK and max_incident_impact in _RANK
                and _RANK[max_incident_impact] < _RANK[indicator])
