from ahn.access_profile import AGENT_SOURCES, AGENT_USER_AGENTS, LLMS_CAP, ROBOTS_CAP, UNCONFIRMED_AGENT_TOKENS, build_profile, parse_robots
from ahn.official_paths import OFFICIAL_PATHS, official_paths
from ahn.triage import AGENT_KINDS_FOR_BLOCK, declared_block, recommendation


def _rule(parsed: dict, agent: str) -> dict:
    return next(item for item in parsed["rules"] if item["agent"] == agent)


def test_empty_file_allows_everyone_without_explicit_groups():
    parsed = parse_robots("")
    assert parsed["wildcard_root_allowed"] is True
    assert parsed["sitemap"] is False
    assert parsed["agents_blocked"] == [] and parsed["agents_allowed"] == []
    assert len(parsed["rules"]) == len(AGENT_USER_AGENTS)
    assert all(r["root_allowed"] and not r["explicit_group"] and r["disallow_count"] == 0 for r in parsed["rules"])


def test_wildcard_disallow_all_blocks_root_without_listing_agents_as_explicit():
    parsed = parse_robots("User-agent: *\nDisallow: /\n")
    assert parsed["wildcard_root_allowed"] is False
    assert all(not r["root_allowed"] and not r["explicit_group"] and r["disallow_count"] == 1 for r in parsed["rules"])
    assert parsed["agents_blocked"] == []


def test_explicit_training_crawler_groups_with_search_crawler_allowed():
    text = "User-agent: GPTBot\nDisallow: /\n\nUser-agent: ClaudeBot\nDisallow: /\n\nUser-agent: Googlebot\nAllow: /\n\nUser-agent: *\nDisallow: /private/\nSitemap: https://example.com/sitemap.xml\n"
    parsed = parse_robots(text)
    assert parsed["agents_blocked"] == ["GPTBot", "ClaudeBot"]
    assert parsed["agents_allowed"] == ["Googlebot"]
    assert parsed["sitemap"] is True
    assert parsed["wildcard_root_allowed"] is True
    gpt = _rule(parsed, "GPTBot")
    assert gpt == {"agent": "GPTBot", "kind": "training_crawler", "explicit_group": True, "root_allowed": False, "disallow_count": 1,
                   "crawl_delay": None, "disallow_paths": ["/"]}
    assert parsed["sitemaps"] == ["https://example.com/sitemap.xml"] and parsed["wildcard_disallow_paths"] == ["/private/"]
    chat = _rule(parsed, "ChatGPT-User")
    assert chat["explicit_group"] is False and chat["root_allowed"] is True and chat["disallow_count"] == 1


def test_allow_disallow_precedence_is_longest_match_then_allow():
    allowed = parse_robots("User-agent: GPTBot\nDisallow: /\nAllow: /$\n")
    assert _rule(allowed, "GPTBot")["root_allowed"] is True
    tie = parse_robots("User-agent: GPTBot\nDisallow: /\nAllow: /\n")
    assert _rule(tie, "GPTBot")["root_allowed"] is True
    specific_block = parse_robots("User-agent: GPTBot\nAllow: /\nDisallow: /*\n")
    assert _rule(specific_block, "GPTBot")["root_allowed"] is False
    empty_disallow = parse_robots("User-agent: GPTBot\nDisallow:\n")
    assert _rule(empty_disallow, "GPTBot") == {"agent": "GPTBot", "kind": "training_crawler", "explicit_group": True, "root_allowed": True, "disallow_count": 0,
                                               "crawl_delay": None, "disallow_paths": []}
    deep_only = parse_robots("User-agent: GPTBot\nDisallow: /admin\nDisallow: /tmp/\n")
    assert _rule(deep_only, "GPTBot")["root_allowed"] is True and _rule(deep_only, "GPTBot")["disallow_count"] == 2


# The 22 tokens the table held before it was checked against vendor documentation, with the kind each had then.
PREVIOUS_KINDS = {
    "ChatGPT-User": "user_agent", "Claude-User": "user_agent", "Perplexity-User": "user_agent", "Meta-ExternalFetcher": "user_agent",
    "MistralAI-User": "user_agent", "OAI-SearchBot": "search_crawler", "Claude-SearchBot": "search_crawler", "PerplexityBot": "search_crawler",
    "Googlebot": "search_crawler", "Bingbot": "search_crawler", "DuckAssistBot": "search_crawler", "Amazonbot": "search_crawler",
    "GPTBot": "training_crawler", "ClaudeBot": "training_crawler", "anthropic-ai": "training_crawler", "Google-Extended": "training_crawler",
    "Applebot-Extended": "training_crawler", "Meta-ExternalAgent": "training_crawler", "CCBot": "training_crawler",
    "Bytespider": "training_crawler", "cohere-ai": "training_crawler", "Diffbot": "training_crawler",
}
# Added from vendor documentation; each is named by its own robots.txt group in the test below.
ADDED_KINDS = {
    "Google-Agent": "user_agent", "Google-GeminiNotebook": "user_agent", "Amzn-User": "user_agent", "Diffbot-User": "user_agent",
    "Google-Pinpoint": "user_agent", "Google-Read-Aloud": "user_agent", "ExaSearchBot": "search_crawler",
    "DuckDuckBot": "search_crawler", "Amzn-SearchBot": "search_crawler", "Meta-WebIndexer": "search_crawler",
    "MistralAI-Index": "search_crawler", "Applebot": "search_crawler", "MistralAI-Training": "training_crawler",
}
# The only kind changes among the previous tokens (vendor documentation says so); everything else keeps its kind.
RECLASSIFIED = {"Diffbot": "search_crawler", "DuckAssistBot": "user_agent"}


def test_token_table_is_unique_sourced_and_only_the_documented_kinds_moved():
    tokens = [token for token, _ in AGENT_USER_AGENTS]
    assert len({token.lower() for token in tokens}) == len(tokens)
    assert {kind for _, kind in AGENT_USER_AGENTS} == {"user_agent", "search_crawler", "training_crawler"}
    assert dict(AGENT_USER_AGENTS) == {**PREVIOUS_KINDS, **RECLASSIFIED, **ADDED_KINDS}
    assert set(AGENT_SOURCES) | UNCONFIRMED_AGENT_TOKENS == set(tokens) and not set(AGENT_SOURCES) & UNCONFIRMED_AGENT_TOKENS
    assert all(url.startswith("https://") for url in AGENT_SOURCES.values())


def test_every_added_or_reclassified_token_is_named_by_its_own_group():
    for token, kind in {**ADDED_KINDS, **RECLASSIFIED}.items():
        parsed = parse_robots(f"User-agent: {token}\nDisallow: /\n\nUser-agent: *\nAllow: /\n")
        assert _rule(parsed, token)["kind"] == kind and _rule(parsed, token)["explicit_group"] is True
        assert token in parsed["agents_blocked"]
        profile = {"robots_status": "ok", "robots": parsed}
        blocks = kind in AGENT_KINDS_FOR_BLOCK
        assert (token in declared_block(profile)) == blocks
        assert (recommendation("site_access", None, profile, None)["action"] == "stop_declared_block") == blocks


def test_training_only_blocks_stay_non_blocking_including_the_new_training_token():
    names = ("GPTBot", "ClaudeBot", "Google-Extended", "Applebot-Extended", "Meta-ExternalAgent", "MistralAI-Training", "CCBot", "Bytespider")
    parsed = parse_robots("".join(f"User-agent: {name}\nDisallow: /\n\n" for name in names) + "User-agent: *\nAllow: /\n")
    assert parsed["agents_blocked"] == [token for token, _ in AGENT_USER_AGENTS if token in names]
    profile = {"robots_status": "ok", "robots": parsed}
    assert declared_block(profile) == []
    assert recommendation("site_access", None, profile, None)["action"] == "proceed"


def test_new_spellings_do_not_cross_match_sibling_tokens():
    parsed = parse_robots("User-agent: MistralAI-Training\nDisallow: /\n\nUser-agent: Amzn-SearchBot\nDisallow: /\n\nUser-agent: Google-GeminiNotebook\nDisallow: /\n")
    assert parsed["agents_blocked"] == ["Google-GeminiNotebook", "Amzn-SearchBot", "MistralAI-Training"]
    assert all(not _rule(parsed, token)["explicit_group"] for token in ("MistralAI-User", "Amzn-User", "Google-Agent", "Googlebot"))
    assert parse_robots("User-agent: Applebot-Extended\nDisallow: /\n")["agents_blocked"] == ["Applebot-Extended"]


def test_bom_crlf_comments_and_junk_are_tolerated():
    text = "﻿# robots\r\nUSER-AGENT: ccbot # training\r\n  disallow : /  \r\nthis line is junk\r\nCrawl-delay: 10\r\nUser-Agent: *\r\nAllow: /\r\n"
    parsed = parse_robots(text)
    assert parsed["agents_blocked"] == ["CCBot"]
    assert parsed["wildcard_root_allowed"] is True
    assert parse_robots(b"User-agent: Bytespider\r\nDisallow: /\xff\xfe\r\n")["rules"]
    assert parse_robots(b"\xef\xbb\xbfUser-agent: Bytespider\nDisallow: /\n")["agents_blocked"] == ["Bytespider"]


def test_group_with_multiple_user_agent_lines_shares_its_rules():
    text = "User-agent: anthropic-ai\nUser-agent: Google-Extended\nUser-agent: Applebot-Extended\nDisallow: /\n\nUser-agent: *\nAllow: /\n"
    parsed = parse_robots(text)
    assert parsed["agents_blocked"] == ["anthropic-ai", "Google-Extended", "Applebot-Extended"]
    assert _rule(parsed, "Googlebot")["explicit_group"] is False
    assert _rule(parsed, "Googlebot")["root_allowed"] is True


def test_token_matching_is_case_insensitive_and_prefix_aware():
    parsed = parse_robots("user-agent: gptbot/1.2\ndisallow: /\n\nUser-agent: CLAUDE\nDisallow: /\n")
    assert _rule(parsed, "GPTBot")["explicit_group"] is True and _rule(parsed, "GPTBot")["root_allowed"] is False
    for token in ("Claude-User", "Claude-SearchBot", "ClaudeBot"):
        assert _rule(parsed, token)["explicit_group"] is True and _rule(parsed, token)["root_allowed"] is False
    assert _rule(parsed, "anthropic-ai")["explicit_group"] is False
    longer = parse_robots("User-agent: Googlebot-News\nDisallow: /\n")
    assert _rule(longer, "Googlebot")["explicit_group"] is False


def test_crawl_delay_sitemaps_and_path_samples_survive_the_summary():
    text = (
        "User-agent: *\nCrawl-delay: 10\nDisallow: /search\nDisallow: /cart\nDisallow: /account\nDisallow: /a\nDisallow: /b\nDisallow: /c\nDisallow: /d\n"
        "Sitemap: https://shop.example/sitemap.xml\nSitemap: https://shop.example/sitemap-products.xml\nSitemap: not-a-url\n\n"
        "User-agent: ChatGPT-User\nCrawl-delay: 2.5\nDisallow: /checkout\n\nUser-agent: GPTBot\nCrawl-delay: nonsense\nDisallow: /\n"
    )
    parsed = parse_robots(text)
    assert parsed["sitemaps"] == ["https://shop.example/sitemap.xml", "https://shop.example/sitemap-products.xml"] and parsed["sitemap"] is True
    assert parsed["wildcard_crawl_delay"] == 10 and parsed["wildcard_root_allowed"] is True
    assert parsed["wildcard_disallow_paths"] == ["/search", "/cart", "/account", "/a", "/b", "/c"]  # capped sample, file order
    chat = _rule(parsed, "ChatGPT-User")
    assert chat["crawl_delay"] == 2.5 and chat["disallow_paths"] == ["/checkout"] and chat["root_allowed"] is True
    gpt = _rule(parsed, "GPTBot")
    assert gpt["crawl_delay"] is None and gpt["disallow_paths"] == ["/"] and gpt["root_allowed"] is False
    claude = _rule(parsed, "Claude-User")  # falls back to the wildcard group, including its delay and samples
    assert claude["crawl_delay"] == 10 and claude["disallow_paths"][:2] == ["/search", "/cart"]


def test_html_body_is_invalid_not_a_permissive_policy(monkeypatch):
    from ahn import access_profile

    class Response:
        status = 200
        headers = {}
        def read(self, _limit):
            return b"\n<!DOCTYPE html><html><body>Not found</body></html>"
        def release_conn(self):
            pass
    class Pool:
        def __init__(self, *_a, **_kw):
            pass
        def request(self, *_a, **_kw):
            return Response()
        def close(self):
            pass
    monkeypatch.setattr(access_profile, "_resolve", lambda host, timeout: {"93.184.216.34"})
    monkeypatch.setattr(access_profile.urllib3, "HTTPSConnectionPool", Pool)
    assert access_profile.fetch_public_file("example.com", "/robots.txt", cap_bytes=1000) == ("invalid", None, "html_body")
    profile = build_profile("example.com", reader=lambda h, p, *, cap_bytes, **_: ("invalid", None, "html_body"))
    assert profile["robots_status"] == "invalid" and profile["robots"] is None and profile["llms_txt_status"] == "absent"
    assert profile["llms_detail"] == "html_body"


def test_rules_before_any_group_and_oversized_input_are_capped_not_fatal():
    assert parse_robots("Disallow: /\nUser-agent: *\nAllow: /\n")["wildcard_root_allowed"] is True
    huge = "User-agent: *\n" + "Disallow: /x\n" * 5000 + "User-agent: GPTBot\nDisallow: /\n"
    parsed = parse_robots(huge)
    assert _rule(parsed, "GPTBot")["explicit_group"] is False
    padded = "# " + "a" * (ROBOTS_CAP + 10) + "\nUser-agent: GPTBot\nDisallow: /\n"
    assert parse_robots(padded)["agents_blocked"] == []


def _stub(robots=("ok", b"User-agent: GPTBot\nDisallow: /\n"), llms=("absent", None)):
    calls = []

    def reader(site_host, path, *, cap_bytes, **_):
        calls.append((site_host, path, cap_bytes))
        return robots if path == "/robots.txt" else llms

    reader.calls = calls
    return reader


def test_build_profile_ok_parses_robots_and_reports_llms_presence():
    reader = _stub(llms=("ok", b"# Example\n"))
    profile = build_profile("example.com", reader=reader)
    assert profile["robots_status"] == "ok"
    assert profile["robots"]["agents_blocked"] == ["GPTBot"]
    assert profile["llms_txt_status"] == "present"
    assert profile["source_urls"] == {"robots_txt": "https://example.com/robots.txt", "llms_txt": "https://example.com/llms.txt"}
    assert reader.calls == [("example.com", "/robots.txt", ROBOTS_CAP), ("example.com", "/llms.txt", LLMS_CAP)]


def test_build_profile_non_ok_statuses_pass_through_with_no_robots_summary():
    for status in ("absent", "redirected", "too_large", "error", "not_public", "refused"):
        profile = build_profile("example.com", reader=_stub(robots=(status, None), llms=(status, None)))
        assert profile["robots_status"] == status and profile["robots"] is None
        assert profile["llms_txt_status"] == ("present" if status == "too_large" else status)
        assert profile["robots_detail"] is None and profile["llms_detail"] is None


def test_build_profile_falls_back_to_www_when_apex_refuses_or_fails():
    calls = []

    def reader(site_host, path, *, cap_bytes, **options):
        calls.append((site_host, path, options.get("_hop", 0)))
        if site_host == "example.com":
            return ("refused", None, "http_503")
        return ("ok", b"User-agent: ChatGPT-User\nDisallow: /\n", "http_200") if path == "/robots.txt" else ("absent", None, "http_404")

    profile = build_profile("example.com", reader=reader)
    assert profile["robots_status"] == "ok" and profile["robots"]["agents_blocked"] == ["ChatGPT-User"]
    assert profile["robots_detail"] == "http_200" and profile["source_urls"]["robots_txt"] == "https://www.example.com/robots.txt"
    assert profile["read_host"] == "www.example.com" and profile["source_urls"]["llms_txt"] == "https://www.example.com/llms.txt"
    # Reads on the www alias carry _hop=1: the apex->www step already spent the single redirect hop.
    assert calls == [("example.com", "/robots.txt", 0), ("www.example.com", "/robots.txt", 1), ("www.example.com", "/llms.txt", 1)]
    # When www refuses as well, the apex refusal stands and carries its detail.
    refused_everywhere = build_profile("example.com", reader=lambda h, p, *, cap_bytes, **_: ("refused", None, "http_403"))
    assert refused_everywhere["robots_status"] == "refused" and refused_everywhere["robots_detail"] == "http_403"
    assert refused_everywhere["read_host"] == "example.com" and refused_everywhere["source_urls"]["robots_txt"] == "https://example.com/robots.txt"
    # A www host is never prefixed twice.
    only_once = []
    build_profile("www.example.com", reader=lambda h, p, *, cap_bytes, **_: (only_once.append(h), ("error", None, "connect_error"))[1])
    assert only_once == ["www.example.com", "www.example.com"]


def test_fetch_public_file_maps_http_statuses(monkeypatch):
    from ahn import access_profile

    class Response:
        def __init__(self, status, headers=None, body=b""):
            self.status, self.headers, self._body = status, headers or {}, body
        def read(self, _limit):
            return self._body
        def release_conn(self):
            pass

    def pool_for(status, body=b""):
        class Pool:
            def __init__(self, *_a, **_kw):
                pass
            def request(self, *_a, **_kw):
                return Response(status, body=body)
            def close(self):
                pass
        return Pool

    monkeypatch.setattr(access_profile, "_resolve", lambda host, timeout: {"93.184.216.34"})
    for status, expected in ((503, ("refused", None, "http_503")), (403, ("refused", None, "http_403")), (429, ("refused", None, "http_429")),
                             (404, ("absent", None, "http_404")), (500, ("error", None, "http_500")), (200, ("ok", b"x", "http_200"))):
        monkeypatch.setattr(access_profile.urllib3, "HTTPSConnectionPool", pool_for(status, b"x"))
        assert access_profile.fetch_public_file("example.com", "/robots.txt", cap_bytes=10) == expected
    assert access_profile.read_public_file("example.com", "/robots.txt", cap_bytes=10) == ("ok", b"x")


def test_official_paths_hit_subdomain_and_miss():
    hit = official_paths("github.com")
    assert hit["name"] == "GitHub" and hit["api_docs"].startswith("https://docs.github.com/")
    assert set(hit) == {"name", "api_docs", "developer_portal", "note", "access"}  # github.com has an access bundle
    assert official_paths("API.GitHub.com.") == hit and hit is not OFFICIAL_PATHS["github.com"]
    assert official_paths("example.invalid") is None
    assert official_paths("") is None


def test_official_paths_table_shape():
    assert len(OFFICIAL_PATHS) >= 50
    for host, entry in OFFICIAL_PATHS.items():
        assert host == host.lower().strip(".") and "/" not in host
        assert set(entry) == {"name", "api_docs", "developer_portal", "note"} and entry["name"]
        assert entry["api_docs"] or entry["developer_portal"] or entry["note"]
        for url in (entry["api_docs"], entry["developer_portal"]):
            assert url is None or url.startswith("https://")


def test_read_public_file_rejects_bad_input_and_contains_resolver_failures(monkeypatch):
    import socket
    import threading
    import time

    from ahn import access_profile

    assert access_profile.read_public_file("not a host", "/robots.txt", cap_bytes=10) == ("error", None)
    assert access_profile.read_public_file("example.com", "robots.txt", cap_bytes=10) == ("error", None)
    assert access_profile.read_public_file("example.com", "//evil", cap_bytes=10) == ("error", None)

    def private(*_, **__):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.5", 443)), (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))]

    monkeypatch.setattr(access_profile.socket, "getaddrinfo", private)
    assert access_profile.read_public_file("example.com", "/robots.txt", cap_bytes=10) == ("not_public", None)

    def failing(*_, **__):
        raise socket.gaierror("no such host")

    monkeypatch.setattr(access_profile.socket, "getaddrinfo", failing)
    assert access_profile.read_public_file("example.com", "/robots.txt", cap_bytes=10) == ("error", None)

    release = threading.Event()

    def slow(*_, **__):
        release.wait(10)  # stands in for a stalled resolver; released below so no thread outlives the test
        return []

    original = access_profile._resolve
    monkeypatch.setattr(access_profile.socket, "getaddrinfo", slow)
    monkeypatch.setattr(access_profile, "_resolve", lambda host, timeout: original(host, 0.05))
    started = time.monotonic()
    try:
        assert access_profile.read_public_file("example.com", "/robots.txt", cap_bytes=10) == ("error", None)
        # The resolver would block for 10 s; the 0.05 s timeout ends the read. The bound is far above the timeout
        # (a loaded machine delays thread start-up) and far below the stall, so only a read that waited fails it.
        assert time.monotonic() - started < 5
    finally:
        release.set()
