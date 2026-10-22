"""Adoption measurement (D49): classification tables, exclusions, the summary arithmetic and the retention numbers. No database."""

from __future__ import annotations

import inspect
import os
import re
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import get_args

import pytest

os.environ.setdefault("AHN_DATABASE_URL", "postgresql://invalid/unused")
os.environ.setdefault("AHN_KEY_PEPPER", "x" * 32)

from ahn import adoption, lookup_signals, mcp_adapter, platform_metrics, traffic
from ahn.models import Recommendation

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "clients" / "python" / "src"))
from ahn_client import _client, claude_agent_sdk, crewai, langchain, openai_agents  # noqa: E402


# --- classification ------------------------------------------------------------------------------

def test_every_client_type_hint_has_a_category():
    assert set(traffic._CATEGORY_OF_TYPE) == set(traffic.CLIENT_KINDS)
    assert {traffic.client_category(kind) for kind in traffic.CLIENT_KINDS} == set(traffic.CLIENT_CATEGORIES) | {traffic.CATEGORY_CRAWLER}
    assert traffic.client_category("anything-else") == "unknown"
    assert traffic.client_category("") == "unknown"


@pytest.mark.parametrize("agent, expected", [
    ("Claude-User/1.0", "claude"), ("Anthropic-MCP-Client", "claude"), ("claude-code/2.0.1", "claude"),
    ("openai-mcp/1.0.0", "chatgpt"), ("ChatGPT-User/1.0", "chatgpt"),
    ("Cursor/0.50", "cursor"), ("Visual Studio Code/1.99", "vscode"), ("vscode-copilot", "vscode"),
    ("python-httpx/0.27", "other_mcp"), ("node", "other_mcp"), ("", "unknown_mcp"), (None, "unknown_mcp"), ("   ", "unknown_mcp"),
    ("Claude-User; cursor", "claude"),  # first match in the fixed order, so the label is stable
])
def test_mcp_host_family_from_the_user_agent(agent, expected):
    assert traffic.mcp_host_kind(agent) == expected
    assert expected in traffic.MCP_HOSTS


@pytest.mark.parametrize("agent, expected", [
    ("ahn-client/0.4.0 (+langchain; +https://agenthelpnetwork.com)", "langchain"),
    ("ahn-client/0.4.0 (+crewai; +https://agenthelpnetwork.com)", "crewai"),
    ("ahn-client/0.4.0 (+openai-agents; +https://agenthelpnetwork.com)", "openai_agents"),
    ("ahn-client/0.4.0 (+claude-agent-sdk; +https://agenthelpnetwork.com)", "claude_agent_sdk"),
    ("AHN-Client/0.4.0 (+LangChain; +https://agenthelpnetwork.com)", "langchain"),   # case-insensitive, like client_type()
    ("ahn-client/0.4.0 (+somethingnew; +https://agenthelpnetwork.com)", "other"),   # well-formed but not listed
    ("ahn-client/0.4.0 (+https://agenthelpnetwork.com)", "none"),                    # plain client, no adapter
    ("ahn-client/0.4.0", "none"),
    ("ahn-claude-code-plugin/0.2", "none"),
    ("Mozilla/5.0 (+langchain; )", "none"),                                          # only ahn-client names an adapter
    ("ahn-client/0.4.0 (+" + "a" * 40 + "; )", "none"),                              # longer than any label the client allows
    ("", "none"), (None, "none"),
])
def test_adapter_label_from_the_user_agent(agent, expected):
    assert traffic.adapter_label(agent) == expected


def test_adapter_labels_match_the_ones_the_shipped_adapters_send():
    sent = {traffic.adapter_label(_client.user_agent(module.ADAPTER)) for module in (langchain, crewai, openai_agents, claude_agent_sdk)}
    assert sent == set(traffic.ADAPTERS)
    assert traffic.adapter_label(_client.user_agent()) == traffic.ADAPTER_NONE


def test_the_raw_header_is_never_a_stored_value():
    # Every label is drawn from a fixed list, so no header text can become a stored value.
    for agent in ("ahn-client/9 (+x; " + "z" * 600, "Claude" + "\u0000" * 10, "cursor\r\nX: y"):
        assert traffic.adapter_label(agent) in {*traffic.ADAPTERS, traffic.ADAPTER_NONE, traffic.ADAPTER_OTHER}
        assert traffic.mcp_host_kind(agent) in traffic.MCP_HOSTS


def test_mcp_adapter_forwards_a_label_not_the_user_agent():
    def ctx(headers):
        return SimpleNamespace(request_context=SimpleNamespace(request=SimpleNamespace(headers=headers)))
    forwarded = mcp_adapter.outer_request_headers(ctx({"user-agent": "Claude-User/1.0 secret-build-123", "cf-connecting-ip": "198.51.100.7"}))
    assert forwarded == {"x-ahn-origin-ip": "198.51.100.7", "x-ahn-origin-client": "claude"}
    assert "secret-build" not in repr(forwarded)
    # An unlisted host is labelled other_mcp; no User-Agent (or no context) sends no label, which the API reads as unknown_mcp.
    assert mcp_adapter.outer_request_headers(ctx({"user-agent": "python-httpx/0.27"}))["x-ahn-origin-client"] == "other_mcp"
    assert "x-ahn-origin-client" not in mcp_adapter.outer_request_headers(ctx({"user-agent": ""}))
    assert "x-ahn-origin-client" not in mcp_adapter.outer_request_headers(ctx({}))


# --- exclusions ----------------------------------------------------------------------------------

@pytest.mark.parametrize("host, test_host", [
    ("example.com", True), ("example.net", True), ("example.org", True), ("www.example.org", True), ("api.example.co.uk", True),
    ("example.io", True),                                                      # example.*
    ("agenthelpnetwork.com", True), ("www.agenthelpnetwork.com", True), ("EXAMPLE.com.", True),
    ("demo.test", True), ("x.invalid", True), ("app.localhost", True),
    ("demo-site.io", False), ("examples.io", False), ("myexample.com", False),  # only the whole label "example"
    ("notagenthelpnetwork.com", False), ("agenthelpnetwork.com.evil.io", False),
])
def test_test_hosts_are_never_counted(host, test_host):
    assert adoption.is_test_host(host) is test_host


def test_crawlers_and_the_web_page_are_stored_but_are_not_adoption():
    assert set(adoption.EXCLUDED) == {"crawler", "web"}
    assert not set(adoption.EXCLUDED) & set(traffic.CLIENT_CATEGORIES)
    assert set(adoption.AUTOMATIC) <= set(traffic.CLIENT_CATEGORIES)


def test_action_and_profile_state_come_from_the_answer():
    assert set(adoption.ACTIONS) == set(get_args(Recommendation.model_fields["action"].annotation))
    assert adoption.action_of({"recommendation": {"action": "stop_declared_block"}}) == "stop_declared_block"
    assert adoption.action_of({"recommendation": {"action": "made-up"}}) == "other"
    assert adoption.action_of({}) == "other"
    assert [adoption.profile_state(p) for p in (None, {}, {"freshness": "pending"}, {"freshness": "stale"}, {"freshness": "fresh"})] == \
           ["none", "none", "pending", "stale", "ready"]


# --- the summary ---------------------------------------------------------------------------------

class Rows:
    """Stands in for the connection: summary() makes one SELECT and reads the rows."""

    def __init__(self, rows):
        self.rows, self.sql = rows, None

    def execute(self, sql, params=()):
        self.sql, self.params = sql, params
        return self

    def fetchall(self):
        return self.rows


def row(day, category, lookups, sources=0, adapter="none", mcp_host="none", action="proceed", state="ready"):
    return {"day": day, "client_category": category, "adapter": adapter, "mcp_host": mcp_host, "action": action,
            "profile_state": state, "lookups": lookups, "sources": sources}


TODAY = date(2026, 10, 22)


def test_m1_counts_the_six_categories_and_leaves_crawlers_and_web_out():
    conn = Rows([
        row(TODAY, "plugin", 2, 2), row(TODAY, "ahn_client", 3, 1, adapter="langchain"), row(TODAY, "ahn_client", 1, 1),
        row(TODAY, "mcp_host", 4, 3, mcp_host="claude"), row(TODAY, "agent_or_script", 5), row(TODAY, "browser", 6, 6),
        row(TODAY, "unknown", 7), row(TODAY, "crawler", 100), row(TODAY, "web", 50),
    ])
    out = adoption.summary(conn, 7, TODAY)
    assert out["m1"] == 2 + 3 + 1 + 4 + 5 + 6 + 7
    assert out["m1_by_category"] == {"plugin": 2, "ahn_client": 4, "mcp_host": 4, "agent_or_script": 5, "browser": 6, "unknown": 7}
    assert out["m1_by_adapter"]["langchain"] == 3 and sum(out["m1_by_adapter"].values()) == 3   # a plain ahn-client row has no adapter
    assert out["m1_by_mcp_host"] == {"claude": 4, "chatgpt": 0, "cursor": 0, "vscode": 0, "other_mcp": 0, "unknown_mcp": 0}
    assert out["left_out"] == {"crawler": 100, "web": 50}
    assert out["automatic_share"] == round((2 + 4 + 4) / 28, 3)
    assert out["m2_sources_upper_bound"] == 2 + 1 + 1 + 3 + 6   # crawler and web rows add nothing, whatever they hold


def test_the_window_is_the_last_n_utc_days_including_today():
    conn = Rows([])
    out = adoption.summary(conn, 7, TODAY)
    assert (out["from"], out["to"], out["days"]) == ("2026-10-16", "2026-10-22", 7)
    assert conn.params == (date(2026, 10, 16), TODAY)            # both ends inclusive in the query
    assert [d["day"] for d in out["m1_by_day"]] == [f"2026-10-{n}" for n in range(16, 23)]
    assert adoption.summary(Rows([]), 1, TODAY)["from"] == "2026-10-22"
    assert adoption.summary(Rows([]), 0, TODAY)["days"] == 1                               # clamped, never an empty window
    assert adoption.summary(Rows([]), 5000, TODAY)["days"] == adoption.RETENTION_DAYS      # nothing older exists
    assert "day >= %s AND day <= %s" in conn.sql


def test_an_empty_window_has_zero_counts_and_no_ratios():
    out = adoption.summary(Rows([]), 7, TODAY)
    assert out["m1"] == 0 and out["automatic_share"] is None and out["m2_sources_upper_bound"] == 0
    assert out["m3"]["pending_share"] is None and out["m3"]["unknown_share_excluding_pending"] is None
    assert all(v == 0 for v in out["m1_by_category"].values()) and len(out["m1_by_category"]) == 6


def test_daily_totals_land_on_their_own_day():
    other = date(2026, 10, 20)
    out = adoption.summary(Rows([row(TODAY, "browser", 2, 1), row(other, "browser", 3, 2), row(other, "plugin", 1, 1)]), 7, TODAY)
    by_day = {d["day"]: d for d in out["m1_by_day"]}
    assert by_day["2026-10-22"] == {"day": "2026-10-22", "lookups": 2, "sources": 1}
    assert by_day["2026-10-20"] == {"day": "2026-10-20", "lookups": 4, "sources": 3}
    assert by_day["2026-10-21"]["lookups"] == 0


def test_m3_shares_pending_over_all_and_unknown_over_the_answered_ones():
    conn = Rows([
        row(TODAY, "browser", 4, action="unknown", state="pending"),        # first lookups: profile still being read
        row(TODAY, "browser", 2, action="unknown", state="ready"),
        row(TODAY, "browser", 3, action="proceed", state="ready"),
        row(TODAY, "plugin", 1, action="use_official_path", state="none"),
        row(TODAY, "crawler", 99, action="unknown", state="pending"),       # not adoption: no effect
    ])
    m3 = adoption.summary(conn, 7, TODAY)["m3"]
    assert m3["pending_share"] == 0.4                                     # 4 of 10
    assert m3["unknown_share_excluding_pending"] == round(2 / 6, 3)       # 2 of the 6 that were not pending
    assert m3["by_action"]["unknown"] == 6 and m3["by_action"]["proceed"] == 3 and m3["by_action"]["use_official_path"] == 1
    assert m3["by_profile_state"] == {"ready": 5, "stale": 0, "pending": 4, "none": 1}


def test_summary_output_has_no_free_text_or_identifier_fields():
    out = adoption.summary(Rows([row(TODAY, "browser", 1)]), 7, TODAY)
    text = repr(out)
    assert not re.search(r"\b(\d{1,3}\.){3}\d{1,3}\b", text)
    for key in ("site_host", "source_digest", "address", "user_agent"):
        assert key not in text


# --- retention and the migration -----------------------------------------------------------------

def test_retention_literal_in_the_cleanup_matches_the_constant():
    source = inspect.getsource(platform_metrics.prune_analytics)
    # `day < CURRENT_DATE - 89` keeps today and the 89 days before: 90 days.
    assert re.search(r"DELETE FROM lookup_outcome_daily WHERE day < CURRENT_DATE - %d\b" % (adoption.RETENTION_DAYS - 1), source)
    assert adoption.RETENTION_DAYS == 90


def test_the_migration_adds_one_table_with_fixed_width_columns_only():
    sql = (ROOT / "migrations" / "028_lookup_outcome_daily.sql").read_text(encoding="utf-8")
    code = "\n".join(line for line in sql.splitlines() if not line.lstrip().startswith("--"))
    assert re.findall(r"CREATE TABLE IF NOT EXISTS (\w+)", code) == ["lookup_outcome_daily"]
    assert not re.search(r"\b(ALTER|DROP|UPDATE|DELETE|TRUNCATE)\b", code)     # nothing existing is touched
    assert "Rollback: DROP TABLE lookup_outcome_daily;" in sql
    assert not re.search(r"\btext\b|\bip\b|inet|site_host|digest|bytea|jsonb?", code, re.I)   # no host, address, digest or free text
    widths = dict(re.findall(r"(\w+) varchar\((\d+)\)", code))
    longest = {
        "client_category": max(len(n) for n in (*traffic.CLIENT_CATEGORIES, traffic.CATEGORY_CRAWLER, traffic.CATEGORY_WEB)),
        "adapter": max(len(n) for n in (*traffic.ADAPTERS, traffic.ADAPTER_NONE, traffic.ADAPTER_OTHER)),
        "mcp_host": max(len(n) for n in (*traffic.MCP_HOSTS, traffic.MCP_HOST_NONE)),
        "action": max(len(n) for n in (*adoption.ACTIONS, adoption.OTHER)),
        "profile_state": max(len(n) for n in adoption.PROFILE_STATES),
    }
    for column, length in longest.items():
        assert int(widths[column]) >= length, f"{column} is too narrow for its longest value"


def test_nothing_in_the_answer_reads_the_adoption_labels():
    # Shadow like D47: the answer is built before the record is made and never reads it back.
    from ahn import service
    check_source = inspect.getsource(service.check)
    assert check_source.index('"recommendation"') < check_source.index("_record_signal(")
    # The only mention is the write of the row _record_signal returned, then a commit; nothing is read back.
    assert check_source.count("adoption") == 1 and "adoption.write(conn, count_row)" in check_source
    assert check_source.index("adoption.write(") > check_source.index("_record_signal(")
    assert check_source.index("conn.commit()", check_source.index("adoption.write(")) > 0   # committed right after
    assert "lookup_outcome_daily" not in check_source


@pytest.mark.parametrize("raw, expected", [(None, 7), ("1", 1), ("7", 7), ("30", 30), ("31", 31), ("90", 90),
                                           ("0", None), ("91", None), ("-1", None), ("abc", None), ("", None), ("9٠", None)])
def test_days_parser_takes_one_to_the_retention_limit(raw, expected):
    assert adoption.RETENTION_DAYS == 90
    assert adoption.parse_days(raw) == expected
