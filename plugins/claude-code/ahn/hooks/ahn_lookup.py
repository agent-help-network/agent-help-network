#!/usr/bin/env python3
"""Agent Help Network lookup hook for Claude Code. Standard library only, Python 3.7+.

Claude Code pipes a PostToolUseFailure or PostToolUse payload on stdin. When a
web step failed, this script keeps only the target hostname, asks Agent Help
Network what the host declares and what it recommends, and prints a short
additionalContext for Claude. It sends a hostname and enum codes, nothing else,
it only reads, and it never blocks or fails the tool call: every path exits 0.

Environment:
  AHN_HOOK_DISABLE=1          do nothing
  AHN_HOOK_SKIP_HOSTS=a.com,b  never look up these hosts or their subdomains
  AHN_HOOK_DEBUG=1            one-line note on stderr about what happened
  AHN_HOOK_FAKE_RESPONSE=path read the answer from a JSON file (no network)
  CLAUDE_PLUGIN_DATA          set by Claude Code; the rate-limit cache lives there
"""

from __future__ import annotations

import ipaddress
import json
import os
import re
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

try:
    import ssl
except ImportError:  # A Python built without ssl cannot look anything up; the hook stays silent.
    ssl = None

API_URL = "https://agenthelpnetwork.com/v1/incidents/check"
USER_AGENT = "ahn-claude-code-plugin/0.1"
OWN_HOST = "agenthelpnetwork.com"
SOCKET_TIMEOUT_SECONDS = 4  # each socket operation (connect, read)
TOTAL_TIMEOUT_SECONDS = 4.0  # the whole lookup, DNS included
WORKER_NAME = "ahn-lookup"
CACHE_FILE = "ahn-hook-cache.json"
CACHE_TTL_SECONDS = 600
CACHE_MAX_ENTRIES = 200
CACHE_MAX_BYTES = 131_072
MAX_CONTEXT = 700
MAX_STDIN_BYTES = 8_000_000
MAX_RESPONSE_BYTES = 262_144

HOST_LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?")
# Special-use and private-network names (RFC 6761/6762/8375, Tor, common LAN
# suffixes). Decided by name only: the hook never resolves anything.
SKIP_TLDS = frozenset({"localhost", "local", "internal", "test", "lan", "arpa", "onion", "invalid", "example",
                       "home", "corp", "intranet", "private", "localdomain", "alt"})
URL_IN_TEXT = re.compile(r"https?://[^\s'\"<>()\[\]{}`]+", re.IGNORECASE)
# Claude Code's own WebFetch refusal ("Claude Code is unable to fetch from <host>") is the only
# place a bare, scheme-less hostname is read from error text. Elsewhere dotted tokens are code
# (document.querySelector, locator.click, urllib3.exceptions...) or path segments (C:\acme.budget).
REFUSAL = re.compile(r"\bis unable to fetch from\s+(?:https?://)?"
                     r"((?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z][a-z0-9-]{1,62})(?![\w-]|\.\w)", re.IGNORECASE)
FULL_URL = re.compile(r"([A-Za-z][A-Za-z0-9+.-]*)://")
# A scheme-less tool argument: host[:port] then a path, query, fragment or nothing. "about:blank",
# "javascript:..." and "mailto:..." do not match.
SCHEMELESS = re.compile(r"[A-Za-z0-9.-]+(?::[0-9]{1,5})?(?:[/?#].*)?", re.DOTALL)
# Fields of an MCP tool's input that name the page it acted on. WebFetch has only "url".
URL_FIELDS = ("url", "uri", "href", "link", "page_url", "pageUrl", "target_url", "targetUrl")
CLASSES = (
    # Not the bare brand name: "cloudflare" also appears in Cloudflare 5xx pages and block pages (error 1020).
    ("human_verification", re.compile(r"captcha|verify (?:that )?you are (?:a )?human|are you a robot|challenge"
                                      r"|checking your browser|just a moment", re.I)),
    ("login_auth", re.compile(r"log[ -]?in\b|sign[ -]?in\b|unauthori[sz]ed|\b401\b|\bauthenticat(?:e|ion)\b", re.I)),
    ("service_error", re.compile(r"\b5\d\d\b|time ?out|timed[ _]?out|unavailable|reset|bad gateway", re.I)),
)
# Nothing was received from the site: Node errno names, Chrome "net::ERR_*" codes (browser MCP tools), and
# Claude Code's pre-request WebFetch refusals.
BEFORE_RESPONSE = re.compile(r"connection[ _]refused|could not resolve|timed[ _]?out|ENOTFOUND|ECONNREFUSED|getaddrinfo"
                             r"|is unable to fetch from|unable to verify if domain|is blocked by the network"
                             r"|ERR_NAME_NOT_RESOLVED|ERR_INTERNET_DISCONNECTED|ERR_ADDRESS_UNREACHABLE", re.I)
HOST_LIKE = re.compile(r"[A-Za-z0-9.-]+")
# Characters IDNA2003 (Python's "idna" codec) maps to a different domain than IDNA2008 (browsers) uses.
IDNA_DEVIATIONS = frozenset("ßς‌‍")
MAX_AGENT_TOKENS = 64

# Closed enums of the response contract (docs/api-contract.en.md; ahn/models.py is the machine form).
# A value outside them is dropped, never shown.
ACTIONS = frozenset({"proceed", "use_official_path", "hand_off_to_human", "wait_provider_incident",
                     "stop_declared_block", "unknown"})
CONFIDENCES = frozenset({"low", "medium", "high"})
ROBOTS_STATUSES = frozenset({"ok", "absent", "redirected", "refused", "invalid", "error", "too_large", "not_public",
                             "pending"})
LLMS_STATUSES = frozenset({"present", "absent", "redirected", "refused", "error", "not_public", "pending"})
INDICATORS = frozenset({"none", "minor", "major", "critical", "maintenance", "unknown"})
FRESHNESS = frozenset({"fresh", "stale", "pending"})
EVIDENCE_LEVELS = frozenset({"no_reports", "single_observation", "multiple_observations", "later_success_observed",
                             "stale_reports"})
NEXT_CHECKS = frozenset({"locate_failure_stage", "before_target_response", "response_observed", "access_denial_observed",
                         "human_handoff", "account_handoff", "robots_fetch_refused", "robots_unknown",
                         "robots_blocks_agents", "robots_allows_agents", "robots_wildcard_disallow", "profile_not_read",
                         "llms_txt_present",
                         "official_path_available", "provider_scope", "provider_clear", "provider_unknown",
                         "reports_unknown", "reports_stale", "reports_unverified"})
AGENT_KINDS = frozenset({"user_agent", "search_crawler", "training_crawler"})
AGENT_TOKEN = re.compile(r"[A-Za-z][A-Za-z0-9-]{1,31}")
# https, a hostname, an optional path of at most 80 characters; no userinfo, port, query or fragment.
OFFICIAL_URL = re.compile(r"https://([A-Za-z0-9.-]{1,253})(/[A-Za-z0-9._~:/+-]{0,79})?")
CLOSING = "Declared policy is not an access guarantee; bypassing site controls is not advised."


def hostname(value: str | None) -> str | None:
    """The bare lowercase hostname in a URL or host string, or None when there is none."""
    if not isinstance(value, str):
        return None
    value = value.strip()
    if not value:
        return None
    try:
        parts = urllib.parse.urlsplit(value if "://" in value else "//" + value)
        host = parts.hostname
    except ValueError:
        return None
    if not host:
        return None
    host = host.rstrip(".").lower()
    if not host.isascii():
        if IDNA_DEVIATIONS.intersection(host):
            return None
        try:
            host = host.encode("idna").decode("ascii")
        except UnicodeError:
            return None
    if len(host) > 253 or not all(HOST_LABEL.fullmatch(label) for label in host.split(".")):
        return None
    return host


def apex(host: str) -> str:
    """Drop a leading "www." label: AHN reads declared policy for apex names, and "www." is the same site."""
    labels = host.split(".")
    return ".".join(labels[1:]) if len(labels) >= 3 and labels[0] == "www" else host


def normalize_host(value: str | None) -> str | None:
    """The hostname AHN is asked about: hostname() without a leading "www."."""
    host = hostname(value)
    return apex(host) if host else None


def url_host(value) -> str | None:
    """Hostname of an http(s) URL or of a scheme-less host[:port][/path] tool argument; None for other schemes."""
    if not isinstance(value, str):
        return None
    value = value.strip()
    scheme = FULL_URL.match(value)
    if scheme:
        if scheme.group(1).lower() not in ("http", "https"):
            return None
    elif not SCHEMELESS.fullmatch(value):
        return None
    return hostname(value)


def special_use(host: str) -> bool:
    """True for single-label, special-use and private-network names and for address literals."""
    labels = host.split(".")
    if len(labels) < 2:
        return True
    last = labels[-1]
    # Public TLDs are alphabetic or IDN; an all-digit or hex last label is an
    # address literal in inet_aton form (10.0.0.1, 127.1, 0x7f.1).
    if last in SKIP_TLDS or not (last.isalpha() or last.startswith("xn--")):
        return True
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        return False


def skip_host(host: str, extra: str = "") -> bool:
    """True for special-use names, AHN's own hosts, and AHN_HOOK_SKIP_HOSTS entries and their subdomains.

    Both the host as given and its "www."-less form are compared, against each entry as given and
    normalised the same way, so "www.secret.example" in the list also covers "secret.example".
    """
    if special_use(host):
        return True
    names = {host, apex(host)}
    suffixes = {OWN_HOST}
    for item in extra.split(","):
        item = item.strip().lstrip("*.")
        if not item:
            continue
        entry = hostname(item) or item.lower().rstrip(".")
        suffixes.update({entry, apex(entry)})
    return any(name == suffix or name.endswith("." + suffix) for name in names for suffix in suffixes)


def host_from_text(text: str, refusal: bool = True) -> str | None:
    """The hostname of the first http(s) URL in an error text, else (when refusal) the host in Claude Code's refusal."""
    for match in URL_IN_TEXT.finditer(text):
        host = hostname(match.group(0).rstrip(".,;:!?"))
        if host:
            return host
    if refusal:
        match = REFUSAL.search(text)
        if match:
            return hostname(match.group(1))
    return None


def without_urls(text: str, host: str | None = None) -> str:
    """Blank each URL and every token naming host or one of its subdomains, so neither a path, a query nor the
    site's own name (developers.cloudflare.com, login.example.com) shapes the classification."""
    text = URL_IN_TEXT.sub(" ", text)
    if not host:
        return text
    host = host.lower()

    def blank(match) -> str:
        token = match.group(0).lower().rstrip(".")
        return " " if token == host or token.endswith("." + host) else match.group(0)

    return HOST_LIKE.sub(blank, text)


def classify(text: str, host: str | None = None) -> tuple[str, str | None]:
    text = without_urls(text or "", host)
    error_class = next((name for name, pattern in CLASSES if pattern.search(text)), "site_access")
    stage = "before_target_response" if BEFORE_RESPONSE.search(text) else None
    return error_class, stage


def http_status(tool_response) -> tuple[int, str] | None:
    """WebFetch reports an HTTP error as a successful call with code/codeText; return them when >= 400."""
    if not isinstance(tool_response, dict):
        return None
    for key in ("code", "status", "statusCode"):
        code = tool_response.get(key)
        if isinstance(code, int) and not isinstance(code, bool) and 400 <= code <= 599:
            reason = tool_response.get("codeText")
            reason = reason if isinstance(reason, str) and re.fullmatch(r"[A-Za-z ]{1,40}", reason) else ""
            return code, reason
    return None


def failure(payload: dict) -> tuple[str, str, str | None] | None:
    """(hostname, error_class, failure_stage) for a failed web step, or None. The hostname keeps any "www."."""
    if not isinstance(payload, dict) or payload.get("tool_name") == "WebSearch":
        return None
    tool = payload.get("tool_name") if isinstance(payload.get("tool_name"), str) else ""
    tool_input = payload.get("tool_input") if isinstance(payload.get("tool_input"), dict) else {}
    event = payload.get("hook_event_name")
    if event == "PostToolUseFailure":
        if payload.get("is_interrupt"):
            return None
        text = payload.get("error") if isinstance(payload.get("error"), str) else ""
        mcp = tool.startswith("mcp__")
        # An MCP failure is looked up only when its input or its error names an http(s) URL.
        candidates = (url_host(tool_input.get(field)) for field in (URL_FIELDS if mcp else ("url",)))
        host = next((name for name in candidates if name), None) or host_from_text(text, refusal=not mcp)
        if not host:
            return None
        error_class, stage = classify(text, host)
        return host, error_class, stage
    if event == "PostToolUse":
        status = http_status(payload.get("tool_response"))
        host = url_host(tool_input.get("url"))
        if not status or not host:
            return None
        code, reason = status
        error_class, _ = classify(f"HTTP {code} {reason}")
        return host, error_class, "response_observed"
    return None


def cache_dir(env=None, windows: bool | None = None) -> str | None:
    """$CLAUDE_PLUGIN_DATA, else a per-user directory: %LOCALAPPDATA%\\ahn on Windows, ~/.cache/ahn elsewhere.

    None when no absolute directory is known; a relative one would land in the project being worked on.
    """
    env = os.environ if env is None else env
    windows = os.name == "nt" if windows is None else windows
    directory = env.get("CLAUDE_PLUGIN_DATA") or ""
    if not os.path.isabs(directory):
        home = os.path.expanduser("~")
        if windows:
            base = env.get("LOCALAPPDATA") or ""
            base = base if os.path.isabs(base) else os.path.join(home, "AppData", "Local")
            directory = os.path.join(base, "ahn")
        else:
            directory = os.path.join(home, ".cache", "ahn")
    return directory if os.path.isabs(directory) else None


def cache_path(env=None, windows: bool | None = None) -> str | None:
    directory = cache_dir(env, windows)
    return os.path.join(directory, CACHE_FILE) if directory else None


def recently_seen(host: str, now: float | None = None, env=None) -> bool:
    """True when host was looked up within the TTL; otherwise records it. Cache errors never stop a lookup."""
    now = time.time() if now is None else now
    path = cache_path(env)
    if not path:
        return False
    try:
        with open(path, encoding="utf-8") as handle:
            text = handle.read(CACHE_MAX_BYTES + 1)
        entries = json.loads(text) if len(text) <= CACHE_MAX_BYTES else {}
        if not isinstance(entries, dict):
            entries = {}
    except (OSError, ValueError, RecursionError):
        entries = {}
    # The magnitude bound keeps now - stamp from overflowing on a hand-written huge integer.
    entries = {key: stamp for key, stamp in entries.items()
               if isinstance(key, str) and isinstance(stamp, (int, float)) and not isinstance(stamp, bool)
               and -1e12 < stamp < 1e12 and 0 <= now - stamp < CACHE_TTL_SECONDS}
    if host in entries:
        return True
    entries[host] = now
    if len(entries) > CACHE_MAX_ENTRIES:
        entries = dict(sorted(entries.items(), key=lambda item: item[1])[-CACHE_MAX_ENTRIES:])
    directory = os.path.dirname(path)
    temporary = None
    try:
        os.makedirs(directory, mode=0o700, exist_ok=True)
        # mkstemp: an unpredictable name, created 0600, in the user's own directory.
        handle, temporary = tempfile.mkstemp(prefix=".ahn-hook-cache.", suffix=".tmp", dir=directory)
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump(entries, stream)
        os.replace(temporary, path)
        temporary = None
    except (OSError, ValueError, TypeError):
        pass
    finally:
        if temporary:
            try:
                os.unlink(temporary)
            except OSError:
                pass
    return False


def lookup_url(host: str, error_class: str, stage: str | None) -> str:
    query = {"site_host": host, "error_class": error_class}
    if stage:
        query["failure_stage"] = stage
    return API_URL + "?" + urllib.parse.urlencode(query)


class RefuseRedirects(urllib.request.HTTPRedirectHandler):
    """Every 3xx is an error: the answer must come from API_URL itself, never another URL or plain HTTP."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise urllib.error.HTTPError(req.full_url, code, "redirect refused", headers, fp)


def build_opener() -> urllib.request.OpenerDirector:
    """An opener that verifies TLS certificates and hostnames and follows no redirect."""
    if ssl is None:
        raise RuntimeError("this Python has no ssl module")
    return urllib.request.build_opener(RefuseRedirects(), urllib.request.HTTPSHandler(context=ssl.create_default_context()))


def urlopen(request, timeout):
    return build_opener().open(request, timeout=timeout)


def fetch(url: str) -> dict:
    fake = os.environ.get("AHN_HOOK_FAKE_RESPONSE")
    if fake:
        with open(fake, encoding="utf-8") as handle:
            return json.load(handle)
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    with urlopen(request, timeout=SOCKET_TIMEOUT_SECONDS) as response:
        body = response.read(MAX_RESPONSE_BYTES + 1)
    if len(body) > MAX_RESPONSE_BYTES:
        raise ValueError("response over the size limit")
    return json.loads(body.decode("utf-8"))


def fetch_within(url: str, seconds: float | None = None) -> dict:
    """fetch(url) in a daemon thread; TimeoutError when it has not finished in time, DNS included."""
    seconds = TOTAL_TIMEOUT_SECONDS if seconds is None else seconds
    result = {}

    def work():
        try:
            result["data"] = fetch(url)
        except BaseException as exc:  # Handed to the caller below.
            result["error"] = exc

    worker = threading.Thread(target=work, name=WORKER_NAME, daemon=True)
    worker.start()
    worker.join(seconds)
    if worker.is_alive():
        raise TimeoutError("no answer in time")
    if "error" in result:
        raise result["error"]
    return result["data"]


def _enum(value, allowed: frozenset, default: str | None = "unknown") -> str | None:
    return value if isinstance(value, str) and value in allowed else default


def official_url(value) -> str | None:
    """The URL rebuilt from its validated parts, or None."""
    match = OFFICIAL_URL.fullmatch(value) if isinstance(value, str) else None
    if not match:
        return None
    host = match.group(1).lower()
    if not all(HOST_LABEL.fullmatch(label) for label in host.split(".")) or special_use(host):
        return None
    return "https://" + host + (match.group(2) or "")


def blocked_agents(robots: dict) -> tuple[list[str], int]:
    """(declared-block agent tokens, number of training-crawler tokens) from robots.agents_blocked.

    Training-crawler blocks are not declared blocks for user-directed agents (contract: Recommendation),
    so they are counted separately when robots.rules gives each token's kind.
    """
    rules = robots.get("rules") if isinstance(robots.get("rules"), list) else []
    kinds = {rule.get("agent"): _enum(rule.get("kind"), AGENT_KINDS, None)
             for rule in rules if isinstance(rule, dict) and isinstance(rule.get("agent"), str)}
    listed = robots.get("agents_blocked") if isinstance(robots.get("agents_blocked"), list) else []
    tokens, seen = [], set()
    for item in listed:
        if len(tokens) >= MAX_AGENT_TOKENS:
            break
        if isinstance(item, str) and item not in seen and AGENT_TOKEN.fullmatch(item):
            seen.add(item)
            tokens.append(item)
    declared = [token for token in tokens if kinds.get(token) != "training_crawler"]
    return declared, len(tokens) - len(declared)


def summary(host: str, data: dict, url: str) -> str:
    """The additionalContext line, at most MAX_CONTEXT characters, built only from contract enums and validated values."""
    if not isinstance(data, dict):
        raise ValueError("unexpected response")
    rec = data.get("recommendation") if isinstance(data.get("recommendation"), dict) else {}
    profile = data.get("access_profile") if isinstance(data.get("access_profile"), dict) else None
    official = data.get("official_paths") if isinstance(data.get("official_paths"), dict) else {}
    provider = data.get("provider_status") if isinstance(data.get("provider_status"), dict) else None
    checks = data.get("next_checks") if isinstance(data.get("next_checks"), list) else []

    blocked, training = None, 0
    if profile:
        robots_status = _enum(profile.get("robots_status"), ROBOTS_STATUSES)
        policy = robots_status + (" (stale)" if profile.get("freshness") == "stale" else "")
        llms = _enum(profile.get("llms_txt_status"), LLMS_STATUSES)
        # Agent lists mean something only when robots.txt was actually read.
        if robots_status == "ok":
            blocked, training = blocked_agents(profile.get("robots") if isinstance(profile.get("robots"), dict) else {})
    else:
        policy, llms = "not read for this hostname", "unknown"
    path = official_url(official.get("api_docs")) or official_url(official.get("developer_portal"))
    path_listed = bool(official.get("api_docs") or official.get("developer_portal"))
    if provider:
        status = f"{_enum(provider.get('indicator'), INDICATORS)}/{_enum(provider.get('freshness'), FRESHNESS)}"
    else:
        status = "not connected (official page listed)" if isinstance(data.get("official_status_page"), dict) else "not connected"
    codes = []
    for item in checks:
        code = _enum(item.get("code"), NEXT_CHECKS, None) if isinstance(item, dict) else None
        if code and code not in codes:
            codes.append(code)
        if len(codes) == 2:
            break

    def render(agent_limit: int | None, show_path: bool, show_url: bool) -> str:
        if blocked is None:
            agents = "unknown"
        elif not blocked:
            agents = "none"
        elif agent_limit is None or len(blocked) <= agent_limit:
            agents = ", ".join(blocked)
        elif agent_limit == 0:
            agents = f"{len(blocked)} agent tokens"
        else:
            agents = ", ".join(blocked[:agent_limit]) + f" (+{len(blocked) - agent_limit} more)"
        if training:
            agents += f" (+{training} training crawler{'s' if training != 1 else ''})"
        official_text = path if path and show_path else "listed" if path_listed else "none"
        full = url if show_url else "GET /v1/incidents/check on agenthelpnetwork.com"
        return (f"AHN {host}: recommendation={_enum(rec.get('action'), ACTIONS)} ({_enum(rec.get('confidence'), CONFIDENCES)}). "
                f"Declared policy: {policy}; blocked agents: {agents}; llms.txt: {llms}. "
                f"Official path: {official_text}. Provider status: {status}. "
                f"Reports: {_enum(data.get('evidence_level'), EVIDENCE_LEVELS)}. Next: {', '.join(codes) or 'none'}. "
                f"Full JSON: {full}. {CLOSING}")

    for options in ((6, True, True), (3, True, True), (0, True, True), (0, False, True), (0, False, False)):
        text = render(*options)
        if len(text) <= MAX_CONTEXT:
            return text
    tail = " " + CLOSING
    return text[:MAX_CONTEXT - len(tail) - 1].rstrip() + "…" + tail


def run(raw: bytes, env=os.environ) -> tuple[dict | None, str]:
    """Return (hook output or None, debug note). Raises nothing the caller must handle besides bugs."""
    if env.get("AHN_HOOK_DISABLE") == "1":
        return None, "disabled"
    payload = json.loads(raw.decode("utf-8", errors="replace"))
    found = failure(payload)
    if not found:
        return None, "no failed web step with a hostname"
    name, error_class, stage = found
    if skip_host(name, env.get("AHN_HOOK_SKIP_HOSTS", "")):
        return None, "skipped host"
    host = apex(name)
    if recently_seen(host, env=env):
        return None, f"{host} looked up within 10 minutes"
    url = lookup_url(host, error_class, stage)
    text = summary(host, fetch_within(url), url)
    event = payload.get("hook_event_name")
    return {"hookSpecificOutput": {"hookEventName": event, "additionalContext": text}}, f"{host} {error_class}"


def main() -> int:
    note = ""
    try:
        output, note = run(sys.stdin.buffer.read(MAX_STDIN_BYTES))
        if output:
            sys.stdout.write(json.dumps(output, ensure_ascii=True))
            sys.stdout.flush()
    except BaseException as exc:  # The hook must never fail or block the tool call.
        note = f"no output ({type(exc).__name__})"
    if os.environ.get("AHN_HOOK_DEBUG") == "1" and note:
        try:
            sys.stderr.write(f"ahn-hook: {note}\n")
        except Exception:
            pass
    return 0


def entry() -> int:
    """main(), then leave at once: a lookup still stuck in DNS or connect must not keep the process alive."""
    status = main()
    if any(thread.name == WORKER_NAME and thread.is_alive() for thread in threading.enumerate()):
        try:
            sys.stdout.flush()
            sys.stderr.flush()
        except Exception:
            pass
        os._exit(status)
    return status


if __name__ == "__main__":
    sys.exit(entry())
