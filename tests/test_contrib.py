"""Data contributions: the contrib/ format check (scripts/check_contrib.py, no network) and the /contribute pages."""

from __future__ import annotations

import copy
import importlib.util
import json
import re
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ahn.main import create_app
from ahn.settings import Settings

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 10, 31, 12, 0, tzinfo=timezone.utc)


def _checker():
    spec = importlib.util.spec_from_file_location("check_contrib", ROOT / "scripts" / "check_contrib.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


CHECK = _checker()

PATH = {"schema_version": 1, "kind": "official_path", "observed_at": "2026-10-31T09:30:00Z",
        "source_url": "https://docs.example.com/api",
        "payload": {"site_host": "example.com", "name": "Example API", "api_docs": "https://docs.example.com/api",
                    "developer_portal": None, "note": None}, "credit": "a-handle"}
STATUS = {"schema_version": 1, "kind": "status_source", "observed_at": "2026-10-31T09:35:00Z",
          "source_url": "https://status.example.com/",
          "payload": {"site_host": "example.com", "source_name": "Example Status", "status_page_url": "https://status.example.com/",
                      "api_url": "https://status.example.com/api/v2/summary.json", "feed_kind": "statuspage"}}
ACCESS = copy.deepcopy(PATH)
ACCESS["payload"]["access"] = {"auth": [{"method": "api_key", "source_url": "https://docs.example.com/api/auth"}],
                               "key_signup_url": "https://docs.example.com/keys", "verified_at": "2026-10-31"}


def check(record):
    return CHECK.check_record(record, now=NOW)


def changed(base, **top):
    record = copy.deepcopy(base)
    for key, value in top.items():
        if value is KeyError:
            record.pop(key)
        else:
            record[key] = value
    return record


def in_payload(base, **fields):
    record = copy.deepcopy(base)
    for key, value in fields.items():
        if value is KeyError:
            record["payload"].pop(key)
        else:
            record["payload"][key] = value
    return record


# --- the format check ---------------------------------------------------------------------------

@pytest.mark.parametrize("record", [PATH, STATUS, ACCESS], ids=["official_path", "status_source", "access"])
def test_good_records_pass_without_warnings(record):
    assert check(record) == ([], [])


def test_link_only_status_mapping_passes():
    record = in_payload(STATUS, api_url=None, feed_kind=None)
    assert check(record) == ([], [])


def test_credit_is_optional():
    assert check(changed(PATH, credit=KeyError)) == ([], [])


@pytest.mark.parametrize("record", [
    changed(PATH, kind="observation"),                                  # not a contributable kind
    changed(PATH, schema_version=2),
    changed(PATH, record_id="ahn:agenthelpnetwork.com:official_path:example.com:2026"),   # envelope fields are ours to fill in
    changed(PATH, signature=None),
    changed(PATH, observed_at="2026-10-31"),
    changed(PATH, observed_at="2026-10-31T09:30:00+00:00"),
    changed(PATH, observed_at="2026-13-31T09:30:00Z"),
    changed(PATH, observed_at="2026-11-05T09:30:00Z"),                  # in the future
    changed(PATH, source_url="http://docs.example.com/api"),
    changed(PATH, source_url="https://docs.example.com/api?token=abc"),     # a credential name in the query is an error
    changed(PATH, source_url="https://docs.example.com/api#access_token=abc"),
    changed(PATH, source_url="https://user:pw@docs.example.com/api"),
    changed(PATH, source_url="https://docs.example.com:8443/api"),
    changed(PATH, source_url="https://127.0.0.1/api"),
    changed(PATH, source_url="https://localhost/api"),
    changed(PATH, source_url="https://docs.example.com/a b"),
    changed(PATH, source_url=KeyError),
    changed(PATH, credit=""),
    changed(PATH, credit="x" * 61),
    changed(PATH, payload=[]),
    in_payload(PATH, site_host="Example.com"),
    in_payload(PATH, site_host="https://example.com"),
    in_payload(PATH, site_host="example.com/path"),
    in_payload(PATH, site_host="example"),
    in_payload(PATH, site_host="10.0.0.1"),
    in_payload(PATH, name=KeyError),
    in_payload(PATH, api_docs=None),                                   # needs a path to document
    in_payload(PATH, extra_field="x"),
    in_payload(PATH, rank=5),
    in_payload(PATH, note="x" * 301),
    in_payload(PATH, note="two\nlines"),
    in_payload(PATH, note="write to me at someone@example.org"),
    in_payload(PATH, note="use Bearer abcdefghijklmnop1234"),
    in_payload(PATH, note="reach me at someone (at) example.org"),     # the usual ways around the address pattern
    in_payload(PATH, note="reach me at someone[at]example[dot]org"),
    in_payload(PATH, note="reach me at someone\uff20example.org"),
    in_payload(PATH, note="key " + "sk_" + "live_" + "A" * 24),            # dummy values shaped like real keys
    in_payload(PATH, note="key " + "rk_" + "test_" + "A" * 24),
    in_payload(PATH, note="pat " + "github_pat_" + "A" * 30),
    in_payload(PATH, note="pat " + "glpat-" + "A" * 20),
    in_payload(PATH, note="key " + "AIza" + "A" * 35),
    in_payload(PATH, note="jwt " + "eyJhbGciOi" + ".eyJzdWIiOiJ4" + ".c2lnbmF0dXJl"),
    in_payload(PATH, note="hook " + "https://hooks.slack.com/services/" + "T000DUMMY/B000DUMMY/xxxxxxxx"),
    in_payload(PATH, note="mixed \u202etxt.exe"),                        # direction control
    in_payload(PATH, note="x" * 2001),
    in_payload(PATH, api_docs="https://docs.example.com/api?key=1"),
    in_payload(PATH, api_docs="docs.example.com/api"),
    in_payload(STATUS, source_name=KeyError),
    in_payload(STATUS, status_page_url=KeyError),
    in_payload(STATUS, feed_kind=None),                                # api_url and feed_kind go together
    in_payload(STATUS, api_url=None),
    in_payload(STATUS, feed_kind="rss_of_my_own"),
    in_payload(STATUS, site_host="not a host"),
])
def test_malformed_records_are_refused(record):
    errors, _ = check(record)
    assert errors, record


def test_forbidden_and_secret_looking_fields_are_refused_at_any_depth():
    for field in ("api_key", "password", "cookie", "email", "reporter_id", "ip_address", "token"):
        errors, _ = check(in_payload(ACCESS, **{field: "x"}))
        assert errors, field
    nested = copy.deepcopy(ACCESS)
    nested["payload"]["access"]["auth"][0]["session"] = "x"
    assert check(nested)[0]


def test_access_bundle_rules_are_the_curation_rules():
    # Unknown auth method, null instead of a left-out field, a future date and a quote over 200 characters all fail.
    assert check(in_payload(ACCESS, access={"auth": [{"method": "magic", "source_url": "https://docs.example.com/x"}],
                                            "verified_at": "2026-10-31"}))[0]
    assert check(in_payload(ACCESS, access={"key_signup_url": None, "verified_at": "2026-10-31"}))[0]
    assert check(in_payload(ACCESS, access={"key_signup_url": "https://docs.example.com/keys", "verified_at": "2026-12-31"}))[0]
    long_quote = {"url": "https://docs.example.com/terms", "quote": "x" * 201}
    assert check(in_payload(ACCESS, access={"terms": long_quote, "verified_at": "2026-10-31"}))[0]
    assert check(in_payload(ACCESS, access={"verified_at": "2026-10-31"}))[0]   # a date alone is not a bundle


def test_a_url_outside_the_hosts_own_domain_is_a_warning_not_an_error():
    errors, warnings = check(in_payload(PATH, api_docs="https://developer.other-publisher.com/api"))
    assert errors == [] and len(warnings) == 1 and "api_docs" in warnings[0]
    errors, warnings = check(in_payload(ACCESS, access={"key_signup_url": "https://keys.other-publisher.com/",
                                                        "verified_at": "2026-10-31"}))
    assert errors == [] and warnings


def test_file_rules(tmp_path):
    def write(name, *lines):
        path = tmp_path / name
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return path

    good = write("2026-11-03-my-handle.jsonl", json.dumps(PATH), "", json.dumps(STATUS))
    assert CHECK.check_file(good, now=NOW) == ([], [])
    assert CHECK.check_file(write("Bad Name.jsonl", json.dumps(PATH)), now=NOW)[0]
    assert CHECK.check_file(write("notes.jsonl", "not json"), now=NOW)[0]
    assert CHECK.check_file(write("empty.jsonl", ""), now=NOW)[0]
    assert CHECK.check_file(write("many.jsonl", *[json.dumps(PATH)] * (CHECK.MAX_LINES + 1)), now=NOW)[0]
    big = tmp_path / "big.jsonl"
    big.write_bytes(b"x" * (CHECK.MAX_FILE_BYTES + 1))
    assert CHECK.check_file(big, now=NOW)[0]
    (tmp_path / "latin.jsonl").write_bytes(b"\xff\xfe")
    assert CHECK.check_file(tmp_path / "latin.jsonl", now=NOW)[0]


def test_addresses_inside_the_access_bundle_get_the_same_query_checks():
    base = {"auth": [{"method": "api_key", "source_url": "https://docs.example.com/api/auth"}],
            "key_signup_url": "https://docs.example.com/keys", "verified_at": "2026-10-31"}
    bad = {
        "key_signup_url": "https://docs.example.com/keys?token=abc123",
        "openapi_url": "https://docs.example.com/openapi.json?key=abc123",
        "rate_limits_url": "https://docs.example.com/limits?sig=abc",
        "terms": {"url": "https://docs.example.com/terms?session=xyz", "quote": "Terms apply."},
        "auth": [{"method": "api_key", "source_url": "https://docs.example.com/auth?api_key=abc"}],
        "free_tier": {"quote": "Free tier available.", "source_url": "https://docs.example.com/pricing?key=abc"},
        "official_mcp": [{"endpoint": "https://mcp.example.com/sse?token=abc", "doc_url": "https://docs.example.com/mcp"}],
    }
    for field, value in bad.items():
        errors, _ = check(in_payload(PATH, access={**base, field: value}))
        assert any("key, token or session" in item for item in errors), (field, errors)
    # an ordinary query is a warning there too, and a fragment anchor is fine
    errors, warnings = check(in_payload(PATH, access={**base, "key_signup_url": "https://docs.example.com/keys?lang=en"}))
    assert not any("query" in item for item in errors) and any("query string" in item for item in warnings)
    assert check(in_payload(ACCESS, access={**base, "key_signup_url": "https://docs.example.com/keys#new"})) == ([], [])


def test_the_check_makes_no_network_request(monkeypatch):
    import socket

    def refuse(*args, **kwargs):
        raise AssertionError("the contribution check must not open a connection")

    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(socket.socket, "connect", refuse)
    assert check(ACCESS) == ([], [])


def test_everything_in_contrib_passes_the_check():
    """The CI hook: a pull request that adds a malformed file under contrib/ fails here."""
    files = CHECK.contrib_files()
    errors = []
    for path in files:
        found, _ = CHECK.check_file(path)
        errors += found
    assert errors == [], errors
    assert CHECK.main([str(path) for path in files]) == 0


def test_readme_examples_pass_the_check():
    text = (ROOT / "contrib" / "README.md").read_text(encoding="utf-8")
    blocks = re.findall(r"```jsonl\n(.*?)```", text, re.S)
    lines = [line for block in blocks for line in block.splitlines() if line.strip()]
    assert len(lines) == 3
    kinds = [json.loads(line)["kind"] for line in lines]
    assert kinds == ["official_path", "status_source", "official_path"]
    for line in lines:
        assert check(json.loads(line)) == ([], []), line


def test_accepted_fields_are_the_expected_ones():
    # Hard-coded on purpose: comparing the checker with the bundle's own constants would only show they agree.
    assert CHECK.PAYLOAD_KEYS["official_path"] == {"site_host", "name", "api_docs", "developer_portal", "note", "access"}
    assert CHECK.PAYLOAD_KEYS["status_source"] == {"site_host", "source_name", "status_page_url", "api_url", "feed_kind"}
    assert set(CHECK.LINE_KEYS) == {"schema_version", "kind", "observed_at", "source_url", "payload", "credit"}
    for name in ("ip_address", "api_key", "reporter_id", "password", "token", "cookie", "email", "rank"):
        assert name in CHECK.FORBIDDEN_KEYS


def _as_contribution(record: dict) -> dict:
    """A bundle record as a contributor would send it: the envelope fields we fill in are left out."""
    line = {key: record[key] for key in ("schema_version", "kind", "observed_at", "source_url", "payload")}
    if line["source_url"] is None:
        del line["source_url"]
    return line


def test_every_curated_entry_passes_as_a_contribution():
    """What the service itself publishes must be acceptable when someone sends it back (warnings are allowed)."""
    from ahn import bundle
    from ahn.official_paths import OFFICIAL_PATHS
    from ahn.status_refresh import OFFICIAL_STATUS_PAGES, SOURCES

    failures = []
    for host in OFFICIAL_PATHS:
        errors, _ = check(_as_contribution(bundle.official_path_record(host, NOW)))
        if errors:
            failures.append((host, errors))
    for host in set(SOURCES) | set(OFFICIAL_STATUS_PAGES):
        errors, _ = check(_as_contribution(bundle.status_source_record(host, NOW)))
        if errors:
            failures.append((host, errors))
    assert failures == [], failures
    assert len(OFFICIAL_PATHS) > 50 and len(SOURCES) > 20


def test_a_host_with_no_public_api_can_be_described():
    record = {"schema_version": 1, "kind": "official_path", "observed_at": "2026-10-31T09:30:00Z",
              "payload": {"site_host": "example.com", "name": "Example", "api_docs": None, "developer_portal": None,
                          "note": "No public API; the site is closed to programmatic use."}}
    assert check(record) == ([], [])
    assert check(in_payload(record, note=None))[0]                          # nothing says what was found
    assert check(changed(record, source_url="https://example.com/terms")) == ([], [])
    assert check(changed(record, source_url="http://example.com/terms"))[0]  # a source, when given, is still checked
    assert check(changed(PATH, source_url=KeyError))[0]                      # with a documentation page the source stays required


def test_fragments_pass_and_query_strings_warn():
    assert check(changed(PATH, source_url="https://docs.example.com/api#section-2")) == ([], [])
    assert check(in_payload(PATH, api_docs="https://docs.example.com/api#auth")) == ([], [])
    errors, warnings = check(in_payload(PATH, api_docs="https://docs.example.com/api?lang=en"))
    assert errors == [] and len(warnings) == 1 and "query string" in warnings[0]
    errors, warnings = check(changed(PATH, source_url="https://docs.example.com/api?lang=en"))
    assert errors == [] and len(warnings) == 1
    assert check(changed(PATH, source_url="https://docs.example.com/api#a=1&b=2"))[1]          # parameters in a fragment warn
    for secret in ("?api_key=1", "?x=1&token=2", "#access_token=abc", "?sig=abc"):
        assert check(changed(PATH, source_url="https://docs.example.com/api" + secret))[0], secret


def test_a_full_width_address_is_caught_only_after_normalising():
    # Full-width letters and dot are outside the plain address pattern; only the NFKC pass turns them into one.
    errors, _ = check(in_payload(PATH, note="mail \uff53\uff4f\uff4d\uff45\uff4f\uff4e\uff45\uff20\uff45\uff58\uff41\uff4d\uff50\uff4c\uff45\uff0e\uff4f\uff52\uff47"))
    assert any("email" in item for item in errors)
    assert not CHECK.EMAIL.search("\uff53\uff4f\uff4d\uff45\uff4f\uff4e\uff45\uff20\uff45\uff58\uff41\uff4d\uff50\uff4c\uff45\uff0e\uff4f\uff52\uff47")


def test_scanning_is_fast_and_long_text_is_refused_before_any_pattern_runs():
    import time

    for text in ("a" * 256_000, "a@" * 100_000, "a." * 100_000, ("a" * 60 + " (at) ") * 20_000):
        started = time.perf_counter()
        errors, _ = check(in_payload(PATH, note=text))
        assert any("over 2000" in item for item in errors) and time.perf_counter() - started < 1.0
    started = time.perf_counter()
    errors, _ = check(in_payload(PATH, zz="a" * 256_000))                  # an unknown key is scanned too, after the limit
    assert any("over 2000" in item for item in errors)
    assert time.perf_counter() - started < 1.0
    assert check(in_payload(PATH, note="x" * 300)) == ([], [])             # ordinary text near the limits still passes


def test_deep_and_wide_json_is_an_error_not_a_crash(tmp_path):
    deep = in_payload(PATH, note=None)
    node = deep["payload"]
    for _ in range(50):
        node["x"] = {}
        node = node["x"]
    assert check(deep)[0]
    assert check(in_payload(PATH, access={"auth": [{"method": "api_key"}] * 60}))[0]
    path = tmp_path / "deep.jsonl"
    path.write_text("[" * 20000 + "]" * 20000 + "\n", encoding="utf-8")
    errors, _ = CHECK.check_file(path, now=NOW)
    assert errors and "not valid JSON" in errors[0]


def test_file_size_is_checked_before_reading_and_symlinks_are_refused(tmp_path, monkeypatch):
    big = tmp_path / "big.jsonl"
    big.write_bytes(b"x" * (CHECK.MAX_FILE_BYTES + 1))
    opened = []
    original = Path.open
    monkeypatch.setattr(Path, "open", lambda self, *a, **k: opened.append(self) or original(self, *a, **k))
    errors, _ = CHECK.check_file(big, now=NOW)
    assert errors and "over" in errors[0] and opened == []
    target = tmp_path / "real.jsonl"
    target.write_text(json.dumps(PATH) + "\n", encoding="utf-8")
    link = tmp_path / "link.jsonl"
    try:
        link.symlink_to(target)
    except (OSError, NotImplementedError):
        pytest.skip("symbolic links are not available here")
    errors, _ = CHECK.check_file(link, now=NOW)
    assert errors and "symbolic link" in errors[0]


def test_unicode_line_separators_do_not_split_a_record(tmp_path):
    record = in_payload(PATH, note="before after end")           # legal inside a JSON string
    path = tmp_path / "sep.jsonl"
    path.write_text(json.dumps(record, ensure_ascii=False) + "\n", encoding="utf-8")
    assert " " in path.read_text(encoding="utf-8")
    assert CHECK.check_file(path, now=NOW) == ([], [])


def test_direction_controls_are_refused_raw_or_escaped(tmp_path):
    for char in ("‪", "‮", "⁦", "⁩"):
        raw = tmp_path / "raw.jsonl"
        raw.write_text(json.dumps(in_payload(PATH, note="a" + char + "b"), ensure_ascii=False) + "\n", encoding="utf-8")
        assert CHECK.check_file(raw, now=NOW)[0], repr(char)
        assert check(in_payload(PATH, note="a" + char + "b"))[0], repr(char)
    escaped = tmp_path / "escaped.jsonl"
    escaped.write_text(json.dumps(in_payload(PATH, note="a‮b")) + "\n", encoding="utf-8")
    assert CHECK.check_file(escaped, now=NOW)[0]


def test_issue_form_has_the_expected_fields():
    form = (ROOT / ".github" / "ISSUE_TEMPLATE" / "data-contribution.yml").read_text(encoding="utf-8")
    for field_id in ("host", "kind", "source", "checked_at", "notes", "credit"):
        assert f"id: {field_id}\n" in form
    assert form.count("type: dropdown") == 1
    assert len(re.findall(r"^        - ", form, re.M)) == 3     # the three kinds
    assert "CC BY 4.0" in form


# --- the page -----------------------------------------------------------------------------------

def _app(**extra):
    return TestClient(create_app(Settings("postgresql://invalid/unused", "x" * 32, public_mode=True, reporting_enabled=True, **extra)))


@pytest.mark.parametrize("lang", ["en", "ko"])
def test_contribute_page_in_both_languages(lang):
    client = _app()
    response = client.get(f"/{lang}/contribute")
    assert response.status_code == 200
    html = response.text
    for anchor in ("kinds", "evidence", "example", "flow", "send", "not-accepted"):
        assert f'id="{anchor}"' in html
    # the three ways to send, the example and the boundary
    assert "issues/new?template=data-contribution.yml" in html
    assert "contrib/README.md" in html and "scripts/check_contrib.py" in html
    assert "report_observation" in html
    assert '"kind": "official_path"' in html and "docs.example.com" in html
    assert f'hreflang="{"ko" if lang == "en" else "en"}"' in html
    assert f'href="/{lang}/contribute"' in html                  # the footer link on the page itself
    assert f'href="/{lang}/agents"' in html
    assert ("CC BY 4.0" in html) and ("Apache-2.0" in html)
    other = "ko" if lang == "en" else "en"
    assert f'href="/{other}/contribute"' in html                 # the language switch lands on the twin page


def test_contribute_page_leaves_out_report_observation_when_reporting_is_off():
    client = TestClient(create_app(Settings("postgresql://invalid/unused", "x" * 32, public_mode=True)))
    for lang in ("en", "ko"):
        assert "report_observation" not in client.get(f"/{lang}/contribute").text


PRESSURE_EN = re.compile(r"(?i)\b(?:must|required|mandatory|obligat\w*|you have to|have to|should|in order to use|as a condition)\b"
                         r"|(?<!don't )(?<!do not )(?<!don’t )(?<!not )(?<!no )\bneeds? to\b")
PRESSURE_KO = ("의무", "반드시", "필수", "해야", "하셔야", "조건으로")


def _plain(html: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html))


def test_pressure_words_are_caught_but_a_plain_negative_is_not():
    for text in ("you must send it", "you should send it", "you need to send it", "it is required", "sending is mandatory"):
        assert PRESSURE_EN.search(text), text
    for text in ("you don't need to send anything", "you do not need to explain", "there is no need to sign up"):
        assert not PRESSURE_EN.search(text), text


def test_everything_a_contributor_reads_is_an_invitation_not_a_requirement():
    client = _app()
    pages = {lang: _plain(client.get(f"/{lang}/contribute").text) for lang in ("en", "ko")}
    form_lines = (ROOT / ".github" / "ISSUE_TEMPLATE" / "data-contribution.yml").read_text(encoding="utf-8").splitlines()
    # the form's own "required:" lines are field settings, not wording
    form = "\n".join(line for line in form_lines if not line.strip().startswith("required:"))
    contrib = (ROOT / "contrib" / "README.md").read_text(encoding="utf-8")
    for name, text in {"page": pages["en"], "issue form": form, "contrib/README.md": contrib}.items():
        found = PRESSURE_EN.search(text)
        assert not found, (name, found and found.group(0))
    # the footnote on the agents page that points here, in both languages
    for lang in ("en", "ko"):
        html = client.get(f"/{lang}/agents").text
        notes = [_plain(m) for m in re.findall(r'<p class="agent-footnote">(.*?)</p>', html, re.S) if f'href="/{lang}/contribute"' in m]
        assert len(notes) == 1, lang
        if lang == "en":
            assert not PRESSURE_EN.search(notes[0]), notes[0]
        else:
            assert "합니다" in notes[0] or "습니다" in notes[0]       # the page's own register
            for word in PRESSURE_KO:
                assert word not in notes[0], word
    for word in PRESSURE_KO:
        assert word not in pages["ko"], word
    assert "Nothing is expected in return" in pages["en"] and "바라는 것은 없고" in pages["ko"]
    assert "Nothing is expected" in contrib


def test_the_issue_form_asks_for_host_and_kind_and_leaves_the_source_optional():
    form = (ROOT / ".github" / "ISSUE_TEMPLATE" / "data-contribution.yml").read_text(encoding="utf-8")
    by_id = {re.search(r"id: (\w+)", block).group(1): block for block in form.split("  - type: ")[1:] if "id:" in block}
    assert "required: true" in by_id["host"] and "required: true" in by_id["kind"]
    assert "required: false" in by_id["source"] and "required: true" not in by_id["source"]


def test_contribute_is_in_the_sitemap_and_linked_from_the_site():
    client = _app()
    sitemap = client.get("/sitemap.xml").text
    for lang in ("en", "ko"):
        assert f"https://agenthelpnetwork.com/{lang}/contribute" in sitemap
        assert f'href="/{lang}/contribute"' in client.get(f"/{lang}/").text          # footer on every page
        assert f'href="/{lang}/contribute"' in client.get(f"/{lang}/agents").text    # the agents page
    assert "/en/contribute" in client.get("/llms.txt").text


def test_unknown_language_is_not_found():
    assert _app().get("/fr/contribute").status_code == 404


def test_readmes_and_contributing_point_to_the_ways_to_send():
    for name in ("README.md", "README.ko.md"):
        text = (ROOT / name).read_text(encoding="utf-8")
        assert "contribute" in text and "contrib/README.md" in text, name
    assert "check_contrib.py" in (ROOT / "CONTRIBUTING.md").read_text(encoding="utf-8")
