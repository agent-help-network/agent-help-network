"""D108: the fixed sentences of GET /v1/why. Every sentence exists in both languages, fills only blanks from the allowed set,
carries none of the words the answer must never use, and is dropped (never cut) when it does not fit."""

from __future__ import annotations

import re

import pytest

from ahn import explain as E, explain_text as X, platform_policies
from ahn.why_models import CHECK_TEXT_MAX, HANDOFF_TEXT_MAX, LAYER_TEXT_MAX, SHORT_TEXT_MAX

BLANK = re.compile(r"{(\w+)}")
CAPS = {"S-": LAYER_TEXT_MAX, "CHK-": CHECK_TEXT_MAX, "H-": HANDOFF_TEXT_MAX, "TL-": 300}
# The longest a blank can be: a quotation 200, an address 200, a host 253, a time or a name short.
BLANK_MAX = {"quote": 200, "quote2": 200, "url": 200, "cause_line": 200, "host": 253}
DEFAULT_BLANK_MAX = 60


def all_templates():
    return [(tid, lang, text) for tid, pair in X.TEMPLATES.items() for lang, text in pair.items()]


def test_every_template_has_both_languages_and_the_same_blanks():
    for tid, pair in X.TEMPLATES.items():
        assert set(pair) == {"en", "ko"} and all(pair.values()), tid
        assert sorted(BLANK.findall(pair["en"])) == sorted(BLANK.findall(pair["ko"])), tid


def test_blanks_are_from_the_allowed_set():
    for tid, lang, text in all_templates():
        assert set(BLANK.findall(text)) <= X.BLANKS, (tid, lang)


def test_ids_are_unique_and_have_a_known_prefix():
    assert all(tid.startswith(("S-", "CHK-", "H-", "TL-")) for tid in X.TEMPLATES)


def test_no_template_has_a_forbidden_word():
    for tid, lang, text in all_templates():
        assert X.forbidden_in(text, lang) == [], (tid, lang)


def test_no_fixed_phrase_has_a_forbidden_word():
    phrases = X.fixed_phrases()
    assert len(phrases) > 100
    for label, lang, text in phrases:
        assert X.forbidden_in(text, lang) == [], (label, lang)


def test_the_curated_platform_lists_forbidden_words_are_not_used_either():
    for tid, lang, text in all_templates():
        low = text.lower()
        assert not [w for w in platform_policies.FORBIDDEN if w in low], (tid, lang)
    for label, lang, text in X.fixed_phrases():
        assert not [w for w in platform_policies.FORBIDDEN if w in text.lower()], (label, lang)


def test_falls_under_is_allowed_only_in_the_sentence_that_says_ahn_does_not_judge():
    assert X.forbidden_in("AHN does not judge whether this site falls under that description.", "en") == []
    assert X.forbidden_in("This site falls under that description.", "en") == ["falls under"]
    assert [t for t, lang, text in all_templates() if lang == "en" and "falls under" in text] == ["S-L2-NOCLASS"]


def test_the_lint_catches_the_words_of_the_contract():
    assert X.forbidden_in("The cause is the site.", "en") and X.forbidden_in("You can use another agent.", "en")
    assert X.forbidden_in("Try a different agent.", "en") and X.forbidden_in("no person needed", "en")
    assert X.forbidden_in("원인은 사이트예요.", "ko") and X.forbidden_in("다른 에이전트로 해 보세요.", "ko")
    assert X.forbidden_in("This works for other agents.", "en")


def test_every_template_leaves_room_for_its_blanks():
    for tid, lang, text in all_templates():
        cap = next(v for k, v in CAPS.items() if tid.startswith(k))
        fixed = len(BLANK.sub("", text))
        assert fixed <= cap * 3 // 5, (tid, lang, fixed)


def test_short_sentences_fit_without_blanks_in_the_short_limit():
    for tid in ("S-W-UNKNOWN", "S-P-NOT-INDICATED", "S-G-BASIS", "S-G-NO-SUCCESS", "S-G-WINDOW-LIMITS", "S-L3-SCOPE", "S-P-VERIFY-DEVICE"):
        for lang in ("en", "ko"):
            assert len(X.TEMPLATES[tid][lang]) <= SHORT_TEXT_MAX, (tid, lang)


def test_a_text_that_would_not_fit_is_dropped_not_cut(caplog):
    quote = "q" * 200
    with caplog.at_level("WARNING"):
        assert E._fill("S-L2-CONFLICT", "en", LAYER_TEXT_MAX, platform="P" * 40, quote=quote, quote2=quote) is None
    assert "why_item_dropped" in caplog.text
    short = E._fill("S-L2-CONFLICT", "en", LAYER_TEXT_MAX, platform="P", quote="a", quote2="b")
    assert short and short.endswith("AHN does not choose between them.")


def test_a_blank_without_a_value_drops_the_sentence():
    assert E._fill("S-L1A-ALLOW", "en", LAYER_TEXT_MAX, host="h.test", date=None, token_scope="x") is None
    assert E._fill("S-L1B-AHN-HOME", "en", LAYER_TEXT_MAX, host="h", date="d", outcome_words="x", vendor_part="") == (
        "When AHN's server requested h's home page on d, it received x. Your tool may get a different answer.")


def test_the_cause_line_is_the_only_text_that_may_hold_words_the_lint_forbids():
    assert BLANK.findall(X.TEMPLATES["S-L1-CAUSE"]["en"]) == ["host", "cause_line"]
    text = E._fill("S-L1-CAUSE", "en", LAYER_TEXT_MAX, host="h.test", cause_line="Probably a temporary outage.")
    assert text.endswith("Probably a temporary outage.") and X.forbidden_in(X.TEMPLATES["S-L1-CAUSE"]["en"], "en") == []


def test_stop_words_cover_every_stop_point():
    assert set(X.STOP_WORDS) == set(E.STOP_POINTS)
    assert X.STOP_WORDS["extra_verification"] == "an extra verification after the password"


def test_reason_phrases_cover_every_reason_the_code_can_give():
    needed = {("site", "cause"), ("site", "robots_block"), ("site", "provider_incident"), ("site", "home_check"), ("site", "change_recorded"),
              ("platform", "stopped_before_site"), ("platform", "stopped_plain"), ("platform", "tools_off"), ("platform", "tools_off_plain"),
              ("platform", "message_documented"), ("platform", "differs_from_browser"), ("account", "extra_verification"),
              ("account", "company_login"), ("account", "sign_in"), ("account", "after_login"), ("agent_env", "cloud_browser"),
              ("task", "asks_each_action")}
    assert needed <= set(X.REASON_WORDS)
    assert set(X.PERSON_REASON_ID) == set(X.REASON_SHORT)
    assert all(tid in X.TEMPLATES for tid in X.PERSON_REASON_ID.values())


@pytest.mark.parametrize("tid", sorted(X.TEMPLATES))
def test_no_template_names_a_real_personal_host_or_a_site_category(tid):
    for lang, text in X.TEMPLATES[tid].items():
        low = text.lower()
        assert "equate" + "plus" not in low
        assert not re.search(r"\bis (a|an) (financial|banking|adult|high-risk)", low), (tid, lang)


def test_the_contracts_and_the_decision_describe_the_endpoint():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    for name in ("docs/api-contract.en.md", "docs/api-contract.md"):
        text = (root / name).read_text(encoding="utf-8")
        assert "/v1/why" in text and "ahn-why/1" in text and "D108" in text, name
    assert "| D108 " in (root / "docs/decisions.md").read_text(encoding="utf-8")
    assert "/v1/why" in (root / "ahn/llms.txt").read_text(encoding="utf-8")
