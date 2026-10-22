"""Open announcements from a Statuspage page's own Atom history (D52).

Many hosted status pages disallow ``/api/`` for automated readers (D50) but allow ``/history.atom``, the page's public
feed of its most recent announcements. That feed is read here as a *positive signal only*: it can say that an announced
incident or maintenance is open, and nothing else. It carries no page indicator, no component state and no impact
level, it lists only the newest entries, and scheduled maintenance dated in the future can fill it so that a new
incident is not listed. So an empty or fully resolved feed is "unknown", never "operational" (unknown is not anything),
and the reading says when the window cannot be trusted (``window_blind``).

Only counts, one time and two booleans leave this module: entry text, titles and links are never kept. The parser is
pure (no network, no database) and never raises on bad input beyond ``AtomError``.

XML safety: the standard library parser is used after the document is refused if it carries a DOCTYPE (or any entity
declaration). Without a DTD there is no internal or external entity to expand, so neither XXE nor entity-expansion
bombs (billion laughs) have anything to work with; a real Statuspage feed has no DTD.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from xml.etree import ElementTree

ATOM_CAP = 131_072            # bytes of one feed read (the largest of 50 sampled feeds was 96 KB; 32 KiB cut off 79 % of them)
MAX_ENTRIES = 100             # entries looked at; a Statuspage feed lists 25
FEED_LIMIT = 25               # the number of entries a Statuspage history feed lists: a feed this full may have dropped older ones
MIN_WINDOW = timedelta(days=7)  # a feed whose past entries reach back less than this cannot show an older open incident
_ATOM = "{http://www.w3.org/2005/Atom}"
_DTD = re.compile(rb"<!\s*(?:DOCTYPE|ENTITY|ELEMENT|ATTLIST|NOTATION)", re.IGNORECASE)
# One update of an entry body, as Statuspage renders it: <small>time</small><br><strong>Status</strong> - text. Only this
# structure counts, so a bold word inside an update's text, or a <strong> with attributes, is never taken for a status.
_STATUS = re.compile(r"<small>[^<]{0,80}</small>\s*<br\s*/?>\s*(?:<strong>([^<]{1,40})</strong>)?")
_CLOSED = frozenset({"resolved", "postmortem", "completed"})
_INCIDENT_OPEN = frozenset({"investigating", "identified", "monitoring"})
_MAINTENANCE_OPEN = frozenset({"in progress", "verifying"})
_SCHEDULED = frozenset({"scheduled"})
_KNOWN = _CLOSED | _INCIDENT_OPEN | _MAINTENANCE_OPEN | _SCHEDULED


class AtomError(ValueError):
    """The body is not a usable Atom feed."""


@dataclass(frozen=True)
class AtomReading:
    open_incidents: int                    # incidents whose newest real status is investigating, identified or monitoring
    open_maintenances: int                 # maintenances in progress or being verified
    incident_updated_at: datetime | None   # newest <updated> of the open incidents; None when there is none or one has no time
    future_entries: int                    # entries dated after the read (scheduled maintenance)
    entries: int
    window_blind: bool                     # a new or old open incident may be missing from the feed
    unreadable: int = 0                    # entries (not dated in the future) whose status could not be read: counted as nothing

    def active_maintenances(self) -> int | None:
        """The maintenance count only when the feed can be trusted for it: with entries dated in the future, or a window
        too short, an open maintenance may have been pushed out of the feed (Cloudflare's reads 0 of its 2)."""
        if self.future_entries or self.window_blind or self.unreadable or not self.entries:
            return None
        return self.open_maintenances


def _time(raw) -> datetime | None:
    if not isinstance(raw, str) or len(raw) > 40:
        return None
    try:
        value = datetime.fromisoformat(raw.strip().replace("Z", "+00:00"))
        return value.astimezone(timezone.utc) if value.tzinfo else None
    except (ValueError, OverflowError):  # 0001-01-01T00:00:00+01:00 cannot be moved to UTC
        return None


def _status_word(html: str | None) -> str | None:
    """The newest real status of an entry: the first <strong> word in the body (updates are newest first) that is not
    exactly "update", lowercased with spaces collapsed and trailing punctuation dropped; None when the body has none. A word
    that is not one of the known states (another language, anything odd) is returned as it is: the caller counts nothing."""
    for match in _STATUS.finditer(html or ""):
        if match.group(1) is None:
            return "?"  # an update head whose status is not a plain <strong>word</strong>: the entry is unreadable, and no older update is consulted
        word = " ".join(match.group(1).lower().split()).rstrip(" .:;,!")
        if word != "update":
            return word
    return None


def parse_history(body: bytes, now: datetime) -> AtomReading:
    if not isinstance(body, (bytes, bytearray)) or len(body) > ATOM_CAP:
        raise AtomError("too_large")
    if b"\x00" in body or _DTD.search(body):
        # A status feed has no DTD: refuse it rather than parse entities. A NUL byte means UTF-16 or UTF-32, whose
        # DOCTYPE the byte pattern above would not see; a Statuspage feed is UTF-8.
        raise AtomError("invalid_body")
    try:
        root = ElementTree.fromstring(bytes(body))
    except (ElementTree.ParseError, ValueError, RecursionError) as exc:
        raise AtomError("invalid_body") from exc
    if root.tag != _ATOM + "feed":
        raise AtomError("invalid_body")
    entries = root.findall(_ATOM + "entry")[:MAX_ENTRIES]
    incidents = maintenances = future = unreadable = 0
    updated: list[datetime | None] = []
    past: list[datetime] = []
    for entry in entries:
        published = _time(entry.findtext(_ATOM + "published"))
        if published is not None and published > now:
            future += 1
            continue  # scheduled for later: not open yet
        if published is not None:
            past.append(published)
        word = _status_word(entry.findtext(_ATOM + "content"))
        if word in _INCIDENT_OPEN:
            incidents += 1
            updated.append(_time(entry.findtext(_ATOM + "updated")))
        elif word in _MAINTENANCE_OPEN:
            maintenances += 1
        elif word not in _KNOWN:
            unreadable += 1  # no status found, or one this module does not know: never an open incident
    total = len(entries)
    window = (now - min(past)) if past else timedelta(0)
    blind = bool(total and (future == total or (total >= FEED_LIMIT and window < MIN_WINDOW)))
    newest = None if (not updated or any(t is None for t in updated)) else max(updated)
    return AtomReading(incidents, maintenances, newest, future, total, blind, unreadable)
