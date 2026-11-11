"""Korean /v1/why answers for the English-sentence scan (D114). Not a test module: no test_ prefix, nothing here is collected.

``ko_answers()`` yields lang=ko answer bodies: every combination of ``test_why_d110.every_answer`` and ``why_extra_inputs.extra_answers``,
plus lookup answers that carry what the lookup puts in the ``server`` block as English text: every next_checks sentence, every cause line
(the fixed ones with a window, the home-page-gate and your-side ones with each age), and every sentence a summary_line is made of.
``english_sentences(text)`` and ``text_leaves(body)`` find the sentences of a body that have no Hangul. It uses only helpers that exist before
D114 (parse_inputs, build with its six arguments, fake_check), so it imports and runs on the earlier commit as well. No database."""

from __future__ import annotations

import itertools
import os
import re
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _why_fixtures import fake_check, run  # noqa: E402
from test_why_d110 import ACTION_OF, STATUS, every_answer  # noqa: E402
from test_why_d112 import template_sentences  # noqa: E402
from why_extra_inputs import extra_answers  # noqa: E402

from ahn import cause, triage  # noqa: E402
from ahn.check_text import VENDOR_NAMES  # noqa: E402

HANGUL = re.compile("[가-힣]")
# What is not an English sentence of ours: code in backticks, addresses and hosts.
_NOT_OURS = (re.compile(r"`[^`]*`"), re.compile(r"https?://\S+"), re.compile(r"\S*=\S*"), re.compile(r"\buser agents?\b"), re.compile(r"\b[\w.-]+\.(?:test|com|org|net|io|dev)\b"))
# Text in quotes is the vendor's or the site's own words (handed through as written) only inside a sentence whose own words are Korean; a
# sentence of ours wrapped in quotes is still ours.
_QUOTE = re.compile(r'"[^"]*"|“[^”]*”')
# A sentence ends at . ! ? followed by a space, or directly by a capital letter or Hangul (robots.txt is not an end).
SENTENCE_END = re.compile(r"(?<=[.!?])\s+|(?<=[.!?])(?=[A-Z가-힣])")
_WORD = re.compile(r"[A-Za-z][A-Za-z0-9_'-]*")
# Names and abbreviations a Korean sentence keeps as they are.
NAMES = frozenset({"AHN", "HTTP", "HTTPS", "API", "URL", "DNS", "TLS", "SSL", "UTC", "JSON", "MCP", "CAPTCHA", "OK", "ID", "robots", "txt", "llms",
                   "Cloudflare", "DataDome", "PerimeterX", "AWS", "WAF", "Vercel"})
# Keys whose string is a code, an address, a time or a field name, never a sentence for a person.
NOT_TEXT_KEYS = frozenset({"url", "schema_version", "site_host", "lang", "id", "code", "who", "state", "layer", "kind", "do", "first", "to",
                           "position", "action", "confidence", "template_id", "checked_at", "window_from", "window_to", "self", "check",
                           "input_echo", "rules_applied", "render_order", "reasons", "basis", "hint", "branch", "failure_stage",
                           "recheck_after", "from", "status_url_kind", "customer_url", "status_url", "lookup_host", "stop_if_you_are"})


def english_sentences(text: str) -> list[str]:
    """The English sentences of ``text``, once code, addresses and hosts are taken out: a sentence with no Hangul and two or more English
    words, or a Korean sentence with two or more English words in a row (one of them lower case, so a name such as "Google Cloud" passes)."""
    for pattern in _NOT_OURS:
        text = pattern.sub(" ", text)
    quotes = []

    def hide(match):
        quotes.append(match.group())
        return f"\x00{len(quotes) - 1}\x00"

    found = []
    for piece in SENTENCE_END.split(_QUOTE.sub(hide, text)):
        sentence = re.sub(r"\x00(\d+)\x00", lambda m: quotes[int(m.group(1))], piece).strip()
        bare = _QUOTE.sub(" ", sentence)
        korean = bool(HANGUL.search(bare))
        rest = bare if korean else sentence
        if not korean:
            if len([w for w in _WORD.findall(rest) if "_" not in w and w not in NAMES]) >= 2:
                found.append(sentence)
            continue
        for run in re.findall(r"(?<![\w-])[A-Za-z][A-Za-z0-9'-]*(?: [A-Za-z][A-Za-z0-9'-]*)+", rest):
            run_words = [w for w in run.split(" ") if w not in NAMES]
            if len(run_words) >= 2 and any(w[0].islower() for w in run_words):
                found.append(sentence)
                break
    return found


def text_leaves(node, path: str = "", template_id: str | None = None):
    """(path, template_id, text) for every string of a body that a person reads."""
    if isinstance(node, dict):
        template_id = node.get("template_id") or node.get("id") or node.get("code") or template_id
        for key, value in node.items():
            if key not in NOT_TEXT_KEYS:
                yield from text_leaves(value, f"{path}.{key}", template_id)
    elif isinstance(node, list):
        for value in node:
            yield from text_leaves(value, f"{path}[]", template_id)
    elif isinstance(node, str):
        yield path, template_id, node


def check_texts() -> list[tuple[str, str]]:
    """Every next_checks sentence the lookup can give, with its code."""
    by_text = {text: code for code, text in triage.CHECKS.items()}
    for text in triage.CHECK_TEXT_KO:
        by_text.setdefault(text, "provider_scope")
    return sorted((code, text) for text, code in by_text.items())


def cause_objects() -> list[dict]:
    """A cause object for every line the lookup can say, at several ages and with and without a window."""
    start = datetime(2026, 11, 1, 3, 4, tzinfo=timezone.utc)
    ended = start + timedelta(days=2)
    out = []
    for key in cause.LINES:
        for window in ((None, None), (start, None), (start, start + timedelta(hours=2)), (start, ended)):
            out.append(cause._cause("transient", "site_errors", "medium", ["outage_episodes"], key, start=window[0], end=window[1]))
    ages = [timedelta(seconds=s) for s in (60, 600, 7200, 86400, 3 * 86400)]
    for outcome, vendor, dated, age in itertools.product(cause.HOME_GATE_KINDS, (None, *VENDOR_NAMES),
                                                         (True, False), ages):
        out.append(cause._cause("undetermined", "home_gate_unconfirmed", "low", ["site_probe"], "home_gate_unconfirmed",
                                line=cause.gate_line(outcome, dated, age, vendor)))
    for age in ages:
        out.append(cause._cause("undetermined", "your_side_likely", "low", ["site_probe_daily"], "your_side_likely", line=cause.side_line(age)))
    return out


def server_text_answers():
    combos = [(None, None), ("timeout_or_5xx", "public_read"), ("page_blank_or_blocked", None), ("extra_verification", "my_account_consumer")]
    dos = ("use_official_path", "hand_off_to_human", "wait_for_provider")

    def make(do, stop, context, **extra):
        check = fake_check(ACTION_OF[do], do, provider_status=STATUS, official_paths={"api_docs": "https://api.fixture-host.test/docs"}, **extra)
        return run(check=check, stop_point=stop, task_context=context, lang="ko")

    for (code, text), do, (stop, context) in itertools.product(check_texts(), dos, combos):
        yield make(do, stop, context, next_checks=[{"code": code, "text": text}])
    for found, do, (stop, context) in itertools.product(cause_objects(), dos, combos):
        yield make(do, stop, context, cause=found)
    for sentence, (stop, context) in itertools.product(template_sentences(), combos[:2]):
        yield make("use_official_path", stop, context, summary_line=sentence.replace("{n}", "7").replace("{m}", "5"))


def ko_answers():
    for body in every_answer():
        if body["lang"] == "ko":
            yield body
    for body in extra_answers():
        if body["lang"] == "ko":
            yield body
    yield from server_text_answers()
