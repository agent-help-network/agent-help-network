"""D108: how GET /v1/why reads what a person chose into the codes the lookup already understands, and what it does
with a value that is not on a list."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from ahn import explain as E
from tests._why_fixtures import TODAY, inputs


def mapped(**kw):
    return E.map_symptoms(inputs(**kw))


@pytest.mark.parametrize("stop,expected", [
    ("before_visit", (None, "before_target_response", "site_open")),
    ("page_blank_or_blocked", ("site_access", None, "site_open")),
    ("human_verification", ("human_verification", None, "human_verification")),
    ("company_login", ("login_auth", None, "login")),
    ("site_login", ("login_auth", None, "login")),
    ("after_login_action_fails", (None, "response_observed", "service_use")),
    ("tools_off_page_visible", (None, None, "other")),
    ("timeout_or_5xx", ("service_error", None, "other")),
    ("not_sure", (None, None, "other")),
    ("extra_verification", ("login_auth", None, "login")),
])
def test_the_ten_stop_points(stop, expected):
    assert mapped(stop_point=stop) == expected


@pytest.mark.parametrize("message,expected", [
    ("human_check", ("human_verification", None, "other")),
    ("sign_in_required", ("login_auth", None, "other")),
    ("access_denied", ("site_access", "access_denial_observed", "other")),
    ("page_not_loading", ("service_error", None, "other")),
    ("approval_on_other_device", ("login_auth", None, "login")),
])
def test_not_sure_is_read_from_the_message_and_a_message_alone_counts_as_not_sure(message, expected):
    assert mapped(stop_point="not_sure", screen_message=message) == expected
    assert mapped(screen_message=message) == expected


def test_a_message_refines_a_page_blank_stop_only_for_access_denied():
    assert mapped(stop_point="page_blank_or_blocked", screen_message="access_denied") == ("site_access", "access_denial_observed", "site_open")
    assert mapped(stop_point="page_blank_or_blocked", screen_message="human_check") == ("site_access", None, "site_open")


def test_approval_on_another_device_fills_a_stop_that_named_no_class():
    assert mapped(stop_point="tools_off_page_visible", screen_message="approval_on_other_device") == ("login_auth", None, "login") or \
        mapped(stop_point="tools_off_page_visible", screen_message="approval_on_other_device")[0] == "login_auth"
    assert mapped(stop_point="timeout_or_5xx", screen_message="approval_on_other_device")[0] == "service_error"


def test_nothing_chosen_maps_to_nothing():
    assert mapped() == (None, None, None)


def test_a_measured_code_wins_over_the_mapping():
    assert mapped(stop_point="company_login", error_class="service_error", failure_stage="response_observed") == \
        ("service_error", "response_observed", "login")


def test_values_outside_their_list_are_ignored_and_never_echoed():
    i = inputs(agent_platform="no-such-product", agent_env="x", external_browser="y", stop_point="z", screen_message="w",
               since_kind="v", task_context="u", error_class="t", failure_stage="s")
    assert (i.agent_platform, i.agent_env, i.external_browser, i.stop_point, i.screen_message, i.since_kind, i.task_context,
            i.error_class, i.failure_stage) == (None,) * 9
    assert E._echo(i) == {}
    assert "no-such-product" not in E._self_link(i) and E._self_link(i) == "/v1/why?site_host=login-fixture-host.com&lang=en"


def test_non_string_values_are_ignored_too():
    assert inputs(stop_point=["before_visit"], agent_platform=5, since_kind={"a": 1}).stop_point is None


def test_since_date_counts_only_with_worked_before():
    d = (TODAY - timedelta(days=5)).isoformat()
    assert inputs(since_kind="worked_before", since_date=d).since_date == TODAY - timedelta(days=5)
    assert inputs(since_kind="never_worked", since_date=d).since_date is None
    assert inputs(since_date=d).since_date is None


def test_since_date_is_clamped_to_90_days_and_a_future_date_is_ignored():
    old = inputs(since_kind="worked_before", since_date=(TODAY - timedelta(days=400)).isoformat())
    assert old.since_date == TODAY - timedelta(days=E.SINCE_MAX_DAYS) and old.since_clamped is True
    assert E._echo(old)["since_clamped"] is True
    assert inputs(since_kind="worked_before", since_date=(TODAY + timedelta(days=1)).isoformat()).since_date is None
    assert inputs(since_kind="worked_before", since_date="not-a-date").since_date is None
    assert inputs(since_kind="worked_before", since_date=TODAY.isoformat()).since_clamped is False


def test_host_cleaning_drops_scheme_path_query_user_and_port():
    for raw in ("https://login-fixture-host.com/a/b?token=abc", "http://user:pw@login-fixture-host.com:8443/x",
                "login-fixture-host.com#frag", "LOGIN-FIXTURE-HOST.COM"):
        host, _ = E.clean_host(raw)
        assert host == "login-fixture-host.com"
    assert E.clean_host("login-fixture-host.com") == ("login-fixture-host.com", False)
    assert E.clean_host("login-fixture-host.com/") == ("login-fixture-host.com", False)
    assert E.clean_host("https://login-fixture-host.com/x?a=1")[1] is True
    assert E.clean_host("user@login-fixture-host.com")[1] is True


def test_a_pasted_address_is_marked_path_dropped_and_the_cut_part_is_not_kept():
    i = inputs("https://login-fixture-host.com/private/path?token=SECRET")
    assert i.host_normalized == "path_dropped" and i.site_host == "login-fixture-host.com"
    assert "SECRET" not in repr(i) and "private" not in repr(i) and "private" not in E._self_link(i)


def test_a_subdomain_is_answered_as_its_apex():
    i = inputs("app.login-fixture-host.com")
    assert i.site_host == "login-fixture-host.com" and i.host_normalized == "apex"


def test_a_www_alias_is_not_reported_as_an_apex_change():
    i = inputs("www.login-fixture-host.com")
    assert i.host_normalized is None


@pytest.mark.parametrize("bad", ["", "   ", "not a host", "localhost", "127.0.0.1", "a" * 300 + ".com", "https://"])
def test_an_invalid_host_raises(bad):
    with pytest.raises(ValueError):
        inputs(bad)


def test_language_falls_back_to_english():
    assert inputs(lang="ko").lang == "ko" and inputs(lang="fr").lang == "en" and inputs(lang=None).lang == "en"
