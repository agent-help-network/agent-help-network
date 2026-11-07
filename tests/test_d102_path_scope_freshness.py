"""D102: three information fields and one sentence tail, with the verdict untouched.

* ``next_action.status_url_covers: "api"`` when ``status_url`` is an official status page that covers only the provider's API (D80).
* ``provider_status.note`` and the end of a measured ``ahn_check.note`` say what ``freshness`` measures.
* ``official_paths.consumer.paths[].topic`` and, with two or more customer pages, " More by topic: official_paths.consumer." at the end of
  a sentence that hands over ``next_action.customer_url`` (left off when it would take the line past 200 characters).

Pure: no database (the service wiring is in test_d102_path_scope_freshness_db.py). The decision code from before D102 is kept as
tests/_pre_d102_triage.py and tests/_pre_d102_response_guide.py; the comparison walks the grid of test_response_guide.space() and asserts both
directions: no pair outside the D102 conditions differs, and every pair inside them differs by exactly the stated addition."""

from __future__ import annotations

import copy
import importlib.util
import itertools
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

ROOT = Path(__file__).resolve().parents[1]
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from test_d85_edge_refusal import ABSENT, ALLOWS, GUIDE_MODES, STAGES  # noqa: E402
from test_response_guide import NOW, space  # noqa: E402
from test_site_probe import row as probe_row  # noqa: E402

from ahn import response_guide, service, site_probe, triage  # noqa: E402
from ahn.models import AhnCheck, ConsumerPath, NextAction, ProviderStatus  # noqa: E402
from ahn.official_paths import CONSUMER_PATHS, CONSUMER_TOPICS, customer_url  # noqa: E402
from ahn.response_guide import CUSTOMER_REF, LINE_MAX, MORE_BY_TOPIC, guide  # noqa: E402
from ahn.service import PROVIDER_STATUS_NOTE, official_page_view  # noqa: E402
from ahn.status_refresh import OFFICIAL_STATUS_PAGES  # noqa: E402
from ahn.triage import failure_observed  # noqa: E402

_spec = importlib.util.spec_from_file_location("pre_d102_triage", HERE / "_pre_d102_triage.py")
pre = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(pre)
_spec_rg = importlib.util.spec_from_file_location("pre_d102_response_guide", HERE / "_pre_d102_response_guide.py")
pre_rg = importlib.util.module_from_spec(_spec_rg)
_spec_rg.loader.exec_module(pre_rg)

FULL = os.environ.get("AHN_D102_FULL") == "1"   # the one-off full walk of the grid (the PR numbers); the suite walks every 29th case
HELP = "https://www.example.com/help"
RETURNS = "https://www.example.com/returns"
APP = "https://apps.apple.com/us/app/example/id123456"
ONE = {"paths": [{"kind": "customer_help", "url": HELP, "source_url": "https://www.example.com/"}], "verified_at": "2026-10-31"}
TWO = {"paths": [{"kind": "customer_help", "url": HELP, "source_url": "https://www.example.com/", "topic": "contact"},
                 {"kind": "customer_help", "url": RETURNS, "source_url": "https://www.example.com/", "topic": "returns"}],
       "verified_at": "2026-10-31"}
NOTOPIC = {"paths": [{k: v for k, v in p.items() if k != "topic"} for p in TWO["paths"]], "verified_at": "2026-10-31"}
THREE = {"paths": [*TWO["paths"], {"kind": "official_app", "url": APP, "source_url": "https://www.example.com/app"}],
         "verified_at": "2026-10-31"}
OFFICIAL = {"name": "Example", "api_docs": "https://developer.example.com/docs", "developer_portal": None, "note": None}
API_PAGE = {"source_name": "DHL API Service Status", "source_url": "https://status.api.dhl.com/", "covers": "api"}
PLAIN_PAGE = {"source_name": "Example Status", "source_url": "https://status.example.com/"}
TAIL_FORMS = {"none": None, "one": ONE, "two": TWO, "three": THREE, "two_no_topic": NOTOPIC}


def with_consumer(official, consumer):
    base = official or {"name": "Example", "api_docs": None, "developer_portal": None, "note": None}
    return {**base, "consumer": consumer} if consumer else official


def rec_of(mod, case, stage, pwf):
    return mod.recommendation(case.get("error_class"), case.get("provider_status"), case.get("access_profile"), case.get("official"),
                              profile_not_read=case.get("not_read", False), provider_regional=case.get("regional", False), now=NOW,
                              agent_vendor=case.get("vendor"), failure_stage=stage, proceed_without_failure=pwf)


def checks_of(mod, case, stage, pwf):
    return mod.next_checks(case.get("error_class"), case.get("provider_status"), "no_reports", stage, case.get("access_profile"),
                           case.get("official"), profile_not_read=case.get("not_read", False), provider_partial=case.get("regional", False),
                           now=NOW, agent_vendor=case.get("vendor"), proceed_without_failure=pwf)


def result_of(case, rec, page=None):
    return {"site_host": case.get("site_host", "example.com"), "provider_status": case.get("provider_status"), "official_status_page": page,
            "access_profile": case.get("access_profile"), "official_paths": case.get("official"), "recommendation": rec}


def grid(stride: int):
    """The D85 grid, each case with its official path as it is and with a one-, two- and three-page consumer path added."""
    for index, case in enumerate(space()):
        if index % stride:
            continue
        yield case
        for consumer in (ONE, TWO, THREE, NOTOPIC):
            yield {**case, "official": with_consumer(case.get("official") or OFFICIAL, consumer)}


def expected(old_out, new_in, page):
    """What the D102 guide must return, from the answer the guide gave before D102: the tail on a sentence that hands over the
    customer page when two or more are listed and it fits, and the status_url_covers key on an API-only official status page."""
    out = copy.deepcopy(old_out)
    action = out["next_action"]
    if "customer_url" in action and CUSTOMER_REF in out["summary_line"] and response_guide.consumer_topic_list(new_in.get("official_paths")) \
            and len(out["summary_line"]) + len(MORE_BY_TOPIC) <= LINE_MAX:
        out["summary_line"] += MORE_BY_TOPIC
    if page and page.get("covers") == "api" and action.get("status_url") == page["source_url"]:
        action["status_url_covers"] = "api"
    return out


def compare(stride: int) -> dict:
    counts = dict(cases=0, rec_pairs=0, guide_pairs=0, tail=0, tail_omitted_long=0, covers=0, unchanged=0)
    for case in grid(stride):
        counts["cases"] += 1
        for stage, pwf in itertools.product(STAGES, (True, False)):
            old_rec, new_rec = rec_of(pre, case, stage, pwf), rec_of(triage, case, stage, pwf)
            assert new_rec == old_rec and checks_of(triage, case, stage, pwf) == checks_of(pre, case, stage, pwf), (case, stage, pwf)
            counts["rec_pairs"] += 1
            for mode, page in itertools.product(GUIDE_MODES, (None, PLAIN_PAGE, API_PAGE)):
                if mode[2] and failure_observed(case.get("error_class"), stage):
                    continue
                args = (*mode, case.get("error_class"), stage, True, True)
                old_out = pre_rg.guide(result_of(case, old_rec, page), *args)
                new_in = result_of(case, new_rec, page)
                new_out = guide(new_in, *args)
                counts["guide_pairs"] += 1
                want = expected(old_out, new_in, page)
                assert new_out == want, (case, stage, mode, page)
                # the verdict-facing fields are byte-for-byte those of before D102
                for key in ("do", "url", "retry_after_seconds"):
                    assert new_out["next_action"][key] == old_out["next_action"][key], (case, stage, mode, key)
                assert new_out.get("links") == old_out.get("links")
                if new_out["summary_line"] != old_out["summary_line"]:
                    counts["tail"] += 1
                    assert new_out["summary_line"] == old_out["summary_line"] + MORE_BY_TOPIC and len(new_out["summary_line"]) <= LINE_MAX
                elif CUSTOMER_REF in old_out["summary_line"] and response_guide.consumer_topic_list(case.get("official")):
                    counts["tail_omitted_long"] += 1
                    assert len(old_out["summary_line"]) + len(MORE_BY_TOPIC) > LINE_MAX
                else:
                    counts["unchanged"] += 1
                if "status_url_covers" in new_out["next_action"]:
                    counts["covers"] += 1
    return counts


def test_the_pairs_that_change_are_exactly_the_d102_conditions_in_the_whole_grid():
    counts = compare(1 if FULL else 29)
    print("D102 comparison", counts)
    assert counts["tail"] > 50 and counts["covers"] > 50 and counts["unchanged"] > 1000
    assert counts["rec_pairs"] > 5000


# --- status_url_covers ------------------------------------------------------------------------------------------------------

def dhl_like(task_step_check: bool, page=API_PAGE, provider=None):
    case = dict(error_class=None, access_profile={"robots_status": "absent", "robots": None, "freshness": "fresh", "llms_txt_status": "absent"},
                official=OFFICIAL, provider_status=provider, site_host="dhl.com")
    rec = rec_of(triage, case, None, True)
    return guide(result_of(case, rec, page), True, False, task_step_check, True, None, None, True, True)


def test_a_dhl_shaped_lookup_names_the_api_scope_and_another_host_has_no_key():
    real = official_page_view(OFFICIAL_STATUS_PAGES["dhl.com"], "dhl.com")
    assert real["covers"] == "api" and real["source_url"] == "https://status.api.dhl.com/"
    for check in (True, False):  # a status_check lookup, and any other (D76: status_url is on every lookup)
        action = dhl_like(check, real)["next_action"]
        assert action["status_url"] == "https://status.api.dhl.com/" and action["status_url_covers"] == "api", check
        assert "status_url_covers" not in dhl_like(check, PLAIN_PAGE)["next_action"]
        assert "status_url_covers" not in dhl_like(check, None)["next_action"]
    other = official_page_view(OFFICIAL_STATUS_PAGES["github.com"], "github.com") if "github.com" in OFFICIAL_STATUS_PAGES else PLAIN_PAGE
    assert "covers" not in other and "status_url_covers" not in dhl_like(True, other)["next_action"]


def test_the_status_check_sentence_is_the_one_from_before_and_the_key_is_not_set_without_status_url():
    old = pre_rg.guide(result_of(dict(error_class=None, access_profile=None, official=OFFICIAL, site_host="dhl.com"),
                                 {"action": "unknown", "confidence": "low", "reasons": []}, API_PAGE), True, False, True, True, None, None, True, True)
    new = dhl_like(True)
    assert "covers only the provider's API" in new["summary_line"]
    case = dict(error_class=None, access_profile=None, official=OFFICIAL, site_host="dhl.com")
    rec = rec_of(triage, case, None, True)
    assert pre_rg.guide(result_of(case, rec, API_PAGE), True, False, True, True, None, None, True, True)["summary_line"] == new["summary_line"]
    assert old["next_action"].get("status_url") == "https://status.api.dhl.com/"
    # with the status guide off there is no status_url, so no scope to describe
    off = guide(result_of(case, rec, API_PAGE), True, False, False, False, None, None, True, True)
    assert "status_url" not in off["next_action"] and "status_url_covers" not in off["next_action"]


def test_a_feed_reading_is_never_described_as_api_only_even_if_the_two_objects_are_both_given():
    provider = {"source_name": "Feed", "source_url": "https://status.example.com/", "indicator": "none", "freshness": "fresh",
                "fetched_at": NOW, "source_updated_at": NOW}
    action = dhl_like(True, API_PAGE, provider)["next_action"]
    assert action["status_url"] == "https://status.example.com/" and "status_url_covers" not in action


def test_the_next_action_model_takes_only_api_and_leaves_the_key_out_when_absent():
    assert NextAction(do="use_official_path", status_url_covers="api").model_dump(mode="json")["status_url_covers"] == "api"
    assert "status_url_covers" not in NextAction(do="use_official_path").model_dump(mode="json", exclude_none=False) \
        or NextAction(do="use_official_path").model_dump(mode="json")["status_url_covers"] is None
    with pytest.raises(ValidationError):
        NextAction(do="use_official_path", status_url_covers="site")
    assert "status_url_covers" not in NextAction(do="use_official_path").model_dump_json()


# --- the two notes ----------------------------------------------------------------------------------------------------------

def test_the_provider_status_note_is_the_fixed_sentence_and_the_model_takes_it():
    assert PROVIDER_STATUS_NOTE == ("freshness is the age of AHN's last read of the feed (fetched_at); source_updated_at is the provider's "
                                    "own page time and need not move with the status.")
    assert len(PROVIDER_STATUS_NOTE) < 160 and "http" not in PROVIDER_STATUS_NOTE
    base = dict(source_name="S", source_url="https://status.example.com/", indicator="none", fetched_at=NOW, source_updated_at=NOW,
                freshness="fresh")
    assert ProviderStatus(**base).model_dump(mode="json").get("note") is None and "note" not in ProviderStatus(**base).model_dump_json()
    assert ProviderStatus(**base, note=PROVIDER_STATUS_NOTE).model_dump(mode="json")["note"] == PROVIDER_STATUS_NOTE


def test_a_measured_ahn_check_note_ends_with_the_freshness_sentence_and_the_others_do_not():
    tail = "freshness is the age of measured_at."
    assert site_probe.FRESHNESS_NOTE == " " + tail
    for outcome in site_probe.OUTCOMES:
        shown = site_probe.view(probe_row(outcome=outcome, http_status=403 if outcome != "unreachable" else None,
                                          bot_vendor="datadome" if outcome == "bot_challenge" else None), datetime(2026, 11, 1, 12, tzinfo=timezone.utc))
        assert shown["note"] == site_probe.SHOW_VIEW_NOTE_MEASURED == site_probe.SHOW_VIEW_NOTE + " " + tail, outcome
        assert AhnCheck(**shown).note.endswith(tail)
    assert site_probe.SHOW_VIEW_NOTE_EDGE.endswith(" " + tail) and site_probe.SHOW_VIEW_NOTE_EDGE.count(tail) == 1
    # a view with no freshness says nothing about it
    for shown in (site_probe.pending_view(), site_probe.not_measured_view("budget"),
                  site_probe.view(probe_row(outcome="not_measured", not_measured_reason="robots_disallowed"), datetime(2026, 11, 1, 12, tzinfo=timezone.utc))):
        assert shown["note"] == site_probe.SHOW_VIEW_NOTE and "freshness" not in shown


def test_the_two_notes_say_the_same_two_things_as_the_contract_and_llms_txt():
    text = (ROOT / "ahn" / "llms.txt").read_text(encoding="utf-8")
    contract = (ROOT / "docs" / "api-contract.en.md").read_text(encoding="utf-8")
    korean = (ROOT / "docs" / "api-contract.md").read_text(encoding="utf-8")
    for doc in (contract, korean):
        assert PROVIDER_STATUS_NOTE in doc and "freshness is the age of measured_at." in doc
    assert "provider_status.note" in text and "status_url_covers" in text and "More by topic: official_paths.consumer." in text
    assert "More by topic: official_paths.consumer." in contract and "status_url_covers" in contract and "`topic`" in contract
    assert "status_url_covers" in korean and "`topic`" in korean


# --- topic ------------------------------------------------------------------------------------------------------------------

def test_topic_is_one_of_six_values_and_left_out_when_not_set():
    item = dict(kind="customer_help", url=HELP, source_url="https://www.example.com/")
    assert "topic" not in ConsumerPath(**item).model_dump_json()
    for topic in CONSUMER_TOPICS:
        assert ConsumerPath(**item, topic=topic).model_dump(mode="json")["topic"] == topic
    for bad in ("refund", "Returns", "", "contact_us", 3):
        with pytest.raises(ValidationError):
            ConsumerPath(**item, topic=bad)
    assert CONSUMER_TOPICS == ("returns", "orders", "contact", "help", "account", "shipping")


def test_the_table_carries_a_topic_on_the_two_bestbuy_pages_only_and_customer_url_ignores_it():
    paths = CONSUMER_PATHS["bestbuy.com"]["paths"]
    assert [p["topic"] for p in paths] == ["contact", "returns"] and all(p["kind"] == "customer_help" for p in paths)
    assert [host for host, entry in CONSUMER_PATHS.items() for p in entry["paths"] if "topic" in p and host != "bestbuy.com"] == []
    for entry in CONSUMER_PATHS.values():
        for p in entry["paths"]:
            ConsumerPath(**p)  # the table is valid against the model
    stripped = {"paths": [{k: v for k, v in p.items() if k != "topic"} for p in paths]}
    assert customer_url({"consumer": CONSUMER_PATHS["bestbuy.com"]}) == customer_url({"consumer": stripped}) == paths[0]["url"]
    # the order and the choice do not depend on the topic: reversing the topics changes nothing
    swapped = {"paths": [{**paths[0], "topic": "returns"}, {**paths[1], "topic": "contact"}]}
    assert customer_url({"consumer": swapped}) == paths[0]["url"]


# --- the tail ---------------------------------------------------------------------------------------------------------------

def customer_answer(consumer, *, error_class="site_access", stage=None, profile=None, official=None):
    case = dict(error_class=error_class, access_profile=profile, official=with_consumer(official or OFFICIAL, consumer))
    rec = rec_of(triage, case, stage, True)
    return guide(result_of(case, rec), True, False, False, True, error_class, stage, True, True), rec


def test_one_customer_page_leaves_every_sentence_byte_for_byte_and_two_or_more_add_the_tail():
    for error_class, stage in itertools.product((None, "site_access", "human_verification", "login_auth", "service_error"), STAGES):
        one, _ = customer_answer(ONE, error_class=error_class, stage=stage)
        case = dict(error_class=error_class, access_profile=None, official=with_consumer(OFFICIAL, ONE))
        before = pre_rg.guide(result_of(case, rec_of(pre, case, stage, True)), True, False, False, True, error_class, stage, True, True)
        assert one == before, (error_class, stage)
        for consumer in (TWO, THREE):
            many, _ = customer_answer(consumer, error_class=error_class, stage=stage)
            line = many["summary_line"]
            if CUSTOMER_REF in one["summary_line"] and len(one["summary_line"]) + len(MORE_BY_TOPIC) <= LINE_MAX:
                assert line == one["summary_line"] + MORE_BY_TOPIC and line.endswith(" More by topic: official_paths.consumer.")
            else:
                assert line == one["summary_line"]
            assert len(line) <= LINE_MAX
            assert {k: v for k, v in many["next_action"].items()} == one["next_action"]  # do, url, customer_url: the same


def test_two_pages_with_no_topic_get_no_tail_and_the_bestbuy_entry_gets_it():
    for error_class, stage in itertools.product((None, "site_access", "login_auth"), STAGES):
        bare, _ = customer_answer(NOTOPIC, error_class=error_class, stage=stage)
        one, _ = customer_answer(ONE, error_class=error_class, stage=stage)
        assert bare == one and MORE_BY_TOPIC not in bare["summary_line"], (error_class, stage)
    # a single tagged page among untagged ones is enough
    mixed = {**TWO, "paths": [TWO["paths"][0], {k: v for k, v in TWO["paths"][1].items() if k != "topic"}]}
    assert response_guide.consumer_topic_list({"consumer": mixed}) and not response_guide.consumer_topic_list({"consumer": NOTOPIC})
    assert not response_guide.consumer_topic_list({"consumer": ONE}) and not response_guide.consumer_topic_list(None)
    # bestbuy.com: two tagged pages
    best = {**OFFICIAL, "consumer": CONSUMER_PATHS["bestbuy.com"]}  # a developer API listed beside the two customer pages, as the tail rows need
    assert response_guide.consumer_topic_list(best)
    pending = {"robots_status": "pending", "robots": None, "freshness": "pending", "llms_txt_status": "pending", "retry_after_seconds": 120}
    hits = tails = 0
    for error_class, stage, profile in itertools.product((None, "site_access", "service_error"), STAGES, (None, pending, ABSENT, ALLOWS)):
        case = dict(error_class=error_class, access_profile=profile, official=best)
        rec = rec_of(triage, case, stage, True)
        args = (True, True, False, True, error_class, stage, True, True)
        line = guide(result_of(case, rec), *args)["summary_line"]
        old = pre_rg.guide(result_of(case, rec_of(pre, case, stage, True)), *args)["summary_line"]
        if CUSTOMER_REF in old:
            hits += 1
            fits = len(old) + len(MORE_BY_TOPIC) <= LINE_MAX
            assert line == (old + MORE_BY_TOPIC if fits else old), (error_class, stage)
            tails += fits
        else:
            assert line == old
    assert hits and tails, (hits, tails)


def test_hosts_that_get_the_tail_are_counted():
    tagged = [host for host, entry in CONSUMER_PATHS.items() if response_guide.consumer_topic_list({"consumer": entry})]
    assert tagged == ["bestbuy.com"]


def test_every_customer_sentence_of_the_guide_is_checked_against_the_200_character_line():
    """Row by row: the sentences that name next_action.customer_url and whether the tail fits after each."""
    sentences = set()

    def collect(value):
        if isinstance(value, str):
            if CUSTOMER_REF in value:
                sentences.add(value)
        elif isinstance(value, dict):
            for item in value.values():
                collect(item)
        elif isinstance(value, (tuple, list)):
            for item in value:
                collect(item)

    for name in dir(response_guide):
        if name.isupper():
            collect(getattr(response_guide, name))
    assert len(sentences) > 20
    fits = [s for s in sentences if len(s) + len(MORE_BY_TOPIC) <= LINE_MAX]
    over = [s for s in sentences if len(s) + len(MORE_BY_TOPIC) > LINE_MAX]
    assert fits, "some sentences take the tail"
    print("D102 customer sentences:", len(sentences), "take the tail:", len(fits), "left bare:", len(over))


def test_a_sentence_that_would_pass_200_characters_is_left_as_it_was(monkeypatch):
    case = dict(error_class=None, access_profile={"robots_status": "pending", "robots": None, "freshness": "pending", "llms_txt_status": "pending",
                                                     "retry_after_seconds": 120}, official=with_consumer(OFFICIAL, TWO))
    rec = rec_of(triage, case, None, True)

    def line():
        return guide(result_of(case, rec), True, False, False, True, None, None, True, True)["summary_line"]

    bare = line()
    assert CUSTOMER_REF in bare and not bare.endswith(MORE_BY_TOPIC) and len(bare) + len(MORE_BY_TOPIC) > LINE_MAX
    # a tail that brings the line exactly to the limit is added, one character more is not
    monkeypatch.setattr(response_guide, "MORE_BY_TOPIC", "x" * (LINE_MAX - len(bare)))
    assert line() == bare + "x" * (LINE_MAX - len(bare)) and len(line()) == LINE_MAX
    monkeypatch.setattr(response_guide, "MORE_BY_TOPIC", "x" * (LINE_MAX - len(bare) + 1))
    assert line() == bare
