"""Robots.txt decisions for status-feed hosts (D50): the pure parts, no database and no network.

The real fetch_public_file runs against a stub connection pool, so each way a robots.txt read can end is the one the
reader really reports.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone

import pytest
import urllib3

from ahn import access_profile, feed_robots, status_refresh
from ahn.feed_robots import DECIDED_TTL, ERROR_TTL, Decision, build, decide_paths
from ahn.status_refresh import Source, feed_paths

NOW = datetime(2026, 10, 22, 12, 0, tzinfo=timezone.utc)
STATUS = "/api/v2/status.json"
PATHS = (STATUS, "/api/v2/incidents/unresolved.json", "/api/v2/scheduled-maintenances/active.json", "/history.atom")
ATLASSIAN_DEFAULT = b"User-agent: *\nDisallow: /api/\nDisallow: /embed/\n"


def allowed(body, path=STATUS):
    return decide_paths(body, [path])[path]


# --- the rules -----------------------------------------------------------------------------------------------------

def test_the_default_atlassian_file_disallows_the_api_and_names_the_rule():
    answers = decide_paths(ATLASSIAN_DEFAULT, PATHS)
    assert [answers[p]["allowed"] for p in PATHS] == [False, False, False, True]  # the Atom history is not under /api/
    assert answers[STATUS] == {"allowed": False, "rule": "disallow: /api/", "group": "*"}
    assert answers["/history.atom"] == {"allowed": True, "rule": None, "group": "*"}


def test_longest_match_wins_and_allow_wins_a_tie():
    body = b"User-agent: *\nDisallow: /api/\nAllow: /api/v2/\n"
    assert allowed(body) == {"allowed": True, "rule": "allow: /api/v2/", "group": "*"}
    assert allowed(b"User-agent: *\nDisallow: /api/v2/\nAllow: /api/v2/\n")["allowed"] is True  # equal length: Allow
    assert allowed(b"User-agent: *\nAllow: /api/\nDisallow: /api/v2/status.json\n")["allowed"] is False  # longer Disallow
    assert allowed(b"User-agent: *\nDisallow:\n")["allowed"] is True  # an empty Disallow allows everything


def test_a_group_naming_agenthelpnetwork_replaces_the_star_group():
    body = b"User-agent: *\nDisallow: /\n\nUser-agent: AgentHelpNetwork\nAllow: /api/v2/\nDisallow: /api/v1/\n"
    assert allowed(body) == {"allowed": True, "rule": "allow: /api/v2/", "group": "agenthelpnetwork"}
    # The name is the product token, not a prefix of it: a group named Agent is somebody else's.
    assert allowed(b"User-agent: Agent\nAllow: /\n\nUser-agent: *\nDisallow: /\n")["allowed"] is False
    # Our own group that says nothing about the path leaves it allowed; the star group is not consulted.
    assert allowed(b"User-agent: AgentHelpNetwork\nDisallow: /x\n\nUser-agent: *\nDisallow: /\n") == {
        "allowed": True, "rule": None, "group": "agenthelpnetwork"}


def test_wildcards_anchors_and_the_question_mark_trap():
    assert allowed(b"User-agent: *\nDisallow: /*/status.json$\n")["allowed"] is False
    assert allowed(b"User-agent: *\nDisallow: /*/status.json$\n", "/api/v2/status.json?x=1")["allowed"] is True  # $ anchors
    # urllib.robotparser reads `Disallow: /?` as `/` and would call every path closed (the google.com misreading).
    google = b"User-agent: *\nDisallow: /search\nDisallow: /?\n"
    assert allowed(google, "/appsstatus/dashboard/incidents.json")["allowed"] is True
    assert allowed(google, "/?q=1")["allowed"] is False


def test_where_the_two_groupings_differ_the_stricter_answer_stands():
    # D37's default ends the star group at the blank line after Crawl-delay; RFC 9309 and Google's parser keep going
    # and merge BadBot's group into it. A reader unsure of what the file says does not read.
    body = b"User-agent: *\nCrawl-delay: 10\n\nUser-agent: BadBot\nDisallow: /\n"
    default, _ = access_profile.split_groups(body)
    strict, _ = access_profile.split_groups(body, google_style=True)
    assert default != strict
    assert allowed(body)["allowed"] is False


def test_the_decision_keeps_the_rule_line_short_and_never_the_file():
    long_rule = b"User-agent: *\nDisallow: /" + b"a" * 300 + b"\n"
    answer = allowed(long_rule, "/" + "a" * 320)
    assert answer["allowed"] is False and len(answer["rule"]) == feed_robots.RULE_CHARS
    decision = build("status.example.com", ("ok", ATLASSIAN_DEFAULT, "http_200"), PATHS, NOW)
    assert decision.sha256 == hashlib.sha256(ATLASSIAN_DEFAULT).hexdigest()
    assert "Embed" not in repr(decision) and "/embed/" not in repr(decision)  # a rule that decided nothing is not kept


# --- how a robots.txt read ends (the real reader, a stub pool) ------------------------------------------------------

class Response:
    def __init__(self, status, headers=None, body=b""):
        self.status, self.headers, self._body = status, headers or {}, body

    def read(self, _limit):
        return self._body

    def release_conn(self):
        pass


def reading(monkeypatch, outcome, body=b"", headers=None):
    """What fetch_public_file(host, '/robots.txt') returns when the host answers `outcome` (a status or an exception)."""
    seen = []

    class Pool:
        def __init__(self, *_a, **_kw):
            pass

        def request(self, method, path, **_kw):
            seen.append(path)
            if isinstance(outcome, BaseException):
                raise outcome
            return Response(outcome, headers, body)

        def close(self):
            pass

    monkeypatch.setattr(access_profile, "_resolve", lambda host, timeout: {"93.184.216.34"})
    monkeypatch.setattr(access_profile.urllib3, "HTTPSConnectionPool", Pool)
    return build("status.example.com", access_profile.fetch_public_file("status.example.com", "/robots.txt", cap_bytes=1000), PATHS, NOW), seen


@pytest.mark.parametrize("outcome,body,headers,status,detail,readable", [
    (200, ATLASSIAN_DEFAULT, None, "ok", "http_200", False),            # the file decides: the API paths are closed
    (404, b"", None, "absent", "http_404", True),                       # no file: everything allowed
    (410, b"", None, "absent", "http_410", True),
    (403, b"", None, "unavailable", "http_403", True),                  # RFC 9309 2.3.1.3: 4xx is "Unavailable"
    (401, b"", None, "unavailable", "http_401", True),
    (451, b"", None, "unavailable", "http_451", True),
    (200, b"<!doctype html><html>not found</html>", None, "unavailable", "html_body", True),  # a soft 404 has no rules
    (500, b"", None, "error", "http_500", False),                       # RFC 9309 2.3.1.4: complete disallow
    (502, b"", None, "error", "http_502", False),
    (503, b"", None, "error", "http_503", False),
    (429, b"", None, "error", "http_429", False),                       # asked to slow down: not a permission
    (301, b"", {"Location": "https://elsewhere.example/robots.txt"}, "error", "redirect:elsewhere.example", False),
    (urllib3.exceptions.ReadTimeoutError(None, "u", "t"), b"", None, "error", "read_timeout", False),
    (urllib3.exceptions.ConnectTimeoutError("t"), b"", None, "error", "connect_timeout", False),
])
def test_how_each_read_ends(monkeypatch, outcome, body, headers, status, detail, readable):
    decision, seen = reading(monkeypatch, outcome, body, headers)
    assert (decision.robots_status, decision.detail) == (status, detail)
    assert seen and set(seen) <= {"/robots.txt"}  # only /robots.txt is ever asked of a feed host here
    if status == "ok":
        assert [decision.allows(p) for p in PATHS] == [False, False, False, True]
    else:
        assert [decision.allows(p) for p in PATHS] == [readable] * len(PATHS)
    assert decision.ttl() == (ERROR_TTL if status == "error" else DECIDED_TTL + feed_robots.slack("status.example.com"))


def test_a_failed_read_is_never_a_permission_and_a_path_without_an_answer_is_not_allowed():
    decision = build("h.example", ("error", None, "dns_failed"), PATHS, NOW)
    assert not any(decision.allows(p) for p in PATHS)
    assert not build("h.example", ("absent", None, "http_404"), PATHS, NOW).allows("/never/asked")
    assert not build("h.example", ("not_public", None, "not_public"), PATHS, NOW).allows(STATUS)
    assert not build("h.example", ("too_large", None, "too_large"), PATHS, NOW).allows(STATUS)


def test_the_cache_window_is_a_day_and_an_hour_after_a_failed_read():
    ok = build("h.example", ("ok", ATLASSIAN_DEFAULT, "http_200"), PATHS, NOW)
    due = NOW + DECIDED_TTL + feed_robots.slack("h.example")  # a day, plus the host's own 0-2 hours
    assert ok.current(PATHS, due - timedelta(seconds=1)) and not ok.current(PATHS, due)
    failed = build("h.example", ("error", None, "http_503"), PATHS, NOW)
    assert failed.current(PATHS, NOW + timedelta(minutes=59)) and not failed.current(PATHS, NOW + ERROR_TTL)
    assert not ok.current(PATHS + ("/new/path",), NOW)  # a path it never answered
    older = Decision(**{**ok.__dict__, "parser_version": "older"})
    assert not older.current(PATHS, NOW)


# --- what is requested, and what is shown -------------------------------------------------------------------------

def test_the_paths_a_feed_host_is_asked_about():
    atlassian = Source("A", "https://status.a.example/", "https://status.a.example/api/v2/status.json")
    assert feed_paths(atlassian) == PATHS
    discovered = Source("D", "https://status.d.example/", "https://status.d.example/api/v2/status.json", "discovered_statuspage")
    assert feed_paths(discovered) == PATHS
    assert feed_paths(Source("I", "https://i.example/", "https://i.example/summary.json", "instatus")) == ("/summary.json",)
    assert feed_paths(Source("B", "https://b.example/", "https://b.example/index.json", "betterstack")) == ("/index.json",)
    google = status_refresh.SOURCES["gmail.com"] if "gmail.com" in status_refresh.SOURCES else None
    if google is not None:
        assert feed_paths(google) == ("/appsstatus/dashboard/incidents.json",)
    # Every curated feed's own path is the first one decided, and no path is invented from a user's input.
    for source in status_refresh.SOURCES.values():
        assert feed_paths(source)[0] == feed_robots.host_and_path(source.api_url)[1]


def test_the_interim_list_only_keeps_an_undecided_host_on_show():
    hosts = feed_robots.interim_hosts()
    assert len(hosts) == 121 and all(h == h.lower() and "/" not in h for h in hosts)
    assert "www.google.com" in hosts and "status.barchart.com" in hosts  # allowed under RFC 9309 (longest match; a 403 is "unavailable")
    assert "discordstatus.com" not in hosts and "www.githubstatus.com" not in hosts
    listed = sorted(hosts)[0]
    assert feed_robots.shown("undecided", listed) and not feed_robots.shown("undecided", "discordstatus.com")
    assert feed_robots.shown("allowed", "discordstatus.com")
    # A stored decision replaces the list, in both directions.
    assert not feed_robots.shown("disallowed", listed) and not feed_robots.shown("unknown", listed)


def test_there_is_no_switch_that_reads_every_feed_anyway():
    import inspect
    import os
    assert "AHN_FEED_ROBOTS" not in inspect.getsource(status_refresh) + inspect.getsource(feed_robots)
    assert not any(k.startswith("AHN_FEED_ROBOTS") for k in os.environ)


def test_every_curated_feed_host_can_be_read_by_the_robots_reader():
    # A host fetch_public_file refuses as invalid would read as "could not be read" forever and never be requested.
    for source in status_refresh.SOURCES.values():
        host, path = feed_robots.host_and_path(source.api_url)
        assert access_profile._HOST_RE.match(host), host
        assert all(feed_robots._PATH_RE.match(p) for p in feed_paths(source)), source.api_url


def test_the_matcher_agrees_with_the_regular_expression_it_replaced_and_cannot_be_made_to_backtrack():
    import random
    import re
    import time

    def reference(pattern, path):  # the pre-D50 matcher
        if not pattern:
            return False
        anchored = pattern.endswith("$")
        core = pattern[:-1] if anchored else pattern
        regex = ".*".join(re.escape(part) for part in core.split("*")) + ("$" if anchored else "")
        return re.match(regex, path) is not None

    rng = random.Random(9309)
    alphabet = "ab/*$?."
    for _ in range(20000):
        pattern = "".join(rng.choice(alphabet) for _ in range(rng.randint(0, 9)))
        path = "".join(rng.choice("ab/?.") for _ in range(rng.randint(0, 12)))
        assert access_profile._path_matches(pattern, path) == reference(pattern, path), (pattern, path)
    for pattern, path, expected in (("/api/", "/api/v2/status.json", True), ("/api/", "/api", False), ("/*/status.json$", "/api/v2/status.json", True),
                                    ("/status.json$", "/status.json?x", False), ("/a*b*c", "/a1b2c3", True), ("/a*b*c$", "/a1b2c3", False),
                                    ("*", "/anything", True), ("/", "/", True), ("/x$", "/x", True), ("", "/x", False)):
        assert access_profile._path_matches(pattern, path) is expected, (pattern, path)
    # A pattern with many `*` and a path that almost matches took a regular expression exponential time (0.35 s at
    # seven stars, doubling per star); here it is bounded by the lengths.
    path = "/" + "a" * 4000
    started = time.perf_counter()
    for pattern in ("/" + "*" * 20 + "Z", "/" + "a*" * 400 + "Z", "/" + "*a" * 300 + "Z$", "/" + "*" * 300 + "a" * 3000 + "Z"):
        assert access_profile._path_matches(pattern, path) is False
    assert time.perf_counter() - started < 0.1
    # The root decision of the declared-policy layer (D48) uses the same matcher: its answers do not move.
    assert access_profile.parse_robots(b"User-agent: *\nDisallow: /*\n")["wildcard_root_allowed"] is False
    assert access_profile.parse_robots(b"User-agent: *\nDisallow: /\nAllow: /$\n")["wildcard_root_allowed"] is True
    assert access_profile.parse_robots(b"User-agent: *\nDisallow: /private\n")["wildcard_root_allowed"] is True


# --- no other code may request a feed (parsed, with the allowed places listed) --------------------------------------------

# Every place in ahn/ that makes or starts an outgoing request or reads a public file, as (module, function, call) with
# its count. A new place, or a changed count, fails this test: it is then decided whether the request is one a robots.txt
# gate must precede (a status feed: add the gate and list it here) or one to another service (list it as such).
REQUEST_SITES = {
    # The declared-policy layer's own reads of /robots.txt and /llms.txt of a looked-up apex host.
    ("access_profile.py", "build_profile", "ref:fetch_public_file"): 1,
    ("access_profile.py", "fetch_public_file", "HTTPSConnectionPool"): 1,
    ("access_profile.py", "fetch_public_file", "fetch_public_file"): 1,
    ("access_profile.py", "fetch_public_file", "ref:fetch_public_file"): 1,
    ("access_profile.py", "fetch_public_file", "request"): 1,
    ("access_profile.py", "get_profile", "ref:fetch_public_file"): 1,
    ("access_profile.py", "read_public_file", "fetch_public_file"): 1,
    ("access_profile.py", "read_public_file", "ref:fetch_public_file"): 1,
    ("background_reads.py", "_paced_reader", "read_profile_file"): 1,
    ("background_reads.py", "read_profile_file", "fetch_public_file"): 1,
    # D59: a dict lookup of a parsed Server Card's remote "url" (the scan's url-like heuristic), not a request. The
    # standard-file reads themselves go through get_profile's reader above.
    ("site_declared.py", "_card_remotes", "get"): 1,
    # The three entry points that reach the feed readers; see test_only_gated_code_reaches_a_feed for what stands before them.
    ("background_reads.py", "_run", "refresh_feed"): 1,
    ("background_reads.py", "discover", "discover_source"): 1,
    # The robots.txt gate's own read: always /robots.txt of the feed host.
    ("feed_robots.py", "__init__", "ref:fetch_public_file"): 1,
    ("feed_robots.py", "decide", "fetch"): 1,
    # Other services: the hosting provider's API, the government lists' publishers, this service's own API.
    ("bandwidth_guard.py", "_is_service_suspended", "get"): 1,
    ("bandwidth_guard.py", "_suspend_service", "post"): 1,
    ("bandwidth_guard.py", "run_guard", "get"): 1,
    ("gov_registry.py", "http_get", "HTTPSConnectionPool"): 1,
    ("gov_registry.py", "http_get", "urlopen"): 1,
    ("main.py", "create_app.limit_body_and_measure", "stream"): 1,  # reading the incoming request body
    ("mcp_adapter.py", "call_api", "request"): 1,
    ("platform_metrics.py", "collect_render_metrics", "get"): 1,
    ("compression.py", "_release", "send"): 2,  # ASGI send: this service's own response, not a request
    ("compression.py", "_send_unchanged", "send"): 2,
    ("compression.py", "handle", "send"): 6,
    # The status-feed readers themselves.
    ("status_scope.py", "read_scope", "fetch"): 1,  # the two scope lists, through the fetcher a gated feed read passes in
    ("status_refresh.py", "_paced.call", "fetch"): 1,
    ("gov_registry.py", "refresh_one", "fetch"): 3,  # the government lists' publishers, through an injected fetcher
    ("status_refresh.py", "_get_capped", "stream"): 1,
    ("status_refresh.py", "_pool_get", "request"): 1,
    ("status_refresh.py", "_read_and_store", "_read_source"): 2,  # the second: the page's Atom history where only the API is disallowed (D52)
    ("status_refresh.py", "_read_discovered_statuspage", "HTTPSConnectionPool"): 1,
    ("status_refresh.py", "_read_discovered_statuspage", "_pool_get"): 2,
    ("status_refresh.py", "_read_source", "_get_capped"): 2,
    ("status_refresh.py", "_read_source", "_read_discovered_statuspage"): 1,
    ("status_refresh.py", "discover_source", "_read_discovered_statuspage"): 1,
}
_CALLS = {"fetch_public_file", "read_public_file", "HTTPSConnectionPool", "HTTPConnectionPool", "PoolManager", "_read_source", "_get_capped",
          "_pool_get", "urlopen", "stream", "request", "http_get", "read_profile_file", "refresh_feed", "discover_source",
          "_read_discovered_statuspage", "create_connection", "fetch", "send"}
_VERBS = {"get", "post", "head", "put", "patch", "delete", "send"}
_ROOTS = {"client", "httpx", "requests", "urllib3", "urllib", "session", "pool", "http", "remote", "aiohttp"}
_REFS = {"fetch_public_file", "read_public_file"}


def request_sites(directory):
    import ast
    import pathlib
    from collections import Counter

    def root(node):
        while isinstance(node, ast.Attribute):
            node = node.value
        return node.id if isinstance(node, ast.Name) else None

    found = Counter()
    fetch_args = []
    for path in sorted(pathlib.Path(directory).rglob("*.py")):
        def visit(node, scope):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                scope = f"{scope}.{node.name}" if scope else node.name
            if isinstance(node, ast.Call):
                f = node.func
                name = f.id if isinstance(f, ast.Name) else f.attr if isinstance(f, ast.Attribute) else ""
                first = node.args[0] if node.args else None
                url_like = (isinstance(first, ast.Constant) and str(first.value).startswith("http")) or (
                    first is not None and not isinstance(first, ast.Constant) and "url" in ast.unparse(first).lower())
                if name in _CALLS or (isinstance(f, ast.Attribute) and name in _VERBS and (root(f.value) in _ROOTS or url_like)):
                    found[(path.name, scope, name)] += 1
                    if name == "fetch" and path.name == "feed_robots.py":
                        fetch_args.append(node.args[1] if len(node.args) > 1 else None)
            elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load) and node.id in _REFS:
                found[(path.name, scope, "ref:" + node.id)] += 1
            for child in ast.iter_child_nodes(node):
                visit(child, scope)

        visit(ast.parse(path.read_text(encoding="utf-8")), "")
    return found, fetch_args


def test_every_request_site_in_the_service_is_known():
    import ast
    import pathlib

    found, fetch_args = request_sites(pathlib.Path(status_refresh.__file__).parent)
    assert dict(found) == REQUEST_SITES
    # The gate reads /robots.txt and nothing else from a feed host.
    assert len(fetch_args) == 1 and isinstance(fetch_args[0], ast.Constant) and fetch_args[0].value == "/robots.txt"


def test_the_scan_sees_a_gateless_read_a_direct_request_and_a_foreign_path(tmp_path):
    # The ways around the gate this test exists to catch, each as a one-function module.
    for body in ("def sneaky(source, client):\n    return _read_source(source, client)\n",
                 "def sneaky(source, client):\n    with client.stream('GET', source.api_url) as r:\n        return r.read()\n",
                 "def sneaky(host):\n    return fetch_public_file(host, '/history.atom', cap_bytes=1)\n",
                 "def sneaky(c):\n    return c.stream('GET', 'https://x.example/api')\n",
                 "def sneaky(c, source):\n    return c.get(source.api_url)\n",
                 "def sneaky(c):\n    return c.get('https://x.example/y')\n"):
        (tmp_path / "background_reads.py").write_text(body, encoding="utf-8")
        found, _ = request_sites(tmp_path)
        assert dict(found) and set(found) - set(REQUEST_SITES), body


def test_only_gated_code_reaches_a_feed():
    import inspect
    import pathlib

    # Inside the listed places the order matters: the gate is asked before the first request, and without one nothing is looked up.
    read = inspect.getsource(status_refresh._read_and_store)
    assert read.index(".decide(") < read.index("_read_source(") and "make_gate(settings)" in read
    discovered = inspect.getsource(status_refresh._read_discovered_statuspage)
    assert discovered.index("gate.decide(") < discovered.index("HTTPSConnectionPool(")
    assert "if gate is None:\n        return None" in inspect.getsource(status_refresh.discover_source).replace("\r\n", "\n")
    assert "_read_and_store(" in inspect.getsource(status_refresh.refresh_one)
    assert "_read_and_store(" in inspect.getsource(status_refresh.refresh_batch)
    text = pathlib.Path(status_refresh.__file__).with_name("background_reads.py").read_text(encoding="utf-8")
    assert "found = discover(site_host, digest, settings=settings)" in text and "status_refresh.make_gate(settings)" in text


# --- modules that may reach the network at all, and every HTTP-verb call inside them -----------------------------------------

# Modules that import a library able to open a connection, and which. A new such import anywhere else fails the test;
# so does any `from <library> import ...` (a bare imported function such as `get` would hide its calls from the scan).
NETWORK_LIBRARIES = {"httpx", "urllib3", "requests", "aiohttp", "socket", "http"}
LIBRARY_MODULES = {
    "access_profile.py": {"socket", "urllib3"},
    "bandwidth_guard.py": {"httpx"},
    "gov_registry.py": {"urllib3"},
    "mcp_adapter.py": {"httpx"},
    "platform_metrics.py": {"httpx"},
    "status_refresh.py": {"httpx", "socket", "urllib3"},
}
# Inside those modules every `.get/.post/.send/.stream/.request/...` call is counted whatever its receiver (a client
# reached through `self.client`, an alias, a variable), so most entries below are dictionary lookups, not requests; a new
# or changed count fails and has to be looked at.
VERB_SITES = {
    ("access_profile.py", "_as_profile", "get"): 3,
    ("access_profile.py", "_merge_use", "get"): 1,
    ("access_profile.py", "_says_no_ai_input", "get"): 2,
    ("access_profile.py", "_signals_shape", "get"): 4,
    ("access_profile.py", "_usage_shape", "get"): 3,
    ("access_profile.py", "_www_redirect_target", "get"): 1,
    ("access_profile.py", "ai_input_declared_no", "get"): 2,
    ("access_profile.py", "content_use_view", "get"): 4,
    ("access_profile.py", "fetch_public_file", "get"): 3,
    ("access_profile.py", "fetch_public_file", "request"): 1,
    ("access_profile.py", "get_profile", "get"): 4,
    ("access_profile.py", "parse_content_use", "get"): 4,
    ("bandwidth_guard.py", "_is_service_suspended", "get"): 3,
    ("bandwidth_guard.py", "_suspend_service", "post"): 1,
    ("bandwidth_guard.py", "_threshold_env", "get"): 1,
    ("bandwidth_guard.py", "main", "get"): 3,
    ("bandwidth_guard.py", "parse_bandwidth", "get"): 8,
    ("bandwidth_guard.py", "run_guard", "get"): 1,
    ("gov_registry.py", "_as_of", "get"): 1,
    ("gov_registry.py", "enabled", "get"): 1,
    ("gov_registry.py", "http_get", "get"): 2,
    ("gov_registry.py", "main", "get"): 1,
    ("gov_registry.py", "newest_uk_csv", "get"): 4,
    ("mcp_adapter.py", "", "get"): 4,
    ("mcp_adapter.py", "api_base", "get"): 1,
    ("mcp_adapter.py", "call_api", "get"): 5,
    ("mcp_adapter.py", "call_api", "request"): 1,
    ("mcp_adapter.py", "main", "get"): 2,
    ("mcp_adapter.py", "outer_request_headers", "get"): 3,
    ("platform_metrics.py", "collect_render_metrics", "get"): 1,
    ("platform_metrics.py", "latest_sample", "get"): 9,
    ("status_refresh.py", "_due_groups", "get"): 2,
    ("status_refresh.py", "_get_capped", "stream"): 1,
    ("status_refresh.py", "_pool_get", "request"): 1,
    ("status_refresh.py", "_read_discovered_statuspage", "get"): 3,
    ("status_refresh.py", "_store_history", "get"): 1,  # digest.get, a dictionary lookup
    ("status_refresh.py", "_store_read", "get"): 9,
    ("status_refresh.py", "main", "get"): 1,
    ("status_refresh.py", "parse_betterstack", "get"): 4,
    ("status_refresh.py", "parse_google_gmail", "get"): 7,
    ("status_refresh.py", "parse_instatus", "get"): 3,
    ("status_refresh.py", "parse_status", "get"): 4,
    ("status_refresh.py", "refresh_batch", "get"): 1,
    ("status_refresh.py", "refresh_one", "get"): 1,
    ("status_refresh.py", "register_source", "get"): 1,
    ("status_refresh.py", "sync_curated_sources", "get"): 1,
}
_ALL_VERBS = {"get", "send", "stream", "request", "post", "head", "put", "patch", "delete"}


def library_imports(directory):
    import ast
    import pathlib

    imported, from_imports = {}, []
    for path in sorted(pathlib.Path(directory).rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            names = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                names = [node.module]
                if node.module.split(".")[0] in NETWORK_LIBRARIES or node.module in {"urllib.request", "http.client"}:
                    from_imports.append((path.name, node.module))
            for name in names:
                top = name.split(".")[0]
                if top in NETWORK_LIBRARIES or name in {"urllib.request", "http.client"}:
                    imported.setdefault(path.name, set()).add("http" if name == "http.client" else "urllib3" if name == "urllib.request" else top)
    return imported, from_imports


def verb_sites(directory, modules):
    import ast
    import pathlib
    from collections import Counter

    found = Counter()
    for name in modules:
        def visit(node, scope):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                scope = f"{scope}.{node.name}" if scope else node.name
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in _ALL_VERBS:
                found[(name, scope, node.func.attr)] += 1
            for child in ast.iter_child_nodes(node):
                visit(child, scope)

        visit(ast.parse((pathlib.Path(directory) / name).read_text(encoding="utf-8")), "")
    return found


def test_only_known_modules_import_a_network_library_and_count_every_verb_call_in_them():
    import pathlib

    directory = pathlib.Path(status_refresh.__file__).parent
    imported, from_imports = library_imports(directory)
    assert imported == {k: set(v) for k, v in LIBRARY_MODULES.items()}
    assert from_imports == []
    assert dict(verb_sites(directory, LIBRARY_MODULES)) == VERB_SITES


def test_the_import_and_verb_scans_see_the_patterns_a_narrower_scan_missed(tmp_path):
    cases = {
        "background_reads.py": ["from httpx import get as g\n\ndef f(u):\n    return g(u)\n",
                                "import httpx\n\ndef f(feed):\n    return httpx.Client().get(feed)\n",
                                "import urllib.request\n\ndef f(u):\n    return urllib.request.urlopen(u)\n",
                                "def f(c, req):\n    return c.send(req)\n"],
    }
    for name, bodies in cases.items():
        for body in bodies:
            (tmp_path / name).write_text(body, encoding="utf-8")
            imported, from_imports = library_imports(tmp_path)
            found, _ = request_sites(tmp_path)
            assert name in imported or from_imports or set(found) - set(REQUEST_SITES), body
    # Inside a module that may import a library, a call through any receiver changes the count.
    (tmp_path / "status_refresh.py").write_text("import httpx\n\nclass A:\n    def f(self, feed):\n        return self.client.get(feed)\n"
                                                "def g(c, feed):\n    target = feed\n    return c.get(target)\n", encoding="utf-8")
    counts = verb_sites(tmp_path, ["status_refresh.py"])
    assert counts[("status_refresh.py", "f", "get")] == 1 and counts[("status_refresh.py", "g", "get")] == 1
    assert set(counts) - set(VERB_SITES)
