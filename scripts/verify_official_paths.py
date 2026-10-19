"""Live re-verification of the curated official-path table (ahn/official_paths.py).

Read-only curation aid; not run in CI. Each distinct URL in the table (api_docs, developer_portal and
any https URL inside a note) gets one HTTPS GET with a 10 s timeout. Redirects are followed by hand so
every hop is rate limited (at most one request per second overall) and the final hostname can be checked
against the publisher: it must share the registrable domain of the table key, of the listed URL, or of a
domain declared in PUBLISHER_DOMAINS below. Nothing else is fetched (no robots.txt, no crawling).

A URL passes only with final status 200, an https final URL, a publisher-owned final host, no redirect to
a sign-in page or bot challenge and no "not found" page title. Transport errors, 429 and 5xx get one retry.
Results are "ok", "blocked:*" (401/403/429/WAF 202 or a challenge page: the publisher refused this automated
check, which is not evidence that the page is gone) or another failure code. Exit code 1 when any URL is
not ok.

    python scripts/verify_official_paths.py                 # whole table
    python scripts/verify_official_paths.py --hosts a.com,b.org --json work/official-paths.json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ahn.official_paths import OFFICIAL_PATHS  # noqa: E402
from ahn.service import PUBLIC_SUFFIXES_2  # noqa: E402

USER_AGENT = "AgentHelpNetwork-curation/0.1 (+https://agenthelpnetwork.com/en/policy)"
MIN_INTERVAL = 1.0
TIMEOUT = 10.0
MAX_HOPS = 5
TITLE_CAP = 65_536
# Shared hosting suffixes: a.github.io and b.github.io are different owners.
PRIVATE_SUFFIXES = frozenset({"github.io", "gitbook.io", "readthedocs.io", "netlify.app", "vercel.app", "pages.dev"})
# Other domains that belong to the same publisher as the table key (docs hosts, parent companies,
# the publisher's own organisation on GitHub). Every cross-domain URL in the table must be declared here.
PUBLISHER_DOMAINS: dict[str, set[str]] = {
    "bitbucket.org": {"atlassian.com"}, "chatgpt.com": {"openai.com"}, "youtube.com": {"google.com"},
    "twitter.com": {"x.com"}, "stackoverflow.com": {"stackapps.com", "stackexchange.com"},
    "stackexchange.com": {"stackapps.com"}, "wikipedia.org": {"mediawiki.org"}, "wikimedia.org": {"mediawiki.org"},
    "amazon.com": {"developer-docs.amazon"},  # Amazon's .amazon brand TLD
    "amazonaws.com": {"amazon.com", "aws.com"}, "shopify.com": {"shopify.dev"}, "slack.com": {"slack.dev"},
    "whatsapp.com": {"facebook.com"}, "notion.so": {"notion.com"}, "atlassian.net": {"atlassian.com"},
    "trello.com": {"atlassian.com"}, "azure.com": {"microsoft.com"}, "linkedin.com": {"microsoft.com"},
    "instagram.com": {"facebook.com"}, "sendgrid.com": {"twilio.com"},
    "ycombinator.com": {"github.com"},  # official HackerNews organisation repository
    "npmjs.com": {"github.com"},  # npm is part of GitHub; registry docs live in the npm organisation
    "steampowered.com": {"steamcommunity.com", "steamgames.com"},
    "medium.com": {"github.com"},  # official Medium organisation repository
    "dev.to": {"forem.com"},
    "bybit.com": {"bybit-exchange.github.io"},  # Bybit's own GitHub Pages site
    "polygon.io": {"massive.com"},  # Polygon.io rebranded as Massive
    "line.me": {"line.biz"}, "expedia.com": {"expediagroup.com"}, "sam.gov": {"gsa.gov"},
    "regulations.gov": {"gsa.gov"}, "medicare.gov": {"cms.gov"}, "service.gov.uk": {"api.gov.uk", "hmrc.gov.uk"},
    "gov.kr": {"data.go.kr"}, "klm.com": {"airfranceklm.com"}, "airfrance.com": {"airfranceklm.com"},
    "hotels.com": {"expediagroup.com"}, "vrbo.com": {"expediagroup.com"}, "ubereats.com": {"uber.com"},
    "venmo.com": {"paypal.com"}, "braintreepayments.com": {"paypal.com"}, "mercadopago.com": {"mercadopago.com.ar"},
    "livenation.com": {"ticketmaster.com"}, "mercadolibre.com": {"mercadolibre.com.ar"},
    "woocommerce.com": {"woocommerce.github.io"},  # WooCommerce's own GitHub Pages site
    "deliveroo.co.uk": {"deliveroo.com"}, "threads.net": {"facebook.com"}, "threads.com": {"facebook.com"},
    "bsky.app": {"bsky.network"},  # Bluesky's protocol services domain
    "mastodon.social": {"joinmastodon.org"}, "snapchat.com": {"snap.com"}, "blogger.com": {"google.com"},
    "blogspot.com": {"google.com"}, "daum.net": {"kakao.com"}, "blizzard.com": {"battle.net"},
    "steamcommunity.com": {"steamgames.com"}, "apnews.com": {"ap.org"}, "bing.com": {"microsoft.com"},
    "gmail.com": {"google.com"}, "outlook.com": {"microsoft.com"}, "live.com": {"microsoft.com"},
    "office.com": {"microsoft.com"}, "sharepoint.com": {"microsoft.com"}, "microsoftonline.com": {"microsoft.com"},
    "icloud.com": {"apple.com"}, "ncloud.com": {"ncloud-docs.com"}, "linode.com": {"akamai.com"},
    "hetzner.com": {"hetzner.cloud"}, "terraform.io": {"hashicorp.com"},
    "launchpad.net": {"ubuntu.com"},  # Canonical
    "readthedocs.org": {"readthedocs.com"}, "adobe.io": {"adobe.com"}, "canva.com": {"canva.dev"},
    "crates.io": {"rust-lang.org"}, "nuget.org": {"microsoft.com"}, "maven.org": {"sonatype.org"},
    "1password.com": {"1password.dev"}, "springer.com": {"springernature.com"}, "nature.com": {"springernature.com"},
    "sciencedirect.com": {"elsevier.com"}, "google-analytics.com": {"google.com"}, "lg.com": {"lge.com"},
    "xiaomi.com": {"mi.com"}, "qq.com": {"tencent.com"}, "wechat.com": {"qq.com"}, "anthropic.com": {"claude.com"},
    "claude.ai": {"claude.com"}, "deepmind.google": {"google.dev"}, "llama.com": {"meta.ai"},
    "moonshot.ai": {"kimi.ai"}, "youtu.be": {"google.com"}, "wa.me": {"facebook.com"}, "t.me": {"telegram.org"},
    "discord.gg": {"discord.com"}, "bit.ly": {"bitly.com"},
    "mychart.com": {"epic.com"},
    "googleapis.com": {"google.com"},
    "googletagmanager.com": {"google.com"},
    "doubleclick.net": {"google.com"},
    "googlesyndication.com": {"google.com"},
    "googleadservices.com": {"google.com"},
    "crashlytics.com": {"google.com"},
    "google.de": {"google.com"},
    "google.co.uk": {"google.com"},
    "google.com.hk": {"google.com"},
    "google.com.br": {"google.com"},
    "g.co": {"google.com"},
    "youtube-nocookie.com": {"google.com"},
    "office365.com": {"microsoft.com"},
    "visualstudio.com": {"microsoft.com"},
    "adnxs.com": {"microsoft.com"},
    "amazon-adsystem.com": {"amazon.com"},
    "amazonalexa.com": {"amazon.com"},
    "fb.com": {"facebook.com"},
    "facebook.net": {"facebook.com"},
    "t.co": {"x.com"},
    "telegram.me": {"telegram.org"},
    "discordapp.com": {"discord.com"},
    "zoom.com": {"zoom.us"},
    "wp.com": {"wordpress.com"},
    "githubusercontent.com": {"github.com"},
    "docker.io": {"docker.com"},
    "jsdelivr.net": {"jsdelivr.com"},
    "cloudfront.net": {"amazon.com", "aws.com"},
    "cloudflare-dns.com": {"cloudflare.com"},
    "fastly.net": {"fastly.com"},
    "oraclecloud.com": {"oracle.com"},
    "aliyuncs.com": {"alibabacloud.com"},
    "myqcloud.com": {"tencentcloud.com"},
    "tencent.com": {"tencentcloud.com"},
    "meraki.com": {"cisco.com"},
    "opendns.com": {"cisco.com"},
    "paloaltonetworks.com": {"pan.dev"},
    "ovh.net": {"ovh.com", "ovhcloud.com"},
    "merriam-webster.com": {"dictionaryapi.com"},
    "amazon.co.uk": {"amazon.com", "developer-docs.amazon"},
    "amazon.de": {"amazon.com", "developer-docs.amazon"},
    "amazon.fr": {"amazon.com", "developer-docs.amazon"},
    "amazon.es": {"amazon.com", "developer-docs.amazon"},
    "amazon.it": {"amazon.com", "developer-docs.amazon"},
    "amazon.ca": {"amazon.com", "developer-docs.amazon"},
    "amazon.in": {"amazon.com", "developer-docs.amazon"},
    "amazon.co.jp": {"amazon.com", "developer-docs.amazon"},
    "amazon.com.br": {"amazon.com", "developer-docs.amazon"},
    "amazon.com.au": {"amazon.com", "developer-docs.amazon"},
    "amazon.co.za": {"amazon.com", "developer-docs.amazon"},
    "gcdn.co": {"gcore.com"},
    "b-cdn.net": {"bunny.net"},
    "myfritz.net": {"fritz.com"},
    "list-manage.com": {"mailchimp.com"},
    "adsrvr.org": {"thetradedesk.com"},
    "yandexcloud.net": {"yandex.cloud"},
    "sberbank.ru": {"sber.ru"},
    "bitrix24.ru": {"bitrix24.com"},
    "mercadolibre.com.ar": {"mercadolibre.com"},
    "supercell.com": {"brawlstars.com", "clashofclans.com"},
    "mega.co.nz": {"mega.io"},
    "ok.ru": {"apiok.ru"},
    "ya.ru": {"yandex.ru"},
    "vkontakte.ru": {"vk.com"},
    "twimg.com": {"x.com"},
    "cdninstagram.com": {"facebook.com"},
    "fbcdn.net": {"facebook.com"},
    "whatsapp.net": {"facebook.com"},
    "licdn.com": {"linkedin.com", "microsoft.com"},
    "ytimg.com": {"google.com"},
    "googlevideo.com": {"google.com"},
    "ggpht.com": {"google.com"},
    "googleusercontent.com": {"google.com"},
    "scdn.co": {"spotify.com"},
    "spotifycdn.com": {"spotify.com"},
    "ttvnw.net": {"twitch.tv"},
    "live-video.net": {"twitch.tv"},
    "tiktokcdn.com": {"tiktok.com"},
    "tiktokv.com": {"tiktok.com"},
    "rbxcdn.com": {"roblox.com"},
    "pvp.net": {"riotgames.com"},
    "steamstatic.com": {"steamcommunity.com", "steamgames.com", "steampowered.com"},
    "steamserver.net": {"steamcommunity.com", "steamgames.com", "steampowered.com"},
    "mzstatic.com": {"apple.com"},
    "icloud-content.com": {"apple.com"},
    "elasticbeanstalk.com": {"amazon.com"},
    "pinimg.com": {"pinterest.com"},
    "no-ip.com": {"noip.com"},
    "vk.ru": {"vk.com"},
    "otto.de": {"otto.market"},
    "shopee.com.br": {"shopee.com"},
    "shopee.co.id": {"shopee.com"},
}
TITLE = re.compile(rb"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)
NOT_FOUND = re.compile(r"\b(404|not found|page not found|does not exist|no longer available)\b", re.IGNORECASE)
BOT_WALL = re.compile(r"pardon our interruption|just a moment\.\.\.|attention required|access denied|are you a robot|robot check|"
                      r"security check|captcha", re.IGNORECASE)
SITE_DOWN = re.compile(r"site ?down|temporarily unavailable", re.IGNORECASE)
SIGN_IN = re.compile(r"/(log-?in|sign-?in|sign_in|sso|oauth2?/authorize|authorize)(/|$)", re.IGNORECASE)
NOTE_URL = re.compile(r"https://[^\s)>,;\"']+")


def registrable(host: str) -> str:
    labels = host.lower().rstrip(".").split(".")
    if len(labels) >= 3 and (".".join(labels[-2:]) in PUBLIC_SUFFIXES_2 or ".".join(labels[-2:]) in PRIVATE_SUFFIXES):
        return ".".join(labels[-3:])
    return ".".join(labels[-2:])


def table_urls(hosts: set[str] | None = None) -> dict[str, list[tuple[str, str]]]:
    """URL -> [(host, field)] for every URL in the table (optionally limited to some hosts)."""
    urls: dict[str, list[tuple[str, str]]] = {}
    for host, entry in OFFICIAL_PATHS.items():
        if hosts and host not in hosts:
            continue
        for field in ("api_docs", "developer_portal"):
            if entry[field]:
                urls.setdefault(entry[field], []).append((host, field))
        for match in NOTE_URL.findall(entry["note"] or ""):
            urls.setdefault(match.rstrip("."), []).append((host, "note"))
    return urls


class Fetcher:
    def __init__(self) -> None:
        self.client = httpx.Client(
            http2=False, follow_redirects=False, timeout=httpx.Timeout(TIMEOUT),
            headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml,*/*;q=0.8", "Accept-Language": "en"},
        )
        self.last = 0.0
        self.requests = 0

    def _get(self, url: str) -> tuple[int, str | None, str]:
        wait = MIN_INTERVAL - (time.monotonic() - self.last)
        if wait > 0:
            time.sleep(wait)
        self.last = time.monotonic()
        self.requests += 1
        with self.client.stream("GET", url) as response:
            location = response.headers.get("location")
            title = ""
            if response.status_code == 200:
                body = b""
                for chunk in response.iter_bytes():
                    body += chunk
                    if len(body) >= TITLE_CAP or b"</title>" in body.lower():
                        break
                found = TITLE.search(body[:TITLE_CAP])
                if found:
                    title = " ".join(found.group(1).decode("utf-8", "replace").split())[:120]
            return response.status_code, location, title

    def check(self, url: str, allowed: set[str]) -> dict:
        current, hops, status, title, error = url, 0, None, "", None
        retried = False
        while True:
            try:
                status, location, title = self._get(current)
            except httpx.HTTPError as exc:
                if not retried:
                    retried = True
                    continue
                error = type(exc).__name__
                break
            if status in {429, 500, 502, 503, 504} and not retried:
                retried = True
                continue
            if status in {301, 302, 303, 307, 308} and location and hops < MAX_HOPS:
                current, hops = urljoin(current, location), hops + 1
                continue
            break
        final = urlsplit(current)
        result = "ok"
        if error:
            result = f"error:{error}"
        elif status in {202, 401, 403, 429}:
            result = f"blocked:{status}"
        elif status != 200:
            result = f"status:{status}"
        elif final.scheme != "https":
            result = "not_https"
        elif registrable(final.hostname or "") not in allowed:
            result = f"foreign_host:{final.hostname}"
        elif "challenge" in final.path.lower() or (final.hostname or "").startswith("unblock.") or BOT_WALL.search(title):
            result = "blocked:challenge"
        elif current != url and SIGN_IN.search(final.path):
            result = "sign_in_page"
        elif NOT_FOUND.search(title) or SITE_DOWN.search(title):
            result = "not_found_title"
        return {"url": url, "final_url": current, "status": status, "hops": hops, "title": title, "result": result}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--hosts", help="comma-separated table keys to check (default: all)")
    parser.add_argument("--json", type=Path, help="write per-URL and per-host results to this file")
    args = parser.parse_args()
    hosts = {h.strip() for h in args.hosts.split(",") if h.strip()} if args.hosts else None
    urls = table_urls(hosts)
    fetcher = Fetcher()
    results: dict[str, dict] = {}
    undeclared: list[str] = []
    for index, (url, users) in enumerate(urls.items(), 1):
        allowed = {registrable(urlsplit(url).hostname or "")}
        for host, _ in users:
            declared = {registrable(host)} | PUBLISHER_DOMAINS.get(host, set())
            if registrable(urlsplit(url).hostname or "") not in declared:
                undeclared.append(f"{host}: {url}")
            allowed |= declared
        results[url] = fetcher.check(url, allowed)
        row = results[url]
        moved = f" -> {row['final_url']}" if row["final_url"] != url else ""
        print(f"[{index}/{len(urls)}] {row['result']:<14} {row['status']!s:<4} {url}{moved}", flush=True)
    per_host: dict[str, dict] = {}
    for url, users in urls.items():
        for host, field in users:
            item = per_host.setdefault(host, {"ok": True, "urls": []})
            item["urls"].append({"field": field, **results[url]})
            item["ok"] &= results[url]["result"] == "ok"
    failed = [url for url, row in results.items() if row["result"] != "ok"]
    blocked = [url for url in failed if results[url]["result"].startswith("blocked:")]
    print()
    print(f"{'host':<28} {'field':<16} {'result':<22} url")
    for host, item in sorted(per_host.items()):
        for row in item["urls"]:
            if row["result"] != "ok" or row["final_url"] != row["url"]:
                print(f"{host:<28} {row['field']:<16} {row['result']:<22} {row['url']} -> {row['final_url']}")
    for line in undeclared:
        print(f"undeclared publisher domain: {line}")
    hosts_ok = sum(item["ok"] for item in per_host.values())
    print(f"\nhosts: {len(per_host)} checked, {hosts_ok} all-ok, {len(per_host) - hosts_ok} with a failure; "
          f"urls: {len(results)} distinct, {len(results) - len(failed)} ok, {len(blocked)} blocked, {len(failed) - len(blocked)} failed; "
          f"requests: {fetcher.requests}")
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps({
            "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "user_agent": USER_AGENT, "hosts": per_host, "undeclared": undeclared,
        }, indent=2, ensure_ascii=False), encoding="utf-8")
    return 1 if failed or undeclared else 0


if __name__ == "__main__":
    raise SystemExit(main())
