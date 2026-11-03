"""D81: wording of the status_check summary line. Verdicts, next_action, links and every other lookup are unchanged.

Three sentences change, only for task_step=status_check without a failure signal:
- the robots sentence after the status sentence says the developer path is for anything beyond the status question;
- a status page that covers only the provider's API says so in the status sentence;
- a name RFC 2606/6761 reserves for documentation gets a sentence of its own."""

from __future__ import annotations

import copy
import itertools

import pytest

from ahn import response_guide
from ahn.lookup_signals import is_reserved_example
from ahn.response_guide import LINE_MAX, ROWS, STATUS_LINE_MAX, STATUS_ROWS, guide
from test_response_guide import (ALL_TOKENS, OFFICIAL, OTHER_PAGE, PAGE_URL, PRINTABLE, answer, block, main_space, page, robots,
                                 signin_page, status_answer)

REAL = "viator.com"
BLOCKED = robots(block(*sorted(ALL_TOKENS)))          # S1: every token blocked
WILDCARD = robots("User-agent: *\nDisallow: /\n")      # O6 with a listed developer path
API_PAGE = {**page(OTHER_PAGE), "covers": "api"}


def real(**kw) -> dict:
    return answer(REAL, access_profile=robots("User-agent: *\nAllow: /\n"), **kw)


def beyond(line: str) -> bool:
    return "for anything beyond the status question, use the listed official developer path" in line.lower()


# --- (1) the developer path is not a way to check status -------------------------------------------------------------

@pytest.mark.parametrize("row", sorted(response_guide.STATUS_AFTER_ROWS))
def test_every_plain_robots_row_with_a_developer_path_says_it_is_for_the_rest_of_the_task(row):
    assert beyond(response_guide.STATUS_AFTER_ROWS[row])
    assert not beyond(ROWS[row][1])
    assert "robots" in response_guide.STATUS_AFTER_ROWS[row].lower() or "refused" in response_guide.STATUS_AFTER_ROWS[row]


def test_a_status_check_on_a_wildcard_block_with_no_status_page_does_not_send_the_caller_to_the_developer_path_for_status():
    result = answer(REAL, official=OFFICIAL, access_profile=WILDCARD)
    plain = guide(copy.deepcopy(result))
    out = guide(copy.deepcopy(result), status_check=True, status_guide=True)
    assert plain["next_action"]["do"] == "use_official_path"
    assert out["summary_line"] == f"{STATUS_ROWS['T4-R']} {response_guide.STATUS_AFTER_ROWS['O6']}"
    assert out["summary_line"].startswith("AHN knows no official status page")
    assert beyond(out["summary_line"]) and not beyond(plain["summary_line"])
    assert out["next_action"] == plain["next_action"] and out["links"] == plain["links"]


def test_the_robots_sentence_is_unchanged_without_task_step_and_for_rows_that_name_no_developer_path():
    result = answer(REAL, official=OFFICIAL, access_profile=WILDCARD)
    assert guide(copy.deepcopy(result), status_check=False, status_guide=True)["summary_line"] == guide(copy.deepcopy(result))["summary_line"]
    stopped = answer(REAL, access_profile=BLOCKED)  # no developer path listed: S1, which names none
    out = guide(stopped, status_check=True)
    assert out["summary_line"].endswith(ROWS["S1"][1]) and not beyond(out["summary_line"])


def test_a_row_with_a_customer_page_keeps_its_sentence_after_the_status_sentence():
    official = {**OFFICIAL, "consumer": {"paths": [{"kind": "customer_help", "url": "https://help.viator.com/"}]}}
    result = answer(REAL, official=official, access_profile=WILDCARD)
    plain = guide(copy.deepcopy(result))
    assert plain["next_action"]["customer_url"] == "https://help.viator.com/"
    out = guide(copy.deepcopy(result), status_check=True)
    assert out["summary_line"].endswith(plain["summary_line"])


def test_every_robots_sentence_keeps_the_d76_and_d79_wording():
    for table in (response_guide.STATUS_AFTER_ROWS, response_guide.STATUS_AFTER_ROWS_BEFORE):
        for row, text in table.items():
            assert 1 <= len(text) <= LINE_MAX and PRINTABLE.fullmatch(text), row
            assert "http" not in text and "Check the site itself" not in text


# --- (2) a status page that covers only the provider's API ------------------------------------------------------------

def test_a_status_page_that_covers_only_the_api_says_so_in_the_status_sentence():
    out = guide(real(official_page=API_PAGE, official=OFFICIAL), status_check=True)
    assert out["summary_line"] == STATUS_ROWS["T3-A"]
    assert "only the provider's API, not the whole site" in out["summary_line"] and "next_action.status_url" in out["summary_line"]
    assert out["next_action"]["status_url"] == OTHER_PAGE


def test_without_covers_the_status_sentence_is_the_one_before():
    out = guide(real(official_page=page(OTHER_PAGE), official=OFFICIAL), status_check=True)
    assert out["summary_line"] == STATUS_ROWS["T3"]


def test_a_robots_row_on_the_same_site_names_no_page_but_still_says_the_api_page_is_not_the_site():
    result = answer(REAL, access_profile=WILDCARD, official=OFFICIAL, official_page={**page("https://status.viator.com/"), "covers": "api"})
    out = guide(copy.deepcopy(result), status_check=True)
    assert out["summary_line"].startswith(STATUS_ROWS["T3-RA"]) and "http" not in out["summary_line"]
    other = copy.deepcopy(result)
    other["official_status_page"].pop("covers")
    assert guide(other, status_check=True)["summary_line"].startswith(STATUS_ROWS["T3-R"])


def test_covers_changes_nothing_but_the_status_sentence():
    for covers in (None, "api"):
        pg = {**page(OTHER_PAGE), **({"covers": covers} if covers else {})}
        result = real(official_page=pg, official=OFFICIAL)
        with_covers = guide(copy.deepcopy(result), status_check=True)
        without = guide(copy.deepcopy({**result, "official_status_page": page(OTHER_PAGE)}), status_check=True)
        assert with_covers["next_action"] == without["next_action"] and with_covers["links"] == without["links"]
        assert guide(copy.deepcopy(result))["summary_line"] == guide(copy.deepcopy({**result, "official_status_page": page(OTHER_PAGE)}))["summary_line"]


def test_a_sign_in_page_keeps_its_sentence_even_with_covers():
    out = guide(real(official_page={**signin_page(OTHER_PAGE), "covers": "api"}, official=OFFICIAL), status_check=True)
    assert out["summary_line"] == STATUS_ROWS["T3-S"]


# --- (3) names reserved for documentation (RFC 2606 / 6761) -----------------------------------------------------------

RESERVED = ["example.com", "example.net", "example.org", "www.example.com", "a.b.example.org", "EXAMPLE.COM", "example.com.",
            "foo.example", "foo.test", "x.invalid", "localhost", "app.localhost", "example"]
NOT_RESERVED = ["viator.com", "notexample.com", "example.com.au", "example.co.uk", "myexample.org", "example-shop.com", "test.com",
                "invalid.com", "localhost.com", "examples.com", "", "dhl.com"]


@pytest.mark.parametrize("host", RESERVED)
def test_reserved_names(host):
    assert is_reserved_example(host)


@pytest.mark.parametrize("host", NOT_RESERVED)
def test_names_that_are_not_reserved(host):
    assert not is_reserved_example(host)


def test_the_reserved_check_extends_the_existing_example_host_set():
    from ahn.lookup_signals import EXAMPLE_HOSTS
    assert all(is_reserved_example(host) for host in EXAMPLE_HOSTS)


@pytest.mark.parametrize("host", ["example.com", "docs.example.org", "shop.test"])
def test_a_status_check_for_a_reserved_name_says_it_runs_no_service(host):
    result = answer(host, access_profile=robots("User-agent: *\nAllow: /\n"))
    plain = guide(copy.deepcopy(result))
    out = guide(copy.deepcopy(result), status_check=True, status_guide=True)
    assert out["summary_line"] == STATUS_ROWS["T-EX"]
    assert out["summary_line"] == ("This is a reserved example domain (RFC 2606) for documentation; "
                                   "it runs no service with a status to check.")
    assert out["next_action"] == plain["next_action"] and out["links"] == plain["links"]


def test_a_reserved_name_without_task_step_and_a_real_name_are_unchanged():
    result = answer("example.com", access_profile=robots("User-agent: *\nAllow: /\n"))
    assert guide(copy.deepcopy(result), status_check=False, status_guide=True)["summary_line"] == ROWS["P1"][1]
    real = answer(REAL, access_profile=robots("User-agent: *\nAllow: /\n"))
    assert guide(real, status_check=True)["summary_line"] == STATUS_ROWS["T4"]


def test_a_reserved_name_keeps_the_waits_and_the_hand_offs():
    handed = answer("example.com", error_class="human_verification", official=OFFICIAL)
    assert guide(copy.deepcopy(handed), status_check=True) == guide(handed)


# --- limits ------------------------------------------------------------------------------------------------------------

def test_every_sentence_and_every_combined_line_fits_the_client_limit():
    after = list(response_guide.STATUS_AFTER_ROWS.values()) + list(response_guide.STATUS_AFTER_ROWS_BEFORE.values())
    for text in list(STATUS_ROWS.values()) + after:
        assert 1 <= len(text) <= LINE_MAX and PRINTABLE.fullmatch(text), text
    for lead in STATUS_ROWS.values():
        for tail in [text for _, text in ROWS.values()] + after:
            assert len(lead) + 1 + len(tail) <= STATUS_LINE_MAX <= 401


# --- nothing but the status_check sentence moves ---------------------------------------------------------------------

HOSTS = [REAL, "example.com", "docs.example.org", "shop.test"]
PAGES = [None, page(), page(OTHER_PAGE), API_PAGE, {**page(PAGE_URL), "covers": "api"}, signin_page(OTHER_PAGE)]


def cases():
    for case in itertools.islice(main_space(), 0, None, 97):
        kw = {k: v for k, v in case.items() if k not in ("site_host", "official_page")}
        for host, pg in itertools.product(HOSTS, PAGES):
            yield answer(host, official_page=pg, **kw)


def test_status_check_changes_only_the_summary_line_and_adds_status_url():
    seen = 0
    for result in cases():
        for on, blocked_default in itertools.product((True, False), (False, True)):
            plain = guide(copy.deepcopy(result), on, blocked_default)
            out = guide(copy.deepcopy(result), on, blocked_default, True, True)
            rest = {k: v for k, v in out["next_action"].items() if k not in ("status_url", "url")}
            base = {k: v for k, v in plain["next_action"].items() if k not in ("status_url", "url")}
            assert rest == base and out.get("links") == plain.get("links")
            assert len(out["summary_line"]) <= STATUS_LINE_MAX
            # without task_step the line is the plain one whatever the host or page
            assert guide(copy.deepcopy(result), on, blocked_default, False, True)["summary_line"] == plain["summary_line"]
            seen += 1
    assert seen > 1000
