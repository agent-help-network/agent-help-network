"""Content-use preferences declared in robots.txt: Cloudflare Content-Signal and IETF AIPREF Content-Usage.

Pure tests: no database and no network. The fixtures copy the shapes seen in the seed-host census of 2026-10-20
and the examples of the two specifications.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone

import pytest

from ahn.access_profile import (
    CONTENT_USE_NOTE, CONTENT_USE_VOCAB, MAX_USE_PATH_SAMPLES, ROBOTS_CAP, ai_input_declared_no, build_profile,
    content_use_view, parse_content_use, parse_robots, pending_profile,
)
from ahn.models import AccessProfile
from ahn.provenance import sha256_hex
from ahn.triage import CHECKS, CHECKS_KO, next_checks, recommendation

# Cloudflare's managed robots.txt block (developers.cloudflare.com, managed-robots-txt, read 2026-10-19), shortened.
CLOUDFLARE_MANAGED = """# BEGIN Cloudflare Managed content
# As a condition of accessing this website, you agree to abide by the following content signals:
# (a)  If a content-signal = yes, you may collect content for the corresponding use.
User-Agent: *
Content-signal: search=yes, ai-train=no, use=reference
Allow: /

User-agent: GPTBot
Disallow: /

User-agent: ClaudeBot
Disallow: /
# END Cloudflare Managed Content
"""

# draft-ietf-aipref-attach-05, section 3.4 (Figure 2).
AIPREF_FIGURE_2 = """User-Agent: *
Allow: /
Disallow: /never/
Content-Usage: train-ai=n
Content-Usage: /ai-ok/ train-ai=y

User-Agent: ExampleBot
Content-Usage: train-ai=y
Allow: /
"""

NOW = datetime(2026, 10, 20, 12, 0, tzinfo=timezone.utc)


def view(text: str, robots_status: str = "ok") -> dict:
    return content_use_view(robots_status, parse_content_use(text), "example.com", NOW, "fresh")


def test_cloudflare_managed_default_is_declared_and_flagged_as_a_vendor_default():
    parsed = parse_content_use(CLOUDFLARE_MANAGED)
    assert parsed["formats"] == ["cloudflare_content_signal"] and parsed["vocab"] == CONTENT_USE_VOCAB
    assert parsed["signals"] == {"search": "yes", "ai_input": "unset", "ai_train": "no", "use": "reference"}
    assert parsed["usage"] is None and parsed["named_groups"] == [] and parsed["ignored_keys"] == []
    assert parsed["vendor_default_hint"] is True and parsed["ai_input_declared_no"] is False
    assert parse_content_use("User-agent: *\nContent-Signal: search=yes,ai-train=no\n")["vendor_default_hint"] is True
    assert parse_content_use("User-agent: *\nContent-Signal: search=yes, ai-train=no, ai-input=yes\n")["vendor_default_hint"] is False
    assert parse_content_use("User-agent: *\nContent-Signal: search=yes, ai-train=no, use=full\n")["vendor_default_hint"] is False


def test_content_signal_does_not_change_the_robots_rules():
    assert parse_robots(CLOUDFLARE_MANAGED) == parse_robots(CLOUDFLARE_MANAGED.replace("Content-signal: search=yes, ai-train=no, use=reference\n", ""))


def test_census_shapes_wildcard_and_named_groups():
    star = parse_content_use("User-agent: *\nContent-Signal: ai-train=no, search=yes, ai-input=yes\nAllow: /\n")
    assert star["signals"] == {"search": "yes", "ai_input": "yes", "ai_train": "no", "use": None}
    # A line repeated in groups that name crawlers: identical groups fold into one entry, known tokens only.
    text = ("User-agent: *\nContent-Signal: ai-train=yes, search=yes, ai-input=yes\nAllow: /\n\n"
            "User-agent: GPTBot\nUser-agent: ChatGPT-User\nContent-Signal: ai-train=yes, search=yes, ai-input=yes\nAllow: /\n\n"
            "User-agent: ClaudeBot\nUser-agent: SomeOtherBot\nContent-Signal: ai-train=yes, search=yes, ai-input=yes\nAllow: /\n")
    parsed = parse_content_use(text)
    assert parsed["named_groups_total"] == 1 and len(parsed["named_groups"]) == 1
    group = parsed["named_groups"][0]
    assert group["agents"] == ["ChatGPT-User", "ClaudeBot", "GPTBot"]  # user-directed agents first, vendor spelling
    assert group["other_agents"] == 1  # SomeOtherBot is counted, never stored
    # Declared only in a group naming user agents (seen on several hosts): nothing for *, one named group.
    named_only = parse_content_use("User-agent: ChatGPT-User\nUser-agent: Claude-User\nContent-Signal: search=yes, ai-input=no\nAllow: /\n")
    assert named_only["signals"] is None and named_only["formats"] == ["cloudflare_content_signal"]
    assert named_only["named_groups"][0]["signals"]["ai_input"] == "no"
    assert named_only["ai_input_no_agents"] == ["ChatGPT-User", "Claude-User"]


def test_agent_names_are_kept_only_from_the_token_table():
    parsed = parse_content_use("User-agent: <b>x</b>\nUser-agent: " + "a" * 60 + "\nUser-agent: good-bot\nUser-agent: perplexity-user\n"
                               "Content-Signal: ai-train=no\n")
    # "<b>x</b>" has no leading name characters, so it names no agent at all (D48 review: the product token is the
    # leading run of [a-z0-9_-], as in Google's robots.cc); the long name and good-bot are counted, never kept.
    assert parsed["named_groups"][0]["agents"] == ["Perplexity-User"] and parsed["named_groups"][0]["other_agents"] == 2


def test_aipref_figure_2_path_scoped_and_named_group():
    parsed = parse_content_use(AIPREF_FIGURE_2)
    assert parsed["formats"] == ["ietf_aipref"]
    assert parsed["usage"] == {"train_ai": "no", "ai_use": "unset", "search": "unset"}
    assert parsed["path_specific_samples"] == ["/ai-ok/"]  # sampled, never folded into the site-wide value
    assert parsed["named_groups"] == [{"agents": [], "other_agents": 1, "signals": None,
                                       "usage": {"train_ai": "yes", "ai_use": "unset", "search": "unset"},
                                       "path_specific_samples": []}]
    shown = view(AIPREF_FIGURE_2)
    assert shown["status"] == "declared" and shown["scope"] == "all_paths"
    only_paths = view("User-agent: *\nContent-Usage: /blog/ train-ai=n\nContent-Usage: /docs/\tai-use=n\n")
    assert only_paths["scope"] == "path_specific" and only_paths["usage"] is None
    assert only_paths["path_specific_samples"] == ["/blog/", "/docs/"]
    assert parse_content_use("User-agent: *\nContent-Usage: / train-ai=n\n")["usage"]["train_ai"] == "no"
    assert parse_content_use("User-agent: *\nContent-Signal: /* ai-train=no\n")["signals"]["ai_train"] == "no"


def test_path_samples_are_capped_in_total_across_groups():
    text = ("User-agent: *\nContent-Usage: /a/ train-ai=n\nContent-Usage: /b/ train-ai=n\n\n"
            "User-agent: GPTBot\nContent-Usage: /c/ train-ai=n\nContent-Usage: /d/ train-ai=n\n\n"
            "User-agent: Claude-User\nContent-Usage: /e/ train-ai=y\n")
    parsed = parse_content_use(text)
    total = parsed["path_specific_samples"] + [path for group in parsed["named_groups"] for path in group["path_specific_samples"]]
    assert parsed["path_specific_samples"] == ["/a/", "/b/"] and len(total) == MAX_USE_PATH_SAMPLES


def test_aipref_vocabulary_rules():
    def usage(line: str) -> tuple[dict | None, list[str]]:
        parsed = parse_content_use(f"User-agent: *\nContent-Usage: {line}\n")
        return parsed["usage"], parsed["ignored_keys"]

    assert usage("train-ai=n, search=y, ai-use=n")[0] == {"train_ai": "no", "ai_use": "no", "search": "yes"}
    assert usage("train-ai=y;source=x, train-ai=n")[0]["train_ai"] == "no"  # parameters ignored, last value kept
    found, ignored = usage("train-genai=n, search=Y, ai-use=yes, train-ai")
    assert found == {"train_ai": "unset", "ai_use": "unset", "search": "unset"}
    assert ignored == ["ai-use", "search", "train-ai", "train-genai"]
    found, ignored = usage("Train-AI=n, search=n")  # keys are case-sensitive: the dictionary does not parse
    assert found == {"train_ai": "unset", "ai_use": "unset", "search": "unset"} and ignored == ["unparsable"]
    assert usage("train-ai=n,")[1] == ["unparsable"]
    assert usage("")[1] == ["unparsable"]


def test_content_signal_case_values_and_bad_lines():
    parsed = parse_content_use("USER-AGENT: *\nCONTENT-SIGNAL: SEARCH=YES, AI-Input = No\ncontent-signal: ai-train=maybe, foo=bar, ai-train\n")
    assert parsed["signals"] == {"search": "yes", "ai_input": "no", "ai_train": "unset", "use": None}
    assert parsed["ignored_keys"] == ["ai-train", "foo"]
    weird = parse_content_use("User-agent: *\nContent-Signal: search=yes, <script>=1\nContent-Signal\nContent-Signal:\n")
    assert weird["signals"]["search"] == "yes" and weird["ignored_keys"] == ["unparsable"]
    assert parse_content_use("User-agent: *\nContent-Signal: ai-train=perhaps\n")["signals"] == {
        "search": "unset", "ai_input": "unset", "ai_train": "unset", "use": None}


def test_conflicts_resolve_to_the_most_restrictive_statement():
    text = ("User-agent: *\nContent-Signal: search=yes, ai-train=yes, use=full\nAllow: /\n\n"
            "User-agent: *\nContent-Signal: ai-train=no, use=reference\nContent-Usage: train-ai=y\nContent-Usage: train-ai=n\n")
    parsed = parse_content_use(text)
    assert parsed["signals"] == {"search": "yes", "ai_input": "unset", "ai_train": "no", "use": "reference"}
    assert parsed["usage"]["train_ai"] == "no"
    assert parsed["formats"] == ["cloudflare_content_signal", "ietf_aipref"]


def test_lines_outside_groups_comments_bom_crlf_and_caps():
    assert parse_content_use("Content-Signal: ai-train=no\nUser-agent: *\nDisallow:\n")["formats"] == []
    assert parse_content_use("User-agent: *\n# Content-Signal: ai-train=no\n")["formats"] == []
    assert parse_content_use("User-agent: *\nContent-Signal: ai-train=no # trailing comment\n")["signals"]["ai_train"] == "no"
    data = "﻿User-agent: *\r\nContent-Signal: ai-input=no\r\n".encode("utf-8")
    assert parse_content_use(data)["signals"]["ai_input"] == "no"
    padded = "# " + "a" * (ROBOTS_CAP + 10) + "\nUser-agent: *\nContent-Signal: ai-train=no\n"
    assert parse_content_use(padded)["formats"] == []
    many = "".join(f"User-agent: bot{i}\nContent-Signal: search=yes, ai-train={'no' if i % 2 else 'yes'}, x{i}=1\n\n" for i in range(20))
    capped = parse_content_use(many)
    assert len(capped["named_groups"]) == 2 and capped["named_groups_total"] == 2 and len(capped["ignored_keys"]) == 5
    # Grouping is split_groups', the same as the robots rules: without a blank line or a rule in between, a
    # Content-Signal line does not end the agent lines, so all twenty names share one group (most restrictive wins).
    joined = parse_content_use(many.replace("\n\n", "\n"))
    assert joined["named_groups_total"] == 1 and joined["named_groups"][0]["signals"]["ai_train"] == "no"
    long_path = parse_content_use("User-agent: *\nContent-Usage: /" + "p" * 200 + " train-ai=n\n")
    assert len(long_path["path_specific_samples"][0]) == 80


def test_a_5000_line_file_parses_in_linear_time():
    distinct = "".join(f"User-agent: bot{i}\nContent-Usage: /p{i}/ train-ai=n\n" for i in range(2500))
    repeated = "".join(f"User-agent: bot{i}\nContent-Signal: search=yes, ai-train=no\n" for i in range(2500))
    for text in (distinct, repeated):
        started = time.perf_counter()
        parsed = parse_content_use(text)
        assert time.perf_counter() - started < 1.0
    assert parsed["named_groups_total"] == 1 and parsed["named_groups"][0]["other_agents"] == 2500


@pytest.mark.parametrize(("robots_status", "status", "reason"), [
    ("absent", "none_declared", "robots_absent"),
    ("refused", "not_read", "robots_refused"),
    ("invalid", "not_read", "robots_invalid"),
    ("redirected", "not_read", "robots_redirected"),
    ("too_large", "not_read", "robots_too_large"),
    ("not_public", "not_read", "robots_not_public"),
    ("error", "not_read", "robots_error"),
    ("pending", "not_read", "pending"),
])
def test_not_reading_is_never_none_declared_and_is_compact(robots_status, status, reason):
    shown = content_use_view(robots_status, None, "www.example.com", None, "pending")
    assert shown == {"status": status, "reason": reason, "source_url": "https://www.example.com/robots.txt",
                     "fetched_at": None, "freshness": "pending"}


def test_ok_reads_declared_none_declared_and_results_that_are_not_current():
    nothing = view("User-agent: *\nDisallow: /private\n")
    assert nothing == {"status": "none_declared", "reason": None, "source_url": "https://example.com/robots.txt",
                       "fetched_at": NOW, "freshness": "fresh"}
    declared = view(CLOUDFLARE_MANAGED)
    assert declared["status"] == "declared" and declared["reason"] is None and declared["scope"] == "all_paths"
    assert declared["note"] == CONTENT_USE_NOTE and declared["vocab"] == CONTENT_USE_VOCAB
    old = content_use_view("ok", None, "example.com", NOW, "stale")
    assert (old["status"], old["reason"], old["freshness"]) == ("not_read", "read_before_content_use", "stale")
    # A result for another robots.txt (the row was rewritten, for example by an older release after a rollback) or
    # from another parser version is not shown as current.
    body = CLOUDFLARE_MANAGED.encode()
    stored = {**parse_content_use(body), "robots_sha256": sha256_hex(body)}
    assert content_use_view("ok", stored, "example.com", NOW, "fresh", sha256_hex(body))["status"] == "declared"
    assert content_use_view("ok", stored, "example.com", NOW, "fresh", sha256_hex(b"other"))["reason"] == "read_before_content_use"
    assert content_use_view("ok", {**stored, "vocab": "ahn-content-use/0"}, "example.com", NOW, "fresh",
                            sha256_hex(body))["reason"] == "read_before_content_use"


def test_build_profile_parses_the_same_bytes_and_makes_no_extra_request():
    calls = []
    body = CLOUDFLARE_MANAGED.encode()

    def reader(site_host, path, *, cap_bytes, **_):
        calls.append(path)
        return ("ok", body, "http_200") if path == "/robots.txt" else ("absent", None, "http_404")

    profile = build_profile("example.com", reader=reader)
    assert calls == ["/robots.txt", "/llms.txt"]
    assert profile["content_use"]["signals"]["ai_train"] == "no"
    assert profile["content_use"]["robots_sha256"] == profile["robots_sha256"] == sha256_hex(body)
    refused = build_profile("example.com", reader=lambda h, p, *, cap_bytes, **_: ("refused", None, "http_403"))
    assert refused["content_use"] is None


def test_the_answer_validates_in_both_shapes():
    profile = pending_profile("example.com", 5)
    assert profile["content_use"]["status"] == "not_read" and profile["content_use"]["reason"] == "pending"
    compact = AccessProfile(**profile, note="n").model_dump(mode="json")["content_use"]
    assert "note" not in compact and "vocab" not in compact and "signals" not in compact
    ok = {**pending_profile("example.com", None), "robots_status": "ok", "freshness": "fresh", "fetched_at": NOW,
          "robots": parse_robots(AIPREF_FIGURE_2), "content_use": view(AIPREF_FIGURE_2), "note": "n"}
    dumped = AccessProfile(**ok).model_dump(mode="json")["content_use"]
    assert dumped["usage"]["train_ai"] == "no" and dumped["named_groups"][0]["other_agents"] == 1 and dumped["note"]


def _profile(text: str) -> dict:
    return {"robots_status": "ok", "robots": parse_robots(text), "llms_txt_status": "absent", "freshness": "fresh",
            "content_use": view(text)}


def test_the_ai_input_check_is_informational_and_never_changes_the_recommendation():
    declaring = "User-agent: *\nContent-Signal: search=yes, ai-input=no, ai-train=no\nAllow: /\n"
    plain = "User-agent: *\nAllow: /\n"
    assert recommendation(None, None, _profile(declaring), None) == recommendation(None, None, _profile(plain), None)
    codes = [item["code"] for item in next_checks(None, None, "no_reports", access_profile=_profile(declaring))]
    assert codes.index("content_use_ai_input_declared_no") == codes.index("robots_allows_agents") + 1
    assert "content_use_ai_input_declared_no" not in [item["code"] for item in next_checks(None, None, "no_reports", access_profile=_profile(plain))]
    for text in (CHECKS["content_use_ai_input_declared_no"], CHECKS_KO["content_use_ai_input_declared_no"]):
        assert "ai-input=no" in text and ("operator's policy" in text or "운영사 정책" in text)
        for word in ("must not", "do not", "refuse", "prohibit", "forbid", "하지 마", "거절", "금지"):
            assert word not in text.lower()


def test_ai_input_follows_the_explicit_group_rule():
    # * says no, but ChatGPT-User has its own group: its effective value comes from that group, the rest from *.
    split = parse_content_use("User-agent: *\nContent-Signal: ai-input=no\n\nUser-agent: ChatGPT-User\nAllow: /\n")
    assert split["ai_input_declared_no"] and "ChatGPT-User" not in split["ai_input_no_agents"]
    assert "Claude-User" in split["ai_input_no_agents"]
    # Every user-directed token has an explicit group without the declaration: nothing applies to them.
    from ahn.access_profile import AGENT_USER_AGENTS
    tokens = [token for token, kind in AGENT_USER_AGENTS if kind == "user_agent"]
    covered = "User-agent: *\nContent-Signal: ai-input=no\n\n" + "".join(f"User-agent: {t}\n" for t in tokens) + "Allow: /\n"
    assert parse_content_use(covered)["ai_input_declared_no"] is False
    # A named group for a user-directed agent counts; one naming only a training crawler does not.
    assert parse_content_use("User-agent: Claude-User\nContent-Signal: ai-input=no\n")["ai_input_no_agents"] == ["Claude-User"]
    assert parse_content_use("User-agent: GPTBot\nContent-Signal: ai-input=no\n")["ai_input_declared_no"] is False
    assert parse_content_use("User-agent: *\nContent-Usage: ai-use=n\n")["ai_input_declared_no"] is True
    # Path-scoped only, ai-train only: no.
    assert parse_content_use("User-agent: *\nContent-Signal: /docs/ ai-input=no\n")["ai_input_declared_no"] is False
    assert parse_content_use("User-agent: *\nContent-Signal: ai-train=no\n")["ai_input_declared_no"] is False
    assert not ai_input_declared_no(content_use_view("refused", None, "example.com", None, "pending"))
    assert not ai_input_declared_no(None)


def test_groups_match_the_robots_rules_split():
    # `User-agent: *` / Content-Signal / blank / `User-agent: ChatGPT-User` / Disallow: the blank-line exception makes
    # two groups for the robots rules, and the same two groups for content use: ChatGPT-User does not inherit * here.
    text = "User-agent: *\nContent-Signal: ai-input=no\n\nUser-agent: ChatGPT-User\nDisallow: /private\n"
    rules = parse_robots(text)
    assert next(rule for rule in rules["rules"] if rule["agent"] == "ChatGPT-User")["explicit_group"] is True
    parsed = parse_content_use(text)
    assert "ChatGPT-User" not in parsed["ai_input_no_agents"] and "Claude-User" in parsed["ai_input_no_agents"]
    # Without the blank line both readings join the agent lines into one group, and ChatGPT-User shares the declaration.
    joined = parse_content_use(text.replace("\n\n", "\n"))
    assert "ChatGPT-User" in joined["ai_input_no_agents"] and joined["signals"]["ai_input"] == "no"
    # A User-agent value starting with "/" (the defect fixed in split_groups) does not raise here either.
    assert parse_content_use("User-agent: /bot\nContent-Signal: ai-train=no\n")["formats"] == ["cloudflare_content_signal"]


def test_ai_input_is_decided_before_groups_are_capped():
    # Seven distinct named declarations; the one for Claude-User is the last in the file and would fall past the cap.
    text = "".join(f"User-agent: bot{i}\nContent-Signal: search=yes, ai-train={'no' if i % 2 else 'yes'}, use={u}\n\n"
                   for i, u in enumerate(("full", "reference", "immediate", "full", "reference", "immediate")))
    text += "User-agent: Claude-User\nContent-Signal: ai-input=no\n"
    parsed = parse_content_use(text)
    assert parsed["named_groups_total"] == 7 and len(parsed["named_groups"]) == 5
    assert parsed["ai_input_no_agents"] == ["Claude-User"] and ai_input_declared_no(view(text))
