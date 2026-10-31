"""D59: standard-location files a host publishes for agents (ahn/site_declared.py). No network, no database."""

from __future__ import annotations

import inspect
import json
from datetime import datetime, timezone

import pytest

from ahn import access_profile, response_guide, site_declared, triage
from ahn.access_profile import build_profile, parse_robots, path_gates, pending_profile
from ahn.models import AccessProfile, SiteDeclared
from ahn.site_declared import (
    AI_CATALOG_PATH, API_CATALOG_PATH, MAX_APIS, MAX_ITEMS, MAX_LINKS, MAX_MCP_SERVERS, MAX_TDM_RULES, MAX_URL_CHARS,
    TDMREP_PATH, parse_ai_catalog, parse_api_catalog, parse_tdmrep, read_all, tdm_header, view,
)

NOW = datetime(2026, 10, 31, 4, 5, 6, tzinfo=timezone.utc)
BASE = "https://example.com" + API_CATALOG_PATH

# RFC 9727 Appendix A.1, verbatim.
RFC9727_A1 = b'''{
 "linkset": [
  {
   "anchor": "https://developer.example.com/apis/foo_api",
   "service-desc": [{"href": "https://developer.example.com/apis/foo_api/spec", "type": "application/yaml"}],
   "status": [{"href": "https://developer.example.com/apis/foo_api/status", "type": "application/json"}],
   "service-doc": [{"href": "https://developer.example.com/apis/foo_api/doc", "type": "text/html"}],
   "service-meta": [{"href": "https://developer.example.com/apis/foo_api/policies", "type": "text/xml"}]
  },
  {
   "anchor": "https://developer.example.com/apis/bar_api",
   "service-desc": [{"href": "https://developer.example.com/apis/bar_api/spec", "type": "application/yaml"}],
   "status": [{"href": "https://developer.example.com/apis/bar_api/status", "type": "application/json"}],
   "service-doc": [{"href": "https://developer.example.com/apis/bar_api/doc", "type": "text/plain"}]
  },
  {
   "anchor": "https://apis.example.net/apis/cantona_api",
   "service-desc": [{"href": "https://apis.example.net/apis/cantona_api/spec", "type": "text/n3"}],
   "service-doc": [{"href": "https://apis.example.net/apis/cantona_api/doc", "type": "text/html"}]
  }
 ]
}'''
# Appendix A.2 (bookmarks), A.4 (nested catalogs) and section 5.1 (a bare string as the api-catalog target).
RFC9727_A2 = b'''{ "linkset": [ { "anchor": "https://www.example.com/.well-known/api-catalog",
    "item": [ {"href": "https://developer.example.com/apis/foo_api"}, {"href": "https://developer.example.com/apis/bar_api"},
              {"href": "https://developer.example.com/apis/cantona_api"} ] } ] }'''
RFC9727_A4 = b'''{ "linkset": [ { "anchor": "https://www.example.com/.well-known/api-catalog",
    "api-catalog": [ {"href": "https://apis.example.com/iot/api-catalog"}, {"href": "https://ecommerce.example.com/api-catalog"},
                     {"href": "https://developer.example.com/gaming/api-catalog"} ] } ] }'''
RFC9727_51 = b'''{ "linkset": [ { "anchor": "https://www.example.com/.well-known/api-catalog",
    "item": [ {"href": "https://developer.example.com/apis/foo_api"}, {"href": "https://developer.example.com/apis/bar_api"},
              {"href": "https://developer.example.com/apis/cantona_api"} ],
    "api-catalog": "https://www.example.net/.well-known/api-catalog" } ] }'''
# W3C TDMRep final report, Examples 1 and 2.
TDM_EX1 = b'[\n  {\n  "location": "/",\n  "tdm-reservation": 1\n  }\n]'
TDM_EX2 = b'''[
  {"location": "/directory-a/", "tdm-reservation": 1},
  {"location": "/directory-b/html/", "tdm-reservation": 1, "tdm-policy":"https://provider.com/policies/policy.json"},
  {"location": "/directory-b/images/*.jpg", "tdm-reservation": 0}
]'''
# MCP Server Card extension, docs/discovery.md: the single- and multiple-server AI Catalog examples.
AI_SINGLE = b'''{"specVersion": "1.0", "entries": [{"identifier": "urn:air:example.com:mcp:weather",
  "type": "application/mcp-server-card+json", "url": "https://example.com/mcp/server-card"}]}'''
AI_MULTI = b'''{"specVersion": "1.0", "entries": [
  {"identifier": "urn:air:acme.com:mcp:code-review", "type": "application/mcp-server-card+json", "url": "https://acme.com/code-review/server-card"},
  {"identifier": "urn:air:acme.com:mcp:docs-search", "type": "application/mcp-server-card+json", "url": "https://acme.com/docs-search/server-card"},
  {"identifier": "urn:air:acme.com:mcp:ci-cd", "type": "application/mcp-server-card+json", "url": "https://acme.com/ci-cd/server-card"}]}'''


@pytest.fixture(autouse=True)
def switches_on(monkeypatch):
    monkeypatch.delenv("AHN_SITE_DECLARED", raising=False)
    monkeypatch.delenv("AHN_MCP_DISCOVERY", raising=False)
    monkeypatch.setattr(site_declared, "_sleep", lambda _s: None)


# --- parsers -------------------------------------------------------------------------------------------------------

def test_rfc9727_a1_keeps_each_api_and_its_rfc8631_links():
    parsed = parse_api_catalog(RFC9727_A1, BASE)
    assert parsed["status"] == "declared" and parsed["apis_total"] == 3 and parsed["items_total"] == 0
    foo = parsed["apis"][0]
    assert foo == {"anchor": "https://developer.example.com/apis/foo_api",
                   "service_desc": ["https://developer.example.com/apis/foo_api/spec"],
                   "service_doc": ["https://developer.example.com/apis/foo_api/doc"],
                   "service_meta": ["https://developer.example.com/apis/foo_api/policies"],
                   "status": ["https://developer.example.com/apis/foo_api/status"]}
    assert "service_meta" not in parsed["apis"][2] and "status" not in parsed["apis"][2]
    assert "items" not in parsed and "nested_catalogs" not in parsed


def test_rfc9727_bookmarks_nesting_and_the_bare_string_target():
    a2 = parse_api_catalog(RFC9727_A2, BASE)
    assert a2["status"] == "declared" and a2["apis_total"] == 0 and "apis" not in a2
    assert a2["items"] == ["https://developer.example.com/apis/foo_api", "https://developer.example.com/apis/bar_api",
                           "https://developer.example.com/apis/cantona_api"] and a2["items_total"] == 3
    a4 = parse_api_catalog(RFC9727_A4, BASE)
    assert a4["nested_catalogs"] == ["https://apis.example.com/iot/api-catalog", "https://ecommerce.example.com/api-catalog",
                                     "https://developer.example.com/gaming/api-catalog"]
    s51 = parse_api_catalog(RFC9727_51, BASE)
    assert s51["nested_catalogs"] == ["https://www.example.net/.well-known/api-catalog"] and s51["items_total"] == 3


def test_api_catalog_failures_caps_and_url_filtering():
    assert parse_api_catalog(b"<html>", BASE) == {"status": "not_read", "reason": "invalid_format"}
    assert parse_api_catalog(b'{"links": []}', BASE) == {"status": "not_read", "reason": "invalid_format"}
    assert parse_api_catalog(b'{"linkset": []}', BASE) == {"status": "none_declared", "reason": "empty_catalog"}
    many = {"linkset": [{"anchor": f"https://e.com/a{i}", "service-doc": [{"href": f"https://e.com/a{i}/d{j}"} for j in range(9)]}
                        for i in range(30)] + [{"item": [{"href": f"https://e.com/i{i}"} for i in range(30)],
                                                "api-catalog": [{"href": f"https://e.com/c{i}"} for i in range(9)]}]}
    parsed = parse_api_catalog(json.dumps(many).encode(), BASE)
    assert len(parsed["apis"]) == MAX_APIS and parsed["apis_total"] == 30
    assert all(len(api["service_doc"]) == MAX_LINKS for api in parsed["apis"])
    assert len(parsed["items"]) == MAX_ITEMS and parsed["items_total"] == 30 and len(parsed["nested_catalogs"]) == MAX_LINKS
    odd = {"linkset": [{"anchor": "/apis/x", "service-doc": [
        {"href": "docs/x"}, {"href": "javascript:alert(1)"}, {"href": "https://user:pw@e.com/x"},
        {"href": "https://e.com/" + "a" * MAX_URL_CHARS}, {"href": "https://e.com/x\ny"}, {"href": 5}]}]}
    api = parse_api_catalog(json.dumps(odd).encode(), BASE)["apis"][0]
    assert api == {"anchor": "https://example.com/apis/x", "service_doc": ["https://example.com/.well-known/docs/x"]}


def test_tdmrep_examples_and_the_root_reservation():
    one = parse_tdmrep(TDM_EX1, "https://example.com" + TDMREP_PATH)
    assert one == {"status": "declared", "rules": [{"location": "/", "tdm_reservation": 1}], "rules_total": 1,
                   "root_reservation": 1, "any_reserved": True}
    two = parse_tdmrep(TDM_EX2, "https://example.com" + TDMREP_PATH)
    assert two["rules"][1] == {"location": "/directory-b/html/", "tdm_reservation": 1,
                               "tdm_policy": "https://provider.com/policies/policy.json"}
    assert two["root_reservation"] is None and two["any_reserved"] is True  # no rule matches "/"
    # The first matching rule in file order decides (TDMRep 6.1), and values other than 0/1 are unset.
    first = parse_tdmrep(b'[{"location": "/*", "tdm-reservation": 0}, {"location": "/", "tdm-reservation": 1}]', "https://e.com/")
    assert first["root_reservation"] == 0
    odd = parse_tdmrep(b'[{"location": "/", "tdm-reservation": true}, {"location": "/a", "tdm-reservation": "1"},'
                       b' {"location": "/b", "tdm-reservation": 2}, {"tdm-reservation": 1}]', "https://e.com/")
    assert [rule["tdm_reservation"] for rule in odd["rules"]] == [None, 1, None] and odd["rules_total"] == 3
    assert "root_reservation" not in {k: v for k, v in odd.items() if v is not None}
    assert parse_tdmrep(b"[]", "https://e.com/") == {"status": "none_declared", "reason": "empty_file"}
    assert parse_tdmrep(b'{"location": "/"}', "https://e.com/") == {"status": "not_read", "reason": "invalid_format"}
    many = json.dumps([{"location": f"/p{i}/", "tdm-reservation": 0} for i in range(40)]).encode()
    parsed = parse_tdmrep(many, "https://e.com/")
    assert len(parsed["rules"]) == MAX_TDM_RULES and parsed["rules_total"] == 40 and parsed["any_reserved"] is False


def test_tdm_headers_examples_3_and_4():
    assert tdm_header("/robots.txt", {"tdm-reservation": "1"}, "https://e.com/robots.txt") == {
        "path": "/robots.txt", "tdm_reservation": 1}
    assert tdm_header("/llms.txt", {"tdm-reservation": "1", "tdm-policy": "https://provider.com/policies/policy.json"},
                      "https://e.com/llms.txt") == {"path": "/llms.txt", "tdm_reservation": 1,
                                                    "tdm_policy": "https://provider.com/policies/policy.json"}
    assert tdm_header("/robots.txt", {}, "https://e.com/") is None
    assert tdm_header("/robots.txt", {"tdm-reservation": "yes"}, "https://e.com/") is None


def test_ai_catalog_examples_inline_cards_and_other_entry_types():
    single = parse_ai_catalog(AI_SINGLE, "https://example.com" + AI_CATALOG_PATH)
    assert single == {"status": "declared", "servers_total": 1, "spec_version": "1.0", "servers": [
        {"identifier": "urn:air:example.com:mcp:weather", "card_url": "https://example.com/mcp/server-card"}]}
    multi = parse_ai_catalog(AI_MULTI, "https://acme.com" + AI_CATALOG_PATH)
    assert [server["card_url"] for server in multi["servers"]] == [
        "https://acme.com/code-review/server-card", "https://acme.com/docs-search/server-card", "https://acme.com/ci-cd/server-card"]
    inline = {"specVersion": "1.0", "entries": [
        {"identifier": "urn:x", "type": "application/mcp-server-card+json",
         "data": {"name": "io.example/demo", "remotes": [{"type": "streamable-http", "url": "https://mcp.example.com/mcp"}]}},
        {"identifier": "urn:a2a", "type": "application/a2a-agent-card+json", "url": "https://example.com/agent.json"},
        {"identifier": "urn:none", "type": "application/mcp-server-card+json"}]}
    parsed = parse_ai_catalog(json.dumps(inline).encode(), "https://example.com/")
    assert parsed["servers"] == [{"identifier": "urn:x", "inline_card": True, "name": "io.example/demo",
                                  "remote_urls": ["https://mcp.example.com/mcp"]}] and parsed["servers_total"] == 1
    only_agents = {"specVersion": "1.0", "entries": [inline["entries"][1]]}
    assert parse_ai_catalog(json.dumps(only_agents).encode(), "https://e.com/") == {"status": "none_declared", "reason": "no_mcp_entries"}
    assert parse_ai_catalog(b'{"mcpServers": {}}', "https://e.com/") == {"status": "not_read", "reason": "invalid_format"}
    many = {"entries": [{"identifier": f"urn:{i}", "type": "application/mcp-server-card+json", "url": f"https://e.com/{i}"} for i in range(9)]}
    parsed = parse_ai_catalog(json.dumps(many).encode(), "https://e.com/")
    assert len(parsed["servers"]) == MAX_MCP_SERVERS and parsed["servers_total"] == 9 and "spec_version" not in parsed


# --- reading: gates, manners, switches -----------------------------------------------------------------------------

class Reader:
    def __init__(self, files=None, headers=None):
        self.files = files or {}
        self.headers = headers or {}
        self.calls = []

    def __call__(self, host, path, *, cap_bytes, **options):
        self.calls.append((host, path, cap_bytes, options.get("accept"), options.get("_hop")))
        if options.get("headers_out") is not None and path in self.headers:
            options["headers_out"].update(self.headers[path])
        return self.files.get(path, ("absent", None, "http_404"))


def _gates(robots: str):
    return path_gates("ok", robots.encode(), "http_200", site_declared.paths())


def test_read_all_requests_only_the_paths_robots_txt_allows():
    reader = Reader({API_CATALOG_PATH: ("ok", RFC9727_A1, "http_200"), AI_CATALOG_PATH: ("ok", AI_SINGLE, "http_200")})
    stored = read_all("example.com", _gates("User-agent: *\nDisallow: /.well-known/tdmrep.json\n"), reader, hop=0,
                      read_at=NOW.isoformat())
    assert [call[1] for call in reader.calls] == [API_CATALOG_PATH, AI_CATALOG_PATH]
    assert stored["tdm"] == {"status": "not_read", "reason": "robots_disallowed"}
    assert stored["api_catalog"]["status"] == "declared" and stored["mcp_discovery"]["status"] == "declared"
    host, _path, cap, accept, hop = reader.calls[0]
    assert host == "example.com" and cap == site_declared.DECLARED_CAP and accept.startswith("application/linkset+json")
    assert hop is None
    # Our own token's group decides, like llms.txt.
    ours = Reader()
    read_all("example.com", _gates("User-agent: AgentHelpNetwork\nDisallow: /.well-known/\n\nUser-agent: *\nAllow: /\n"),
             ours, hop=0, read_at="x")
    assert ours.calls == []
    unreadable = Reader()
    gates = path_gates("error", None, "http_503", site_declared.paths())
    stored = read_all("example.com", gates, unreadable, hop=0, read_at="x")
    assert unreadable.calls == [] and stored["api_catalog"] == {"status": "not_read", "reason": "robots_unreadable"}


def test_read_all_labels_missing_files_redirects_and_failures_without_calling_them_none_declared():
    reader = Reader({API_CATALOG_PATH: ("redirected", None, "http_301"), TDMREP_PATH: ("invalid", None, "html_body"),
                     AI_CATALOG_PATH: ("error", None, "connect_timeout")})
    stored = read_all("www.example.com", _gates("User-agent: *\nAllow: /\n"), reader, hop=1, read_at="x")
    assert stored["api_catalog"] == {"status": "not_read", "reason": "redirected"}
    assert stored["tdm"] == {"status": "not_read", "reason": "html_body"}  # a page, maybe a challenge: not an absence
    assert stored["mcp_discovery"] == {"status": "not_read", "reason": "connect_timeout"}
    assert {call[4] for call in reader.calls} == {1}  # the www fallback spent the one hop
    boom = read_all("example.com", _gates(""), lambda *a, **k: 1 / 0, hop=0, read_at="x")
    assert boom["api_catalog"] == {"status": "not_read", "reason": "reader_error"}


def test_read_all_labels_each_answer_kind():
    # Only 404 and 410 ("absent") mean no file; a refusal, an oversized file and a web page are not readings.
    cases = {("refused", None, "http_403"): {"status": "not_read", "reason": "http_403"},
             ("too_large", None, "too_large"): {"status": "not_read", "reason": "too_large"},
             ("absent", None, "http_410"): {"status": "none_declared", "reason": "absent"},
             ("invalid", None, "html_body"): {"status": "not_read", "reason": "html_body"}}
    for answer, expected in cases.items():
        stored = read_all("example.com", _gates(""), Reader({API_CATALOG_PATH: answer}), hop=0, read_at="x")
        assert stored["api_catalog"] == expected, answer


def test_values_with_control_characters_are_dropped_not_cleaned():
    bad = ["a\u0000b", "a\u001bb", "a\u007fb", "a\u0085b", "a\u202eb", "a\u2066b", "a\u200fb"]
    for value in bad:
        catalog = {"specVersion": "1.0", "entries": [
            {"identifier": "urn:" + value, "type": "application/mcp-server-card+json", "url": "https://e.com/" + value},
            {"identifier": "urn:ok", "type": "application/mcp-server-card+json", "data": {"name": value, "remotes": [{"url": "https://e.com/m"}]}}]}
        parsed = parse_ai_catalog(json.dumps(catalog).encode(), "https://e.com/")
        assert parsed["servers"] == [{"identifier": "urn:ok", "inline_card": True, "remote_urls": ["https://e.com/m"]}], value
        rules = parse_tdmrep(json.dumps([{"location": "/" + value, "tdm-reservation": 1}, {"location": "/ok", "tdm-reservation": 0}]).encode(), "https://e.com/")
        assert [rule["location"] for rule in rules["rules"]] == ["/ok"], value
        api = parse_api_catalog(json.dumps({"linkset": [{"anchor": "https://e.com/" + value, "service-doc": [{"href": "https://e.com/d"}]}]}).encode(), "https://e.com/")
        assert api["apis"][0]["anchor"] is None, value
        assert "\u0000" not in json.dumps([parsed, rules, api])
    # Ordinary whitespace inside a name is collapsed, not a reason to drop it.
    named = {"entries": [{"identifier": "urn:x", "type": "application/mcp-server-card+json", "data": {"name": "Demo\tServer\n"}}]}
    assert parse_ai_catalog(json.dumps(named).encode(), "https://e.com/")["servers"][0]["name"] == "Demo Server"


def test_reads_pause_between_requests(monkeypatch):
    pauses = []
    monkeypatch.setattr(site_declared, "_sleep", pauses.append)
    read_all("example.com", _gates(""), Reader(), hop=0, read_at="x")
    assert pauses == [site_declared.PAUSE_SECONDS] * 2


def test_the_mcp_switch_stops_the_read_and_drops_the_field(monkeypatch):
    monkeypatch.setenv("AHN_MCP_DISCOVERY", "0")
    reader = Reader()
    stored = read_all("example.com", _gates(""), reader, hop=0, read_at=NOW.isoformat())
    assert AI_CATALOG_PATH not in [call[1] for call in reader.calls] and "mcp_discovery" not in stored
    shown = view("ok", stored, "example.com", NOW, "fresh")
    assert "mcp_discovery" not in shown
    assert "mcp_discovery" not in SiteDeclared(**shown).model_dump(mode="json")


def test_the_site_declared_switch_stops_everything(monkeypatch):
    monkeypatch.setenv("AHN_SITE_DECLARED", "0")
    profile = build_profile("example.com", reader=Reader({"/robots.txt": ("ok", b"User-agent: *\nAllow: /\n", "http_200")}))
    assert profile["_site_declared"] is None
    assert "site_declared" not in pending_profile("example.com", 5)
    assert view("ok", None, "example.com", NOW, "fresh") is None


def test_build_profile_passes_gates_and_tdm_headers_already_seen():
    reader = Reader({"/robots.txt": ("ok", b"User-agent: *\nDisallow: /.well-known/ai-catalog.json\n", "http_200"),
                     "/llms.txt": ("ok", b"# x\n", "http_200")},
                    headers={"/robots.txt": {"tdm-reservation": "1"}})
    profile = build_profile("example.com", reader=reader)
    declared = profile["_site_declared"]
    assert declared["read_host"] == "example.com" and declared["hop"] == 0
    assert declared["headers"] == [{"path": "/robots.txt", "tdm_reservation": 1}]
    assert declared["gates"][AI_CATALOG_PATH] == (False, "robots_disallowed") and declared["gates"][API_CATALOG_PATH] == (True, None)
    # build_profile itself requests only robots.txt and llms.txt; the standard files follow the stored row.
    assert [call[1] for call in reader.calls] == ["/robots.txt", "/llms.txt"]
    failed = build_profile("example.com", reader=lambda h, p, *, cap_bytes, **_: ("error", None, "connect_error"))
    assert failed["_site_declared"] is None
    refused = build_profile("example.com", reader=lambda h, p, *, cap_bytes, **_: ("refused", None, "http_403"))
    assert refused["robots_status"] == "refused" and refused["_site_declared"] is None


def test_fetch_public_file_keeps_only_tdm_headers():
    assert "headers_out" in inspect.signature(access_profile.fetch_public_file).parameters
    assert site_declared.TDM_HEADERS == ("tdm-reservation", "tdm-policy")


# --- the answer ----------------------------------------------------------------------------------------------------

def _stored():
    reader = Reader({API_CATALOG_PATH: ("ok", RFC9727_A1, "http_200"), TDMREP_PATH: ("absent", None, "http_404"),
                     AI_CATALOG_PATH: ("ok", AI_SINGLE, "http_200")}, headers={API_CATALOG_PATH: {"tdm-reservation": "1"}})
    return read_all("example.com", _gates(""), reader, hop=0, read_at=NOW.isoformat())


def test_view_of_a_current_read_validates_and_marks_mcp_experimental():
    shown = view("ok", _stored(), "example.com", NOW, "fresh")
    assert shown["api_catalog"]["source_url"] == "https://example.com/.well-known/api-catalog"
    assert shown["tdm"]["status"] == "declared" and shown["tdm"]["headers"] == [
        {"path": API_CATALOG_PATH, "tdm_reservation": 1}]  # a header declares even where tdmrep.json is missing
    assert (shown["tdm"]["file_status"], shown["tdm"]["file_reason"]) == ("none_declared", "absent") and "reason" not in shown["tdm"]
    assert shown["mcp_discovery"]["experimental"] is True and shown["note"] == site_declared.NOTE
    dumped = SiteDeclared(**shown).model_dump(mode="json")
    assert dumped["mcp_discovery"]["servers"][0]["card_url"] == "https://example.com/mcp/server-card"
    assert "reason" not in dumped["api_catalog"] and "items" not in dumped["api_catalog"]


def test_view_never_shows_a_result_from_another_read():
    stored = _stored()
    later = datetime(2026, 11, 1, tzinfo=timezone.utc)
    stale = view("ok", stored, "example.com", later, "fresh")
    assert {stale[key]["reason"] for key in ("api_catalog", "tdm", "mcp_discovery")} == {"read_before_site_declared"}
    assert "note" not in stale and "headers" not in stale["tdm"]
    assert view("ok", None, "example.com", NOW, "fresh")["tdm"]["reason"] == "read_before_site_declared"
    assert view("ok", {**stored, "version": "old"}, "example.com", NOW, "fresh")["tdm"]["reason"] == "read_before_site_declared"
    assert view("not_public", None, "example.com", NOW, "fresh")["tdm"]["reason"] == "not_public"
    assert view("error", None, "example.com", None, "stale")["tdm"]["reason"] == "robots_unreadable"
    # Between the robots row and the file reads (and after a failed store) the row holds a pending marker.
    marker = site_declared.pending_marker(NOW.isoformat())
    assert {view("ok", marker, "example.com", NOW, "fresh")[key]["reason"] for key in ("api_catalog", "tdm", "mcp_discovery")} == {"pending"}
    assert view("ok", marker, "example.com", later, "fresh")["tdm"]["reason"] == "read_before_site_declared"
    # A host that refused robots.txt itself is not asked for its other files.
    refused = view("refused", None, "example.com", NOW, "fresh")
    assert {refused[key]["reason"] for key in ("api_catalog", "tdm", "mcp_discovery")} == {"robots_refused"}
    assert view("refused", stored, "example.com", NOW, "fresh")["api_catalog"]["reason"] == "robots_refused"
    pending = pending_profile("example.com", 5)["site_declared"]
    assert pending["api_catalog"] == {"status": "not_read", "source_url": "https://example.com/.well-known/api-catalog",
                                      "reason": "pending"}
    assert pending["freshness"] == "pending" and pending["fetched_at"] is None
    AccessProfile(**pending_profile("example.com", 5), note="n")


def test_the_verdict_never_reads_site_declared():
    # recommendation, next_checks, summary_line and next_action are the same with and without the field.
    robots = "User-agent: GPTBot\nDisallow: /\n\nUser-agent: *\nAllow: /\n"
    plain = {"robots_status": "ok", "robots": parse_robots(robots), "llms_txt_status": "absent", "freshness": "fresh",
             "fetched_at": NOW, "read_host": "example.com"}
    rich = {**plain, "site_declared": view("ok", _stored(), "example.com", NOW, "fresh")}
    official = {"developer_docs": "https://developer.example.com/"}
    for error_class in (None, "http_403", "captcha"):
        assert triage.recommendation(error_class, None, plain, official) == triage.recommendation(error_class, None, rich, official)
        assert triage.recommendation(error_class, None, plain, None) == triage.recommendation(error_class, None, rich, None)
        assert (triage.next_checks(error_class, None, "no_reports", access_profile=plain, official=official)
                == triage.next_checks(error_class, None, "no_reports", access_profile=rich, official=official))
        verdict = triage.recommendation(error_class, None, plain, official)
        result = {"site_host": "example.com", "recommendation": verdict, "official_paths": official}
        assert (response_guide.guide({**result, "access_profile": plain})
                == response_guide.guide({**result, "access_profile": rich}))
    for module in (triage, response_guide):
        assert "site_declared" not in inspect.getsource(module)
