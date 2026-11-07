"""D108: the page's script cuts a pasted address to the hostname the service would use. The same table runs through the server's
own code (explain.parse_inputs) and through ahn/static/why.js under Node; without Node only the table's expected answers are checked."""

import json
import shutil
import subprocess
from datetime import date
from pathlib import Path

import pytest

from ahn import explain as E
from ahn.service import PUBLIC_AGGREGATE_TLDS, PUBLIC_SUFFIXES_2

ROOT = Path(__file__).resolve().parents[1]
# (what was pasted, the hostname that is sent or None when nothing valid is left)
CASES = [
    ("example.org", "example.org"),
    ("  Example.ORG  ", "example.org"),
    ("https://example.org", "example.org"),
    ("HTTP://example.org/", "example.org"),
    ("https://example.org/account/settings?tab=2#top", "example.org"),
    ("example.org/path", "example.org"),
    ("example.org?token=abc", "example.org"),
    ("example.org#frag", "example.org"),
    ("https://user:secret@example.org:8443/x", "example.org"),
    ("user@example.org", "example.org"),
    ("example.org:8080", "example.org"),
    ("example.org.", "example.org"),
    ("www.example.org", "www.example.org"),
    ("https://tenant-one.example.org/login", "example.org"),
    ("acme.example.co.uk", "example.co.uk"),
    ("www.example.co.uk", "www.example.co.uk"),
    ("a.b.example.org", "example.org"),
    ("xn--tenant.example.org", "example.org"),
    ("xn--80ak6aa92e.com", "xn--80ak6aa92e.com"),
    ("example.co.uk", "example.co.uk"),
    ("localhost", None),
    ("printer.local", None),
    ("192.168.1.10", None),
    ("127.1", None),
    ("not a host", None),
    ("", None),
    ("https://", None),
    ("a_b.example.org", None),
    ("-bad.example.org", None),
    ("example.123", None),
]


def server_side(raw):
    try:
        inputs = E.parse_inputs(raw, today=date(2026, 11, 7))
    except ValueError:
        return None
    return inputs.site_host


@pytest.mark.parametrize("raw,expected", CASES)
def test_the_server_gives_the_table_s_answer(raw, expected):
    assert server_side(raw) == expected


@pytest.mark.skipif(shutil.which("node") is None, reason="Node is not installed; the table above is checked against the server only")
def test_the_script_gives_the_same_answers_as_the_server():
    program = """
const {makeRules} = require(process.argv[1]);
const input = JSON.parse(process.argv[2]);
const sanitize = makeRules(input.rules);
process.stdout.write(JSON.stringify(input.cases.map((raw) => sanitize(raw))));
"""
    payload = {"rules": {"suffixes2": sorted(PUBLIC_SUFFIXES_2), "tlds": sorted(PUBLIC_AGGREGATE_TLDS)}, "cases": [raw for raw, _ in CASES]}
    done = subprocess.run(["node", "-e", program, str(ROOT / "ahn/static/why.js"), json.dumps(payload)],
                          capture_output=True, text=True, timeout=60, check=True)
    got = json.loads(done.stdout)
    for (raw, expected), seen in zip(CASES, got):
        assert seen == expected, (raw, seen, expected)
        assert seen == server_side(raw), raw
