"""A curated task route record must point at its own site: every URL host must be the key or a subdomain of it. No database, no network."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from pydantic import ValidationError

from ahn import task_routes, task_routes_data
from ahn.task_routes import TaskRoute, official_url_problem
from ahn.task_routes_data import _route

URL_FIELDS = ("official_url", "source_url")

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("verify_official_paths_xdom", ROOT / "scripts" / "verify_official_paths.py")
V = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(V)

TERMS = "https://patient.thirtymadison.com/legals/terms-and-conditions"


def key_domain_problems(table):
    problems = []
    for key, records in table.items():
        for record in records:
            for field in URL_FIELDS:
                url = record.get(field)
                if not url:
                    continue
                host = (urlsplit(url).hostname or "").lower()
                key_host = key.lower()
                on_key = host == key_host or host.endswith("." + key_host)
                if not on_key and (key, host) not in task_routes.CURATED_CROSS_DOMAIN:
                    problems.append(f"{key} {field} {host}")
    return problems


def test_curated_urls_stay_on_the_key_domain():
    assert key_domain_problems(task_routes_data.TASK_ROUTES_CURATED) == []


def _table(url):
    return {"calm.com": [{"official_url": "https://www.calm.com/help", "source_url": url}]}


def test_subdomains_of_the_key_pass():
    assert key_domain_problems(_table("https://support.calm.com/terms")) == []


def test_other_domain_is_flagged_and_can_be_allowed_explicitly(monkeypatch):
    table = _table("https://www.example.org/terms")
    assert key_domain_problems(table) == ["calm.com source_url www.example.org"]
    monkeypatch.setattr(task_routes, "CURATED_CROSS_DOMAIN", frozenset({("calm.com", "www.example.org")}))
    assert key_domain_problems(table) == []
    # the allowance is for that exact pair only
    assert key_domain_problems({"calm.com": [{"official_url": "https://other.example.org/x"}]}) != []


def test_sibling_hosts_under_a_shared_parent_are_flagged():
    # government keys are whole hosts, so a sibling under the same parent is another site
    assert key_domain_problems({"dmv.ca.gov": [{"source_url": "https://edd.ca.gov/x"}]}) == [
        "dmv.ca.gov source_url edd.ca.gov"
    ]
    assert key_domain_problems({"my.uscis.gov": [{"source_url": "https://www.uscis.gov/x"}]}) == [
        "my.uscis.gov source_url www.uscis.gov"
    ]


def test_allowance_is_tied_to_its_key(monkeypatch):
    monkeypatch.setattr(task_routes, "CURATED_CROSS_DOMAIN", frozenset({("calm.com", "www.example.org")}))
    assert key_domain_problems({"headspace.com": [{"source_url": "https://www.example.org/terms"}]}) == [
        "headspace.com source_url www.example.org"
    ]


def test_lookalike_suffix_is_not_the_key_domain():
    assert key_domain_problems(_table("https://notcalm.com/terms")) != []


def test_curated_check_fails_for_a_moved_record(monkeypatch):
    moved = {"calm.com": [{"official_url": "https://www.example.org/terms"}]}
    monkeypatch.setattr(task_routes_data, "TASK_ROUTES_CURATED", moved)
    with pytest.raises(AssertionError):
        test_curated_urls_stay_on_the_key_domain()
    monkeypatch.setattr(task_routes, "CURATED_CROSS_DOMAIN", frozenset({("calm.com", "www.example.org")}))
    test_curated_urls_stay_on_the_key_domain()


# --- The listed pairs: a curated record may point at the operator's document on another domain ---------------------------------------

def test_the_shipped_pairs_are_the_reviewed_set():
    assert task_routes.CURATED_CROSS_DOMAIN == {("keeps.com", "patient.thirtymadison.com"),
                                                ("nurx.com", "patient.thirtymadison.com")}


@pytest.mark.parametrize("key", ["keeps.com", "nurx.com"])
def test_a_listed_pair_passes_for_a_curated_record(key):
    assert official_url_problem(key, TERMS, curated=True) is None
    assert official_url_problem("www." + key, TERMS, curated=True) is None  # the key is read as route_host does


def test_the_pair_is_tied_to_its_key():
    assert official_url_problem("hims.com", TERMS, curated=True) == "not on the site's own registrable domain"
    assert official_url_problem("thirtymadison.com", "https://www.example.org/x", curated=True) is not None


def test_the_url_host_must_match_exactly():
    for url in ("https://www.thirtymadison.com/legals/terms-and-conditions",
                "https://thirtymadison.com/legals/terms-and-conditions",
                "https://other.patient.thirtymadison.com/legals/terms-and-conditions"):
        assert official_url_problem("keeps.com", url, curated=True) is not None


def test_a_listed_pair_is_refused_outside_the_curated_context():
    assert official_url_problem("keeps.com", TERMS) == "not on the site's own registrable domain"
    assert official_url_problem("keeps.com", TERMS, curated=False) == "not on the site's own registrable domain"


@pytest.mark.parametrize("url", [TERMS + "?a=1", TERMS + "/123456", "http://patient.thirtymadison.com/legals/terms",
                                 "https://patient.thirtymadison.com:8443/legals/terms", TERMS + "#x"])
def test_the_other_rules_still_apply_to_a_listed_pair(url):
    assert official_url_problem("keeps.com", url, curated=True) is not None


def _record(**fields):
    return {"site_host": "keeps.com", "sensitive": False,
            **_route("cancel_subscription", obstacle="wrong_channel", official_url=TERMS, source_url=TERMS, verified_at="2026-11-02"),
            **fields}


def test_the_model_takes_a_curated_record_on_a_listed_pair_and_refuses_an_operator_one():
    route = TaskRoute.model_validate(_record())
    assert route.model_dump(mode="json")["official_url"] == TERMS
    TaskRoute.model_validate(_record(site_host="nurx.com"))
    with pytest.raises(ValidationError):
        TaskRoute.model_validate(_record(site_host="hims.com"))
    operator = {"site_host": "keeps.com", "sensitive": False, "task_kind": "cancel_subscription", "obstacle": "wrong_channel",
                "routes_tried": ["web_form"], "route_worked": None, "basis": "own_attempt", "outcome": "no_progress",
                "period": "2026-Q4", "source_kind": "operator_panel", "official_url": TERMS}
    with pytest.raises(ValidationError):
        TaskRoute.model_validate(operator)


def test_the_table_checker_reads_the_same_list():
    route = _route("cancel_subscription", obstacle="wrong_channel", official_url=TERMS, source_url=TERMS, verified_at="2026-11-02")
    today = V.date(2026, 11, 13)
    assert V.task_routes_table_problems({"keeps.com": [route], "nurx.com": [route]}, today=today) == []
    assert V.task_routes_table_problems({"hims.com": [route]}, today=today)


def test_the_table_checker_follows_the_list_it_does_not_copy_it(monkeypatch):
    route = _route("cancel_subscription", obstacle="wrong_channel", official_url=TERMS, source_url=TERMS, verified_at="2026-11-02")
    today = V.date(2026, 11, 13)
    monkeypatch.setattr(task_routes, "CURATED_CROSS_DOMAIN", frozenset())
    assert V.task_routes_table_problems({"keeps.com": [route]}, today=today)
    monkeypatch.setattr(task_routes, "CURATED_CROSS_DOMAIN", frozenset({("hims.com", "patient.thirtymadison.com")}))
    assert V.task_routes_table_problems({"hims.com": [route]}, today=today) == []
    assert V.task_routes_table_problems({"keeps.com": [route]}, today=today)
