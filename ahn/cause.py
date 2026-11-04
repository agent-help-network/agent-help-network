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
  A login wall does not raise ``consecutive_refusals``, so its second measurement in a row is read from ``site_probe_daily``
  instead (``_login_wall_twice``, D84): the last two home-page checks stored were both a login wall. It applies only to a
  lookup that names a failure other than ``login_auth``: for a step that failed on a sign-in the site is expected to ask for
  one, so the answer stays as it was (the client reads ``home_now_gated`` as "stop visiting", not as "ask the user to log in").
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

Wording (D78): the ``home_gate_unconfirmed`` and ``no_measurement`` lines name AHN's own check and are never an instruction to
the caller; a host AHN does not measure because of its robots.txt (and the row is recent) gets its own no_measurement
sentence and no ``recheck_after``; the general no_measurement sentence speaks of a usable check near that time, without a
count of hours; ``note`` says the object is information only. None of this reads or changes ``branch``, ``hint``,
``confidence``, ``basis``, ``recommendation`` or ``next_action``.

Wording (D82): the ``site_errors`` and ``recovered_site`` lines say that AHN's OWN checks did not reach the site and that this
can be AHN's network path alone. Their ``confidence`` is lowered from high to medium when nothing but AHN's own measurement
(``outage_episodes``) supports them: no published, non-synthetic observation with outcome ``error`` for the host and no
OTHERS_MIN_SOURCES separate clients with service errors in ``lookup_signals_hourly`` within one and the same hour, both within the
period of the measurement (the table counts a client again in every hour, so hours are never added up).
The ``home_gate_unconfirmed`` lines name the age of the check (the ``age_seconds`` of ``ahn_check``, told in the same words as the MCP first line: minutes, hours under 24 hours, then days) and, when the stored check has one, its bot-defense provider. ``branch``, ``hint``, ``basis`` and everything outside
``cause`` stay as they were.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from .check_text import VENDOR_NAMES, age_text as _age_text, unfollowed_www_redirect
from .signal_history import PROVIDER_PROBLEMS, STALE_AFTER

LOG = logging.getLogger(__name__)

# Hints from host_access_changes (D75).
CHANGE_WINDOW = timedelta(days=14)     # a recorded change this long before the failure time still counts
CHANGE_LEAD = timedelta(hours=24)      # a change recorded up to this long after it counts too: a re-read finds a change late
BOT_DEFENSE_LOOKBACK = timedelta(days=14)   # a provider seen in the home-page counts of these days before is not "new"

LINE_MAX = 200                         # a cause line, so an agent can put it into a briefing as it is
RAW_DAYS = 14                          # tables with raw measurements are pruned after this long
FUTURE_SKEW = timedelta(minutes=5)     # a failed_at this far ahead is a clock difference, not a prediction
PROVIDER_MAX_AGE = timedelta(days=3)   # a status-page problem older than this at the failure time is not "temporary"
MEASURE_WINDOW = timedelta(hours=12)   # how close a home-page check must be to the failure time to describe it
ROBOTS_LINE_FRESH = timedelta(hours=24)   # a robots.txt reason names the present only while the stored row is this recent
NOTE = "Information only; follow next_action."   # on every cause, as ahn_check carries its own note
PROBE_FRESH = timedelta(days=8)        # a first refusal is re-measured after 24 hours, later ones every 7 days (site_probe)
OTHERS_MIN_SOURCES = 3                 # other clients seen with service errors in the failure hour: "several"
OTHERS_MEDIUM_SOURCES = 10
PERSISTENT_DAYS = 3                    # separate UTC days of refused lookups
PERSISTENT_MIN_SOURCES = OTHERS_MIN_SOURCES   # distinct clients in one hour of such a day (one client looping cannot make a day)

D84_ERROR_CLASSES = ("site_access", "human_verification")   # the failures to which two login walls in a row apply (D84)
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
    "site_errors": "AHN's own checks have not reached the site {win}; that can be AHN's network path alone, so it does not tell whether your tool can reach the site.",
    "recovered_site": "AHN's own checks did not reach the site {win}; they answer again. That can be AHN's network path alone.",
    "rate_limited": "Probably temporary: several clients were rate limited this hour. Wait, then retry.",
    "others_failing_same_hour": "Probably temporary: other clients saw service errors this hour. Retry later.",
    "home_now_gated": "Likely a policy block: the site refused AHN's last two home-page checks.",
    "home_now_gated_login_wall": "Likely a sign-in requirement: the site's home page showed a sign-in wall on AHN's last two checks.",
    "home_now_gated_latest_login_wall": "Likely a policy block: the site refused AHN's home-page checks at least twice in a row; the latest showed a sign-in wall.",
    "persistent_refusal": "Likely a policy block: clients were refused on 3 or more separate days.",
    "robots_now_disallows": "Likely a policy block: the site's robots.txt now disallows this kind of agent {win}.",
    "terms_signal_changed": "Terms signal changed {win}: llms.txt removed or data-mining reserved. Not an access block.",
    "new_bot_defense": "Likely a policy block: the home page now shows a bot-defense page from a new provider {win}.",
    "your_side_likely": "Not determined: AHN's home-page checks that day were normal, but they do not see single pages; the cause may be on your side.",
    "no_measurement": "Not determined: AHN has no usable home-page check of this site near that time.",
    "no_measurement_robots_disallowed": "Not determined: AHN does not measure this site because its robots.txt disallows AHN's check.",
    "no_measurement_robots_refused": "Not determined: AHN does not measure this site because the site refused AHN's request for robots.txt.",
    "too_old": "Not determined: that time is past the 14 days of measurements kept; only provider status history was read.",
}

# The last refusal of the home page, named by what AHN saw (no count). The
# line says whose check it was and that the next check is AHN's, not an instruction to the caller. Without a stored time to
# come back at, "recheck_after" is left out.
HOME_GATE_KINDS = {"bot_challenge": "bot-challenge", "refused": "HTTP", "login_wall": "login-wall"}
# Display names come from check_text (the MCP first line uses the same table). generic_captcha and unknown name no provider, so
# they are left out of this sentence (nothing is guessed).
UNNAMED_VENDORS = frozenset({"generic_captcha", "unknown"})


def age_text(age: timedelta) -> str:
    """The age of a check as ahn_check's age_seconds gives it: the same words as the MCP first line (check_text.age_text)."""
    return _age_text(int(age.total_seconds()))


def side_line(age: timedelta) -> str:
    """D92: the your_side_likely sentence when the home-page check is a stored one: how old it is, and that it covers the home page only."""
    return f"Not determined: AHN's home-page check {age_text(age)} was normal, but it does not see single pages; the cause may be on your side."


def gate_line(outcome: str, dated: bool, age: timedelta, vendor: str | None = None) -> str:
    """The home_gate_unconfirmed sentence: whose check, how old it is, what it saw, and that the next check is AHN's."""
    kind = HOME_GATE_KINDS[outcome]
    name = VENDOR_NAMES.get(vendor or "") if outcome == "bot_challenge" and vendor not in UNNAMED_VENDORS else None
    what = f"{name} {kind}" if name else kind
    article = "an" if what.split()[0] in ("HTTP", "AWS") else "a"
    when = age_text(age)
    head = f"Not determined: AHN's check {when} saw {article} {what} refusal; "
    if dated:
        return head + "recheck_after is AHN's next check, not an instruction."
    return head + "AHN checks again. That is AHN's check, not an instruction."


ROBOTS_REASONS = {"robots_disallowed": "no_measurement_robots_disallowed", "robots_refused": "no_measurement_robots_refused"}
# Stored reasons for which no measurement will come, so the stored time is never offered as recheck_after. not_eligible (not a
# public host) keeps the general sentence: only the time is left out. robots_unreadable is read again, so it stays.
NO_RECHECK_REASONS = frozenset(ROBOTS_REASONS) | {"not_eligible"}

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
           end: datetime | None = None, recheck: datetime | None = None, line: str | None = None) -> dict:
    out = {"branch": branch, "hint": hint, "confidence": confidence, "basis": basis}
    if start is not None:
        out["window"] = {"from": _iso(start), **({"to": _iso(end)} if end is not None else {})}
    out["line"] = line if line is not None else line_for(line_key, start, end)
    out["note"] = NOTE
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
    if confidence == "high" and not _others_saw_errors(conn, host, row["first_fail_at"], ended or now):
        confidence = "medium"   # only AHN's own measurement says so, and it may be AHN's path alone (D82)
    if ended is not None and row["ended_reason"] == "answered":
        return _cause("transient", "recovered", confidence, ["outage_episodes"], "recovered_site", start=row["first_fail_at"], end=ended)
    return _cause("transient", "site_errors", confidence, ["outage_episodes"], "site_errors", start=row["first_fail_at"], end=ended)


def _others_saw_errors(conn, host: str, start: datetime, end: datetime) -> bool:
    """Something other than AHN's own measurement saw the host failing between ``start`` and ``end`` (D82): a published,
    non-synthetic observation with outcome ``error`` on a service-error incident, or OTHERS_MIN_SOURCES separate clients' lookups
    with a service error within ONE hour of the period (the same counts as ``others_failing_same_hour``, D47)."""
    if conn.execute(
            """SELECT 1 FROM observations o JOIN incidents i ON i.id=o.incident_id
               WHERE i.site_host IN (%s, 'www.' || %s) AND i.error_class='service_error' AND i.visibility='published' AND o.review_state='published'
                 AND o.synthetic=false AND o.withdrawn_at IS NULL AND o.outcome='error' AND o.observed_at >= %s AND o.observed_at <= %s
               LIMIT 1""", (host, host, start, end)).fetchone():
        return True
    # one hour at a time: lookup_signals records the same client again in every hour, so clients are only separate within an hour
    row = conn.execute(
        """SELECT 1 FROM lookup_signals_hourly WHERE site_host=%s AND hour >= %s AND hour <= %s
             AND http_status_class NOT IN ('429','401','403','407','451','402')
             AND (error_class='service_error' OR http_status_class IN ('5xx','408'))
           GROUP BY hour HAVING sum(sources) >= %s LIMIT 1""",
        (host, _floor_hour(start), end, OTHERS_MIN_SOURCES)).fetchone()
    return row is not None


def _probe(conn, host: str):
    return conn.execute(
        "SELECT outcome,probed_at,next_due_at,consecutive_refusals,not_measured_reason,bot_vendor,redirect FROM site_probe WHERE site_host=%s",
        (host,)).fetchone()


def _login_wall_twice(conn, host: str) -> bool:
    """The last two home-page checks stored for the host were both a login wall (D84). Read from ``site_probe_daily``: every
    stored check adds one to its day's count of that outcome, so the checks since the last day with any other outcome are the
    login-wall counts of the days after it. Any other outcome ends the run, a ``not_measured`` row included, as it resets
    ``consecutive_refusals``. A day that also holds another outcome is not counted (the order inside a day is not kept), so
    a mixed sequence stays unconfirmed. A day of ``redirected_same_host`` also ends the run (D91: kept on purpose; the daily
    counts cannot tell a www redirect AHN did not follow, which ``schedule`` does not reset, from a path or locale move,
    which it does, so reading it as neutral could confirm two login walls that a real answer separated; the cost is only a
    confirmation that waits for the next check)."""
    rows = conn.execute(
        "SELECT day, outcome, sum(probes) AS n FROM site_probe_daily WHERE site_host=%s AND probes>0 AND day >= CURRENT_DATE - 34 GROUP BY day, outcome",
        (host,)).fetchall()
    other = [row["day"] for row in rows if row["outcome"] != "login_wall"]
    last_other = max(other) if other else None
    return sum(row["n"] for row in rows if row["outcome"] == "login_wall" and (last_other is None or row["day"] > last_other)) >= 2


def _gated(conn, host: str, probe, now: datetime, error_class: str | None) -> dict | None:
    """Two refusals in a row of AHN's own home-page check. One refusal alone is undetermined."""
    if probe is None or probe["outcome"] not in GATE_OUTCOMES or error_class == "service_error" or probe["probed_at"] < now - PROBE_FRESH:
        return None
    if probe["consecutive_refusals"] >= 2:
        return _cause("policy_closed", "home_now_gated", "medium", ["site_probe"],
                      "home_now_gated_latest_login_wall" if probe["outcome"] == "login_wall" else "home_now_gated")
    # D84: only for the two failures a closed home page explains; a sign-in step (login_auth), an empty class and any class added later are left as they were
    if probe["outcome"] == "login_wall" and error_class in D84_ERROR_CLASSES and _login_wall_twice(conn, host):
        return _cause("policy_closed", "home_now_gated", "medium", ["site_probe", "site_probe_daily"], "home_now_gated_login_wall")
    recheck = probe["next_due_at"] if probe["next_due_at"] > now else None
    line = gate_line(probe["outcome"], recheck is not None, now - probe["probed_at"], probe["bot_vendor"])
    return _cause("undetermined", "home_gate_unconfirmed", "low", ["site_probe"], "home_gate_unconfirmed", recheck=recheck, line=line)


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
    """(any home-page check around the failure time, and all of them normal). AHN reads no page content. A stored check that
    ended at a www redirect AHN did not follow (D88) is no measurement of the home page: it is neither normal nor a
    refusal. The day's counts keep no redirect record, so a ``redirected_same_host`` count may be such a redirect and is
    never taken as a normal check on the day-count path, whatever the current row is (D91; D88 looked at the current row
    only, which misread a day of unfollowed redirects once the row had moved on); the day's other outcomes still count."""
    unfollowed = probe is not None and unfollowed_www_redirect(probe["outcome"], probe["redirect"])
    if probe is not None and probe["outcome"] != "not_measured" and abs(probe["probed_at"] - when.at) <= MEASURE_WINDOW:
        if unfollowed:
            return False, False
        return True, probe["outcome"] in NORMAL_OUTCOMES
    rows = conn.execute(
        "SELECT outcome FROM site_probe_daily WHERE site_host=%s AND day=%s AND outcome<>'not_measured' AND probes>0"
        " AND outcome<>'redirected_same_host'",
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
    gate = _gated(conn, host, probe, now, error_class)
    if gate and gate["branch"] == "policy_closed" and "site_probe_daily" not in gate["basis"]:
        # (a D84 login-wall confirmation waits its turn below, after persistent_refusal and others_failing_same_hour, as the unconfirmed sentence did)
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
    # a stored robots.txt reason: the stored time is the next robots.txt look, not a measurement, so it is never offered
    robots = ROBOTS_REASONS.get(probe["not_measured_reason"]) if probe is not None and probe["outcome"] == "not_measured" else None
    if probe is not None and probe["outcome"] == "not_measured" and probe["not_measured_reason"] in NO_RECHECK_REASONS:
        recheck = None
    if probe is not None and unfollowed_www_redirect(probe["outcome"], probe["redirect"]):
        # D88: the next check may end at the same redirect (the www name's robots.txt can disallow it), so a home-page
        # measurement is not promised and the stored time is not offered
        recheck = None
    measured, normal = _measurement(conn, host, probe, when)
    # the terms signal is information, never a block: it only fills the place of "nothing measured" or of nothing at all
    terms = _terms_signal_changed(history) if history else None
    if not measured:
        if terms:
            return terms
        # AHN does not measure a host whose robots.txt disallows or refused it: the stored row says why, and no later
        # measurement is due (the next robots.txt look is not a measurement), so no recheck_after is given
        if robots and probe["probed_at"] >= now - ROBOTS_LINE_FRESH:
            return _cause("undetermined", "no_measurement", "low", ["site_probe"], robots)
        return _cause("undetermined", "no_measurement", "low", ["site_probe"] if probe is not None else [], "no_measurement",
                      recheck=recheck)
    if normal and (failure_stage == "before_target_response" or refusal_like):
        # D92: a stored check near the failure time is dated; otherwise the day's counts describe it (fixed sentence)
        dated = probe is not None and probe["outcome"] != "not_measured" and abs(probe["probed_at"] - when.at) <= MEASURE_WINDOW
        return _cause("undetermined", "your_side_likely", "low", ["site_probe"], "your_side_likely", recheck=recheck,
                      line=side_line(now - probe["probed_at"]) if dated else None)
    return terms
