from ahn.triage import CHECKS, next_checks, recommendation


def test_old_reports_do_not_claim_no_reports_exist():
    checks = next_checks(None, None, "stale_reports")
    assert checks[-1]["code"] == "reports_stale"
    assert "older published observations" in checks[-1]["text"]


def test_unspecified_failure_starts_with_locating_the_response():
    checks = next_checks(None, None, "no_reports")
    assert checks[0]["code"] == "locate_failure_stage"
    assert "before attributing a site failure" in checks[0]["text"]


def test_human_challenge_and_active_provider_issue_remain_distinct():
    provider = {"freshness": "fresh", "indicator": "minor"}
    codes = [item["code"] for item in next_checks("human_verification", provider, "no_reports")]
    assert codes == ["human_handoff", "provider_scope", "reports_unknown"]


def test_clear_provider_statement_does_not_prove_task_access():
    provider = {"freshness": "fresh", "indicator": "none"}
    checks = next_checks("login_auth", provider, "single_observation")
    assert [item["code"] for item in checks] == ["account_handoff", "provider_clear", "reports_unverified"]
    assert "does not prove access" in checks[1]["text"]


def test_reported_failure_stage_changes_advice_without_claiming_cause():
    checks = next_checks("site_access", None, "no_reports", "before_target_response")
    assert [item["code"] for item in checks] == ["before_target_response", "provider_unknown", "reports_unknown"]
    assert "does not establish a site outage" in checks[0]["text"]
    denied = next_checks(None, None, "no_reports", "access_denial_observed")
    assert "do not bypass" in denied[0]["text"]


def test_maintenance_is_waited_out_like_minor_but_says_it_is_scheduled_maintenance():
    minor = {"freshness": "fresh", "indicator": "minor"}
    maintenance = {"freshness": "fresh", "indicator": "maintenance"}
    assert recommendation("site_access", maintenance, None, None) == recommendation("site_access", minor, None, None)
    assert recommendation("site_access", maintenance, None, None)["action"] == "wait_provider_incident"
    checks = next_checks(None, maintenance, "no_reports")
    assert [item["code"] for item in checks] == ["locate_failure_stage", "provider_scope", "reports_unknown"]
    text = checks[1]["text"]
    assert "scheduled maintenance" in text and "retrying in a loop" in text
    # Existing values keep their text.
    assert next_checks(None, minor, "no_reports")[1]["text"] == CHECKS["provider_scope"]
    assert "scheduled maintenance" not in CHECKS["provider_scope"]


def test_stale_maintenance_is_not_treated_as_a_current_statement():
    stale = {"freshness": "stale", "indicator": "maintenance"}
    assert recommendation("site_access", stale, None, None)["action"] != "wait_provider_incident"
    assert "provider_scope" not in [item["code"] for item in next_checks(None, stale, "no_reports")]
