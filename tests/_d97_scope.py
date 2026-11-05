"""D97 scope for the older comparison tests (D87, D92, D93, D95, D96).

Those tests compare the guide with a frozen copy from before their own change. D97 later changed the summary_line of U6, U7 and U9
(and added next_action.customer_url there when a customer path is curated). This
helper turns the answer of the older guide into the answer D97 must give, written from the D97 contract (the sentences are spelled out
here, not imported), so those tests keep asserting that nothing else differs. It changes nothing when the D87 switch is off, the row is
not U6/U7/U9, a status answer applies, or the failure is not a service_error (a failure of another kind is not changed by D97)."""

from __future__ import annotations

import copy

from ahn import response_guide

_CAUSE = {"U6": "the site refused AHN's plain robots.txt read too", "U7": "AHN could not read the site's robots.txt either",
          "U9": "AHN has no declared policy for this host"}
_CAUSE_5XX = {"U6": "; the site also refused AHN's plain robots.txt read", "U7": ", and could not read the site's robots.txt",
              "U9": ", and has no declared policy for this host"}
_CUSTOMER_TAIL = {"U6": "The site refused even a plain automated robots.txt read. Page automation may be refused; hand off if needed. "
                        "A customer page is listed (next_action.customer_url) for tasks a person does on the site.",
                  "U7": "The site's robots.txt could not be read, so no declared policy is known. A customer page is listed "
                        "(next_action.customer_url) for tasks a person does on the site. Follow your operator's rules.",
                  "U9": "No declared policy could be read for this host. A customer page is listed (next_action.customer_url) for tasks a "
                        "person does on the site. Follow your operator's rules and the site's terms."}


def customer_of(official) -> str | None:
    raw = response_guide.customer_url(official)
    return response_guide.safe_url(raw) if raw else None


def no_path_sentence(row, error_class, stage, customer):
    """The D97 sentence of a U6/U7/U9 row, or None where D97 leaves the row alone."""
    signal = error_class is not None or stage is not None
    if signal and error_class != "service_error":
        return None
    if error_class == "service_error" and stage == "before_target_response":
        head = f"No response came (timeout or connection), and {_CAUSE[row]}. "
        return head + ("Check your tool; retry once later, then hand your user next_action.customer_url." if customer
                       else "Check your tool and path; retry once later; then tell your user.")
    if error_class == "service_error":
        head = f"AHN cannot judge a single page's 5xx{_CAUSE_5XX[row]}. "
        return head + ("Retry once later; if it persists, tell your user and hand them next_action.customer_url." if customer
                       else "Retry once later; if it persists, tell your user.")
    return _CUSTOMER_TAIL[row] if customer else None


def adjust(old_out, row, on, status_check, error_class, stage, kind, official):
    """The answer of the older guide, as D97 changes it (a deep copy), or ``old_out`` itself when D97 does not apply."""
    if not (kind and on and not status_check and row in ("U6", "U7", "U9")):
        return old_out
    customer = customer_of(official)
    sentence = no_path_sentence(row, error_class, stage, customer)
    if sentence is None:
        return old_out
    out = copy.deepcopy(old_out)
    out["summary_line"] = sentence
    if customer:
        out["next_action"]["customer_url"] = customer
    return out

