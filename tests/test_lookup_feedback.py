"""Lookup feedback (D58): the one-time token, the REST body rules and the MCP tool rate_last_lookup. No database needed."""

from __future__ import annotations

import asyncio
import base64
import hmac
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("AHN_DATABASE_URL", "postgresql://invalid/unused")
os.environ.setdefault("AHN_KEY_PEPPER", "x" * 32)

from ahn import feedback, mcp_adapter  # noqa: E402
from ahn.feedback import FeedbackError, issue, verify  # noqa: E402
from ahn.main import create_app  # noqa: E402
from ahn.models import FEEDBACK_TOKEN, CheckResponse, FeedbackIn  # noqa: E402
from ahn.settings import Settings  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
PEPPER = "test-pepper-must-be-at-least-thirty-two-chars"
NOW = datetime(2026, 10, 30, 14, 37, 12, tzinfo=timezone.utc)


def token(host="demo-site.io", action="use_official_path", kind="mcp", counted=True, now=NOW, pepper=PEPPER):
    return issue(pepper, host, action, kind, counted, now)


def resign(payload: str, pepper=PEPPER) -> str:
    """A token over any payload, signed the way the service signs (to show the checks after the MAC hold too)."""
    raw = payload.encode("ascii")
    key = hmac.new(pepper.encode(), b"ahn-lookup-feedback-v1", sha256).digest()
    b64 = lambda data: base64.urlsafe_b64encode(data).rstrip(b"=").decode()  # noqa: E731
    return b64(raw) + "." + b64(hmac.new(key, raw, sha256).digest())


def payload_of(text: str) -> str:
    body = text.split(".")[0]
    return base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)).decode()


ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"


def respellings(text: str) -> list[str]:
    """Other spellings of the same token: a different last character of the payload or MAC part that a lenient base64
    decoder reads as the same bytes (the spare low bits are ignored)."""
    decode = lambda part: base64.urlsafe_b64decode(part + "=" * (-len(part) % 4))  # noqa: E731
    body, mac = text.split(".")
    found = []
    for index, part in ((0, body), (1, mac)):
        for char in ALPHABET:
            other = part[:-1] + char
            if other != part and decode(other) == decode(part):
                found.append(other + "." + mac if index == 0 else body + "." + other)
    return found


# --- the token ------------------------------------------------------------------------------------

def test_a_token_round_trips_its_claims():
    claims = verify(PEPPER, token(), NOW + timedelta(minutes=5))
    assert (claims.host, claims.action, claims.client_kind, claims.counted) == ("demo-site.io", "use_official_path", "mcp", True)
    assert claims.issued_at == NOW.replace(microsecond=0)
    assert verify(PEPPER, token(counted=False), NOW).counted is False


def test_tokens_are_unique_and_fit_the_published_pattern():
    first, second = token(), token()
    assert first != second  # random nonce: two lookups in the same second never share a token
    longest = token(host=("a" * 63 + ".") * 3 + "b" * 59 + ".io")
    for value in (first, longest):
        assert FEEDBACK_TOKEN.fullmatch(value) and len(value) <= 530


def test_the_token_holds_only_the_listed_facts():
    parts = payload_of(token()).split("|")
    assert parts[:6] == ["f1", "demo-site.io", "use_official_path", "mcp", str(int(NOW.timestamp())), "1"] and len(parts) == 7
    assert len(parts[6]) == 32 and int(parts[6], 16) >= 0


@pytest.mark.parametrize("bad", ["", "x", "abc.def", "a" * 600, token() + "x", token().replace(".", "!"), "." + "A" * 43,
                                 (lambda t: t.split(".")[0] + "." + ("B" if t.split(".")[1][0] == "A" else "A") + t.split(".")[1][1:])(token())])
def test_a_forged_or_malformed_token_is_refused(bad):
    with pytest.raises(FeedbackError) as caught:
        verify(PEPPER, bad, NOW)
    assert (caught.value.code, caught.value.status) == ("invalid_feedback_token", 400)


def test_only_the_spelling_the_service_issued_is_accepted():
    """B1: base64 leaves spare bits in the last character. Each other spelling of a genuine token decodes to the same
    signed bytes; it must be refused, or one token could be used once per spelling."""
    value = token()
    variants = respellings(value)
    assert len([v for v in variants if v.split(".")[0] == value.split(".")[0]]) == 3  # the 32-byte MAC has 2 spare bits
    for variant in variants:
        with pytest.raises(FeedbackError, match="invalid_feedback_token"):
            verify(PEPPER, variant, NOW)
    assert verify(PEPPER, value, NOW).host == "demo-site.io"


def test_the_used_token_key_is_the_signed_content_not_the_spelling():
    value = token()
    claims = verify(PEPPER, value, NOW)
    assert claims.digest == feedback.fingerprint(value) == sha256(payload_of(value).encode()).hexdigest()
    for variant in respellings(value):
        assert feedback.fingerprint(variant) == claims.digest  # every spelling would land on the same row anyway


def test_a_token_signed_with_another_key_is_refused():
    with pytest.raises(FeedbackError, match="invalid_feedback_token"):
        verify(PEPPER, token(pepper="another-pepper-also-at-least-thirty-two-chars"), NOW)


def test_changing_the_host_inside_a_token_breaks_it():
    """The host travels inside the token and is covered by the MAC: a token for one host cannot be made to count for another."""
    original = token(host="demo-site.io")
    body, mac = original.split(".")
    changed = payload_of(original).replace("demo-site.io", "other-site.io").encode()
    forged = base64.urlsafe_b64encode(changed).rstrip(b"=").decode() + "." + mac
    with pytest.raises(FeedbackError, match="invalid_feedback_token"):
        verify(PEPPER, forged, NOW)
    assert verify(PEPPER, token(host="other-site.io"), NOW).host == "other-site.io"


@pytest.mark.parametrize("payload", [
    "f2|demo-site.io|proceed|mcp|{t}|1|" + "a" * 32,              # unknown version
    "f1|Demo-Site.io|proceed|mcp|{t}|1|" + "a" * 32,              # not a valid host
    "f1|demo-site.io|delete_all|mcp|{t}|1|" + "a" * 32,           # action outside the list
    "f1|demo-site.io|proceed|robot|{t}|1|" + "a" * 32,            # client kind outside the list
    "f1|demo-site.io|proceed|mcp|{t}|2|" + "a" * 32,              # counted flag
    "f1|demo-site.io|proceed|mcp|{t}|1|short",                    # nonce
    "f1|demo-site.io|proceed|mcp|{t}|1",                          # missing field
    "f1|demo-site.io|proceed|mcp|soon|1|" + "a" * 32,             # time
    "f1|demo-site.io|proceed|mcp|{future}|1|" + "a" * 32,         # issued in the future
])
def test_a_correctly_signed_token_with_wrong_contents_is_still_refused(payload):
    text = payload.format(t=int(NOW.timestamp()), future=int((NOW + timedelta(minutes=5)).timestamp()))
    with pytest.raises(FeedbackError, match="invalid_feedback_token"):
        verify(PEPPER, resign(text), NOW)


def test_a_token_expires_one_hour_after_the_lookup():
    value = token()
    issued = NOW.replace(microsecond=0)
    assert verify(PEPPER, value, issued + timedelta(seconds=3599)).host == "demo-site.io"
    for later in (timedelta(seconds=3600), timedelta(days=3)):
        with pytest.raises(FeedbackError) as caught:
            verify(PEPPER, value, issued + later)
        assert (caught.value.code, caught.value.status) == ("feedback_token_expired", 410)


def test_record_refuses_codes_outside_the_lists_before_touching_the_database():
    class NoDatabase:
        def __getattr__(self, name):
            raise AssertionError("the database was touched")
    for outcome, path in (("worked", "unknown"), ("succeeded", "elsewhere"), ("", "unknown")):
        with pytest.raises(FeedbackError, match="invalid_request"):
            feedback.record(NoDatabase(), PEPPER, token(), outcome, path, NOW, count=True)
    with pytest.raises(FeedbackError, match="invalid_feedback_token"):
        feedback.record(NoDatabase(), PEPPER, "nope", "succeeded", "unknown", NOW, count=True)


def test_the_answer_field_lists_the_codes_in_the_contract_order():
    field = feedback.feedback_field(PEPPER, "demo-site.io", "proceed", "plugin", True, NOW)
    assert list(field) == ["token", "expires_in_seconds", "outcomes", "paths"]
    assert field["expires_in_seconds"] == 3600
    assert field["outcomes"] == ["succeeded", "still_blocked", "handed_off", "gave_up"]
    assert field["paths"] == ["official_path", "same_site", "unknown"]


def test_feedback_is_the_last_key_of_an_answer_and_is_left_out_when_absent():
    fields = list(CheckResponse.model_fields)
    assert fields[-1] == "feedback_token" and fields[-3:-1] == ["failure_stage", "next_checks"]


# --- REST body rules (validation runs before any database access) ---------------------------------

@pytest.fixture
def client():
    return TestClient(create_app(Settings("postgresql://invalid/unused", PEPPER, public_mode=True, collect_lookup_metrics=True)))


@pytest.mark.parametrize("field,value,allowed", [
    ("outcome", "worked", ["succeeded", "still_blocked", "handed_off", "gave_up"]),
    ("path", "elsewhere", ["official_path", "same_site", "unknown"]),
])
def test_a_wrong_enum_is_refused_with_the_allowed_values(client, field, value, allowed):
    body = {"token": token(), "outcome": "succeeded", field: value}
    response = client.post("/v1/feedback", json=body)
    assert response.status_code == 400
    assert response.json() == {"error": "invalid_request", "field": field, "allowed_values": allowed}
    assert value not in response.text or value in allowed  # the rejected value is not echoed


@pytest.mark.parametrize("body", [
    {"outcome": "succeeded"},                                                  # no token
    {"token": "t", "outcome": "succeeded", "site_host": "demo-site.io"},       # no hostname is accepted
    {"token": "t", "outcome": "succeeded", "missing": ["answer_too_long"]},    # not part of this change
    {"token": "x" * 2000, "outcome": "succeeded"},
])
def test_other_body_problems_are_a_plain_invalid_request(client, body):
    response = client.post("/v1/feedback", json=body)
    assert response.status_code in (400, 413) and response.json()["error"] in ("invalid_request", "body_too_large_or_unframed")
    assert "demo-site.io" not in response.text


def test_a_forged_token_is_refused_before_the_database(client):
    response = client.post("/v1/feedback", json={"token": "not-a-token", "outcome": "succeeded"})
    assert response.status_code == 400 and response.json() == {"error": "invalid_feedback_token"}


def test_the_endpoint_is_in_the_openapi_schema_with_its_three_fields(client):
    schema = client.get("/openapi.json").json()
    post = schema["paths"]["/v1/feedback"]["post"]
    ref = post["requestBody"]["content"]["application/json"]["schema"]["$ref"].split("/")[-1]
    assert set(schema["components"]["schemas"][ref]["properties"]) == {"token", "outcome", "path"}


# --- MCP ------------------------------------------------------------------------------------------

def tools_in(public: str, reporting: str, switch: str = "1") -> dict:
    code = ("import asyncio, json; from ahn.mcp_adapter import mcp; "
            "print(json.dumps({t.name: [t.input_schema, t.annotations.model_dump(), t.description, t.output_schema] "
            "for t in asyncio.run(mcp.list_tools())}))")
    env = {**os.environ, "AHN_PUBLIC_MODE": public, "AHN_REPORTING_ENABLED": reporting, "AHN_LOOKUP_FEEDBACK": switch}
    done = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env, capture_output=True, text=True, timeout=120)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout.strip().splitlines()[-1])


@pytest.mark.parametrize("public,reporting", [("1", "0"), ("1", "1"), ("0", "0")])
def test_rate_last_lookup_is_listed_with_honest_annotations(public, reporting):
    tools = tools_in(public, reporting)
    schema, annotations, description, _ = tools["rate_last_lookup"]
    assert sorted(schema["properties"]) == ["outcome", "path", "token"] and schema["required"] == ["token", "outcome"]
    assert schema["properties"]["outcome"]["enum"] == ["succeeded", "still_blocked", "handed_off", "gave_up"]
    assert schema["properties"]["path"]["enum"] == ["official_path", "same_site", "unknown"]
    # The token is described with the REST limits: the same pattern and the same maximum length.
    assert schema["properties"]["token"]["pattern"] == FEEDBACK_TOKEN.pattern
    assert schema["properties"]["token"]["maxLength"] == 530 == FeedbackIn.model_fields["token"].metadata[0].max_length
    assert annotations["read_only_hint"] is False and annotations["destructive_hint"] is False
    assert annotations["idempotent_hint"] is True and annotations["open_world_hint"] is False
    assert description.startswith("If an earlier check_incident answer gave you a feedback token and you then tried again, say whether it worked. "
                                  "Sends only the token and the outcome code, no hostname.")
    # The lookup tool itself is unchanged: still read-only.
    assert tools["check_incident"][1]["read_only_hint"] is True


def test_the_switch_also_leaves_the_tool_out():
    for public, reporting in (("1", "0"), ("0", "0")):
        tools = tools_in(public, reporting, switch="0")
        assert "rate_last_lookup" not in tools and "check_incident" in tools


def closed_objects(schema, where="root"):
    """Paths of object schemas that refuse keys they do not list."""
    found = []
    if isinstance(schema, dict):
        if schema.get("additionalProperties") is False or (schema.get("type") == "object" and "properties" in schema
                                                            and schema.get("additionalProperties") is not True):
            found.append(where)
        for key, value in schema.items():
            found += closed_objects(value, f"{where}.{key}")
    elif isinstance(schema, list):
        for index, value in enumerate(schema):
            found += closed_objects(value, f"{where}[{index}]")
    return found


def test_the_advertised_output_schemas_accept_additive_fields():
    """A client that cached tools/list must keep accepting answers after an optional key is added (D58)."""
    tools = tools_in("1", "0")
    for name in ("check_incident", "rate_last_lookup"):
        output = tools[name][3]
        assert output["additionalProperties"] is True and closed_objects(output) == [], name
    output = tools["check_incident"][3]
    assert "feedback_token" in output["properties"] and "feedback_token" not in output.get("required", [])
    for nested in ("AccessProfile", "OfficialPaths", "NextAction", "Feedback"):
        assert output["$defs"][nested]["additionalProperties"] is True
    # The server's own models stay closed: an unknown key in what it returns is still an error.
    assert CheckResponse.model_config["extra"] == "forbid"


def test_the_tool_sends_only_token_outcome_and_path(monkeypatch):
    seen = {}

    async def fake(method, path, *, params=None, payload=None, write=False, extra_headers=None):
        seen.update(method=method, path=path, payload=payload, write=write, params=params)
        return {"accepted": True}
    monkeypatch.setattr(mcp_adapter, "call_api", fake)
    value = token()
    result = asyncio.run(mcp_adapter.mcp.call_tool("rate_last_lookup", {"token": value, "outcome": "succeeded", "path": "official_path"}))
    assert result.structured_content == {"accepted": True}
    assert seen == {"method": "POST", "path": "/v1/feedback", "payload": {"token": value, "outcome": "succeeded", "path": "official_path"},
                    "write": False, "params": None}
    asyncio.run(mcp_adapter.rate_last_lookup(value, "gave_up"))
    assert seen["payload"] == {"token": value, "outcome": "gave_up", "path": "unknown"}


@pytest.mark.parametrize("arguments,message", [
    ({"token": "nope", "outcome": "succeeded"}, "invalid_feedback_token"),
    ({"token": None, "outcome": "succeeded"}, "invalid_feedback_token"),
    ({"outcome": "worked"}, "outcome must be one of: succeeded, still_blocked, handed_off, gave_up"),
    ({"outcome": "succeeded", "path": "elsewhere"}, "path must be one of: official_path, same_site, unknown"),
])
def test_the_tool_refuses_bad_values_without_calling_the_service(monkeypatch, arguments, message):
    async def fake(*_args, **_kwargs):
        raise AssertionError("the service was called")
    monkeypatch.setattr(mcp_adapter, "call_api", fake)
    with pytest.raises(mcp_adapter.ToolError, match=message):
        asyncio.run(mcp_adapter.rate_last_lookup(**{"token": token(), **arguments}))
