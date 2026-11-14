"""D116 scope for the older comparison tests that hold a frozen copy of ``response_guide`` (D92, D93, D95, D96, D97, D101, D102).

D116 changed two things in the summary_line that those frozen copies cannot know: (1) a 5xx sentence says "timeout or 5xx", and
(2) a reported refusal (error_class=site_access or failure_stage=access_denial_observed) on a blocked-mode proceed row, with the D87 switch on,
gets the resolved refusal sentence. ``install`` wraps ``guide`` of the frozen copy so its answer carries exactly those two changes and nothing
else; the comparison with the current guide then still proves that no other byte differs. The wording changes are spelled out here."""

from __future__ import annotations

import copy
import inspect

from ahn import response_guide

_WORDING = (("Either way, AHN cannot judge this 5xx.", "AHN cannot judge this timeout or 5xx."),
            ("cannot judge this 5xx.", "cannot judge this timeout or 5xx."),
            ("AHN cannot judge a single page's 5xx. Retry once later; if it persists, tell your user and hand them next_action.customer_url.",
             "AHN cannot judge a page's timeout or 5xx. Retry once later; if it lasts, tell your user; hand them next_action.customer_url."),
            ("single page's 5xx", "single page's timeout or 5xx"))


def _refused(old_line, kind, error_class, stage):
    if not (kind and (error_class == "site_access" or stage == "access_denial_observed")):
        return None
    for plain, refused in ((response_guide.BLOCKED_PROCEED_ROWS, response_guide.REFUSED_PROCEED_ROWS),
                           (response_guide.BLOCKED_PROCEED_CONSUMER_ROWS, response_guide.REFUSED_PROCEED_CONSUMER_ROWS)):
        for row, sentence in plain.items():
            if row in refused and old_line == sentence:
                return refused[row]
    return None


def carry(old_out, kind, error_class, stage):
    line = old_out.get("summary_line")
    if not isinstance(line, str):
        return old_out
    new = _refused(line, kind, error_class, stage)
    if new is None:
        new = line
        for before, after in _WORDING:
            new = new.replace(before, after)
    if new == line:
        return old_out
    out = copy.deepcopy(old_out)
    out["summary_line"] = new
    return out


def install(pre_rg):
    original = pre_rg.guide
    signature = inspect.signature(original)

    def guide(*args, **kwargs):
        out = original(*args, **kwargs)
        bound = signature.bind_partial(*args, **kwargs).arguments
        return carry(out, bool(bound.get("failure_kind_lines", False)), bound.get("error_class"), bound.get("failure_stage"))

    pre_rg.guide = guide
