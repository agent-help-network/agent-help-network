"""D111 (pure, no database): a hand_off_to_human first line never offers another try, a status page that failed is not sent back to,
provider_status says how old the provider's own update time is, and /v1/why echoes the names of the parameters it ignored.

The test-marked lookups (trigger_kind 'test') are in test_d111_test_marked_db.py."""

from __future__ import annotations

import itertools
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from test_d101_failure_line_matches_do import STAGES, guide_args, rec_of, result_of, variants  # noqa: E402

from ahn import explain, response_guide, service, site_probe, triage  # noqa: E402
from ahn.models import ProviderStatus  # noqa: E402
from ahn.response_guide import ROWS, guide, status_self_reference  # noqa: E402
from ahn.why_models import WhyInputEcho  # noqa: E402

NOW = datetime(2026, 11, 8, 12, 0, tzinfo=timezone.utc)
# The words of a first line that offers another try or a check of the caller's own tool (D111). A hand-off tells the user now.
RETRY_WORDS = re.compile(r"retry|try again|one more try|once more|another try|check your tool|tool and path", re.I)
RETRY_WORDS_KO = re.compile(r"재시도|다시 시도|한 번 더|도구를 .{0,12}확인")


def hand_off_sentences() -> dict[str, str]:
    """Every fixed hand_off_to_human sentence the guide holds, by name."""
    found = {f"ROWS[{row}]": text for row, (do, text) in ROWS.items() if do == "hand_off_to_human"}
    for name in ("HANDOFF_NO_RESPONSE_ROWS", "HANDOFF_SERVICE_ERROR_ROWS"):
        found.update({f"{name}[{row}]": text for row, text in getattr(response_guide, name).items()})
    found.update({f"EDGE_CONSUMER_ROWS[{row}]": text for row, text in response_guide.EDGE_CONSUMER_ROWS.items()})
    for name in ("HANDOFF_NO_RESPONSE_CUSTOMER", "HANDOFF_SERVICE_ERROR_CUSTOMER", "H3_STALE", "H3_STALE_CONSUMER"):
        found[name] = getattr(response_guide, name)
    found.update({f"STATUS_ROWS[{row}]": text for row, text in response_guide.STATUS_ROWS.items() if row.endswith("-F")})
    return found


def test_no_fixed_hand_off_sentence_offers_another_try():
    sentences = hand_off_sentences()
    assert len(sentences) > 20
    for name, text in sentences.items():
        assert not RETRY_WORDS.search(text), (name, text)


def test_no_hand_off_answer_the_grid_produces_offers_another_try():
    seen = 0
    for case in variants(13):
        for stage, same in itertools.product(STAGES, (False, True)):
            rec = rec_of(triage, case, stage, True, ref=True, d101=True, same=same)
            for status_check in (False, True):
                args = list(guide_args(case, stage, True))
                out = guide(result_of(case, rec), args[0], False, status_check, True, *args[4:])
                if out["next_action"]["do"] == "hand_off_to_human":
                    seen += 1
                    assert not RETRY_WORDS.search(out["summary_line"]), (case, stage, out["summary_line"])
    assert seen > 100


def test_the_tool_check_moved_to_next_checks_and_both_languages_say_tell_the_user_now():
    en, ko = triage.CHECKS[triage.NO_PATH_CODE], triage.CHECKS_KO[triage.NO_PATH_CODE]
    assert "Tell your user now" in en and "check your own tool" in en
    assert "사용자에게 지금 알리" in ko and not RETRY_WORDS_KO.search(ko.split("별도로")[0])
    assert "자신의 도구" in ko  # the tool check is kept, in next_checks, as a separate step


def test_the_do_and_the_recommendation_of_a_hand_off_do_not_depend_on_the_wording():
    for case in variants(29):
        for stage in STAGES:
            rec = rec_of(triage, case, stage, True, ref=True, d101=True, same=True)
            out = guide(result_of(case, rec), *guide_args(case, stage, True))
            assert out["next_action"]["do"] in {do for do, _ in ROWS.values()} | {"no_clear_action"}
            assert set(rec) >= {"action", "confidence", "reasons"}


# --- the status page that failed ---------------------------------------------------------------------------------------------

FEED = "https://status.provider-example.org/api/v2/summary.json"


def status_result(site_host, status_url):
    rec = {"action": "wait_for_provider", "confidence": "high", "reasons": ["provider_incident"]}
    return {"site_host": site_host, "provider_status": {"source_name": "P", "source_url": status_url, "indicator": "major", "freshness": "fresh",
                                                         "basis": "incident", "active_incidents": 1, "max_incident_impact": "major",
                                                         "incident_updated_at": "2026-11-08T11:00:00Z", "active_maintenances": 0},
            "official_status_page": None, "access_profile": None, "official_paths": None, "recommendation": rec}


def test_status_self_reference_needs_a_failure_and_the_same_host_or_its_root_domain():
    page = "https://status.provider-example.org/"
    assert status_self_reference(page, "status.provider-example.org", "service_error", None)
    assert status_self_reference("https://provider-example.org/status", "www.provider-example.org", None, "before_target_response")
    assert not status_self_reference(page, "status.provider-example.org", None, None)           # no failure reported
    assert not status_self_reference(page, "other-example.net", "service_error", None)          # another site
    assert not status_self_reference(None, "provider-example.org", "service_error", None)


def test_a_failed_status_page_is_not_sent_back_to_the_feed_json_is_or_nothing():
    result = status_result("status.provider-example.org", "https://status.provider-example.org/")
    kwargs = dict(error_class="service_error", failure_stage=None)
    def go(feed_url):
        return guide(result, True, False, False, True, kwargs["error_class"], kwargs["failure_stage"], True, True, feed_url=feed_url)["next_action"]
    assert go(FEED)["status_url"] == FEED
    assert "status_url" not in go(None) and "status_url_covers" not in go(None)
    # no failure reported: the page is handed over as before
    plain = guide(result, True, False, False, True, None, None, True, True, feed_url=FEED)["next_action"]
    assert plain["status_url"] == "https://status.provider-example.org/"
    # another site's status page is still handed over on a failure
    other = status_result("shop-example.net", "https://status.provider-example.org/")
    assert guide(other, True, False, False, True, "service_error", None, True, True, feed_url=FEED)["next_action"]["status_url"] \
        == "https://status.provider-example.org/"


def test_only_status_url_and_the_row_of_the_status_page_change_when_it_is_left_out():
    result = status_result("status.provider-example.org", "https://status.provider-example.org/")
    on = guide(result, True, False, False, True, "service_error", None, True, True)
    off = guide(result, True, False, False, False, "service_error", None, True, True)
    assert on["next_action"].get("status_url") is None
    assert {k: v for k, v in on["next_action"].items() if k != "status_url"} == off["next_action"]
    assert on["summary_line"] == off["summary_line"]


# --- provider_status.source_age_days -----------------------------------------------------------------------------------------

def iso(days, extra_hours=0):
    return (NOW - timedelta(days=days, hours=extra_hours)).strftime("%Y-%m-%dT%H:%M:%SZ")


def stored(updated):
    out = {"source_name": "P", "source_url": "https://status.example.net/", "indicator": "none", "freshness": "fresh", "basis": "components_only",
           "active_incidents": 0, "max_incident_impact": "none", "active_maintenances": 0, "fetched_at": iso(0), "source_updated_at": updated}
    return out


def test_source_age_days_is_whole_days_and_stale_only_over_thirty():
    for days, hours, age, stale in ((0, 5, 0, False), (3, 2, 3, False), (30, 0, 30, False), (30, 23, 30, False), (31, 0, 31, True), (400, 0, 400, True)):
        view = service.provider_status_view(stored(iso(days, hours)), NOW)
        assert view["source_age_days"] == age, (days, hours)
        assert view.get("provider_page_stale") is (True if stale else None), (days, hours)
        assert (service.PROVIDER_PAGE_STALE_NOTE in view["note"]) is stale
        assert view["note"].startswith(service.PROVIDER_STATUS_NOTE)
        shown = ProviderStatus(**view).model_dump(exclude_unset=False, mode="json")
        assert ("provider_page_stale" in shown) is stale and shown["source_age_days"] == age


def test_a_missing_or_unusable_time_leaves_the_age_out_and_nothing_else_changes():
    for value in (None, "", "not a time", "2999-01-01T00:00:00Z"):
        view = service.provider_status_view(stored(value), NOW)
        assert "source_age_days" not in view and "provider_page_stale" not in view and view["note"] == service.PROVIDER_STATUS_NOTE
    # freshness, indicator and the rest are those of the stored reading
    old = service.provider_status_view(stored(iso(90)), NOW)
    assert {k: v for k, v in old.items() if k not in ("source_age_days", "provider_page_stale", "note")} == \
        {k: v for k, v in stored(iso(90)).items()}
    assert old["freshness"] == "fresh"
    dumped = ProviderStatus(**service.provider_status_view(stored(None), NOW)).model_dump(mode="json")
    assert "source_age_days" not in dumped and "provider_page_stale" not in dumped


# --- ahn_check note, /v1/why ignored_params -----------------------------------------------------------------------------------

def test_the_freshness_note_is_built_from_the_fresh_window():
    assert f"fresh means measured within the last {site_probe.FRESH_HOURS} hours" in site_probe.FRESHNESS_NOTE
    assert site_probe.FRESH_HOURS == 24


def test_ignored_params_are_sorted_names_only_capped_and_plain():
    assert explain.clean_ignored_params(["zeta", "alpha", "alpha", "x" * 41, "bad name", "a=b", "", 5]) == ("alpha", "zeta")
    assert len(explain.clean_ignored_params([f"p{i:02d}" for i in range(30)])) == explain.IGNORED_PARAMS_MAX
    assert explain.clean_ignored_params("alpha") == () and explain.clean_ignored_params(None) == ()
    inputs = explain.parse_inputs("provider-example.org", today=NOW.date(), ignored_params=["token", "alpha"])
    echo = explain._echo(inputs)
    assert echo["ignored_params"] == ["alpha", "token"]
    assert "ignored_params" not in explain._echo(explain.parse_inputs("provider-example.org", today=NOW.date()))
    assert WhyInputEcho(**{**echo}).ignored_params == ["alpha", "token"]
