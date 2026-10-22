"""Declared agent policy of a public hostname, read from its own robots.txt and llms.txt.

Safety posture: only eligible public apex hosts and their own www. alias, only public (global)
IP addresses, at most one redirect followed (a single hop from the host to its own www. alias
for the same path; any other 3xx is recorded as ``redirected`` and not followed), hard byte
caps, one read attempt per host per cache window (24 hours for a lookup, ERROR_RETRY after a
transport failure), and never a probe: the two well-known files are the only paths ever
requested, and the result is reported as DECLARED policy, never as an access guarantee.
"""

from __future__ import annotations

import concurrent.futures
import ipaddress
import json
import re
import socket
import ssl
import time
import threading
from datetime import datetime, timedelta, timezone

import urllib3
from urllib.parse import urlparse
from psycopg.types.json import Jsonb

from .db import connect
from .provenance import operator_id, sha256_hex, vantage

# Only user_agent and search_crawler tokens can make a declared block (triage.AGENT_KINDS_FOR_BLOCK); a training_crawler
# token is reported but never blocks. Spellings and kinds follow each vendor's own documentation, see AGENT_SOURCES.
AGENT_USER_AGENTS: tuple[tuple[str, str], ...] = (
    ("ChatGPT-User", "user_agent"), ("Claude-User", "user_agent"), ("Perplexity-User", "user_agent"),
    ("Meta-ExternalFetcher", "user_agent"), ("MistralAI-User", "user_agent"), ("Google-Agent", "user_agent"),
    ("Google-GeminiNotebook", "user_agent"), ("Amzn-User", "user_agent"), ("Diffbot-User", "user_agent"),
    ("DuckAssistBot", "user_agent"), ("OAI-SearchBot", "search_crawler"),
    ("Claude-SearchBot", "search_crawler"), ("PerplexityBot", "search_crawler"), ("Googlebot", "search_crawler"),
    ("Bingbot", "search_crawler"), ("DuckDuckBot", "search_crawler"), ("Amazonbot", "search_crawler"),
    ("Amzn-SearchBot", "search_crawler"), ("ExaSearchBot", "search_crawler"), ("Meta-WebIndexer", "search_crawler"), ("MistralAI-Index", "search_crawler"),
    ("Applebot", "search_crawler"), ("Diffbot", "search_crawler"),
    ("GPTBot", "training_crawler"), ("ClaudeBot", "training_crawler"), ("anthropic-ai", "training_crawler"),
    ("Google-Extended", "training_crawler"), ("Applebot-Extended", "training_crawler"), ("Meta-ExternalAgent", "training_crawler"),
    ("MistralAI-Training", "training_crawler"), ("CCBot", "training_crawler"), ("Bytespider", "training_crawler"),
    ("cohere-ai", "training_crawler"),
)
_GOOGLE_CRAWLERS = "https://developers.google.com/crawling/docs/crawlers-fetchers/"
_OPENAI, _PERPLEXITY, _MISTRAL = "https://developers.openai.com/api/docs/bots", "https://docs.perplexity.ai/guides/bots", "https://docs.mistral.ai/robots"
_ANTHROPIC = "https://support.claude.com/en/articles/8896518-does-anthropic-crawl-data-from-the-web-and-how-can-site-owners-block-the-crawler"
_META = "https://developers.facebook.com/documentation/sharing/webmasters/web-crawlers"
_AMAZON, _APPLE, _DIFFBOT = "https://developer.amazon.com/amazonbot", "https://support.apple.com/en-us/119829", "https://www.diffbot.com/docs/crawl/faq/robots-txt"
# Vendor documentation that names each token (primary sources only, checked 2026-10-18). Tokens without an entry are
# UNCONFIRMED_AGENT_TOKENS: kept because sites still list them, but no vendor page was found that spells them.
AGENT_SOURCES: dict[str, str] = {
    "ChatGPT-User": _OPENAI, "OAI-SearchBot": _OPENAI, "GPTBot": _OPENAI,
    "Claude-User": _ANTHROPIC, "Claude-SearchBot": _ANTHROPIC, "ClaudeBot": _ANTHROPIC,
    "Perplexity-User": _PERPLEXITY, "PerplexityBot": _PERPLEXITY,
    "Meta-ExternalFetcher": _META, "Meta-WebIndexer": _META, "Meta-ExternalAgent": _META,
    "MistralAI-User": _MISTRAL, "MistralAI-Index": _MISTRAL, "MistralAI-Training": _MISTRAL,
    "Google-Agent": _GOOGLE_CRAWLERS + "google-user-triggered-fetchers", "Google-GeminiNotebook": _GOOGLE_CRAWLERS + "google-user-triggered-fetchers",
    "ExaSearchBot": "https://exa.ai/docs/reference/exa-crawler",
    "Googlebot": _GOOGLE_CRAWLERS + "google-common-crawlers", "Google-Extended": _GOOGLE_CRAWLERS + "google-common-crawlers",
    "Bingbot": "https://www.bing.com/webmasters/help/which-crawlers-does-bing-use-8c184ec0",
    "DuckAssistBot": "https://duckduckgo.com/duckduckgo-help-pages/results/duckassistbot",
    "DuckDuckBot": "https://duckduckgo.com/duckduckgo-help-pages/results/duckduckbot",
    "Amazonbot": _AMAZON, "Amzn-User": _AMAZON, "Amzn-SearchBot": _AMAZON,
    "Applebot": _APPLE, "Applebot-Extended": _APPLE,
    "Diffbot": _DIFFBOT, "Diffbot-User": _DIFFBOT,
    "CCBot": "https://commoncrawl.org/ccbot",
}
UNCONFIRMED_AGENT_TOKENS = frozenset({"anthropic-ai", "Bytespider", "cohere-ai"})
USER_AGENT = "AgentHelpNetwork/0.2 (+https://agenthelpnetwork.com/en/policy)"
ROBOTS_CAP = 262_144
LLMS_CAP = 4_096
MAX_LINES = 5_000
MAX_SITEMAPS = 5
MAX_PATH_SAMPLES = 6
_HTML_MARKERS = (b"<!doctype", b"<html", b"<head", b"<body", b"<?xml")
_HOST_RE = re.compile(r"^(?=.{1,253}$)([a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z][a-z0-9-]{0,62}$")
_HOST_LOCKS: dict[str, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()


def robots_text(text: str | bytes) -> str:
    """The file as text: capped at ROBOTS_CAP, decoded leniently, without a BOM, every line ending a newline."""
    if isinstance(text, bytes):
        text = text[:ROBOTS_CAP].decode("utf-8", errors="replace")
    return text[:ROBOTS_CAP].lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n")


# How lines form groups, from the two primary sources (read 2026-10-20):
# - RFC 9309 section 2.1/2.2 grammar: a group is one or more user-agent lines followed by allow/disallow rules, empty
#   lines allowed anywhere. Section 2.2.4: parsing other records "MUST NOT interfere" with those, e.g. a Sitemap line
#   must not end a group. Section 2.2.2: rules outside any group are ignored. https://www.rfc-editor.org/rfc/rfc9309.html
# - Google's reference parser, robots.cc: only HandleAllow/HandleDisallow set seen_separator_; HandleUserAgent starts a
#   new group only when seen_separator_ is set; Sitemap and unknown keys are no-ops (HandleSitemap,
#   HandleUnknownAction). robots_test.cc ID_LineSyntax_Groups_OtherRules pins this with a Sitemap line and an unknown
#   line between two user-agent lines. https://github.com/google/robotstxt
# So a user-agent line opens a new group at the start or after an allow/disallow line, and every other line, known
# (Sitemap, Crawl-delay) or unknown (Content-Signal, Host, typos), leaves the grouping as it is.
#
# One exception by default, where parsers disagree: once a group's agent lines have been followed by another record
# (Crawl-delay, Content-Signal, Sitemap, an unknown key) and then by a blank line, the next user-agent line opens a new
# group. `User-agent: *` / `Crawl-delay: 10` / blank / `User-agent: BadBot` / `Disallow: /` means "everyone, slowly;
# BadBot not at all", and Python's urllib.robotparser also ends a group there (a blank line ends an entry, and
# Crawl-delay or Request-rate ends its agent lines). Google's parser merges the two and blocks `*` too. Reading the
# stricter way would report working sites as closed, so the answer uses this reading, and parse_robots reports where
# google_style=True (strict RFC 9309 and robots.cc) would answer differently. A blank line alone, with no other record
# before it (agent / blank / agent), still joins the agent lines as RFC 9309 says.
# Unicode dashes a hand-edited robots.txt can carry in a user-agent name (U+2010 hyphen to U+2015 horizontal bar, U+2212
# minus, U+FE63 small hyphen-minus, U+FF0D fullwidth hyphen-minus), read as "-": `perplexity\u2011user` names
# Perplexity-User. It only ever makes a name match more tokens, never fewer.
_DASHES = str.maketrans({char: "-" for char in "\u2010\u2011\u2012\u2013\u2014\u2015\u2212\ufe63\uff0d"})


def split_groups(text: str | bytes, *, google_style: bool = False) -> tuple[list[dict], list[str]]:
    """The file's groups and sitemaps; the one group split every reader of robots.txt uses. Never raises.

    ``google_style=True`` drops the blank-line exception above and groups exactly as RFC 9309 and Google's robots.cc.
    Returns ``(groups, sitemaps)``. ``groups`` is a list in file order; each group is a dict:
      ``agents``: list[str], the product tokens its user-agent lines name, lowercased (first word of the value, cut at
                  ``/``). A line with no such word (empty value, ``/bot``) adds no name but still belongs to the group.
      ``rules``: list[tuple[str, str]], ``("allow" | "disallow", value)`` in file order.
      ``crawl_delay``: float | None, the last Crawl-delay in the group that parses as 0-3600 seconds.
      ``other``: list[tuple[str, str]], ``(key, value)`` of every other line inside the group in file order, the key
                 lowercased with spaces removed (Crawl-delay included, Sitemap not), for non-REP records such as
                 Content-Signal. Lines before the first user-agent line belong to no group and are dropped.
    ``sitemaps`` is a list of up to MAX_SITEMAPS distinct http(s) Sitemap URLs from anywhere in the file.
    """
    groups: list[dict] = []
    current: dict | None = None
    in_agent_lines = False  # from a user-agent line until the next allow/disallow line
    other_record = False    # a non-rule record came after this group's agent lines, before any rule
    break_pending = False   # ... and a blank line followed it: the next user-agent line opens a new group
    sitemaps: list[str] = []
    for line in robots_text(text).splitlines()[:MAX_LINES]:
        if not line.strip():
            if in_agent_lines and other_record and not google_style:
                break_pending = True
            continue
        key, separator, value = line.split("#", 1)[0].partition(":")
        if not separator:
            continue
        key, value = key.strip().lower().replace(" ", ""), value.strip()
        if key in ("user-agent", "useragent"):
            if current is None or not in_agent_lines or break_pending:
                current = {"agents": [], "rules": [], "crawl_delay": None, "other": []}
                groups.append(current)
                other_record = break_pending = False
            words = value.split("/", 1)[0].split()
            if words:
                current["agents"].append(words[0].translate(_DASHES).lower())
            in_agent_lines = True
            continue
        if key in ("allow", "disallow"):
            in_agent_lines = other_record = break_pending = False
            if current is not None:
                current["rules"].append((key, value))
            continue
        if in_agent_lines:
            other_record = True
        if key == "sitemap":
            if value.startswith(("https://", "http://")) and len(value) <= 300 and len(sitemaps) < MAX_SITEMAPS and value not in sitemaps:
                sitemaps.append(value)
        elif current is not None:
            current["other"].append((key, value))
            if key == "crawl-delay":
                try:
                    delay = float(value)
                except ValueError:
                    continue
                if 0 <= delay <= 3600:
                    current["crawl_delay"] = delay
    return groups, sitemaps


def _disallow_samples(rules: list[tuple[str, str]]) -> list[str]:
    """A few disallowed path prefixes, so 'root allowed' is not read as 'everything allowed'."""
    seen: list[str] = []
    for kind, pattern in rules:
        if kind == "disallow" and pattern and pattern not in seen:
            seen.append(pattern[:80])
            if len(seen) >= MAX_PATH_SAMPLES:
                break
    return seen


def _path_matches(pattern: str, path: str) -> bool:
    if not pattern:
        return False
    anchored = pattern.endswith("$")
    core = pattern[:-1] if anchored else pattern
    regex = ".*".join(re.escape(part) for part in core.split("*")) + ("$" if anchored else "")
    return re.match(regex, path) is not None


def _root_allowed(rules: list[tuple[str, str]]) -> bool:
    """Longest-match precedence; equal length → Allow wins; no match → allowed."""
    best_len, allowed = -1, True
    for kind, pattern in rules:
        if not _path_matches(pattern, "/"):
            continue
        length = len(pattern)
        if length > best_len or (length == best_len and kind == "allow"):
            best_len, allowed = length, kind == "allow"
    return allowed


def _applies(agent: str, token: str) -> bool:
    return agent != "*" and (agent == token or (len(agent) >= 3 and token.startswith(agent)))


def parse_robots(text: str | bytes) -> dict:
    """Pure. Declared root ('/') policy per known agent token; tolerant of junk, BOM, CRLF, comments.

    ``grouping_differs_by_parser`` lists ``*`` and the known tokens whose root decision (explicit_group, root_allowed)
    would differ if the file were grouped the Google way (split_groups google_style=True); empty when both agree.
    """
    text = robots_text(text)
    groups, sitemaps = split_groups(text)
    summary = _summarize(groups, sitemaps)
    google_groups, _ = split_groups(text, google_style=True)
    differs: list[str] = []
    if google_groups != groups:
        google = _summarize(google_groups, sitemaps)
        if google["wildcard_root_allowed"] != summary["wildcard_root_allowed"]:
            differs.append("*")
        differs += [ours["agent"] for ours, theirs in zip(summary["rules"], google["rules"])
                    if (ours["explicit_group"], ours["root_allowed"]) != (theirs["explicit_group"], theirs["root_allowed"])]
    summary["grouping_differs_by_parser"] = differs
    return summary


def _summarize(groups: list[dict], sitemaps: list[str]) -> dict:
    wildcard = [group for group in groups if "*" in group["agents"]]
    wildcard_rules = [rule for group in wildcard for rule in group["rules"]]
    wildcard_root_allowed = _root_allowed(wildcard_rules)
    wildcard_delay = next((g["crawl_delay"] for g in wildcard if g["crawl_delay"] is not None), None)
    rules, blocked, allowed = [], [], []
    for token, kind in AGENT_USER_AGENTS:
        lowered = token.lower()
        matched = [group for group in groups if any(_applies(agent, lowered) for agent in group["agents"])]
        explicit = bool(matched)
        effective = [rule for group in (matched if explicit else wildcard) for rule in group["rules"]]
        root_allowed = _root_allowed(effective) if explicit else wildcard_root_allowed
        delay = next((g["crawl_delay"] for g in matched if g["crawl_delay"] is not None), None) if explicit else wildcard_delay
        rules.append({
            "agent": token, "kind": kind, "explicit_group": explicit, "root_allowed": root_allowed,
            "disallow_count": sum(1 for rule_kind, pattern in effective if rule_kind == "disallow" and pattern),
            "crawl_delay": delay, "disallow_paths": _disallow_samples(effective),
        })
        if explicit:
            (allowed if root_allowed else blocked).append(token)
    return {
        "rules": rules, "wildcard_root_allowed": wildcard_root_allowed, "sitemap": bool(sitemaps),
        "sitemaps": sitemaps, "wildcard_crawl_delay": wildcard_delay, "wildcard_disallow_paths": _disallow_samples(wildcard_rules),
        "agents_blocked": blocked, "agents_allowed": allowed,
    }


# Content-use preferences declared inside robots.txt: Cloudflare Content Signals (`Content-Signal:`) and IETF AIPREF
# (`Content-Usage:`, draft-ietf-aipref-attach-05 with the draft-ietf-aipref-vocab-08 vocabulary). They are read from the
# file already fetched for the robots rules (no extra request), reported as information only, and never change a
# recommendation: they say how the host wants content used, not whether it may be fetched.
# The parser version leads the string: a stored result written by another parser version is not shown as current.
CONTENT_USE_VOCAB = ("ahn-content-use/1; Cloudflare Content Signals; "
                     "IETF AIPREF draft-ietf-aipref-attach-05 / draft-ietf-aipref-vocab-08 (drafts)")
CONTENT_USE_NOTE = (
    "Content-use preferences the host declares in robots.txt (Cloudflare Content-Signal, IETF AIPREF Content-Usage draft). "
    "Information only: not an access rule, not permission, and not a legal reading. Whether a preference covers a "
    "user-directed agent is up to each agent's operator."
)
_SIGNAL_KEYS = {"search": "search", "ai-input": "ai_input", "ai-train": "ai_train"}
_SIGNAL_USE = ("immediate", "reference", "full")  # Cloudflare's experimental `use`, most to least restrictive
_AIPREF_KEYS = {"train-ai": "train_ai", "ai-use": "ai_use", "search": "search"}
# Storage limits, stated in the privacy policy (tests/test_policy_coverage.py checks the numbers).
MAX_USE_PATH_SAMPLES = 3     # path samples in total, across the * group and all named groups
MAX_USE_NAMED_GROUPS = 5
MAX_USE_GROUP_AGENTS = 8     # names per named group, only tokens from AGENT_USER_AGENTS; other names are only counted
MAX_USE_IGNORED = 5
_SF_KEY = re.compile(r"^[a-z*][a-z0-9_\-.*]*$")
_SF_BARE_ITEM = re.compile(r"""^(?:[A-Za-z*][A-Za-z0-9!#$%&'*+\-.^_`|~:/]*|-?[0-9][0-9.]*|"[^"]*"|\?[01]|:[A-Za-z0-9+/=]*:)$""")
_USE_LABEL = re.compile(r"^[a-z0-9_\-.*]{1,32}$")
_ALL_PATHS = ("", "/", "/*")
_KNOWN_TOKENS = {token.lower(): (token, kind) for token, kind in AGENT_USER_AGENTS}
_USER_AGENT_TOKENS = tuple(token for token, kind in AGENT_USER_AGENTS if kind == "user_agent")


def _use_label(key: str) -> str:
    """A key the parser did not use, reported by name only; anything that is not a short token is 'unparsable'."""
    key = key.strip().lower()
    return key if _USE_LABEL.match(key) else "unparsable"


def _split_use_path(value: str) -> tuple[str, str]:
    """Both formats allow an optional leading path: a value that starts with '/' carries one, up to the first blank."""
    if not value.startswith("/"):
        return "", value
    parts = value.split(None, 1)
    return parts[0], (parts[1] if len(parts) > 1 else "")


def _parse_content_signal(value: str, ignored: list[str]) -> dict:
    """`search=yes, ai-train=no[, use=reference]`: names and values case-insensitive, only yes/no (and the three use values)."""
    found: dict = {}
    for item in value.split(","):
        if not item.strip():
            continue
        key, sep, val = item.partition("=")
        key, val = key.strip().lower(), val.strip().lower()
        if sep and key in _SIGNAL_KEYS and val in ("yes", "no"):
            _merge_use(found, {_SIGNAL_KEYS[key]: val})
        elif sep and key == "use" and val in _SIGNAL_USE:
            _merge_use(found, {"use": val})
        else:
            ignored.append(_use_label(key))
    return found


def _parse_content_usage(value: str, ignored: list[str]) -> dict:
    """AIPREF preferences: a Structured Fields dictionary whose known keys take the tokens y or n.

    Per draft-ietf-aipref-vocab-08: keys are case-sensitive and a dictionary that does not parse (an uppercase key,
    an empty member) leaves every category unknown; unknown keys are ignored; a value other than y/n leaves that
    category unknown; parameters are ignored; a repeated key keeps its last value.
    """
    members: dict[str, str] = {}
    for member in value.split(","):
        member = member.split(";", 1)[0].strip()
        key, sep, val = member.partition("=")
        key, val = key.strip(), val.strip()
        if not _SF_KEY.match(key) or (sep and not _SF_BARE_ITEM.match(val)):
            ignored.append("unparsable")
            return {}
        members[key] = val if sep else "?1"
    found: dict = {}
    for key, val in members.items():
        if key in _AIPREF_KEYS and val in ("y", "n"):
            found[_AIPREF_KEYS[key]] = "yes" if val == "y" else "no"
        else:
            ignored.append(_use_label(key))
    return found


def _merge_use(target: dict, new: dict) -> None:
    """Several statements for the same scope: the most restrictive one applies (draft-ietf-aipref-vocab-08 section 5.1).

    The same rule is applied to repeated Content-Signal lines, which Cloudflare's documentation does not address.
    """
    for key, val in new.items():
        old = target.get(key)
        if old is None:
            target[key] = val
        elif key == "use":
            target[key] = min(old, val, key=_SIGNAL_USE.index)
        elif "no" in (old, val):
            target[key] = "no"


def _signals_shape(found: dict, seen: bool) -> dict | None:
    if not seen:
        return None
    return {"search": found.get("search", "unset"), "ai_input": found.get("ai_input", "unset"),
            "ai_train": found.get("ai_train", "unset"), "use": found.get("use")}


def _usage_shape(found: dict, seen: bool) -> dict | None:
    if not seen:
        return None
    return {"train_ai": found.get("train_ai", "unset"), "ai_use": found.get("ai_use", "unset"), "search": found.get("search", "unset")}


def _use_lines(group: dict) -> list[tuple[str, str]]:
    """The group's Content-Signal and Content-Usage records, from split_groups' `other` list (keys already lowercased)."""
    return [(key, value) for key, value in group["other"] if key in ("content-signal", "content-usage")]


def _group_use(group: dict, ignored: list[str]) -> dict:
    """One group's site-wide signals and usage (most restrictive wins), plus the paths of path-scoped lines."""
    signals: dict = {}
    usage: dict = {}
    seen = {"content-signal": False, "content-usage": False}
    paths: list[str] = []
    formats: set[str] = set()
    for key, value in _use_lines(group):
        formats.add("cloudflare_content_signal" if key == "content-signal" else "ietf_aipref")
        path, rest = _split_use_path(value)
        parsed = _parse_content_signal(rest, ignored) if key == "content-signal" else _parse_content_usage(rest, ignored)
        if path not in _ALL_PATHS:
            # A path-scoped preference covers part of the site only; it is sampled, never folded into the site-wide value.
            if path[:80] not in paths and len(paths) < MAX_USE_PATH_SAMPLES:
                paths.append(path[:80])
            continue
        seen[key] = True
        _merge_use(signals if key == "content-signal" else usage, parsed)
    return {"formats": formats, "signals": signals, "usage": usage, "seen": seen, "paths": paths}


def _says_no_ai_input(signals: dict, usage: dict) -> bool:
    return signals.get("ai_input") == "no" or usage.get("ai_use") == "no"


def _ai_input_no_agents(groups: list[dict], results: list[dict | None], star: tuple[dict, dict]) -> list[str]:
    """User-directed agent tokens whose effective site-wide preference is ai-input=no or ai-use=n.

    Same group rule as the robots rules: a token named by any group (with or without Content-* lines) takes the
    declarations of the groups that name it, and only a token named by no group falls back to the `*` group.
    """
    found = []
    for token in _USER_AGENT_TOKENS:
        lowered = token.lower()
        matched = [index for index, group in enumerate(groups) if any(_applies(agent, lowered) for agent in group["agents"])]
        if matched:
            signals: dict = {}
            usage: dict = {}
            for index in matched:
                if results[index] is not None:
                    _merge_use(signals, results[index]["signals"])
                    _merge_use(usage, results[index]["usage"])
        else:
            signals, usage = star
        if _says_no_ai_input(signals, usage):
            found.append(token)
    return found


def _agent_rank(token: str) -> int:
    return 0 if _KNOWN_TOKENS[token.lower()][1] == "user_agent" else 1


def parse_content_use(text: str | bytes) -> dict:
    """Pure. Content-use preferences declared in robots.txt, normalized to yes/no/unset; nothing else of the file is kept.

    `signals`/`usage` describe the `User-agent: *` group for the whole site (no path, `/` or `/*`); `named_groups` the
    groups that name other user agents (up to 5, identical declarations folded); path-scoped lines are only sampled (at
    most 3 paths in total). Agent names are kept only when they are tokens of AGENT_USER_AGENTS; other names are counted.
    `ignored_keys` names keys or values that were not understood ('unparsable' for anything that is not a short token).
    `ai_input_no_agents` is computed from every group before any cap applies.
    """
    formats: set[str] = set()
    ignored: list[str] = []
    star_signals: dict = {}
    star_usage: dict = {}
    star_seen = {"content-signal": False, "content-usage": False}
    star_paths: list[str] = []
    named: dict[str, dict] = {}  # serialized declaration -> entry, so folding stays linear in the number of groups
    # The same groups as the robots answer (split_groups with its default reading, as parse_robots uses), so the
    # declarations and the ai-input decision below follow exactly the grouping the robots rules are judged by.
    groups, _sitemaps = split_groups(text)
    results: list[dict | None] = []
    for group in groups:
        result = _group_use(group, ignored) if _use_lines(group) else None
        results.append(result)
        if result is None:
            continue
        formats |= result["formats"]
        if "*" in group["agents"]:
            _merge_use(star_signals, result["signals"])
            _merge_use(star_usage, result["usage"])
            for key in star_seen:
                star_seen[key] = star_seen[key] or result["seen"][key]
            star_paths += [path for path in result["paths"] if path not in star_paths]
            continue
        entry = {
            "signals": _signals_shape(result["signals"], result["seen"]["content-signal"]),
            "usage": _usage_shape(result["usage"], result["seen"]["content-usage"]),
            "path_specific_samples": result["paths"],
        }
        key = json.dumps(entry, sort_keys=True)
        item = named.get(key)
        if item is None:
            item = named[key] = {"agents": set(), "other_agents": set(), **entry}
        for agent in group["agents"]:
            # Only names from this service's agent-token table are kept; any other name is only counted.
            if agent in _KNOWN_TOKENS:
                item["agents"].add(_KNOWN_TOKENS[agent][0])
            else:
                item["other_agents"].add(agent)
    no_agents = _ai_input_no_agents(groups, results, (star_signals, star_usage))
    # Declarations that restrict AI input come first, so the cap never drops the ones an agent most needs.
    ordered = sorted(named.values(), key=lambda item: not _says_no_ai_input(item["signals"] or {}, item["usage"] or {}))
    budget = MAX_USE_PATH_SAMPLES - len(star_paths[:MAX_USE_PATH_SAMPLES])
    named_groups = []
    for item in ordered[:MAX_USE_NAMED_GROUPS]:
        agents = sorted(item["agents"], key=lambda token: (_agent_rank(token), token.lower()))
        paths = item["path_specific_samples"][:max(budget, 0)]
        budget -= len(paths)
        named_groups.append({
            "agents": agents[:MAX_USE_GROUP_AGENTS],
            "other_agents": len(item["other_agents"]) + max(len(agents) - MAX_USE_GROUP_AGENTS, 0),
            "signals": item["signals"], "usage": item["usage"], "path_specific_samples": paths,
        })
    return {
        "vocab": CONTENT_USE_VOCAB,
        "formats": sorted(formats),
        "signals": _signals_shape(star_signals, star_seen["content-signal"]),
        "usage": _usage_shape(star_usage, star_seen["content-usage"]),
        "path_specific_samples": star_paths[:MAX_USE_PATH_SAMPLES],
        "named_groups": named_groups,
        "named_groups_total": len(named),
        # Cloudflare's managed robots.txt inserts `search=yes, ai-train=no` (now with `use=reference`) by default, so a
        # match may be a default the site owner never chose. A hint, not a finding: an owner can write the same line.
        "vendor_default_hint": (star_signals.get("search") == "yes" and star_signals.get("ai_train") == "no"
                                and "ai_input" not in star_signals and star_signals.get("use") in (None, "reference")),
        "ai_input_declared_no": bool(no_agents),
        "ai_input_no_agents": no_agents,
        "ignored_keys": sorted(set(ignored))[:MAX_USE_IGNORED],
    }


_STORED_KEYS = ("formats", "signals", "usage", "path_specific_samples", "named_groups", "named_groups_total",
                "vendor_default_hint", "ai_input_declared_no", "ai_input_no_agents", "ignored_keys")


def content_use_view(robots_status: str, stored: dict | None, read_host: str, fetched_at, freshness: str,
                     robots_sha256: str | None = None) -> dict:
    """access_profile.content_use: what was declared, or why nothing can be said. Not reading is never 'none declared'.

    A stored result counts only when it was written by this parser version for the robots.txt the row now describes
    (same SHA-256); otherwise (a row cached before migration 021, or rewritten by another version after a rollback)
    it reads as not_read / read_before_content_use until the next read. Anything but a declaration is a compact answer.
    """
    view = {"status": "not_read", "reason": None, "source_url": f"https://{read_host}/robots.txt",
            "fetched_at": fetched_at, "freshness": freshness}
    current = (stored is not None and stored.get("vocab") == CONTENT_USE_VOCAB
               and (robots_sha256 is None or stored.get("robots_sha256") == robots_sha256))
    if robots_status == "ok" and current:
        if not stored.get("formats"):
            view["status"] = "none_declared"
            return view
        view.update({key: stored.get(key) for key in _STORED_KEYS})
        view["status"] = "declared"
        view["scope"] = ("all_paths" if view["signals"] is not None or view["usage"] is not None
                         else "path_specific" if view["path_specific_samples"] else None)
        view["vocab"], view["note"] = CONTENT_USE_VOCAB, CONTENT_USE_NOTE
    elif robots_status == "ok":
        view["reason"] = "read_before_content_use"  # no current result for this file; the next read fills it
    elif robots_status == "absent":
        view.update(status="none_declared", reason="robots_absent")  # no robots.txt: nothing declared, definitively
    elif robots_status == "pending":
        view["reason"] = "pending"
    else:
        view["reason"] = f"robots_{robots_status}"
    return view


def ai_input_declared_no(content_use: dict | None) -> bool:
    """A user-directed agent token's effective site-wide preference is ai-input=no or ai-use=n (computed before any cap)."""
    return bool(content_use and content_use.get("status") == "declared" and content_use.get("ai_input_declared_no"))


def _resolve(site_host: str, timeout: float) -> set[str]:
    """getaddrinfo in a worker thread so a stalled resolver cannot hold the request."""
    executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    try:
        future = executor.submit(socket.getaddrinfo, site_host, 443, type=socket.SOCK_STREAM)
        return {item[4][0] for item in future.result(timeout=timeout)}
    finally:
        executor.shutdown(wait=False)


MAX_ADDRESSES_TRIED = 3
CONNECT_BUDGET_FACTOR = 2  # all connection attempts of one read share timeout_connect * this
# Whole-body time limit. The per-receive read timeout alone lets a host that trickles one byte at a
# time hold a reader indefinitely; past this budget the read ends as read_timeout.
BODY_READ_BUDGET = 10.0
_clock = time.monotonic  # patched in tests


def read_capped(response, cap_bytes: int, *, budget: float = BODY_READ_BUDGET, clock=None) -> tuple[bytes | None, str | None]:
    """Read at most cap_bytes + 1 bytes of a streamed urllib3 response within ``budget`` seconds.

    Returns (body, None), or (None, 'too_large') / (None, 'read_timeout'). Chunks are read with
    read1, one receive at a time, so the budget is checked between receives: a read ends at most one
    receive timeout after the budget. A stand-in without read1 gets one bounded read.
    """
    read1 = getattr(response, "read1", None)
    if read1 is None:
        body = response.read(cap_bytes + 1)
        return (None, "too_large") if len(body) > cap_bytes else (body, None)
    clock = clock or _clock
    deadline = clock() + budget
    data = bytearray()
    while True:
        if clock() > deadline:
            return None, "read_timeout"
        chunk = read1(min(65_536, cap_bytes + 1 - len(data)))
        if not chunk:
            return bytes(data), None
        data.extend(chunk)
        if len(data) > cap_bytes:
            return None, "too_large"


def _transport_label(exc: BaseException) -> str:
    """Why a pinned-IP HTTPS read failed, as a short label; a timeout is not a refusal and TLS is not DNS."""
    if isinstance(exc, urllib3.exceptions.NameResolutionError):
        return "dns_failed"
    if isinstance(exc, (urllib3.exceptions.SSLError, ssl.SSLError)):
        return "tls_error"
    if isinstance(exc, urllib3.exceptions.NewConnectionError):  # subclass of ConnectTimeoutError: check first
        return "connect_error"
    if isinstance(exc, urllib3.exceptions.ConnectTimeoutError):
        return "connect_timeout"
    if isinstance(exc, (urllib3.exceptions.ReadTimeoutError, TimeoutError)):
        return "read_timeout"
    if isinstance(exc, (urllib3.exceptions.ProtocolError, urllib3.exceptions.DecodeError)):
        return "read_error"
    return "connect_error"


def _address_order(addresses: set[str]) -> list[str]:
    """IPv4 before IPv6 (the deployment has no IPv6 egress), then a stable order; at most MAX_ADDRESSES_TRIED."""
    return sorted(addresses, key=lambda ip: (ipaddress.ip_address(ip).version != 4, ip))[:MAX_ADDRESSES_TRIED]


def _www_redirect_target(response, site_host: str, path: str) -> str | None:
    """The only redirect we follow: apex → its own www host, same scheme and path (one hop)."""
    location = (response.headers.get("Location") or "").strip()
    for candidate in (f"https://www.{site_host}{path}", f"https://www.{site_host}{path}?"):
        if location == candidate:
            return f"www.{site_host}"
    return None


# An edge that answers robots.txt itself with one of these is refusing automated
# reads, which is a signal in its own right and distinct from a transport failure.
REFUSAL_STATUSES = frozenset({401, 403, 429, 503})
# Transport failures are retried after this long; definitive answers keep max_age.
ERROR_RETRY = timedelta(hours=1)


def fetch_public_file(
    site_host: str, path: str, *, cap_bytes: int, timeout_connect: float = 2.0, timeout_read: float = 3.0,
    _hop: int = 0,
) -> tuple[str, bytes | None, str | None]:
    """Fetch one well-known file from a pinned public IP; never raises for network, DNS, TLS or HTTP failures.

    Returns (status, body, detail). detail is a short machine label such as
    'http_503', 'dns_timeout' or 'connect_error' so the answer can say why.
    """
    site_host = site_host.strip().rstrip(".").lower()
    if not _HOST_RE.match(site_host) or not path.startswith("/") or "//" in path or cap_bytes < 1:
        return ("error", None, "invalid_input")
    try:
        try:
            addresses = _resolve(site_host, 2.0)
        except concurrent.futures.TimeoutError:
            return ("error", None, "dns_timeout")
        except (OSError, socket.gaierror):
            return ("error", None, "dns_failed")
        if not addresses or len(addresses) > 16:
            return ("error", None, "dns_failed")
        if any(not ipaddress.ip_address(ip).is_global for ip in addresses):
            return ("not_public", None, "not_public")
        last_label = "connect_error"
        # One address of a host can be dead from this network while another answers (seen with amazon.com),
        # so a failure to connect moves on to the next address. All attempts share one connection-time
        # budget, so a host that drops every packet costs at most CONNECT_BUDGET_FACTOR connect timeouts.
        connect_budget = timeout_connect * CONNECT_BUDGET_FACTOR
        for address in _address_order(addresses):
            if connect_budget <= 0:
                break
            started = _clock()
            pool = urllib3.HTTPSConnectionPool(
                address, port=443, server_hostname=site_host, assert_hostname=site_host,
                timeout=urllib3.Timeout(connect=min(timeout_connect, connect_budget), read=timeout_read), retries=False,
            )
            try:
                try:
                    response = pool.request(
                        "GET", path, headers={"Host": site_host, "User-Agent": USER_AGENT, "Accept": "text/plain,*/*;q=0.5"},
                        redirect=False, preload_content=False,
                    )
                except urllib3.exceptions.ConnectTimeoutError as exc:
                    # Refused, unreachable or a SYN timeout: nothing was established, try the next address.
                    # Anything after a connection is established is reported as is and never retried.
                    last_label = _transport_label(exc)
                    pool.close()
                    connect_budget -= _clock() - started
                    continue
            except Exception:
                pool.close()
                raise
            try:
                status = response.status
                if status in (404, 410):
                    return ("absent", None, f"http_{status}")
                if 300 <= status < 400:
                    # Many apex hosts answer only on www; that is still the host's own file.
                    www = _www_redirect_target(response, site_host, path) if _hop == 0 else None
                    if www:
                        response.release_conn()
                        pool.close()
                        return fetch_public_file(www, path, cap_bytes=cap_bytes, timeout_connect=timeout_connect,
                                                 timeout_read=timeout_read, _hop=1)
                    target = urlparse((response.headers.get("Location") or "").strip()).hostname or ""
                    return ("redirected", None, (f"redirect:{target}" if target else f"http_{status}")[:32])
                if status in REFUSAL_STATUSES:
                    return ("refused", None, f"http_{status}")
                if status != 200:
                    return ("error", None, f"http_{status}")
                body, failure = read_capped(response, cap_bytes)
                if failure == "too_large":
                    return ("too_large", None, "too_large")
                if failure:
                    return ("error", None, failure)
                if body.lstrip()[:12].lower().startswith(_HTML_MARKERS):
                    # A web page served for an unknown path (soft 404) is not a policy file.
                    return ("invalid", None, "html_body")
                return ("ok", body, "http_200")
            finally:
                response.release_conn()
                pool.close()
        return ("error", None, last_label)
    except Exception as exc:  # a lookup must never fail or hang because a third-party host misbehaves
        return ("error", None, _transport_label(exc))


def read_public_file(site_host: str, path: str, *, cap_bytes: int, timeout_connect: float = 2.0,
                     timeout_read: float = 3.0, _hop: int = 0) -> tuple[str, bytes | None]:
    status, body, _detail = fetch_public_file(site_host, path, cap_bytes=cap_bytes, timeout_connect=timeout_connect,
                                              timeout_read=timeout_read, _hop=_hop)
    return (status, body)


def _read(reader, site_host: str, path: str, cap_bytes: int, **options) -> tuple[str, bytes | None, str | None]:
    """Accept both the 2-tuple reader contract (tests, callers) and the detailed 3-tuple; options reach the reader."""
    result = reader(site_host, path, cap_bytes=cap_bytes, **options)
    if len(result) == 2:
        return (result[0], result[1], None)
    return (result[0], result[1], result[2])


def build_profile(site_host: str, *, reader=fetch_public_file) -> dict:
    robots_status, robots_body, robots_detail = _read(reader, site_host, "/robots.txt", ROBOTS_CAP)
    read_host = site_host
    www_attempt = None
    if robots_status in ("error", "refused", "redirected", "invalid") and not site_host.startswith("www."):
        # Apex failed or refused: the www alias is still the host's own file and often the one that answers.
        # The apex->www step is the one redirect hop a read may spend: the www host has to answer itself.
        alt_status, alt_body, alt_detail = _read(reader, f"www.{site_host}", "/robots.txt", ROBOTS_CAP, _hop=1)
        if alt_status in ("ok", "absent", "too_large"):
            robots_status, robots_body, robots_detail, read_host = alt_status, alt_body, alt_detail, f"www.{site_host}"
        else:
            # Not stored with the profile. The signal history uses it to tell "the www alias answered (a refusal,
            # a redirect, a web page)" from a failure of the whole host; the requests made are the same.
            www_attempt = {"robots_status": alt_status, "robots_detail": alt_detail}
    robots = parse_robots(robots_body) if robots_status == "ok" and robots_body is not None else None
    # Parsed from the same bytes as the robots rules: content-use preferences cost no extra request.
    # The robots.txt hash travels with the result, so a later rewrite of the row by another reader is detectable.
    content_use = {**parse_content_use(robots_body), "robots_sha256": sha256_hex(robots_body)} if robots is not None else None
    llms_status, llms_body, llms_detail = _read(reader, read_host, "/llms.txt", LLMS_CAP, **({"_hop": 1} if read_host != site_host else {}))
    # A soft-404 page is 'absent' for llms.txt purposes; the detail keeps the evidence.
    llms_txt_status = "present" if llms_status in ("ok", "too_large") else "absent" if llms_status == "invalid" else llms_status
    return {
        "robots_status": robots_status, "robots": robots, "robots_detail": robots_detail, "content_use": content_use,
        "llms_txt_status": llms_txt_status, "llms_detail": llms_detail, "read_host": read_host,
        "source_urls": {"robots_txt": f"https://{read_host}/robots.txt", "llms_txt": f"https://{read_host}/llms.txt"},
        # Provenance only: hashes of the bytes received; the files themselves are never stored.
        "robots_sha256": sha256_hex(robots_body) if robots_status == "ok" else None,
        "llms_sha256": sha256_hex(llms_body) if llms_status == "ok" else None,
        "www_attempt": www_attempt,
    }


def _freshness(fetched_at, error_at, now: datetime, max_age: timedelta) -> str:
    if fetched_at is None:
        return "pending"
    if fetched_at >= now - max_age and (error_at is None or fetched_at >= error_at):
        return "fresh"
    return "stale"


_ROW_COLUMNS = "fetched_at,robots_status,robots,llms_txt_status,last_error_at,robots_detail,llms_detail,read_host,content_use,robots_sha256"


def _as_profile(site_host: str, row, now: datetime, max_age: timedelta) -> dict:
    # The host that supplied the files: the apex, or its www alias after the fallback. Rows written
    # before the column existed, and rows that never got an answer, name the looked-up host.
    read_host = row["read_host"] or site_host
    freshness = _freshness(row["fetched_at"], row["last_error_at"], now, max_age)
    return {
        "robots_status": row["robots_status"], "robots": row["robots"], "robots_detail": row["robots_detail"],
        "llms_txt_status": row["llms_txt_status"], "llms_detail": row["llms_detail"], "read_host": read_host,
        "source_urls": {"robots_txt": f"https://{read_host}/robots.txt", "llms_txt": f"https://{read_host}/llms.txt"},
        "fetched_at": row["fetched_at"], "freshness": freshness,
        "content_use": content_use_view(row["robots_status"], row.get("content_use"), read_host, row["fetched_at"], freshness,
                                        row.get("robots_sha256")),
    }


def _cache_window(row, max_age: timedelta) -> timedelta:
    """A transport failure is retried after ERROR_RETRY; every definitive answer keeps the caller's max_age."""
    failed_last = row["last_error_at"] is not None and (row["fetched_at"] is None or row["last_error_at"] > row["fetched_at"])
    return min(max_age, ERROR_RETRY) if failed_last else max_age


def pending_profile(site_host: str, retry_after_seconds: int | None) -> dict:
    """The answer for an eligible host with no cached read yet; retry_after_seconds only when a read is queued."""
    profile = {
        "robots_status": "pending", "robots": None, "robots_detail": None,
        "llms_txt_status": "pending", "llms_detail": None, "read_host": site_host,
        "source_urls": {"robots_txt": f"https://{site_host}/robots.txt", "llms_txt": f"https://{site_host}/llms.txt"},
        "fetched_at": None, "freshness": "pending",
        "content_use": content_use_view("pending", None, site_host, None, "pending"),
    }
    if retry_after_seconds is not None:
        profile["retry_after_seconds"] = retry_after_seconds
    return profile


def cached_profile(conn, site_host: str, *, now: datetime | None = None, max_age_hours: int = 24) -> tuple[dict | None, bool]:
    """(the cached profile or None, whether a read is due) from one SELECT; never touches the network.

    "Due" uses the same window get_profile applies before it reads (max_age, or ERROR_RETRY after a
    transport failure), so the lookup path queues exactly the reads get_profile would have made.
    """
    now = now or datetime.now(timezone.utc)
    max_age = timedelta(hours=max_age_hours)
    row = conn.execute(f"SELECT {_ROW_COLUMNS} FROM host_access_profile WHERE site_host=%s", (site_host,)).fetchone()
    if not row:
        return None, True
    latest = max((t for t in (row["fetched_at"], row["last_error_at"]) if t is not None), default=None)
    due = latest is None or latest < now - _cache_window(row, max_age)
    return _as_profile(site_host, row, now, max_age), due


def get_profile(settings, site_host: str, *, now=None, max_age_hours: int = 24, reader=fetch_public_file,
                trigger: str = "lookup") -> dict | None:
    """Cached declared policy; one read attempt per host per window (max_age, or ERROR_RETRY after a transport failure).

    ``trigger`` ("lookup" or "seed") only picks the daily per-host counter of the signal history of a curated host
    (ahn/signal_history.py), which is written in a savepoint next to the cache row: it never changes or stops the read.
    """
    now = now or datetime.now(timezone.utc)
    max_age = timedelta(hours=max_age_hours)
    query = f"SELECT {_ROW_COLUMNS} FROM host_access_profile WHERE site_host=%s"
    with _LOCKS_GUARD:
        lock = _HOST_LOCKS.setdefault(site_host, threading.Lock())
    with lock:
        with connect(settings) as conn:
            row = conn.execute(query, (site_host,)).fetchone()
        if row:
            latest = max((t for t in (row["fetched_at"], row["last_error_at"]) if t is not None), default=None)
            if latest is not None and latest >= now - _cache_window(row, max_age):
                return _as_profile(site_host, row, now, max_age)
        profile = build_profile(site_host, reader=reader)
        with connect(settings) as conn:
            if profile["robots_status"] == "error":
                # Transport failure: keep the previous declared policy, record the attempt and why it failed.
                row = conn.execute(
                    f"""INSERT INTO host_access_profile (site_host,fetched_at,robots_status,robots,llms_txt_status,last_error_at,robots_detail,llms_detail)
                        VALUES (%s,NULL,'error',NULL,%s,%s,%s,%s) ON CONFLICT (site_host) DO UPDATE SET
                        last_error_at=EXCLUDED.last_error_at,robots_detail=EXCLUDED.robots_detail
                        RETURNING {_ROW_COLUMNS}""",
                    (site_host, profile["llms_txt_status"], now, profile["robots_detail"], profile["llms_detail"]),
                ).fetchone()
            else:
                # ok/absent/redirected/too_large/not_public/refused are answers in their own right.
                # Provenance travels with the definitive answer it describes; a later transport failure keeps both.
                row = conn.execute(
                    f"""INSERT INTO host_access_profile (site_host,fetched_at,robots_status,robots,llms_txt_status,last_error_at,robots_detail,llms_detail,read_host,
                                                         origin_operator,vantage,reader_version,robots_sha256,llms_sha256,content_use)
                        VALUES (%s,%s,%s,%s,%s,NULL,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (site_host) DO UPDATE SET
                        fetched_at=EXCLUDED.fetched_at,robots_status=EXCLUDED.robots_status,robots=EXCLUDED.robots,
                        llms_txt_status=EXCLUDED.llms_txt_status,last_error_at=NULL,
                        robots_detail=EXCLUDED.robots_detail,llms_detail=EXCLUDED.llms_detail,read_host=EXCLUDED.read_host,
                        origin_operator=EXCLUDED.origin_operator,vantage=EXCLUDED.vantage,reader_version=EXCLUDED.reader_version,
                        robots_sha256=EXCLUDED.robots_sha256,llms_sha256=EXCLUDED.llms_sha256,content_use=EXCLUDED.content_use
                        RETURNING {_ROW_COLUMNS}""",
                    (site_host, now, profile["robots_status"], Jsonb(profile["robots"]) if profile["robots"] is not None else None,
                     profile["llms_txt_status"], profile["robots_detail"], profile["llms_detail"], profile["read_host"],
                     operator_id(), Jsonb(vantage()), USER_AGENT, profile.get("robots_sha256"), profile.get("llms_sha256"),
                     Jsonb(profile["content_use"]) if profile.get("content_use") is not None else None),
                ).fetchone()
            from .signal_history import record_read
            record_read(conn, site_host, profile, now, trigger)
        return _as_profile(site_host, row, now, max_age)
