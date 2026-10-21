"""Hostname extraction and the list of hosts that are never looked up.

Mirrors normalize_host and skip_host in plugins/claude-code/ahn/hooks/ahn_lookup.py;
tests/test_ahn_client.py checks the two stay in step. The client refuses a little more than the plugin does, for
inputs the plugin has not been given yet: cluster-internal TLDs (svc, consul, cluster), wildcard-DNS and
address-embedding names (nip.io, sslip.io, 10-0-0-1.example.com) and a URL with a backslash (two readings). The plugin
may adopt the same rules; until then tests/test_ahn_client_adapters.py states these differences. Nothing here
resolves a name.
"""

from __future__ import annotations

import ipaddress
import os
import re
import urllib.parse
from typing import Optional

OWN_HOST = "agenthelpnetwork.com"

HOST_LABEL = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
# Special-use and private-network names (RFC 6761/6762/8375, Tor, common LAN suffixes).
SKIP_TLDS = frozenset({"localhost", "local", "internal", "test", "lan", "arpa", "onion", "invalid", "example",
                       "home", "corp", "intranet", "private", "localdomain", "alt",
                       # Cluster-internal names: Kubernetes services (web.default.svc), Consul, cluster DNS.
                       "svc", "consul", "cluster"})
# Wildcard-DNS services that turn an address into a name (10.0.0.1.nip.io, 10-0-0-1.sslip.io): the name stands for an address.
ADDRESS_NAME_SUFFIXES = ("nip.io", "sslip.io", "xip.io")
# An IPv4 address written into a name with dots or dashes (192-168-1-5.example.com, ec2-10-0-0-1.compute.internal).
EMBEDDED_IPV4 = re.compile(r"(?<![0-9])[0-9]{1,3}([.-])[0-9]{1,3}\1[0-9]{1,3}\1[0-9]{1,3}(?![0-9])")
# Characters IDNA2003 (Python's "idna" codec) maps to a different domain than IDNA2008 (browsers) uses: faß.de would be
# looked up as fass.de. Such names are refused, as the Claude Code plugin does.
IDNA_DEVIATIONS = frozenset("\u00df\u03c2\u200c\u200d")


def normalize_host(value: object) -> Optional[str]:
    """A bare lowercase hostname from a URL or host string; None when there is none.

    Scheme, userinfo, port, path, query and fragment are dropped, as is one leading
    ``www.`` (the service reads declared policy for apex names only).
    """
    if not isinstance(value, str):
        return None
    value = value.strip()
    if not value:
        return None
    if "\\" in value:
        # Browsers read a backslash as a slash (https://evil.com\@good.com goes to evil.com); urlsplit does not (good.com).
        # With two readings possible, no host is looked up.
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
    labels = host.split(".")
    if not all(HOST_LABEL.fullmatch(label) for label in labels) or len(host) > 253:
        return None
    if len(labels) >= 3 and labels[0] == "www":
        host = ".".join(labels[1:])
    return host


def skip_host(host: str, extra: str = "") -> bool:
    """True for local, private, special-use, address-literal, address-embedding, single-label and own hosts."""
    labels = host.split(".")
    if len(labels) < 2:
        return True
    last = labels[-1]
    # Public TLDs are alphabetic or IDN; an all-digit or hex last label is an address
    # literal in inet_aton form (10.0.0.1, 127.1, 0x7f.1).
    if last in SKIP_TLDS or not (last.isalpha() or last.startswith("xn--")):
        return True
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        pass
    if EMBEDDED_IPV4.search(host):
        return True
    suffixes = [OWN_HOST, *ADDRESS_NAME_SUFFIXES] + [item.strip().lower().lstrip(".") for item in extra.split(",") if item.strip()]
    return any(host == suffix or host.endswith("." + suffix) for suffix in suffixes)


def eligible_host(value: object, env=None) -> Optional[str]:
    """The hostname to look up for ``value``, or None when nothing may be sent."""
    env = os.environ if env is None else env
    if env.get("AHN_CLIENT_DISABLE") == "1":
        return None
    host = normalize_host(value)
    if host is None or skip_host(host, env.get("AHN_CLIENT_SKIP_HOSTS", "")):
        return None
    return host
