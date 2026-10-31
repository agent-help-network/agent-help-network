"""What a host publishes for agents at standard locations, read next to its robots.txt (D59).

Three files, each at the one path its specification fixes, and the TDM response headers seen on the files read:

* ``/.well-known/api-catalog`` (RFC 9727): the host's own list of its APIs, a Linkset (RFC 9264,
  ``application/linkset+json``). Kept: per API its anchor and the ``service-desc``, ``service-doc``, ``service-meta``
  and ``status`` links (RFC 8631), the ``item`` bookmarks and nested ``api-catalog`` links.
* ``/.well-known/tdmrep.json`` (W3C TDMRep Community Group final report, 2024-05-10): rules of ``location``,
  ``tdm-reservation`` (1 reserved, 0 not reserved, anything else unset) and optional ``tdm-policy``. The same two
  properties can also arrive as ``tdm-reservation`` / ``tdm-policy`` HTTP response headers; the ones seen on the
  responses to the files read here are kept with the path they came on.
* ``/.well-known/ai-catalog.json`` (experimental, ``AHN_MCP_DISCOVERY=0`` turns it off): the AI Catalog that the MCP
  Server Card extension (SEP-2127, docs/discovery.md of modelcontextprotocol/experimental-ext-server-card) names for
  domain-level discovery. Only entries of type ``application/mcp-server-card+json`` are kept: their identifier and the
  card's URL, or for an inline card its name and remote URLs. The card itself is not fetched.

Reading manners (D-1031-1): a file is requested only where the host's robots.txt, read just before with the same
user agent, allows that path for us (the same gate as llms.txt, D56); only the paths above, never a guessed one; one
attempt per file per profile read (the profile's 24-hour cache window); the same pinned-IP reader, timeouts and
redirect rule as robots.txt, with a 64 KB cap. Only the parsed fields below are stored, never the files.

Information only: none of this changes ``recommendation`` or ``next_checks``. It is not an access rule, not
permission and not a legal reading, and the links it lists are reported as published, not opened or checked.
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime
from urllib.parse import urljoin, urlsplit

VERSION = "ahn-site-declared/1"
API_CATALOG_PATH = "/.well-known/api-catalog"
TDMREP_PATH = "/.well-known/tdmrep.json"
AI_CATALOG_PATH = "/.well-known/ai-catalog.json"
DECLARED_CAP = 65_536
# Storage limits, stated in the privacy policy (tests/test_policy_coverage.py checks the numbers).
MAX_APIS = 10            # APIs (anchors with RFC 8631 links) kept from one catalog
MAX_LINKS = 3            # links kept per relation of one API, and nested catalogs kept
MAX_ITEMS = 10           # item bookmarks kept
MAX_TDM_RULES = 10
MAX_TDM_HEADERS = 5      # TDM response headers kept, with the path each came on
MAX_MCP_SERVERS = 5
MAX_URL_CHARS = 300
MAX_TEXT_CHARS = 120     # an identifier, a server name, a location pattern
PAUSE_SECONDS = 0.5      # between two reads of the same host
API_RELATIONS = {"service-desc": "service_desc", "service-doc": "service_doc", "service-meta": "service_meta",
                 "status": "status"}
MCP_CARD_TYPE = "application/mcp-server-card+json"
TDM_HEADERS = ("tdm-reservation", "tdm-policy")
ACCEPT = {API_CATALOG_PATH: "application/linkset+json, application/json;q=0.9, */*;q=0.1",
          TDMREP_PATH: "application/json, */*;q=0.1",
          AI_CATALOG_PATH: "application/ai-catalog+json, application/json;q=0.9, */*;q=0.1"}
NOTE = ("Files the host publishes for agents at standard locations: its API catalog (RFC 9727), its text-and-data-mining "
        "reservation (W3C TDMRep) and, experimentally, its MCP server catalog (AI Catalog, MCP SEP-2127). Information "
        "only: not an access rule, not permission, and not a legal reading. Links are reported as published; they were "
        "not opened or checked.")
_sleep = time.sleep


def enabled() -> bool:
    """AHN_SITE_DECLARED=0 stops every read below and leaves site_declared out of the answer."""
    return os.environ.get("AHN_SITE_DECLARED", "1").strip() != "0"


def mcp_enabled() -> bool:
    """AHN_MCP_DISCOVERY=0 stops the experimental AI Catalog read and leaves mcp_discovery out of the answer."""
    return enabled() and os.environ.get("AHN_MCP_DISCOVERY", "1").strip() != "0"


def paths() -> tuple[str, ...]:
    """The standard paths read for the current switches, in read order."""
    return (API_CATALOG_PATH, TDMREP_PATH) + ((AI_CATALOG_PATH,) if mcp_enabled() else ())


def _url(value, base: str) -> str | None:
    """An absolute http(s) URL as published (a relative one resolved against the file's own URL), or None.

    Credentials in the URL, other schemes, control characters and anything over MAX_URL_CHARS are dropped."""
    if not isinstance(value, str):
        return None
    value = value.strip()
    if not value or len(value) > MAX_URL_CHARS or any(ord(char) < 0x21 or ord(char) == 0x7f for char in value):
        return None
    try:
        absolute = urljoin(base, value)
        parts = urlsplit(absolute)
    except ValueError:
        return None
    if parts.scheme not in ("https", "http") or not parts.hostname or parts.username or parts.password:
        return None
    return absolute if len(absolute) <= MAX_URL_CHARS else None


def _text(value) -> str | None:
    if not isinstance(value, str):
        return None
    value = " ".join(value.split())
    return value[:MAX_TEXT_CHARS] if value else None


def _hrefs(targets, base: str) -> list[str]:
    """The target URLs of one relation. RFC 9264 makes it an array of target objects; a bare string (seen in
    RFC 9727 section 5.1's example) is read as one target."""
    if isinstance(targets, (str, dict)):
        targets = [targets]
    if not isinstance(targets, list):
        return []
    found: list[str] = []
    for target in targets[:200]:
        href = _url(target.get("href") if isinstance(target, dict) else target, base)
        if href and href not in found:
            found.append(href)
    return found


def parse_api_catalog(body: bytes, base: str) -> dict:
    """Pure. RFC 9727 API catalog -> {"status", "reason"?, apis, apis_total, items, items_total, nested_catalogs}."""
    try:
        document = json.loads(body)
    except Exception:  # not JSON, not UTF-8, nested too deep
        return {"status": "not_read", "reason": "invalid_format"}
    linkset = document.get("linkset") if isinstance(document, dict) else None
    if not isinstance(linkset, list):
        return {"status": "not_read", "reason": "invalid_format"}
    apis: list[dict] = []
    items: list[str] = []
    nested: list[str] = []
    for context in linkset[:500]:
        if not isinstance(context, dict):
            continue
        links = {key: _hrefs(context.get(rel), base) for rel, key in API_RELATIONS.items()}
        for href in _hrefs(context.get("item"), base):
            if href not in items:
                items.append(href)
        for href in _hrefs(context.get("api-catalog"), base):
            if href not in nested:
                nested.append(href)
        if any(links.values()):
            api = {"anchor": _url(context.get("anchor"), base)}
            api.update({key: hrefs[:MAX_LINKS] for key, hrefs in links.items() if hrefs})
            apis.append(api)
    if not (apis or items or nested):
        return {"status": "none_declared", "reason": "empty_catalog"}
    out: dict = {"status": "declared", "apis_total": len(apis), "items_total": len(items)}
    # Empty lists are left out to keep answers small; the totals always say how many were published.
    for key, values in (("apis", apis[:MAX_APIS]), ("items", items[:MAX_ITEMS]), ("nested_catalogs", nested[:MAX_LINKS])):
        if values:
            out[key] = values
    return out


def _reservation(value) -> int | None:
    """TDMRep 5.1: 1 or 0; any other value is a protocol error and reads as unset (None)."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and value in (0, 1):
        return value
    if isinstance(value, str) and value.strip() in ("0", "1"):
        return int(value.strip())
    return None


def _location_matches(pattern: str, path: str) -> bool:
    from .access_profile import _path_matches  # access_profile imports this module lazily
    return _path_matches(pattern, path)


def parse_tdmrep(body: bytes, base: str) -> dict:
    """Pure. W3C TDMRep tdmrep.json -> {"status", "reason"?, rules, rules_total, root_reservation, any_reserved}.

    ``root_reservation`` is what the file says for the path "/" (the first matching rule in file order, TDMRep 6.1;
    None when no rule matches or its value is unset). A rule without a location string is skipped."""
    try:
        document = json.loads(body)
    except Exception:
        return {"status": "not_read", "reason": "invalid_format"}
    if not isinstance(document, list):
        return {"status": "not_read", "reason": "invalid_format"}
    rules: list[dict] = []
    for rule in document[:500]:
        if not isinstance(rule, dict) or not isinstance(rule.get("location"), str) or not rule["location"].strip():
            continue
        entry = {"location": rule["location"].strip()[:MAX_TEXT_CHARS], "tdm_reservation": _reservation(rule.get("tdm-reservation"))}
        policy = _url(rule.get("tdm-policy"), base)
        if policy:
            entry["tdm_policy"] = policy
        rules.append(entry)
    if not rules:
        return {"status": "none_declared", "reason": "empty_file"}
    root = next((rule["tdm_reservation"] for rule in rules if _location_matches(rule["location"], "/")), None)
    return {"status": "declared", "rules": rules[:MAX_TDM_RULES], "rules_total": len(rules), "root_reservation": root,
            "any_reserved": any(rule["tdm_reservation"] == 1 for rule in rules)}


def tdm_header(path: str, headers: dict | None, base: str) -> dict | None:
    """The tdm-reservation / tdm-policy response headers (TDMRep 6.2) seen on one response, or None."""
    if not headers:
        return None
    reservation = _reservation(headers.get("tdm-reservation"))
    policy = _url(headers.get("tdm-policy"), base)
    if reservation is None and policy is None:
        return None
    seen = {"path": path, "tdm_reservation": reservation}
    if policy:
        seen["tdm_policy"] = policy
    return seen


def _card_remotes(card: dict, base: str) -> list[str]:
    remotes = card.get("remotes")
    if not isinstance(remotes, list):
        return []
    found: list[str] = []
    for remote in remotes[:50]:
        url = _url(remote.get("url"), base) if isinstance(remote, dict) else None
        if url and url not in found:
            found.append(url)
    return found[:MAX_LINKS]


def parse_ai_catalog(body: bytes, base: str) -> dict:
    """Pure. AI Catalog -> MCP Server Card entries: {"status", "reason"?, servers, servers_total, spec_version}.

    Per the Server Card discovery document: entries whose ``type`` is application/mcp-server-card+json, each with an
    ``identifier`` and exactly one of ``url`` (the hosted card) or ``data`` (the card inline)."""
    try:
        document = json.loads(body)
    except Exception:
        return {"status": "not_read", "reason": "invalid_format"}
    entries = document.get("entries") if isinstance(document, dict) else None
    if not isinstance(entries, list):
        return {"status": "not_read", "reason": "invalid_format"}
    servers: list[dict] = []
    for entry in entries[:500]:
        if not isinstance(entry, dict) or entry.get("type") != MCP_CARD_TYPE:
            continue
        server: dict = {"identifier": _text(entry.get("identifier"))}
        card_url = _url(entry.get("url"), base)
        if card_url:
            server["card_url"] = card_url
        elif isinstance(entry.get("data"), dict):
            card = entry["data"]
            server["inline_card"] = True
            if _text(card.get("name")):
                server["name"] = _text(card.get("name"))
            server["remote_urls"] = _card_remotes(card, base)
        else:
            continue  # neither a usable url nor an inline card
        servers.append(server)
    if not servers:
        return {"status": "none_declared", "reason": "no_mcp_entries"}
    out = {"status": "declared", "servers": servers[:MAX_MCP_SERVERS], "servers_total": len(servers)}
    version = _text(document.get("specVersion"))
    if version:
        out["spec_version"] = version[:16]
    return out


PARSERS = {API_CATALOG_PATH: parse_api_catalog, TDMREP_PATH: parse_tdmrep, AI_CATALOG_PATH: parse_ai_catalog}
KEYS = {API_CATALOG_PATH: "api_catalog", TDMREP_PATH: "tdm", AI_CATALOG_PATH: "mcp_discovery"}


def _result(path: str, status: str, body: bytes | None, detail: str | None, base: str) -> dict:
    """One file's stored result from one fetch_public_file answer."""
    if status == "ok" and body is not None:
        return PARSERS[path](body, base)
    if status == "absent":
        return {"status": "none_declared", "reason": "absent"}
    if status == "invalid":  # a web page served for the path: a soft 404, no file
        return {"status": "none_declared", "reason": "soft_404"}
    if status == "redirected":
        return {"status": "not_read", "reason": "redirected"}
    return {"status": "not_read", "reason": (detail or status or "error")[:32]}


def read_all(read_host: str, gates: dict, reader, *, hop: int, header_seen: list[dict] | None = None,
             read_at: str) -> dict:
    """Read the standard files the gates allow, one attempt each; never raises. Returns the stored shape.

    ``gates`` maps each path to (allowed, reason) from the host's robots.txt (access_profile.path_gate).
    ``header_seen`` carries TDM headers already seen on this read's robots.txt and llms.txt responses."""
    base_of = lambda path: f"https://{read_host}{path}"  # noqa: E731
    stored: dict = {"version": VERSION, "read_at": read_at}
    headers = list(header_seen or [])
    first = True
    for path in paths():
        allowed, reason = gates.get(path, (False, "robots_unreadable"))
        if not allowed:
            stored[KEYS[path]] = {"status": "not_read", "reason": reason}
            continue
        if not first:
            _sleep(PAUSE_SECONDS)
        first = False
        seen: dict = {}
        options = {"accept": ACCEPT[path], "headers_out": seen}
        if hop:
            options["_hop"] = hop
        try:
            result = reader(read_host, path, cap_bytes=DECLARED_CAP, **options)
        except Exception:  # a reader stand-in that raises must not lose the profile read
            result = ("error", None, "reader_error")
        status, body, detail = (tuple(result) + (None,))[:3]
        stored[KEYS[path]] = _result(path, status, body, detail, base_of(path))
        header = tdm_header(path, seen, base_of(path))
        if header:
            headers.append(header)
    stored["tdm_headers"] = headers[:MAX_TDM_HEADERS]
    return stored


def _section(path: str, read_host: str, stored: dict | None, reason: str | None) -> dict:
    view = {"status": "not_read", "source_url": f"https://{read_host}{path}"}
    if stored is None:
        view["reason"] = reason
        return view
    view.update({k: v for k, v in stored.items() if v is not None})
    return view


def _same_instant(stamp, fetched_at) -> bool:
    try:
        return datetime.fromisoformat(stamp) == fetched_at
    except (TypeError, ValueError):
        return False


def view(robots_status: str, stored: dict | None, read_host: str, fetched_at, freshness: str) -> dict | None:
    """access_profile.site_declared, or None when AHN_SITE_DECLARED=0. Not reading is never 'none_declared'.

    A stored result counts only when this version wrote it for the read the row now describes (``read_at`` equals the
    row's fetched_at); otherwise every file reads as not_read (pending, or read_before_site_declared)."""
    if not enabled():
        return None
    current = (isinstance(stored, dict) and stored.get("version") == VERSION and fetched_at is not None
               and _same_instant(stored.get("read_at"), fetched_at))
    if robots_status == "not_public":
        reason = "not_public"  # the host itself is not eligible: nothing was requested
    elif robots_status == "error" and fetched_at is None:
        reason = "robots_unreadable"  # no robots.txt answer yet, so no path was allowed
    elif robots_status == "pending" or fetched_at is None:
        reason = "pending"
    elif not current:
        reason = "read_before_site_declared"
    else:
        reason = None
    out: dict = {}
    for path in paths():
        key = KEYS[path]
        out[key] = _section(path, read_host, stored.get(key) if current else None, reason)
        if key == "mcp_discovery":
            out[key]["experimental"] = True
    headers = (stored.get("tdm_headers") or []) if current else []
    if headers:
        out["tdm"]["headers"] = headers
        if out["tdm"]["status"] in ("none_declared", "not_read") and current:
            # A header is a declaration even where the file is missing (TDMRep 6.2 and 6.7).
            out["tdm"]["status"] = "declared"
            out["tdm"].pop("reason", None)
    out.update({"fetched_at": fetched_at, "freshness": freshness})
    if any(out[KEYS[path]]["status"] == "declared" for path in paths()):
        out["note"] = NOTE
    return out
