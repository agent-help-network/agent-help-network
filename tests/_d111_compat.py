"""D111: what an answer from before D111 looks like once the two D111 changes that reach older grids are applied to it.

The older decision-order tests (D85 .. D100) keep a frozen copy of the guide from before their own change and compare the live guide with it
across a grid. D111 changed two things inside those grids, neither of which is a judgment: the hand-off first line no longer offers another
try, and next_action.status_url is left out when the status page is the host that failed. These helpers carry an old answer to the D111 shape
so the old tests keep proving what they were written to prove (that nothing else moved). The D111 behaviour itself is pinned in
test_d111_handoff_status_age.py and test_d85_edge_refusal.py."""

from __future__ import annotations

import copy

from ahn import response_guide

# old wording -> D111 wording, for the hand-off first lines that existed before D111 (H3, H3-R, their customer-page and stale forms)
SENTENCES = (
    ("Do not switch tools or user agents to retry; tell your user, who can", "Do not switch tools or user agents; tell your user now, who can"),
    ("Do not switch tools or user agents to retry; hand your user", "Do not switch tools or user agents; hand your user"),
    ("Do not switch tools to retry; hand your user", "Do not switch tools; hand your user"),
)


def line(text: str) -> str:
    for old, new in SENTENCES:
        text = text.replace(old, new)
    return text


def answer(out: dict, site_host: str, error_class, failure_stage, status_url, status_on: bool = True) -> dict:
    """The answer a pre-D111 guide gave, as the D111 guide gives it: the hand-off wording, and no status_url that points at the host that failed.
    ``status_url`` is the status page address the guide worked from (response_guide.status_url_of(result))."""
    out = copy.deepcopy(out)
    out["summary_line"] = line(out["summary_line"])
    if status_on and response_guide.status_self_reference(status_url, site_host, error_class, failure_stage):
        out["next_action"].pop("status_url", None)
        out["next_action"].pop("status_url_covers", None)
    return out
