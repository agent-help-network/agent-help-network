"""Extra inputs for comparing the /v1/why judgment fields across two commits (not a test module: no test_ prefix, nothing here is collected).

``extra_answers()`` yields answer bodies for inputs the other every-combination helpers do not have: a subdomain input (host_normalized
"apex"), a lookup answer with a customer page (official_paths.consumer), and a lookup answer whose provider status read the indicator but not
the incident list (the status row T1-U condition). It uses only the helpers that exist before D113 (parse_inputs, build with its six arguments,
fake_check), so it imports and runs on the earlier commit as well. No database; the lookup answers are made by hand."""

from __future__ import annotations

import itertools
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _why_fixtures import TODAY, fake_check, view  # noqa: E402
from test_why_d110 import ACTION_OF, STATUS  # noqa: E402

from ahn import explain as E  # noqa: E402

CUSTOMER = "https://help.fixture-host.test/contact"
HOSTS = ("login-fixture-host.com", "status.login-fixture-host.com", "app.login-fixture-host.com")
UNREAD_STATUS = dict(STATUS, indicator="none", active_incidents=None, freshness="fresh")
DOS = ("use_official_path", "wait_for_provider")


def _checks(do: str) -> dict:
    official = {"api_docs": "https://api.fixture-host.test/docs"}
    plain = fake_check(ACTION_OF[do], do, provider_status=STATUS, official_paths=official)
    customer = fake_check(ACTION_OF[do], do, provider_status=STATUS, official_paths={**official, "consumer": {"paths": [{"kind": "customer_help", "url": CUSTOMER}]}},
                          next_action={"do": do, "customer_url": CUSTOMER})
    unread = fake_check(ACTION_OF[do], do, provider_status=UNREAD_STATUS, official_paths=official,
                        next_checks=[{"code": "provider_clear", "text": "The provider's status page reports no active issue."}])
    return {"plain": plain, "customer": customer, "unread": unread}


def extra_answers():
    stops = list(E.STOP_POINTS)[::2] + [None]
    for host, do, kind, stop, context, lang in itertools.product(HOSTS, DOS, ("plain", "customer", "unread"), stops,
                                                                ["public_read", "my_account_consumer", None], ["en", "ko"]):
        check = _checks(do)[kind]
        inputs = E.parse_inputs(host, today=TODAY, stop_point=stop, task_context=context, lang=lang, error_class="site_access")
        yield E.build(inputs, check, view(lang), [], [], TODAY)
