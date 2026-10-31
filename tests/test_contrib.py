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
    changed(PATH, source_url="https://docs.example.com/api?token=abc"),
    changed(PATH, source_url="https://docs.example.com/api#top"),
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


def test_accepted_fields_follow_the_bundle_allowlist():
    from ahn import bundle

    assert CHECK.PAYLOAD_KEYS["official_path"] == bundle.PAYLOAD_KEYS["official_path"]
    assert CHECK.PAYLOAD_KEYS["status_source"] == bundle.PAYLOAD_KEYS["status_source"]
    assert set(CHECK.LINE_KEYS) <= bundle.ENVELOPE_KEYS | {"credit"}
    assert bundle.FORBIDDEN_KEYS <= CHECK.FORBIDDEN_KEYS


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


def test_contribute_page_reads_as_an_invitation_not_a_requirement():
    client = _app()
    plain = {lang: re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", client.get(f"/{lang}/contribute").text)) for lang in ("en", "ko")}
    for word in ("must", "required", "mandatory", "obligat", "you have to", "in order to use", "as a condition"):
        assert word not in plain["en"].lower(), word
    for word in ("의무", "반드시", "필수", "해야 해요", "조건으로"):
        assert word not in plain["ko"], word
    assert "Nothing is expected in return" in plain["en"] and "바라는 것은 없고" in plain["ko"]


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
