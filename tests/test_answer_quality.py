"""scripts/answer_quality.py: set file, offline coverage, answer classification, rate limit, change report (no network)."""

from __future__ import annotations

import importlib.util
import json
import sys
from collections import Counter
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load(name: str):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


aq = _load("answer_quality")


# --------------------------------------------------------------------------- the committed set

def test_v1_set_is_one_hundred_valid_unique_hosts_with_known_sources():
    rows = aq.load_set(aq.DEFAULT_SET)
    assert len(rows) == 100
    assert len({host for host, _ in rows}) == 100
    assert Counter(source for _, source in rows) == {"bench_wall": 13, "bench_task": 26, "lookup": 30, "d35": 31}
    assert aq.set_version(aq.DEFAULT_SET) == "v1"
    assert not {"agenthelpnetwork.com", "example.com", "example.org"} & {host for host, _ in rows}


def test_set_hash_ignores_line_ending_style(tmp_path):
    (tmp_path / "a.txt").write_bytes(b"github.com x\nquora.com y\n")
    (tmp_path / "b.txt").write_bytes(b"github.com x\r\nquora.com y\r\n")
    assert aq.file_sha256(tmp_path / "a.txt") == aq.file_sha256(tmp_path / "b.txt")


def test_load_set_rejects_bad_duplicate_and_non_registrable_hosts(tmp_path):
    for text in ("Example.COM x\n", "a.b.example.com x\n", "github.com x\ngithub.com y\n", "10.0.0.1 x\n"):
        path = tmp_path / "set.txt"
        path.write_text(text, encoding="utf-8")
        with pytest.raises(ValueError):
            aq.load_set(path)


def test_load_set_skips_comments_and_blank_lines_and_defaults_the_source(tmp_path):
    path = tmp_path / "failure_hosts_v7.txt"
    path.write_text("# comment\n\ngithub.com bench_wall\nstripe.com\n", encoding="utf-8")
    assert aq.load_set(path) == [("github.com", "bench_wall"), ("stripe.com", "unknown")]
    assert aq.set_version(path) == "v7"


# --------------------------------------------------------------------------- offline coverage

@pytest.fixture
def tables(monkeypatch):
    """A small code table, so the numbers do not move when the real tables grow."""
    paths = {
        "a.com": {"api_docs": "https://docs.a.com/", "access": {"auth": []}},
        "b.com": {"developer_portal": "https://dev.b.com/"},
        "c.com": {"note": "no link"},  # an entry without api_docs or developer_portal is not an official path
    }
    monkeypatch.setattr(aq, "official_paths", lambda host, today=None: paths.get(host))
    monkeypatch.setattr(aq, "SOURCES", {"a.com": object()})
    monkeypatch.setattr(aq, "OFFICIAL_STATUS_PAGES", {"b.com": ("B", "https://status.b.com/")})


def test_offline_row_reads_each_table(tables):
    seeds = frozenset({"a.com", "c.com"})
    assert aq.offline_row("a.com", seeds) == {"seed": True, "official_path": True, "status": "feed", "access_bundle": True}
    assert aq.offline_row("b.com", seeds) == {"seed": False, "official_path": True, "status": "page", "access_bundle": False}
    assert aq.offline_row("c.com", seeds) == {"seed": True, "official_path": False, "status": None, "access_bundle": False}
    assert aq.offline_row("d.com", seeds) == {"seed": False, "official_path": False, "status": None, "access_bundle": False}


def test_offline_row_answers_www_as_the_apex(tables):
    assert aq.offline_row("www.a.com", frozenset({"a.com"}))["official_path"] is True


def test_offline_metrics_counts_and_percentages(tables):
    seeds = frozenset({"a.com", "c.com"})
    rows = [aq.offline_row(h, seeds) for h in ("a.com", "b.com", "c.com", "d.com")]
    m = aq.offline_metrics(rows)
    assert m["hosts"] == 4
    assert m["seed"] == {"count": 2, "pct": 50.0}
    assert m["official_path"] == {"count": 2, "pct": 50.0}
    assert m["status"] == {"count": 2, "pct": 50.0}
    assert m["access_bundle"] == {"count": 1, "pct": 25.0}
    assert (m["status_feed"], m["status_page_only"]) == (1, 1)
    assert m["all_four"] == {"count": 1, "pct": 25.0}
    assert m["none"] == {"count": 1, "pct": 25.0}  # d.com only


def test_offline_metrics_on_an_empty_set_does_not_divide_by_zero():
    assert aq.offline_metrics([])["seed"] == {"count": 0, "pct": 0.0}


# --------------------------------------------------------------------------- answer classification

PENDING = {"freshness": "pending", "retry_after_seconds": 30, "robots_status": None}
OFFICIAL = {"api_docs": "https://docs.x.com/"}


def answer(action, *, official=None, profile=None, provider=None, page=None, confidence="low", reasons=()):
    return {"recommendation": {"action": action, "confidence": confidence, "reasons": list(reasons)},
            "official_paths": official, "access_profile": profile, "provider_status": provider,
            "official_status_page": page, "provider_source_state": "not_verified_by_current_methods"}


@pytest.mark.parametrize("action", ["stop_declared_block", "wait_provider_incident", "hand_off_to_human"])
def test_the_three_stop_or_wait_actions_are_actionable_without_an_official_path(action):
    c = aq.classify_answer(answer(action))
    assert c["actionable"] is True and c["no_data"] is False


def test_official_path_makes_an_answer_actionable_even_while_the_profile_is_pending():
    c = aq.classify_answer(answer("use_official_path", official=OFFICIAL, profile=PENDING))
    assert c["actionable"] and c["official_path"] and c["profile_pending"] and not c["no_data"]


def test_official_entry_without_a_link_does_not_count():
    c = aq.classify_answer(answer("unknown", official={"note": "x"}))
    assert not c["actionable"] and not c["official_path"] and c["no_data"]


def test_unknown_with_nothing_is_no_data_but_status_data_is_not():
    assert aq.classify_answer(answer("unknown", profile=PENDING))["no_data"] is True
    assert aq.classify_answer(answer("unknown", provider={"indicator": "none"}))["no_data"] is False
    assert aq.classify_answer(answer("unknown", page={"source_url": "https://s.x.com/"}))["no_data"] is False
    c = aq.classify_answer(answer("proceed", confidence="low"))
    assert not c["actionable"] and not c["no_data"]


def test_profile_pending_needs_a_queued_read():
    stale = {"freshness": "pending"}  # no retry_after_seconds: no read is queued, not "pending" for the agent
    assert aq.classify_answer(answer("unknown", profile=stale))["profile_pending"] is False
    assert aq.classify_answer(answer("unknown", profile=PENDING))["profile_pending"] is True


def test_access_bundle_is_read_from_the_official_paths_of_the_answer():
    c = aq.classify_answer(answer("use_official_path", official={**OFFICIAL, "access": {"auth": []}}))
    assert c["access_bundle"] is True


# --------------------------------------------------------------------------- live run with a fake server

class FakeServer:
    def __init__(self, bodies: dict[str, object]):
        self.bodies = bodies
        self.calls: list[tuple[str, dict]] = []

    def __call__(self, url, headers, timeout):
        self.calls.append((url, headers))
        host = parse_qs(urlsplit(url).query)["site_host"][0]
        value = self.bodies.get(host, 404)
        if isinstance(value, Exception):
            raise value
        if isinstance(value, int):
            return value, "{}"
        if isinstance(value, str):
            return 200, value
        return 200, json.dumps(value)


class FakeClock:
    def __init__(self):
        self.now = 100.0
        self.sleeps: list[float] = []
        self.starts: list[float] = []

    def clock(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


def test_live_run_sends_the_test_user_agent_and_the_host_only():
    server = FakeServer({"a.com": answer("use_official_path", official=OFFICIAL)})
    clock = FakeClock()
    result = aq.run_live(["a.com"], "https://ahn.example/", fetch=server, sleep=clock.sleep, clock=clock.clock)
    (url, headers), = server.calls
    assert url == "https://ahn.example/v1/incidents/check?site_host=a.com"
    assert headers["User-Agent"].startswith("ahn-quality-check/1")
    assert result["a.com"]["ok"] and result["a.com"]["actionable"]


def test_live_run_never_exceeds_one_request_per_second():
    hosts = [f"h{i}.com" for i in range(5)]
    server = FakeServer({h: answer("unknown") for h in hosts})
    clock = FakeClock()
    starts: list[float] = []

    def fetch(url, headers, timeout):
        starts.append(clock.now)
        clock.now += 0.2  # a fast answer: the run must still wait out the rest of the second
        return server(url, headers, timeout)

    aq.run_live(hosts, "https://ahn.example", fetch=fetch, sleep=clock.sleep, clock=clock.clock)
    assert len(starts) == 5
    assert all(b - a >= 1.0 - 1e-9 for a, b in zip(starts, starts[1:]))


def test_live_run_records_errors_and_goes_on():
    server = FakeServer({"ok.com": answer("unknown"), "err.com": 503, "boom.com": TimeoutError("slow"),
                         "junk.com": "not json", "list.com": "[1]"})
    clock = FakeClock()
    hosts = ["ok.com", "err.com", "boom.com", "junk.com", "list.com"]
    result = aq.run_live(hosts, "https://ahn.example", fetch=server, sleep=clock.sleep, clock=clock.clock)
    assert [result[h]["ok"] for h in hosts] == [True, False, False, False, False]
    assert result["err.com"]["error"] == "http_503"
    assert result["boom.com"]["error"] == "TimeoutError"
    assert result["junk.com"]["error"] == "invalid_json" and result["list.com"]["error"] == "invalid_json"
    assert len(server.calls) == 5  # one attempt each, no retry


def test_live_metrics_use_the_answered_hosts_as_denominator():
    results = {
        "a": {"ok": True, **aq.classify_answer(answer("use_official_path", official=OFFICIAL))},
        "b": {"ok": True, **aq.classify_answer(answer("unknown", profile=PENDING))},
        "c": {"ok": True, **aq.classify_answer(answer("stop_declared_block"))},
        "d": {"ok": True, **aq.classify_answer(answer("proceed"))},
        "e": {"ok": False, "http_status": 500, "error": "http_500"},
    }
    m = aq.live_metrics(results)
    assert (m["hosts"], m["answered"], m["errors"]) == (5, 4, 1)
    assert m["actionable"] == {"count": 2, "pct": 50.0}
    assert m["profile_pending"] == {"count": 1, "pct": 25.0}
    assert m["no_data"] == {"count": 1, "pct": 25.0}
    assert m["actions"] == {"proceed": 1, "stop_declared_block": 1, "unknown": 1, "use_official_path": 1}


# --------------------------------------------------------------------------- change against the previous run

def row(host, *, seed=True, official=True, actionable=True, action="use_official_path", no_data=False, ok=True, source="d35"):
    live = {"ok": ok, "action": action, "actionable": actionable, "no_data": no_data} if ok else {"ok": False, "error": "http_500"}
    return {"host": host, "source": source, "offline": {"seed": seed, "official_path": official, "status": None, "access_bundle": False},
            "live": live}


def result_with(hosts, version="v1", actionable_pct=50.0):
    return {"schema_version": 1, "run_date": "2026-10-22", "set": {"version": version}, "hosts": hosts,
            "offline": {"metrics": {"seed": {"count": 1, "pct": 50.0}, "official_path": {"count": 1, "pct": 50.0},
                                    "status": {"count": 0, "pct": 0.0}, "access_bundle": {"count": 0, "pct": 0.0},
                                    "all_four": {"count": 0, "pct": 0.0}}},
            "live": {"metrics": {"actionable": {"count": 1, "pct": actionable_pct}, "profile_pending": {"count": 0, "pct": 0.0},
                                 "no_data": {"count": 0, "pct": 0.0}}}}


def test_compare_without_a_previous_run():
    assert aq.compare(result_with([row("a.com")]), None) == {"previous_run": None}


def test_compare_lists_hosts_that_got_worse_and_better():
    before = result_with([row("a.com"), row("b.com"), row("c.com"), row("d.com", actionable=False, action="unknown", no_data=True),
                          row("e.com"), row("gone.com")], actionable_pct=60.0)
    before["run_date"] = "2026-10-15"
    now = result_with([
        row("a.com", official=False, actionable=False, action="unknown", no_data=True),  # lost the path, answer got empty
        row("b.com"),  # unchanged
        row("c.com", ok=False),  # lookup failed
        row("d.com"),  # better
        row("e.com", seed=False),  # lost its seed entry only
        row("new.com"),
    ], actionable_pct=50.0)
    change = aq.compare(now, before)
    assert change["previous_run"] == "2026-10-15" and change["same_set"] is True
    worse = {w["host"]: w["reasons"] for w in change["worse_hosts"]}
    assert worse["a.com"] == ["lost_official_path", "no_longer_actionable", "became_no_data"]
    assert worse["c.com"] == ["lookup_failed"]
    assert worse["e.com"] == ["lost_seed"]
    assert "b.com" not in worse and "d.com" not in worse
    assert change["better_hosts"] == ["d.com"]
    assert {"host": "d.com", "from": "unknown", "to": "use_official_path"} in change["action_changes"]
    assert change["new_hosts"] == ["new.com"] and change["dropped_hosts"] == ["gone.com"]
    assert change["live_pct_points"]["actionable"] == -10.0
    assert change["offline_pct_points"]["seed"] == 0.0


def test_compare_flags_a_changed_set_version():
    change = aq.compare(result_with([row("a.com")], version="v2"), result_with([row("a.com")], version="v1"))
    assert change["same_set"] is False


def test_previous_result_picks_the_newest_earlier_file(tmp_path):
    for name, version in (("2026-10-08.json", 1), ("2026-10-15.json", 1), ("2026-10-22.json", 1), ("notes.json", 1), ("2026-10-01.json", 99)):
        (tmp_path / name).write_text(json.dumps({"schema_version": version, "run_date": name[:10]}), encoding="utf-8")
    assert aq.previous_result(tmp_path, "2026-10-22")["run_date"] == "2026-10-15"  # today's own file is not "previous"
    assert aq.previous_result(tmp_path / "missing", "2026-10-22") is None


def test_previous_result_skips_an_unreadable_file(tmp_path):
    (tmp_path / "2026-10-15.json").write_text("{broken", encoding="utf-8")
    (tmp_path / "2026-10-08.json").write_text(json.dumps({"schema_version": 1, "run_date": "2026-10-08"}), encoding="utf-8")
    assert aq.previous_result(tmp_path, "2026-10-22")["run_date"] == "2026-10-08"


# --------------------------------------------------------------------------- whole run

def small_set(tmp_path):
    path = tmp_path / "failure_hosts_v1.txt"
    path.write_text("github.com bench_wall\nquora.com d35\n", encoding="utf-8")
    return path


def test_build_result_offline_only_has_no_live_section_and_makes_no_request(tmp_path):
    def forbidden(*_a, **_k):
        raise AssertionError("offline run must not touch the network")

    result = aq.build_result(small_set(tmp_path), run_date="2026-10-22", base_url="https://x", live=False, fetch=forbidden)
    assert "live" not in result and result["set"]["hosts"] == 2 and result["set"]["version"] == "v1"
    assert [h["host"] for h in result["hosts"]] == ["github.com", "quora.com"]
    assert set(result["by_source"]) == {"bench_wall", "d35"}
    assert result["changes"] == {"previous_run": None}


def test_build_result_with_a_fake_server(tmp_path):
    server = FakeServer({"github.com": answer("use_official_path", official=OFFICIAL), "quora.com": answer("unknown")})
    clock = FakeClock()
    result = aq.build_result(small_set(tmp_path), run_date="2026-10-22", base_url="https://x", live=True,
                             fetch=server, sleep=clock.sleep, clock=clock.clock)
    live = result["live"]["metrics"]
    assert live["actionable"] == {"count": 1, "pct": 50.0} and live["no_data"] == {"count": 1, "pct": 50.0}
    assert result["by_source"]["bench_wall"]["actionable"] == 1 and result["by_source"]["d35"]["no_data"] == 1
    assert "hosts" not in aq.summary(result)
    json.dumps(result)  # serialisable


def test_main_writes_the_dated_file_and_reports_the_change(tmp_path, capsys, monkeypatch):
    out = tmp_path / "out"
    out.mkdir()
    (out / "2026-10-15.json").write_text(json.dumps({**result_with([row("github.com")]), "run_date": "2026-10-15"}), encoding="utf-8")
    server = FakeServer({"github.com": answer("unknown"), "quora.com": answer("unknown")})
    clock = FakeClock()
    monkeypatch.setattr(aq, "default_fetch", server)
    monkeypatch.setattr(aq.time, "sleep", clock.sleep)
    monkeypatch.setattr(aq.time, "monotonic", clock.clock)
    code = aq.main(["--set", str(small_set(tmp_path)), "--out-dir", str(out), "--date", "2026-10-22", "--base-url", "https://x"])
    assert code == 0
    saved = json.loads((out / "2026-10-22.json").read_text(encoding="utf-8"))
    assert len(saved["hosts"]) == 2 and saved["changes"]["previous_run"] == "2026-10-15"
    printed = json.loads(capsys.readouterr().out)
    assert "hosts" not in printed and printed["live"]["metrics"]["answered"] == 2
    assert printed["changes"]["worse_hosts"][0]["host"] == "github.com"


def test_main_offline_only_and_no_save(tmp_path, capsys):
    out = tmp_path / "out"
    assert aq.main(["--set", str(small_set(tmp_path)), "--out-dir", str(out), "--offline-only", "--no-save", "--date", "2026-10-22"]) == 0
    assert not out.exists()
    assert json.loads(capsys.readouterr().out)["offline"]["metrics"]["hosts"] == 2


# --------------------------------------------------------------------------- D48: a second pass with agent_vendor

def test_vendor_pass_scores_the_same_hosts_again_with_the_vendor_and_lists_the_action_changes(tmp_path):
    calls: list[str] = []

    def fetch(url, headers, timeout):
        calls.append(url)
        query = parse_qs(urlsplit(url).query)
        vendor = query.get("agent_vendor", [None])[0]
        action = {"github.com": "use_official_path", "quora.com": "proceed" if vendor == "anthropic" else "unknown"}[query["site_host"][0]]
        return 200, json.dumps(answer(action, official=OFFICIAL if action == "use_official_path" else None))

    clock = FakeClock()
    result = aq.build_result(small_set(tmp_path), run_date="2026-10-22", base_url="https://x", live=True,
                             fetch=fetch, sleep=clock.sleep, clock=clock.clock, vendor_pass="anthropic")
    assert calls == ["https://x/v1/incidents/check?site_host=github.com", "https://x/v1/incidents/check?site_host=quora.com",
                     "https://x/v1/incidents/check?site_host=github.com&agent_vendor=anthropic",
                     "https://x/v1/incidents/check?site_host=quora.com&agent_vendor=anthropic"]
    assert result["live_vendor"]["agent_vendor"] == "anthropic"
    assert result["live_vendor"]["action_differs"] == [{"host": "quora.com", "without": "unknown", "with": "proceed"}]
    assert result["live"]["metrics"]["actions"] == {"unknown": 1, "use_official_path": 1}
    assert result["live_vendor"]["metrics"]["actions"] == {"proceed": 1, "use_official_path": 1}
    assert all("live_vendor" in row for row in result["hosts"]) and "agent_vendor" not in json.dumps(result["live"])
    # Without the option nothing changes: one pass, no vendor in any request.
    plain = aq.build_result(small_set(tmp_path), run_date="2026-10-22", base_url="https://x", live=True,
                            fetch=fetch, sleep=clock.sleep, clock=clock.clock)
    assert "live_vendor" not in plain and len(calls) == 6 and "agent_vendor" not in calls[-1]


def test_vendor_pass_accepts_only_listed_vendors(capsys):
    with pytest.raises(SystemExit):
        aq.main(["--offline-only", "--no-save", "--vendor-pass", "Anthropic"])
    assert "--vendor-pass" in capsys.readouterr().err
