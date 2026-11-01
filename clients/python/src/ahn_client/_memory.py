"""Optional helper: a local file of a site's recent failures and the standing advice a pattern of them calls for.

Nothing here touches the network and nothing is sent anywhere. The file holds, per host, at most 14 days of
``(date, hour, branch, hint, error_class)`` rows; the values are checked against fixed lists, so no free text is
stored. ``advice`` turns the rows into codes an agent can write into its own memory next to the site (``branch`` and
``hint`` are the ``cause`` fields of a lookup made with ``failed_at``, where the service supports it).
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from datetime import date, datetime, timedelta, timezone
from typing import Dict, List, Optional

from ._hosts import normalize_host

__all__ = ["FailureLog", "ADVICE"]

WINDOW_DAYS = 14
BRANCHES = ("policy_closed", "transient", "undetermined")
ERROR_CLASSES = ("site_access", "login_auth", "human_verification", "service_error")
# A closure the lookup itself confirmed counts at once; any other policy_closed needs the same refusal on 3 days in a row.
CONFIRMED_CLOSURE_HINTS = ("robots_now_disallows", "home_now_gated")
_HINT = re.compile(r"^[a-z][a-z_]{0,39}$")
MAX_ROWS_PER_HOST = 200
MAX_HOSTS = 500

# Advice code -> what to write into memory. The wording is ours; nothing from a site is in it.
ADVICE: Dict[str, str] = {
    "stop_automatic_visits": "Take this site out of the automatic visits: use its official API or feed, hand the task to the person, "
                             "or say in the briefing that automatic visits are blocked. Do not retry daily.",
    "move_visit_time": "Move the visit to another time of day: failures keep coming at the same hour (provider incidents or maintenance).",
    "reduce_frequency": "Visit less often and honor retry_after: the site keeps rate limiting.",
    "ask_user_to_log_in": "Ask the person to sign in again: the session has probably expired.",
    "check_tool_and_path": "Check the tool and the path (the page structure may have changed) and say so in the briefing instead of retrying.",
    "restore_original_method": "The declared policy went back the other way: return to the original method and delete the workaround from memory.",
}


class FailureLog:
    """Rows of recent failures in one JSON file."""

    def __init__(self, path):
        self.path = os.fspath(path)

    def _load(self) -> Dict[str, List[dict]]:
        try:
            with open(self.path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, ValueError):
            return {}
        hosts = data.get("hosts") if isinstance(data, dict) else None
        if not isinstance(hosts, dict):
            return {}
        return {name: [row for row in rows if isinstance(row, dict)] for name, rows in hosts.items() if isinstance(rows, list)}

    def _save(self, hosts: Dict[str, List[dict]]) -> None:
        directory = os.path.dirname(os.path.abspath(self.path))
        os.makedirs(directory, exist_ok=True)
        handle, temp = tempfile.mkstemp(dir=directory, prefix=".ahn-failures-")
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as out:
                json.dump({"schema": "ahn-failures/1", "hosts": hosts}, out, sort_keys=True)
            os.replace(temp, self.path)
        except BaseException:
            try:
                os.unlink(temp)
            except OSError:
                pass
            raise

    @staticmethod
    def _prune(hosts: Dict[str, List[dict]], today: date) -> Dict[str, List[dict]]:
        cutoff = (today - timedelta(days=WINDOW_DAYS)).isoformat()
        kept = {name: [row for row in rows if isinstance(row.get("date"), str) and row["date"] >= cutoff] for name, rows in hosts.items()}
        return {name: rows for name, rows in kept.items() if rows}

    @staticmethod
    def _utc(when: Optional[datetime]) -> datetime:
        return (when or datetime.now(timezone.utc)).astimezone(timezone.utc)

    def record(self, host: str, branch: str, hint: Optional[str] = None, *, error_class: Optional[str] = None,
               when: Optional[datetime] = None) -> bool:
        """Add one failure of ``host`` at ``when`` (default now, kept as UTC date and hour). False when a value is not allowed."""
        name = normalize_host(host)
        if name is None or branch not in BRANCHES:
            return False
        if hint is not None and not (isinstance(hint, str) and _HINT.fullmatch(hint)):
            return False
        if error_class is not None and error_class not in ERROR_CLASSES:
            return False
        moment = self._utc(when)
        hosts = self._prune(self._load(), moment.date())
        if name not in hosts and len(hosts) >= MAX_HOSTS:
            return False
        rows = hosts.setdefault(name, [])
        rows.append({"date": moment.date().isoformat(), "hour": moment.hour, "branch": branch, "hint": hint, "error_class": error_class})
        hosts[name] = rows[-MAX_ROWS_PER_HOST:]
        self._save(hosts)
        return True

    def recent(self, host: str, *, now: Optional[datetime] = None) -> List[dict]:
        """The host's rows of the last 14 days."""
        name = normalize_host(host)
        return list(self._prune(self._load(), self._utc(now).date()).get(name or "", []))

    def forget(self, host: str) -> None:
        name = normalize_host(host)
        hosts = self._load()
        if name in hosts:
            del hosts[name]
            self._save(hosts)

    def advice(self, host: str, *, bundle_policy_reverted: bool = False, now: Optional[datetime] = None) -> List[str]:
        """Advice codes (keys of ``ADVICE``) for the host's last 14 days.

        ``bundle_policy_reverted`` is True when a newer bundle shows the host's policy going back the other way.
        """
        rows = self.recent(host, now=now)
        found: List[str] = ["restore_original_method"] if bundle_policy_reverted else []
        closed = [row for row in rows if row.get("branch") == "policy_closed"]
        days = sorted({date.fromisoformat(row["date"]) for row in closed})
        streak = best = 1 if days else 0
        for before, after in zip(days, days[1:]):
            streak = streak + 1 if (after - before).days == 1 else 1
            best = max(best, streak)
        if best >= 3 or any(row.get("hint") in CONFIRMED_CLOSURE_HINTS for row in closed):
            found.append("stop_automatic_visits")
        hours = [row.get("hour") for row in rows if row.get("branch") == "transient" and row.get("hint") in ("provider_incident", "site_errors")]
        if any(hours.count(hour) >= 3 for hour in set(hours)):
            found.append("move_visit_time")
        if sum(1 for row in rows if row.get("branch") == "transient" and row.get("hint") == "rate_limited") >= 3:
            found.append("reduce_frequency")
        your_side = [row for row in rows if row.get("branch") == "undetermined" and row.get("hint") == "your_side_likely"]
        login = sum(1 for row in your_side if row.get("error_class") == "login_auth")
        if login >= 2:
            found.append("ask_user_to_log_in")
        if len(your_side) - login >= 2:
            found.append("check_tool_and_path")
        return found
