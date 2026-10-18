#!/usr/bin/env python3
"""Agent Help Network lookup hook for Claude Code. Standard library only.

Claude Code pipes a PostToolUseFailure or PostToolUse payload on stdin. When a
web step failed, this script keeps only the target hostname, asks Agent Help
Network what the host declares and what it recommends, and prints a short
additionalContext for Claude. It sends a hostname and enum codes, nothing else,
and it never blocks or fails the tool call: every path exits 0.

Environment:
  AHN_HOOK_DISABLE=1          do nothing
  AHN_HOOK_SKIP_HOSTS=a.com,b  never look up these hosts or their subdomains
  AHN_HOOK_DEBUG=1            one-line note on stderr about what happened
  AHN_HOOK_FAKE_RESPONSE=path read the answer from a JSON file (no network)
"""

from __future__ import annotations

import ipaddress
import json
import os
import re
import sys
import tempfile
import time
import urllib.parse
import urllib.request

API_URL = "https://agenthelpnetwork.com/v1/incidents/check"
USER_AGENT = "ahn-claude-code-plugin/0.1"
OWN_HOST = "agenthelpnetwork.com"
TIMEOUT_SECONDS = 4
CACHE_FILE = "ahn-hook-cache.json"
CACHE_TTL_SECONDS = 600
CACHE_MAX_ENTRIES = 200
MAX_CONTEXT = 700
MAX_STDIN_BYTES = 8_000_000
MAX_RESPONSE_BYTES = 262_144

HOST_LABEL = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
# Special-use and private-network names (RFC 6761/6762/8375, Tor, common LAN
# suffixes). Decided by name only: the hook never resolves anything.
SKIP_TLDS = frozenset({"localhost", "local", "internal", "test", "lan", "arpa", "onion", "invalid", "example",
                       "home", "corp", "intranet", "private", "localdomain", "alt"})
URL_IN_TEXT = re.compile(r"https?://[^\s'\"<>()\[\]{}`]+", re.IGNORECASE)
BARE_HOST = re.compile(r"(?<![\w.@/:-])((?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z][a-z0-9-]{1,62})(?![\w-]|\.\w)",
                       re.IGNORECASE)
# A bare token like "index.js" or "notes.md" is a file name, not a host.
FILE_SUFFIXES = frozenset({"js", "mjs", "cjs", "ts", "tsx", "jsx", "py", "pyc", "json", "jsonl", "txt", "md", "html", "htm",
                           "css", "scss", "png", "jpg", "jpeg", "gif", "svg", "webp", "ico", "pdf", "csv", "xml", "yml",
                           "yaml", "toml", "ini", "cfg", "conf", "log", "lock", "sh", "ps1", "bat", "cmd", "exe", "dll",
                           "so", "zip", "gz", "tar", "rs", "go", "rb", "java", "kt", "swift", "php", "sql", "db", "env",
                           "map", "wasm", "woff", "woff2", "ttf", "mp4", "mp3", "wav", "mov", "tmp", "bak"})
CLASSES = (
    ("human_verification", re.compile(r"captcha|verify (?:that )?you are (?:a )?human|are you a robot|challenge|cloudflare", re.I)),
    ("login_auth", re.compile(r"log[ -]?in\b|sign[ -]?in\b|unauthori[sz]ed|\b401\b|\bauthenticat(?:e|ion)\b", re.I)),
    ("service_error", re.compile(r"\b5\d\d\b|time ?out|timed ?out|unavailable|reset|bad gateway", re.I)),
)
BEFORE_RESPONSE = re.compile(r"connection refused|could not resolve|timed ?out|ENOTFOUND|ECONNREFUSED|getaddrinfo"
                             r"|is unable to fetch from", re.I)
CODE = re.compile(r"^[a-z][a-z0-9_]{0,47}$")
TOKEN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,47}$")
SAFE_URL = re.compile(r"^https://[A-Za-z0-9.-]+(?:/[A-Za-z0-9._~/%+=&?#-]*)?$")


def normalize_host(value: str | None) -> str | None:
    """A bare lowercase hostname from a URL or host string; None when there is none."""
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
        try:
            host = host.encode("idna").decode("ascii")
        except UnicodeError:
            return None
    labels = host.split(".")
    if not all(HOST_LABEL.fullmatch(label) for label in labels) or len(host) > 253:
        return None
    # AHN reads declared policy for apex names; "www." is the same site.
    if len(labels) >= 3 and labels[0] == "www":
        host = ".".join(labels[1:])
    return host


def skip_host(host: str, extra: str = "") -> bool:
    """True for local, private, special-use, address-literal, single-label and own hosts."""
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
        pass
    suffixes = [OWN_HOST] + [item.strip().lower().lstrip(".") for item in extra.split(",") if item.strip()]
    return any(host == suffix or host.endswith("." + suffix) for suffix in suffixes)


def host_from_text(text: str) -> str | None:
    for match in URL_IN_TEXT.finditer(text):
        host = normalize_host(match.group(0))
        if host:
            return host
    for match in BARE_HOST.finditer(text):
        token = match.group(1)
        if token.rsplit(".", 1)[-1].lower() in FILE_SUFFIXES:
            continue
        host = normalize_host(token)
        if host:
            return host
    return None


def without_urls(text: str) -> str:
    """Replace each URL by its hostname so paths and queries never shape the classification."""
    return URL_IN_TEXT.sub(lambda match: normalize_host(match.group(0)) or "", text)


def classify(text: str) -> tuple[str, str | None]:
    text = without_urls(text or "")
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
    """(host, error_class, failure_stage) for a failed web step, or None."""
    if not isinstance(payload, dict) or payload.get("tool_name") == "WebSearch":
        return None
    tool_input = payload.get("tool_input") if isinstance(payload.get("tool_input"), dict) else {}
    event = payload.get("hook_event_name")
    if event == "PostToolUseFailure":
        if payload.get("is_interrupt"):
            return None
        text = payload.get("error") if isinstance(payload.get("error"), str) else ""
        host = normalize_host(tool_input.get("url")) or host_from_text(text)
        if not host:
            return None
        error_class, stage = classify(text)
        return host, error_class, stage
    if event == "PostToolUse":
        status = http_status(payload.get("tool_response"))
        host = normalize_host(tool_input.get("url"))
        if not status or not host:
            return None
        code, reason = status
        error_class, _ = classify(f"HTTP {code} {reason}")
        return host, error_class, "response_observed"
    return None


def cache_path() -> str:
    return os.path.join(tempfile.gettempdir(), CACHE_FILE)


def recently_seen(host: str, now: float | None = None) -> bool:
    """True when host was looked up within the TTL; otherwise records it. Cache errors never stop a lookup."""
    now = time.time() if now is None else now
    path = cache_path()
    try:
        with open(path, encoding="utf-8") as handle:
            entries = json.load(handle)
        if not isinstance(entries, dict):
            entries = {}
    except (OSError, ValueError):
        entries = {}
    entries = {key: stamp for key, stamp in entries.items()
               if isinstance(key, str) and isinstance(stamp, (int, float)) and 0 <= now - stamp < CACHE_TTL_SECONDS}
    if host in entries:
        return True
    entries[host] = now
    if len(entries) > CACHE_MAX_ENTRIES:
        entries = dict(sorted(entries.items(), key=lambda item: item[1])[-CACHE_MAX_ENTRIES:])
    try:
        temporary = f"{path}.{os.getpid()}.tmp"
        with open(temporary, "w", encoding="utf-8") as handle:
            json.dump(entries, handle)
        os.replace(temporary, path)
    except OSError:
        pass
    return False


def lookup_url(host: str, error_class: str, stage: str | None) -> str:
    query = {"site_host": host, "error_class": error_class}
    if stage:
        query["failure_stage"] = stage
    return API_URL + "?" + urllib.parse.urlencode(query)


def urlopen(request, timeout):
    return urllib.request.urlopen(request, timeout=timeout)


def fetch(url: str) -> dict:
    fake = os.environ.get("AHN_HOOK_FAKE_RESPONSE")
    if fake:
        with open(fake, encoding="utf-8") as handle:
            return json.load(handle)
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    with urlopen(request, timeout=TIMEOUT_SECONDS) as response:
        return json.loads(response.read(MAX_RESPONSE_BYTES).decode("utf-8"))


def _code(value, default: str = "unknown") -> str:
    return value if isinstance(value, str) and CODE.fullmatch(value) else default


def _url(value) -> str | None:
    return value if isinstance(value, str) and len(value) <= 200 and SAFE_URL.fullmatch(value) else None


def summary(host: str, data: dict, url: str) -> str:
    """The additionalContext line, at most MAX_CONTEXT characters, built only from validated fields."""
    if not isinstance(data, dict):
        raise ValueError("unexpected response")
    rec = data.get("recommendation") if isinstance(data.get("recommendation"), dict) else {}
    profile = data.get("access_profile") if isinstance(data.get("access_profile"), dict) else None
    official = data.get("official_paths") if isinstance(data.get("official_paths"), dict) else {}
    provider = data.get("provider_status") if isinstance(data.get("provider_status"), dict) else None
    checks = data.get("next_checks") if isinstance(data.get("next_checks"), list) else []

    if profile:
        robots = _code(profile.get("robots_status"))
        rules = profile.get("robots") if isinstance(profile.get("robots"), dict) else {}
        listed = rules.get("agents_blocked") if isinstance(rules.get("agents_blocked"), list) else []
        blocked = [item for item in listed if isinstance(item, str) and TOKEN.fullmatch(item)]
        llms = _code(profile.get("llms_txt_status"))
    else:
        robots, blocked, llms = "not read for this hostname", None, "unknown"
    path = _url(official.get("api_docs")) or _url(official.get("developer_portal"))
    path_listed = bool(official.get("api_docs") or official.get("developer_portal"))
    if provider:
        status = f"{_code(provider.get('indicator'))}/{_code(provider.get('freshness'))}"
    else:
        status = "not connected (official page listed)" if isinstance(data.get("official_status_page"), dict) else "not connected"
    codes = [_code(item.get("code")) for item in checks if isinstance(item, dict)][:2]

    def render(agent_limit: int | None, show_path: bool, show_url: bool) -> str:
        if blocked is None:
            agents = "unknown"
        elif not blocked:
            agents = "none" if robots == "ok" else "unknown"
        elif agent_limit is None or len(blocked) <= agent_limit:
            agents = ", ".join(blocked)
        elif agent_limit == 0:
            agents = f"{len(blocked)} agent tokens"
        else:
            agents = ", ".join(blocked[:agent_limit]) + f" (+{len(blocked) - agent_limit} more)"
        official_text = path if path and show_path else "listed" if path_listed else "none"
        full = url if show_url else "GET /v1/incidents/check on agenthelpnetwork.com"
        return (f"AHN {host}: recommendation={_code(rec.get('action'))} ({_code(rec.get('confidence'))}). "
                f"Declared policy: {robots}; blocked agents: {agents}; llms.txt: {llms}. "
                f"Official path: {official_text}. Provider status: {status}. "
                f"Reports: {_code(data.get('evidence_level'))}. Next: {', '.join(codes) or 'none'}. "
                f"Full JSON: {full}. "
                "Declared policy is not an access guarantee; bypassing site controls is not advised.")

    for options in ((6, True, True), (3, True, True), (0, True, True), (0, False, True), (0, False, False)):
        text = render(*options)
        if len(text) <= MAX_CONTEXT:
            return text
    tail = " Declared policy is not an access guarantee; bypassing site controls is not advised."
    return text[:MAX_CONTEXT - len(tail) - 1].rstrip() + "…" + tail


def run(raw: bytes, env=os.environ) -> tuple[dict | None, str]:
    """Return (hook output or None, debug note). Raises nothing the caller must handle besides bugs."""
    if env.get("AHN_HOOK_DISABLE") == "1":
        return None, "disabled"
    payload = json.loads(raw.decode("utf-8", errors="replace"))
    found = failure(payload)
    if not found:
        return None, "no failed web step with a hostname"
    host, error_class, stage = found
    if skip_host(host, env.get("AHN_HOOK_SKIP_HOSTS", "")):
        return None, "skipped host"
    if recently_seen(host):
        return None, f"{host} looked up within 10 minutes"
    url = lookup_url(host, error_class, stage)
    text = summary(host, fetch(url), url)
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


if __name__ == "__main__":
    sys.exit(main())
