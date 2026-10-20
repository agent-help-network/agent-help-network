"""Export v1: RFC 8785 canonical JSON, envelope fields, Ed25519 signing. Pure: no database, no network."""

from __future__ import annotations

import io
import json
from datetime import datetime, timezone
from uuid import UUID

import pytest

from ahn import export


# --- RFC 8785 test vectors -----------------------------------------------------------------------

def test_jcs_rfc8785_example_object():
    # RFC 8785 section 3.2.2 / appendix: numbers, a string with escapes, and the literals.
    value = {
        "numbers": [333333333.33333329, 1e30, 4.50, 2e-3, 0.000000000000000000000000001],
        "string": "€$\x0f\nA'B\"\\\\\"/",
        "literals": [None, True, False],
    }
    assert export.canonicalize(value) == (
        '{"literals":[null,true,false],"numbers":[333333333.3333333,1e+30,4.5,0.002,1e-27],'
        '"string":"€$\\u000f\\nA\'B\\"\\\\\\\\\\"/"}'
    )


def test_jcs_sorts_keys_by_utf16_code_units():
    # RFC 8785 section 3.2.3 sorting example.
    value = {"€": "Euro Sign", "\r": "Carriage Return", "דּ": "Hebrew Letter Dalet With Dagesh", "1": "One",
             "\U0001f600": "Emoji: Grinning Face", "\u0080": "Control", "ö": "Latin Small Letter O With Diaeresis"}
    keys = list(json.loads(export.canonicalize(value)))
    assert keys == ["\r", "1", "\u0080", "ö", "€", "\U0001f600", "דּ"]


@pytest.mark.parametrize("number,text", [
    (0, "0"), (-0.0, "0"), (1.0, "1"), (100.0, "100"), (-1.5, "-1.5"), (0.1, "0.1"),
    (1e20, "100000000000000000000"), (1e21, "1e+21"), (123456789012345680000.0, "123456789012345680000"),
    (1e-6, "0.000001"), (1e-7, "1e-7"), (1.5e-7, "1.5e-7"), (5e-324, "5e-324"),
    (1.7976931348623157e308, "1.7976931348623157e+308"), (9007199254740992, "9007199254740992"),
    (2**60, "1152921504606847000"), (295147905179352830000.0, "295147905179352830000"),
])
def test_jcs_numbers_follow_ecmascript(number, text):
    assert export.canonicalize(number) == text


def test_jcs_rejects_non_json_values():
    for bad in (float("nan"), float("inf"), {1: "x"}, object()):
        with pytest.raises((TypeError, ValueError)):
            export.canonicalize(bad)


def test_jcs_escapes_only_what_rfc8785_escapes():
    assert export.canonicalize("a/b\u007f\u001f\t") == '"a/b\u007f\\u001f\\t"'
    assert export.canonicalize("한국어") == '"한국어"'


# --- Envelopes ------------------------------------------------------------------------------------

FETCHED = datetime(2026, 10, 19, 4, 5, 6, 789000, tzinfo=timezone.utc)
ENVELOPE_FIELDS = ["schema_version", "kind", "record_id", "origin_operator", "vantage", "reader_version", "observed_at",
                   "received_at", "expires_at", "source_url", "content_sha256", "payload", "signature"]


def test_access_profile_envelope_fields(monkeypatch):
    monkeypatch.delenv("AHN_OPERATOR_ID", raising=False)
    row = {"site_host": "example.org", "fetched_at": FETCHED, "robots_status": "ok", "robots": {"rules": []},
           "robots_detail": "http_200", "llms_txt_status": "absent", "llms_detail": "http_404", "read_host": "www.example.org",
           "origin_operator": None, "vantage": None, "reader_version": None, "robots_sha256": "a" * 64, "llms_sha256": None}
    record = export.access_profile_envelope(row)
    assert list(record) == ENVELOPE_FIELDS
    assert record["schema_version"] == 1 and record["kind"] == "access_profile"
    assert record["record_id"] == "ahn:agenthelpnetwork.com:access_profile:example.org:2026-10-19T04:05:06.789000Z"
    assert record["origin_operator"] == "agenthelpnetwork.com"  # rows read before provenance existed are ours
    assert record["observed_at"] == record["received_at"] == "2026-10-19T04:05:06.789000Z"
    assert record["expires_at"] == "2026-10-20T04:05:06.789000Z"
    assert record["source_url"] == "https://www.example.org/robots.txt" and record["content_sha256"] == "a" * 64
    assert record["payload"]["llms_txt_url"] == "https://www.example.org/llms.txt" and record["signature"] is None


def test_status_envelope_expires_after_seven_days():
    row = {"site_host": "example.org", "source_name": "Example Status", "status_page_url": "https://status.example.org/",
           "api_url": "https://status.example.org/api/v2/status.json", "curated_by": "agenthelpnetwork.com",
           "evidence": "discovered_cname", "evidence_url": "https://status.example.org/", "indicator": "none",
           "source_updated_at": None, "fetched_at": FETCHED, "origin_operator": "agenthelpnetwork.com",
           "vantage": {"kind": "datacenter"}, "reader_version": "AgentHelpNetwork/0.2", "body_sha256": "b" * 64}
    record = export.status_envelope(row)
    assert record["expires_at"] == "2026-10-26T04:05:06.789000Z"
    assert record["source_url"] == row["api_url"] and record["content_sha256"] == "b" * 64
    assert record["payload"]["evidence"] == "discovered_cname"


def test_observation_envelope_keeps_only_public_fields_cut_to_the_hour():
    own = UUID("11111111-2222-3333-4444-555555555555")
    row = {"id": own, "observed_at": FETCHED, "received_at": FETCHED, "attempt_actor": "agent", "attempt_env": "unknown",
           "outcome": "blocked", "origin_operator": "agenthelpnetwork.com", "origin_observation_id": own,
           "received_channel": "mcp", "site_host": "example.org", "task_step": "site_open", "error_class": "site_access"}
    record = export.observation_envelope(row)
    assert record["observed_at"] == "2026-10-19T04:00:00Z" and record["received_at"] == "2026-10-19T04:00:00Z"
    assert record["record_id"].startswith("ahn:agenthelpnetwork.com:observation:example.org:2026-10-19T04:00:00Z:")
    assert str(own) not in json.dumps(record)  # the operator's own id is never exported, only a digest of it
    assert set(record["payload"]) == {"site_host", "observed_at", "task_step", "error_class", "attempt_actor",
                                      "attempt_env", "outcome", "received_channel"}
    assert record["source_url"] is None and record["content_sha256"] is None and record["expires_at"] == "2026-10-26T04:00:00Z"


def test_operator_and_vantage_come_from_the_environment(monkeypatch):
    from ahn import provenance

    monkeypatch.setenv("AHN_OPERATOR_ID", "ops.example.net")
    monkeypatch.setenv("AHN_VANTAGE", '{"kind":"residential","region":"KR","asn":4766}')
    assert provenance.operator_id() == "ops.example.net"
    assert provenance.vantage() == {"kind": "residential", "region": "KR", "asn": 4766, "declared": True}
    monkeypatch.setenv("AHN_OPERATOR_ID", "Not A Host!")
    monkeypatch.setenv("AHN_VANTAGE", "not json")
    assert provenance.operator_id() == "agenthelpnetwork.com"
    assert provenance.vantage() == {"kind": "datacenter", "region": "US", "asn": None, "declared": True}
    assert provenance.sha256_hex(b"") == "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    assert provenance.sha256_hex(None) is None


# --- Signing -------------------------------------------------------------------------------------

def test_signature_round_trip_with_a_temporary_key(tmp_path, monkeypatch):
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    key = Ed25519PrivateKey.generate()
    path = tmp_path / "export-signing.pem"
    path.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    loaded = export.load_signing_key(str(path))
    monkeypatch.setenv("AHN_EXPORT_KEY_ID", "agenthelpnetwork.com:2026-10")
    record = export.envelope("status", site_host="example.org", observed_at=FETCHED, received_at=FETCHED, payload={"indicator": "none"})
    signed = export.sign(record, loaded, export.key_id())
    assert signed["signature"]["alg"] == "ed25519" and signed["signature"]["key_id"] == "agenthelpnetwork.com:2026-10"
    assert "=" not in signed["signature"]["sig"]  # base64url without padding
    assert export.verify(signed, key.public_key())
    # The signature covers every other member: changing one breaks it.
    tampered = {**signed, "payload": {"indicator": "major"}}
    assert not export.verify(tampered, key.public_key())
    # It is computed over the JCS form, so key order in transit does not matter.
    reordered = dict(reversed(list(signed.items())))
    assert export.verify(reordered, key.public_key())


def test_key_id_defaults_to_operator_and_month(monkeypatch):
    monkeypatch.delenv("AHN_EXPORT_KEY_ID", raising=False)
    monkeypatch.delenv("AHN_OPERATOR_ID", raising=False)
    assert export.key_id(datetime(2026, 10, 19, tzinfo=timezone.utc)) == "agenthelpnetwork.com:2026-10"
    monkeypatch.setenv("AHN_EXPORT_KEY_ID", "bad id")
    assert export.key_id(datetime(2026, 11, 1, tzinfo=timezone.utc)) == "agenthelpnetwork.com:2026-11"


def test_non_ed25519_key_is_refused(tmp_path):
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec

    path = tmp_path / "wrong.pem"
    path.write_bytes(ec.generate_private_key(ec.SECP256R1()).private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    with pytest.raises(ValueError):
        export.load_signing_key(str(path))


def test_export_writes_one_json_line_per_row(monkeypatch):
    rows = [{"site_host": "example.org", "fetched_at": FETCHED, "robots_status": "absent", "robots": None,
             "robots_detail": "http_404", "llms_txt_status": "absent", "llms_detail": "http_404", "read_host": None}]

    class Conn:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def execute(self, sql, params):
            assert "fetched_at IS NOT NULL" in sql
            return iter(rows)

    monkeypatch.setattr("ahn.db.connect", lambda _settings: Conn())
    out = io.StringIO()
    assert export.export(None, "access_profile", out) == 1
    line = json.loads(out.getvalue())
    assert line["kind"] == "access_profile" and line["signature"] is None and line["source_url"] == "https://example.org/robots.txt"
    with pytest.raises(ValueError):
        export.export(None, "everything", out)
