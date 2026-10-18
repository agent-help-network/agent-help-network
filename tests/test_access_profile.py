from ahn.access_profile import AGENT_USER_AGENTS, LLMS_CAP, ROBOTS_CAP, build_profile, parse_robots
from ahn.official_paths import OFFICIAL_PATHS, official_paths


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
    assert gpt == {"agent": "GPTBot", "kind": "training_crawler", "explicit_group": True, "root_allowed": False, "disallow_count": 1}
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
    assert _rule(empty_disallow, "GPTBot") == {"agent": "GPTBot", "kind": "training_crawler", "explicit_group": True, "root_allowed": True, "disallow_count": 0}
    deep_only = parse_robots("User-agent: GPTBot\nDisallow: /admin\nDisallow: /tmp/\n")
    assert _rule(deep_only, "GPTBot")["root_allowed"] is True and _rule(deep_only, "GPTBot")["disallow_count"] == 2


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
    for status in ("absent", "redirected", "too_large", "error", "not_public"):
        profile = build_profile("example.com", reader=_stub(robots=(status, None), llms=(status, None)))
        assert profile["robots_status"] == status and profile["robots"] is None
        assert profile["llms_txt_status"] == ("present" if status == "too_large" else status)


def test_official_paths_hit_subdomain_and_miss():
    hit = official_paths("github.com")
    assert hit["name"] == "GitHub" and hit["api_docs"].startswith("https://docs.github.com/")
    assert set(hit) == {"name", "api_docs", "developer_portal", "note"}
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

    def slow(*_, **__):
        time.sleep(0.5)
        return []

    original = access_profile._resolve
    monkeypatch.setattr(access_profile.socket, "getaddrinfo", slow)
    monkeypatch.setattr(access_profile, "_resolve", lambda host, timeout: original(host, 0.05))
    started = time.monotonic()
    assert access_profile.read_public_file("example.com", "/robots.txt", cap_bytes=10) == ("error", None)
    assert time.monotonic() - started < 0.4
