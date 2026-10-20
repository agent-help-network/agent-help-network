"""Group split of robots.txt (RFC 9309 section 2.1/2.2.4 and Google's robots.cc): only an allow/disallow line ends a
group's user-agent lines, every other line leaves the grouping alone, and no input makes the parser raise."""

import random

from ahn.access_profile import AGENT_USER_AGENTS, MAX_SITEMAPS, _applies, _root_allowed, parse_robots, split_groups


def _rule(parsed: dict, agent: str) -> dict:
    return next(item for item in parsed["rules"] if item["agent"] == agent)


def test_unknown_key_between_user_agent_lines_keeps_one_group():
    # The adversarial-review reproduction: Content-Signal used to close ChatGPT-User's group, which then had no rules.
    text = "User-agent: ChatGPT-User\nContent-Signal: ai-input=no\nUser-agent: Claude-User\nDisallow: /\n"
    parsed = parse_robots(text)
    assert parsed["agents_blocked"] == ["ChatGPT-User", "Claude-User"] and parsed["agents_allowed"] == []
    assert parse_robots(text.replace("Content-Signal: ai-input=no\n", ""))["agents_blocked"] == ["ChatGPT-User", "Claude-User"]
    groups, _ = split_groups(text)
    assert groups == [{"agents": ["chatgpt-user", "claude-user"], "rules": [("disallow", "/")], "crawl_delay": None,
                       "other": [("content-signal", "ai-input=no")]}]


def test_every_non_rule_key_between_user_agent_lines_is_ignored_for_grouping():
    # Google's ID_LineSyntax_Groups_OtherRules, plus the other records seen in real files.
    for line in ("Sitemap: https://example.com/sitemap.xml", "Invalid-Unknown-Line: unknown", "Crawl-delay: 10",
                 "Content-Usage: train-ai=n", "Host: example.com", "Clean-param: ref /", "Request-rate: 1/5",
                 "no colon on this line", "# a comment", ":", "   ", "Disalow: /typo"):
        text = f"User-agent: GPTBot\n{line}\nUser-agent: *\nDisallow: /\n"
        parsed = parse_robots(text)
        assert parsed["agents_blocked"] == ["GPTBot"], line
        assert len(split_groups(text)[0]) == 1, line


def test_crawl_delay_between_user_agent_lines_applies_to_the_whole_group():
    groups, _ = split_groups("User-agent: Bingbot\nCrawl-delay: 5\n\nUser-agent: GPTBot\nDisallow: /private\n")
    assert groups == [{"agents": ["bingbot", "gptbot"], "rules": [("disallow", "/private")], "crawl_delay": 5.0,
                       "other": [("crawl-delay", "5")]}]
    parsed = parse_robots("User-agent: Bingbot\nCrawl-delay: 5\n\nUser-agent: GPTBot\nDisallow: /private\n")
    assert _rule(parsed, "Bingbot")["crawl_delay"] == 5.0 and _rule(parsed, "Bingbot")["disallow_paths"] == ["/private"]


def test_several_user_agent_lines_share_one_group_and_a_rule_line_ends_it():
    text = ("User-agent: GPTBot\nUser-agent: ClaudeBot\n\nUser-agent: Claude-User\nDisallow: /\nAllow: /public/\n"
            "Crawl-delay: 3\nContent-Signal: search=yes\n"
            "User-agent: Googlebot\nAllow: /\n")
    groups, _ = split_groups(text)
    assert [group["agents"] for group in groups] == [["gptbot", "claudebot", "claude-user"], ["googlebot"]]
    assert groups[0]["rules"] == [("disallow", "/"), ("allow", "/public/")]
    assert groups[0]["crawl_delay"] == 3.0 and groups[0]["other"] == [("crawl-delay", "3"), ("content-signal", "search=yes")]
    parsed = parse_robots(text)
    assert parsed["agents_blocked"] == ["Claude-User", "GPTBot", "ClaudeBot"]
    assert parsed["agents_allowed"] == ["Googlebot"]


def test_rule_lines_before_the_first_group_are_ignored_and_blank_lines_never_split():
    text = "Disallow: /\nAllow: /x\nContent-Signal: ai-train=no\nCrawl-delay: 9\n\n\nUser-agent: GPTBot\n\n\nUser-agent: ClaudeBot\n\nDisallow: /\n"
    groups, _ = split_groups(text)
    assert groups == [{"agents": ["gptbot", "claudebot"], "rules": [("disallow", "/")], "crawl_delay": None, "other": []}]
    parsed = parse_robots(text)
    assert parsed["wildcard_root_allowed"] is True and parsed["agents_blocked"] == ["GPTBot", "ClaudeBot"]
    # Google's ID_LineSyntax_Groups: an allow line between groups belongs to the group before it.
    groups, _ = split_groups("User-agent: FooBot\nDisallow: /\n\n\nAllow: /w/\nUser-agent: BarBot\nDisallow: /\n")
    assert [group["rules"] for group in groups] == [[("disallow", "/"), ("allow", "/w/")], [("disallow", "/")]]


def test_sitemap_lines_anywhere_are_collected_and_never_split_a_group():
    lines = [f"Sitemap: https://example.com/s{i}.xml" for i in range(MAX_SITEMAPS + 2)]
    text = f"{lines[0]}\nUser-agent: ChatGPT-User\n{lines[1]}\nUser-agent: GPTBot\n{lines[2]}\nDisallow: /\n" + "\n".join(lines[3:]) + "\n"
    groups, sitemaps = split_groups(text)
    assert len(groups) == 1 and groups[0]["agents"] == ["chatgpt-user", "gptbot"] and groups[0]["other"] == []
    assert sitemaps == [line.split(": ", 1)[1] for line in lines[:MAX_SITEMAPS]]
    assert split_groups("Sitemap: /relative.xml\nSitemap: ftp://x.example/s\n")[1] == []


def test_odd_user_agent_values_never_raise_and_still_join_the_group():
    for value in ("/bot", "/", "//", " / ", "", "   ", "/1.0 GPTBot", "\t/x"):
        text = f"User-agent: {value}\nUser-agent: GPTBot\nDisallow: /\n"
        parsed = parse_robots(text)  # used to raise IndexError for values starting with "/"
        assert parsed["agents_blocked"] == ["GPTBot"], repr(value)
        groups, _ = split_groups(text)
        assert groups[0]["agents"] == ["gptbot"], repr(value)
    groups, _ = split_groups("User-agent: GPTBot/1.1 (+https://openai.com/gptbot)\nUser-agent: * extra\nDisallow: /\n")
    assert groups[0]["agents"] == ["gptbot", "*"]
    only_odd = parse_robots("User-agent: /bot\nDisallow: /\n")
    assert only_odd["agents_blocked"] == [] and only_odd["wildcard_root_allowed"] is True


def test_keys_and_agent_names_are_case_insensitive():
    text = "USER-AGENT: gptbot\nuser agent: CLAUDE-USER\nCONTENT-SIGNAL: ai-input=no\nUserAgent: ChatGPT-user\nDISALLOW: /\nSITEMAP: https://example.com/s.xml\n"
    groups, sitemaps = split_groups(text)
    assert groups == [{"agents": ["gptbot", "claude-user", "chatgpt-user"], "rules": [("disallow", "/")], "crawl_delay": None,
                       "other": [("content-signal", "ai-input=no")]}]
    assert sitemaps == ["https://example.com/s.xml"]
    assert parse_robots(text)["agents_blocked"] == ["ChatGPT-User", "Claude-User", "GPTBot"]


def test_bytes_bom_and_line_endings_match_the_text_form():
    text = "User-agent: ChatGPT-User\r\nContent-Signal: x\rUser-agent: *\r\nDisallow: /\n"
    expected = split_groups(text.replace("\r\n", "\n").replace("\r", "\n"))
    assert split_groups(text) == expected
    assert split_groups(("﻿" + text).encode("utf-8")) == expected
    assert split_groups(b"# \xff\xfe\nUser-agent: GPTBot\nX-\xc3: \xff\nDisallow: /\n")[0] == [
        {"agents": ["gptbot"], "rules": [("disallow", "/")], "crawl_delay": None, "other": [("x-�", "�")]}]


# ---- properties over random input ----------------------------------------------------------------------------------

_KEYS = ["User-agent", "user-agent", "USER-AGENT", "User agent", "useragent", "Allow", "allow", "Disallow", "DISALLOW",
         "Sitemap", "Crawl-delay", "Content-Signal", "Content-Usage", "Host", "Noindex", "Unknown-Key", "", " ", "#", "User-agent#"]
_AGENTS = [token for token, _kind in AGENT_USER_AGENTS] + ["*", "* ", "/bot", "/", "", " ", "Claude", "GPT", "Go", "*bot",
                                                           "gptbot/1.1", "Mozilla/5.0 (X)", "chatgpt-user,claude-user", "ü-bot"]
_VALUES = ["/", "", "/*", "/$", "/private", "/*.pdf$", "*", "$", "https://example.com/s.xml", "10", "-1", "nan", "inf",
           "1e400", "ai-input=no", "/ /", "# not a comment value", ":", "::", "\x00", " ", "\x0c"]


def _random_line(rng: random.Random) -> str:
    roll = rng.random()
    if roll < 0.05:
        return ""
    if roll < 0.08:
        return "".join(rng.choice(" :#/*$\t\x00 \x85abcAZ-_é﻿\r") for _ in range(rng.randint(0, 12)))
    key = rng.choice(_KEYS)
    if key.strip().lower().replace(" ", "") in ("user-agent", "useragent"):
        value = rng.choice(_AGENTS)
    else:
        value = rng.choice(_VALUES)
    separator = rng.choice([":", ": ", " : ", ":\t", " ", ""]) if rng.random() < 0.1 else ": "
    comment = rng.choice(["", "", "", " # note", "#"])
    indent = rng.choice(["", " ", "\t"])
    return f"{indent}{key}{separator}{value}{comment}"


def test_parser_never_raises_on_random_text_or_bytes():
    rng = random.Random(9309)
    for _ in range(4000):
        text = "\n".join(_random_line(rng) for _ in range(rng.randint(0, 25)))
        parsed = parse_robots(text)
        assert len(parsed["rules"]) == len(AGENT_USER_AGENTS)
        split_groups(text)
    for _ in range(2000):
        blob = bytes(rng.randrange(256) for _ in range(rng.randint(0, 200)))
        parse_robots(blob)
        parse_robots(blob.decode("latin-1"))
        split_groups(blob)


def _reference_root(text: str, token: str) -> tuple[bool, bool]:
    """(explicit_group, root_allowed) for one token, modelled on Google's robots.cc RobotsMatcher.

    seen_separator_ is set only by allow/disallow lines and resets the agent flags at the next user-agent line; every
    other line is a no-op. Agent names are matched with this service's own lenient prefix rule (_applies), and rules
    of a group that names both the token and "*" count for both, as in robots.cc.
    """
    specific, wildcard = [], []
    seen_separator = seen_specific = seen_global = ever_specific = False
    for line in text.split("\n"):
        key, separator, value = line.split("#", 1)[0].partition(":")
        if not separator:
            continue
        key, value = key.strip().lower().replace(" ", ""), value.strip()
        if key in ("user-agent", "useragent"):
            if seen_separator:
                seen_specific = seen_global = seen_separator = False
            words = value.split("/", 1)[0].split()
            agent = words[0].lower() if words else ""
            if agent == "*":
                seen_global = True
            elif _applies(agent, token.lower()):
                seen_specific = ever_specific = True
        elif key in ("allow", "disallow") and (seen_specific or seen_global):
            seen_separator = True
            if seen_specific:
                specific.append((key, value))
            if seen_global:
                wildcard.append((key, value))
    return ever_specific, _root_allowed(specific if ever_specific else wildcard)


def test_parser_agrees_with_the_reference_group_model_on_random_files():
    rng = random.Random(2026_10_20)
    for _ in range(3000):
        text = "\n".join(_random_line(rng).replace("\r", "").replace(" ", "").replace("\x85", "").replace("\x0c", "")
                         .replace("\x1c", "").replace("\x1d", "").replace("\x1e", "").replace("\x0b", "").replace("﻿", "")
                         for _ in range(rng.randint(0, 25)))
        parsed = parse_robots(text)
        for rule in parsed["rules"]:
            assert (rule["explicit_group"], rule["root_allowed"]) == _reference_root(text, rule["agent"]), (text, rule["agent"])
