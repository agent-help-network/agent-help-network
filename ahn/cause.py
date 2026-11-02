"""Why did the step fail: a policy block, a temporary problem, or not yet known (D73).

A lookup that reports a failure (``error_class`` or ``failure_stage``) may get an optional ``cause`` object. It is
computed after the recommendation is final, from tables AHN already keeps for public hosts, and it changes none of the
answer: ``recommendation`` and ``next_action.do`` are what they would be without it (AHN_CAUSE_HINT=0 leaves ``cause`` out).

Inputs (read only, one savepoint, no network): ``provider_status_events``, ``outage_episodes`` (signal A), ``site_probe``,
``site_probe_daily``, ``lookup_signals_hourly`` and ``host_access_changes`` (D75), plus the optional ``failed_at`` and the
accepted ``agent_vendor``. Nothing is stored. The caller's own codes (``http_status``, ``retry_after``, ``challenge``,
``via``) are never read here (D47): counts of other lookups are used only when at least OTHERS_MIN_SOURCES separate
clients contributed to them. ``line`` is a fixed English template filled with times only: no text from a site and
no hostname ever reaches it.

Rules that hold for every hint:

* A refusal AHN measured (bot challenge, 403, login wall, rate limit on the home page) is never evidence of a temporary
  problem (D68). It is counted on the policy side, separately, and only as ``consecutive_refusals`` >= 2.
* One refusal is never a policy change: it gives ``undetermined``; the second measurement in a row gives ``policy_closed``.
* Hints from the history of access-profile changes (D75) look at changes that were recorded from CHANGE_WINDOW before the
  failure time to CHANGE_LEAD after it, and only while the changed field still has that value now. ``robots_now_disallows``
  (high) is the caller's vendor tokens, or AHN's own token when no vendor is known, going from allow to block, once.
  ``new_bot_defense`` (medium) names a home-page block that two refusals already confirm, when its bot-defense provider
  was not in the daily counts of the 14 days before. ``terms_signal_changed`` (medium, undetermined) is information only:
  llms.txt removed, or a data-mining reservation added; it takes the place of "nothing measured" and never of another
  hint, and its line says it is not an access block. A service error from the caller never gets one of these three.

Time handling: ``failed_at`` is an ISO 8601 time. Without it the failure is taken to be now. It is not stored or counted.
Raw measurements are kept 14 days, provider status changes 90 days, so a time older than 14 days is answered from the
provider's status history alone. A value that cannot be used (bad format, more than five minutes in the future) gives no
``cause`` at all, never a cause for a different time.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from .signal_history import PROVIDER_PROBLEMS, STALE_AFTER

LOG = logging.getLogger(__name__)

# Hints from host_access_changes (D75).
CHANGE_WINDOW = timedelta(days=14)     # a recorded change this long before the failure time still counts
CHANGE_LEAD = timedelta(hours=24)      # a change recorded up to this long after it counts too: a re-read finds a change late
BOT_DEFENSE_LOOKBACK = timedelta(days=14)   # a provider seen in the home-page counts of these days before is not "new"

LINE_MAX = 120                         # a cause line, so an agent can put it into a briefing as it is
RAW_DAYS = 14                          # tables with raw measurements are pruned after this long
FUTURE_SKEW = timedelta(minutes=5)     # a failed_at this far ahead is a clock difference, not a prediction
PROVIDER_MAX_AGE = timedelta(days=3)   # a status-page problem older than this at the failure time is not "temporary"
MEASURE_WINDOW = timedelta(hours=12)   # how close a home-page check must be to the failure time to describe it
PROBE_FRESH = timedelta(days=8)        # a refusal is re-measured after 7 days (site_probe.REFUSED_EXCLUDE_DAYS)
OTHERS_MIN_SOURCES = 3                 # other clients seen with service errors in the failure hour: "several"
OTHERS_MEDIUM_SOURCES = 10
PERSISTENT_DAYS = 3                    # separate UTC days of refused lookups
PERSISTENT_MIN_SOURCES = OTHERS_MIN_SOURCES   # distinct clients in one hour of such a day (one client looping cannot make a day)

GATE_OUTCOMES = frozenset({"bot_challenge", "refused", "login_wall"})   # rate_limited is neither gate nor outage
NORMAL_OUTCOMES = frozenset({"reachable", "redirected_same_host"})
OUTAGE_KINDS = frozenset({"no_response", "tls_error", "server_error"})
REFUSAL_CLASSES = ("login_auth", "human_verification")

BRANCHES = ("policy_closed", "transient", "undetermined")
HINTS = {
    "policy_closed": ("home_now_gated", "persistent_refusal", "robots_now_disallows", "new_bot_defense"),
    "transient": ("provider_incident", "site_errors", "recovered", "rate_limited", "others_failing_same_hour"),
    "undetermined": ("your_side_likely", "no_measurement", "home_gate_unconfirmed", "terms_signal_changed"),
}
CONFIDENCES = ("high", "medium", "low")
BASIS = ("provider_status_events", "outage_episodes", "site_probe", "site_probe_daily", "lookup_signals_hourly",
         "host_access_changes")
INFO_ONLY_HINTS = frozenset({"terms_signal_changed"})   # information about terms, not a cause of the failure

# Fixed sentences. "{win}" is a time window or empty. Every line is printable ASCII and at most LINE_MAX characters.
LINES: dict[str, str] = {
    "provider_incident": "Temporary: provider status page shows a problem {win}. Retry later.",
    "provider_maintenance": "Temporary: provider maintenance {win}. Retry after it ends.",
    "recovered_provider": "Temporary: provider problem {win} has cleared. Retry.",
    "site_errors": "Temporary: AHN could not reach the site {win}. Retry later.",
    "recovered_site": "Temporary: AHN could not reach the site {win}; it answers again. Retry.",
    "rate_limited": "Probably temporary: several clients were rate limited this hour. Wait, then retry.",
    "others_failing_same_hour": "Probably temporary: other clients saw service errors this hour. Retry later.",
    "home_now_gated": "Likely a policy block: the site refused AHN's last two home-page checks.",
    "persistent_refusal": "Likely a policy block: clients were refused on 3 or more separate days.",
    "robots_now_disallows": "Likely a policy block: the site's robots.txt now disallows this kind of agent {win}.",
    "terms_signal_changed": "Terms signal changed {win}: llms.txt removed or data-mining reserved. Not an access block.",
    "new_bot_defense": "Likely a policy block: the home page now shows a bot-defense page from a new provider {win}.",
    "your_side_likely": "Not determined: AHN's check was normal then; the cause may be on your side. AHN reads no page content.",
    "no_measurement": "Not determined: AHN has no measurement of this site around that time.",
    "home_gate_unconfirmed": "Not determined: the site refused AHN's last home-page check once; one more would confirm a block.",
    "too_old": "Not determined: that time is past the 14 days of measurements kept; only provider status history was read.",
}

_TIME = re.compile(r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(:\d{2}(\.\d{1,6})?)?(Z|[+-]\d{2}:\d{2})?$")


@dataclass(frozen=True)
class When:
    """The failure time the cause is judged at. ``sent`` is False when the caller gave none (then it is now)."""
    at: datetime
    sent: bool
    old: bool


def parse_failed_at(raw, now: datetime) -> When | None:
    """The time to judge at, or None when a value was sent and cannot be used (no cause is then given).

    Not sent (None, empty or blank): now. A time without an offset is read as UTC. More than FUTURE_SKEW ahead of ``now``
    is unusable; within it, it is taken as now. Older than RAW_DAYS is accepted and marked ``old``."""
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return When(now, False, False)
    if not isinstance(raw, str) or len(raw) > 40 or not _TIME.fullmatch(raw.strip()):
        return None
    text = raw.strip()
    try:
        at = datetime.fromisoformat(text[:-1] + "+00:00" if text.endswith("Z") else text)
    except ValueError:
        return None
    at = at.replace(tzinfo=timezone.utc) if at.tzinfo is None else at.astimezone(timezone.utc)
    if at > now + FUTURE_SKEW:
        return None
    at = min(at, now)
    return When(at, True, at < now - timedelta(days=RAW_DAYS))


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _stamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%m-%d %H:%M")


def _window_text(start: datetime, end: datetime | None) -> str:
    start = start.astimezone(timezone.utc)
    if end is None:
        return f"(since {_stamp(start)} UTC)"
    end = end.astimezone(timezone.utc)
    if end.date() == start.date():
        return f"({_stamp(start)}-{end:%H:%M} UTC)"
    return f"({_stamp(start)} to {_stamp(end)} UTC)"


def line_for(key: str, start: datetime | None = None, end: datetime | None = None) -> str:
    text = LINES[key]
    if "{win}" in text:
        text = text.replace("{win}", _window_text(start, end)) if start is not None else text.replace(" {win}", "")
        if len(text) > LINE_MAX:
            text = LINES[key].replace(" {win}", "")
    return text


def _cause(branch: str, hint: str, confidence: str, basis: list[str], line_key: str, *, start: datetime | None = None,
           end: datetime | None = None, recheck: datetime | None = None) -> dict:
    out = {"branch": branch, "hint": hint, "confidence": confidence, "basis": basis}
    if start is not None:
        out["window"] = {"from": _iso(start), **({"to": _iso(end)} if end is not None else {})}
    out["line"] = line_for(line_key, start, end)
    if recheck is not None:
        out["recheck_after"] = _iso(recheck)
    return out


def _floor_hour(value: datetime) -> datetime:
    return value.replace(minute=0, second=0, microsecond=0)


def _rate_limited(conn, host: str, when: When) -> dict | None:
    """Several distinct clients were rate limited (429) on this host in the failure hour."""
    if when.old:
        return None
    row = conn.execute(
        "SELECT COALESCE(sum(sources),0) AS n FROM lookup_signals_hourly WHERE site_host=%s AND hour=%s AND http_status_class='429'",
        (host, _floor_hour(when.at))).fetchone()
    if row["n"] < OTHERS_MIN_SOURCES:
        return None
    return _cause("transient", "rate_limited", "medium", ["lookup_signals_hourly"], "rate_limited")


def _provider(conn, host: str, when: When, now: datetime, refusal_like: bool) -> dict | None:
    """A problem on the provider's status page at the failure time; ``recovered`` once it has cleared."""
    last = conn.execute(
        "SELECT at,to_indicator FROM provider_status_events WHERE site_host=%s AND at <= %s ORDER BY at DESC, id DESC LIMIT 1",
        (host, when.at)).fetchone()
    if last is None or last["to_indicator"] not in PROVIDER_PROBLEMS or when.at - last["at"] > PROVIDER_MAX_AGE:
        return None
    following = conn.execute(
        "SELECT at FROM provider_status_events WHERE site_host=%s AND at > %s ORDER BY at, id LIMIT 1", (host, when.at)).fetchone()
    end = following["at"] if following and following["at"] <= now else None
    severe = last["to_indicator"] in ("major", "critical")
    # A login or challenge page is not what a status-page problem usually looks like: one step less certain.
    confidence = "high" if severe and not refusal_like else "medium"
    if end is not None:
        return _cause("transient", "recovered", confidence, ["provider_status_events"], "recovered_provider", start=last["at"], end=end)
    return _cause("transient", "provider_incident", confidence, ["provider_status_events"],
                  "provider_maintenance" if last["to_indicator"] == "maintenance" else "provider_incident", start=last["at"])


def _site_errors(conn, host: str, when: When, now: datetime) -> dict | None:
    """AHN's own reads could not reach the host (not a refusal: D68) over a period that covers the failure time."""
    row = conn.execute(
        """SELECT kind,first_fail_at,confirmed_at,ended_at,ended_reason,last_event_at,held_overlap FROM outage_episodes
           WHERE site_host=%s AND signal='A' AND first_fail_at <= %s AND (ended_at IS NULL OR ended_at > %s)
           ORDER BY first_fail_at DESC LIMIT 1""",
        (host, when.at, when.at)).fetchone()
    if row is None or row["kind"] not in OUTAGE_KINDS or row["held_overlap"]:
        return None  # a period that overlapped many hosts failing at once is held back as a problem on AHN's side
    if row["ended_at"] is None and row["last_event_at"] < now - STALE_AFTER:
        return None
    confidence = "high" if row["confirmed_at"] is not None else "medium"
    ended = row["ended_at"] if row["ended_at"] is not None and row["ended_at"] <= now else None
    if ended is not None and row["ended_reason"] == "answered":
        return _cause("transient", "recovered", confidence, ["outage_episodes"], "recovered_site", start=row["first_fail_at"], end=ended)
    return _cause("transient", "site_errors", confidence, ["outage_episodes"], "site_errors", start=row["first_fail_at"], end=ended)


def _probe(conn, host: str):
    return conn.execute(
        "SELECT outcome,probed_at,next_due_at,consecutive_refusals FROM site_probe WHERE site_host=%s", (host,)).fetchone()


def _gated(probe, now: datetime, error_class: str | None) -> dict | None:
    """Two refusals in a row of AHN's own home-page check. One refusal alone is undetermined."""
    if probe is None or probe["outcome"] not in GATE_OUTCOMES or error_class == "service_error" or probe["probed_at"] < now - PROBE_FRESH:
        return None
    if probe["consecutive_refusals"] >= 2:
        return _cause("policy_closed", "home_now_gated", "medium", ["site_probe"], "home_now_gated")
    recheck = probe["next_due_at"] if probe["next_due_at"] > now else None
    return _cause("undetermined", "home_gate_unconfirmed", "low", ["site_probe"], "home_gate_unconfirmed", recheck=recheck)


def _persistent(conn, host: str, when: When, error_class: str | None, failure_stage: str | None) -> dict | None:
    """Lookups refused on PERSISTENT_DAYS separate UTC days, each day with several distinct clients in one hour."""
    if not (error_class in ("site_access", "human_verification") or failure_stage == "access_denial_observed"):
        return None
    first = _floor_hour(when.at).replace(hour=0) - timedelta(days=6)
    rows = conn.execute(
        """SELECT day, max(hs) AS most FROM (
               SELECT (hour AT TIME ZONE 'UTC')::date AS day, hour, sum(sources) AS hs FROM lookup_signals_hourly
               WHERE site_host=%s AND hour >= %s AND hour <= %s
                 AND (http_status_class IN ('403','451','402')
                      OR (http_status_class NOT IN ('429','401','407','5xx','408')
                          AND (challenge='header' OR error_class IN ('site_access','human_verification'))))
               GROUP BY 1, 2) hourly GROUP BY day""",
        (host, first, when.at)).fetchall()
    if sum(1 for row in rows if row["most"] >= PERSISTENT_MIN_SOURCES) >= PERSISTENT_DAYS:
        return _cause("policy_closed", "persistent_refusal", "medium", ["lookup_signals_hourly"], "persistent_refusal")
    return None


def _others(conn, host: str, when: When, error_class: str | None, failure_stage: str | None) -> dict | None:
    """Several distinct clients with a service error for this host in the failure hour."""
    if error_class not in (None, "service_error") or failure_stage == "access_denial_observed":
        return None
    row = conn.execute(
        """SELECT COALESCE(sum(sources),0) AS n FROM lookup_signals_hourly WHERE site_host=%s AND hour=%s
             AND http_status_class NOT IN ('429','401','403','407','451','402')
             AND (error_class='service_error' OR http_status_class IN ('5xx','408'))""",
        (host, _floor_hour(when.at))).fetchone()
    if row["n"] < OTHERS_MIN_SOURCES:
        return None
    return _cause("transient", "others_failing_same_hour", "medium" if row["n"] >= OTHERS_MEDIUM_SOURCES else "low",
                  ["lookup_signals_hourly"], "others_failing_same_hour")


def _history(conn, host: str, when: When, now: datetime):
    """(changes recorded around the failure time, the latest recorded code of each field), or None when there is no change
    in that window or the table cannot be read (a missing table leaves out only these hints, not the rest of the cause)."""
    try:
        with conn.transaction():
            window = conn.execute(
                "SELECT field,from_code,to_code,at FROM host_access_changes WHERE site_host=%s AND at >= %s AND at <= %s ORDER BY at, id",
                (host, when.at - CHANGE_WINDOW, min(when.at + CHANGE_LEAD, now))).fetchall()
            if not window:
                return None
            latest = conn.execute(
                "SELECT DISTINCT ON (field) field,to_code FROM host_access_changes WHERE site_host=%s AND at <= %s ORDER BY field, at DESC, id DESC",
                (host, now)).fetchall()
    except Exception:
        LOG.warning("cause_history_unavailable")
        return None
    return window, {row["field"]: row["to_code"] for row in latest}


def _robots_now_disallows(history, agent_vendor: str | None) -> dict | None:
    """The caller's vendor tokens (D48) went from allow to block in a re-read, and are still blocked. Without a known
    vendor, AHN's own token stands in; a caller that names its vendor is never told about a token that does not govern it."""
    from .access_profile import GOVERNING_TOKENS
    from .host_access_changes import OUR_TOKEN, VERDICT_PREFIX

    window, latest = history
    tokens = GOVERNING_TOKENS.get(agent_vendor, ()) or (OUR_TOKEN,)
    fields = {VERDICT_PREFIX + token for token in tokens}
    hits = [row["at"] for row in window
            if row["field"] in fields and (row["from_code"], row["to_code"]) == ("allow", "block") and latest.get(row["field"]) == "block"]
    if not hits:
        return None
    return _cause("policy_closed", "robots_now_disallows", "high", ["host_access_changes"], "robots_now_disallows", start=min(hits))


def _terms_signal_changed(history) -> dict | None:
    """llms.txt removed, or a data-mining reservation added (root or any rule), and still so. Information, not a block."""
    window, latest = history
    hits = [row["at"] for row in window
            if (row["field"] == "llms_presence" and (row["from_code"], row["to_code"]) == ("present", "absent")
                and latest.get("llms_presence") == "absent")
            or (row["field"] in ("tdm_root", "site_declared_reservation") and row["to_code"] == "reserved"
                and latest.get(row["field"]) == "reserved")]
    if not hits:
        return None
    return _cause("undetermined", "terms_signal_changed", "medium", ["host_access_changes"], "terms_signal_changed", start=min(hits))


def _new_bot_defense(conn, host: str, history) -> dict | None:
    """A home-page bot-defense provider that appeared and stays, and that the daily counts did not show in the 14 days before."""
    window, latest = history
    for row in window:
        if row["field"] != "bot_vendor" or row["to_code"] == "none" or latest.get("bot_vendor") != row["to_code"]:
            continue
        day = row["at"].astimezone(timezone.utc).date()
        seen = conn.execute(
            "SELECT 1 FROM site_probe_daily WHERE site_host=%s AND bot_vendor=%s AND day >= %s AND day < %s LIMIT 1",
            (host, row["to_code"], day - BOT_DEFENSE_LOOKBACK, day)).fetchone()
        if not seen:
            return _cause("policy_closed", "new_bot_defense", "medium", ["host_access_changes", "site_probe_daily"],
                          "new_bot_defense", start=row["at"])
    return None


def _measurement(conn, host: str, probe, when: When) -> tuple[bool, bool]:
    """(any home-page check around the failure time, and all of them normal). AHN reads no page content."""
    if probe is not None and probe["outcome"] != "not_measured" and abs(probe["probed_at"] - when.at) <= MEASURE_WINDOW:
        return True, probe["outcome"] in NORMAL_OUTCOMES
    rows = conn.execute(
        "SELECT outcome FROM site_probe_daily WHERE site_host=%s AND day=%s AND outcome<>'not_measured' AND probes>0",
        (host, when.at.astimezone(timezone.utc).date())).fetchall()
    if not rows:
        return False, False
    return True, all(row["outcome"] in NORMAL_OUTCOMES for row in rows)


def compute(conn, host: str, now: datetime, when: When, *, error_class: str | None = None,
            failure_stage: str | None = None, agent_vendor: str | None = None) -> dict | None:
    """The ``cause`` object for a failed step at ``when``, or None when nothing can be said. Reads only; the caller
    runs it in a savepoint and leaves ``cause`` out on any error.

    Order: several clients rate limited in the failure hour, then the provider's status page, then AHN's own reads of the
    host, then the policy signals (robots.txt now disallows; two refusals of the home page, named as a new bot defense when
    the provider is new; refusals over several days), then other clients' lookups, and last the cases that stay
    undetermined, where the terms signal (information only) takes the place of "nothing measured"."""
    refusal_like = error_class in REFUSAL_CLASSES or failure_stage == "access_denial_observed"
    probe = _probe(conn, host)
    # other clients' 429s say nothing temporary about a host whose home page AHN saw refuse (D68)
    refused = probe is not None and probe["outcome"] in GATE_OUTCOMES and probe["probed_at"] >= now - PROBE_FRESH
    found = None if refused else _rate_limited(conn, host, when)
    if found:
        return found
    found = _provider(conn, host, when, now, refusal_like)
    if found:
        return found
    if when.old:
        return _cause("undetermined", "no_measurement", "low", ["provider_status_events"], "too_old")
    if not refusal_like:
        found = _site_errors(conn, host, when, now)
        if found:
            return found
    history = _history(conn, host, when, now) if error_class != "service_error" else None
    if history:
        found = _robots_now_disallows(history, agent_vendor)
        if found:
            return found
    gate = _gated(probe, now, error_class)
    if gate and gate["branch"] == "policy_closed":
        # a new bot-defense provider names the block more exactly, but only once two refusals confirm it (D68)
        return (_new_bot_defense(conn, host, history) if history else None) or gate
    for check in (lambda: _persistent(conn, host, when, error_class, failure_stage),
                  lambda: _others(conn, host, when, error_class, failure_stage)):
        found = check()
        if found:
            return found
    if gate:
        return gate
    recheck = probe["next_due_at"] if probe is not None and probe["next_due_at"] > now else None
    measured, normal = _measurement(conn, host, probe, when)
    # the terms signal is information, never a block: it only fills the place of "nothing measured" or of nothing at all
    terms = _terms_signal_changed(history) if history else None
    if not measured:
        return terms or _cause("undetermined", "no_measurement", "low", ["site_probe"] if probe is not None else [], "no_measurement",
                               recheck=recheck)
    if normal and (failure_stage == "before_target_response" or refusal_like):
        return _cause("undetermined", "your_side_likely", "low", ["site_probe"], "your_side_likely", recheck=recheck)
    return terms
