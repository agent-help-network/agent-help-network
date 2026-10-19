"""Read official public status feeds for hosts that have actually been queried.

Statuspage has no universal hostname-to-page directory. Source mappings below are
verified publisher endpoints, never URLs assembled from a user's hostname.
"""

from __future__ import annotations

import os
import ipaddress
import json
import re
import socket
import ssl
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

import dns.exception
import dns.resolver
import httpx
import urllib3

from .db import connect
from .settings import Settings


class FeedFailure(ValueError):
    """A feed read that ended for a known reason; the label is stored as status_cache.last_error_detail."""

    def __init__(self, label: str):
        super().__init__(label)
        self.label = label


FAILURE_LABELS = frozenset({"too_large", "invalid_body", "dns_failed", "connect_error", "tls_error", "timeout",
                            "read_error", "no_cname", "id_mismatch", "not_public", "error"})
_DNS_HINTS = ("getaddrinfo", "name or service", "nodename nor servname", "name resolution", "no address associated")
_TLS_HINTS = ("ssl", "certificate", "tls")


def failure_label(exc: BaseException) -> str:
    """Short lowercase reason for a failed feed read: ``http_NNN`` or one of FAILURE_LABELS. Never raises."""
    if isinstance(exc, FeedFailure):
        return exc.label
    if isinstance(exc, httpx.HTTPStatusError):
        return f"http_{exc.response.status_code}"
    try:
        message = str(exc).lower()
    except Exception:  # a pathological __str__ must not turn a failed read into an unrecorded one
        message = ""
    if isinstance(exc, (urllib3.exceptions.NameResolutionError, socket.gaierror)):
        return "dns_failed"
    if isinstance(exc, (urllib3.exceptions.SSLError, ssl.SSLError)):
        return "tls_error"
    # urllib3's NewConnectionError inherits from its ConnectTimeoutError, so connection failures go before timeouts.
    if isinstance(exc, (httpx.ConnectError, urllib3.exceptions.NewConnectionError, ConnectionError)):
        if any(hint in message for hint in _DNS_HINTS):
            return "dns_failed"
        if any(hint in message for hint in _TLS_HINTS):
            return "tls_error"
        return "connect_error"
    if isinstance(exc, (httpx.TimeoutException, urllib3.exceptions.TimeoutError, TimeoutError)):
        return "timeout"
    if isinstance(exc, (httpx.HTTPError, urllib3.exceptions.ProtocolError, urllib3.exceptions.DecodeError)):
        return "read_error"
    if isinstance(exc, (json.JSONDecodeError, ValueError, TypeError, KeyError, RecursionError)):
        return "invalid_body"
    if isinstance(exc, OSError):
        return "connect_error"
    return "error"


@dataclass(frozen=True)
class Source:
    name: str
    page_url: str
    api_url: str
    kind: str = "statuspage"


SOURCES: dict[str, Source] = {
    "github.com": Source("GitHub Status", "https://www.githubstatus.com/", "https://www.githubstatus.com/api/v2/status.json"),
    "github.io": Source("GitHub Status (includes Pages)", "https://www.githubstatus.com/", "https://www.githubstatus.com/api/v2/status.json"),
    "openai.com": Source("OpenAI Status", "https://status.openai.com/", "https://status.openai.com/api/v2/status.json"),
    "chatgpt.com": Source("OpenAI Status", "https://status.openai.com/", "https://status.openai.com/api/v2/status.json"),
    "cloudflare.com": Source("Cloudflare Status", "https://www.cloudflarestatus.com/", "https://www.cloudflarestatus.com/api/v2/status.json"),
    "cloudflare.net": Source("Cloudflare Status", "https://www.cloudflarestatus.com/", "https://www.cloudflarestatus.com/api/v2/status.json"),
    "discord.com": Source("Discord Status", "https://discordstatus.com/", "https://discordstatus.com/api/v2/status.json"),
    "discord.gg": Source("Discord Status", "https://discordstatus.com/", "https://discordstatus.com/api/v2/status.json"),
    "discord.media": Source("Discord Status", "https://discordstatus.com/", "https://discordstatus.com/api/v2/status.json"),
    "discordapp.com": Source("Discord Status", "https://discordstatus.com/", "https://discordstatus.com/api/v2/status.json"),
    "reddit.com": Source("Reddit Status", "https://www.redditstatus.com/", "https://www.redditstatus.com/api/v2/status.json"),
    "twitch.tv": Source("Twitch Status", "https://status.twitch.com/", "https://status.twitch.com/api/v2/status.json"),
    "doctolib.fr": Source("Doctolib Status", "https://status.doctolib.com/", "https://status.doctolib.com/api/v2/status.json"),
    "doctolib.net": Source("Doctolib Status", "https://status.doctolib.com/", "https://status.doctolib.com/api/v2/status.json"),
    "wikimedia.org": Source("Wikimedia Status (major outages)", "https://www.wikimediastatus.net/", "https://www.wikimediastatus.net/api/v2/status.json"),
    "hcaptcha.com": Source("hCaptcha Status", "https://www.hcaptchastatus.com/", "https://www.hcaptchastatus.com/api/v2/status.json"),
    "vercel.app": Source("Vercel Status", "https://www.vercel-status.com/", "https://www.vercel-status.com/api/v2/status.json"),
    "vercel.com": Source("Vercel Status", "https://www.vercel-status.com/", "https://www.vercel-status.com/api/v2/status.json"),
    "akamai.net": Source("Akamai Status", "https://www.akamaistatus.com/", "https://www.akamaistatus.com/api/v2/status.json"),
    "calendly.com": Source("Calendly Status", "https://calendlystatus.com/", "https://calendlystatus.com/api/v2/status.json"),
    "godaddy.com": Source("GoDaddy Status", "https://status.godaddy.com/", "https://status.godaddy.com/api/v2/status.json"),
    "netlify.app": Source("Netlify Status", "https://www.netlifystatus.com/", "https://www.netlifystatus.com/api/v2/status.json"),
    "netlify.com": Source("Netlify Status", "https://www.netlifystatus.com/", "https://www.netlifystatus.com/api/v2/status.json"),
    "bugsnag.com": Source("BugSnag SaaS Status", "https://bugsnag.status.smartbear.com/", "https://bugsnag.status.smartbear.com/api/v2/status.json"),
    "surveymonkey.com": Source("SurveyMonkey Status", "https://surveymonkey.statuspage.io/", "https://surveymonkey.statuspage.io/api/v2/status.json"),
    "dropbox.com": Source("Dropbox Status", "https://status.dropbox.com/", "https://status.dropbox.com/api/v2/status.json"),
    "digitalocean.com": Source("DigitalOcean Status", "https://status.digitalocean.com/", "https://status.digitalocean.com/api/v2/status.json"),
    "shopify.com": Source("Shopify Status", "https://www.shopifystatus.com/", "https://www.shopifystatus.com/api/v2/status.json"),
    "tailscale.com": Source("Tailscale Status", "https://status.tailscale.com/", "https://status.tailscale.com/api/v2/status.json"),
    "render.com": Source("Render Status", "https://status.render.com/", "https://status.render.com/api/v2/status.json"),
    "claude.com": Source("Claude Status", "https://status.claude.com/", "https://status.claude.com/api/v2/status.json"),
    "claude.ai": Source("Claude Status", "https://status.claude.com/", "https://status.claude.com/api/v2/status.json"),
    "gmail.com": Source("Google Workspace Status Dashboard — Gmail", "https://www.google.com/appsstatus/dashboard/", "https://www.google.com/appsstatus/dashboard/incidents.json", "google_gmail"),
    "base.org": Source("Base Status (network and services)", "https://status.base.org/", "https://status.base.org/api/v2/status.json"),
    "doi.org": Source("DOI Foundation Status (resolution and website)", "https://doi.statuspage.io/", "https://doi.statuspage.io/api/v2/status.json"),
    "braze.com": Source("Braze Status", "https://braze.statuspage.io/", "https://braze.statuspage.io/api/v2/status.json"),
    "atlassian.net": Source("Atlassian Cloud Status", "https://status.atlassian.com/", "https://status.atlassian.com/api/v2/status.json"),
    # Added from the official-path host list; each page was read 2026-10-18 and its owner shown from the publisher's own domain or docs.
    "1password.com": Source("1Password Status", "https://status.1password.com/", "https://status.1password.com/api/v2/status.json"),
    "adjust.com": Source("Adjust Status", "https://status.adjust.com/", "https://status.adjust.com/api/v2/status.json"),
    "affirm.com": Source("Affirm Status", "https://status.affirm.com/", "https://status.affirm.com/api/v2/status.json"),
    "ai21.com": Source("AI21 Status", "https://status.ai21.com/", "https://status.ai21.com/api/v2/status.json"),
    "airtable.com": Source("Airtable Status", "https://status.airtable.com/", "https://status.airtable.com/api/v2/status.json"),
    "akamai.com": Source("Akamai Status", "https://www.akamaistatus.com/", "https://www.akamaistatus.com/api/v2/status.json"),
    "alchemy.com": Source("Alchemy Status", "https://status.alchemy.com/", "https://status.alchemy.com/api/v2/status.json"),
    "alpaca.markets": Source("Alpaca Status", "https://status.alpaca.markets/", "https://status.alpaca.markets/api/v2/status.json"),
    "amplitude.com": Source("Amplitude Status", "https://status.amplitude.com/", "https://status.amplitude.com/api/v2/status.json"),
    "anthropic.com": Source("Claude Status", "https://status.claude.com/", "https://status.claude.com/api/v2/status.json"),
    "appsflyer.com": Source("AppsFlyer Status", "https://status.appsflyer.com/", "https://status.appsflyer.com/api/v2/status.json"),
    "asana.com": Source("Asana Status", "https://status.asana.com/", "https://status.asana.com/api/v2/status.json"),
    "assemblyai.com": Source("AssemblyAI Status", "https://status.assemblyai.com/", "https://status.assemblyai.com/api/v2/status.json"),
    "atlassian.com": Source("Atlassian Cloud Status", "https://status.atlassian.com/", "https://status.atlassian.com/api/v2/status.json"),
    "authorize.net": Source("Authorize.Net Status", "https://status.authorize.net/", "https://status.authorize.net/api/v2/status.json"),
    "autodesk.com": Source("Autodesk Health", "https://health.autodesk.com/", "https://health.autodesk.com/api/v2/status.json"),
    "bigcommerce.com": Source("BigCommerce Status", "https://status.bigcommerce.com/", "https://status.bigcommerce.com/api/v2/status.json"),
    "bit.ly": Source("Bitly Status", "https://status.bitly.com/", "https://status.bitly.com/api/v2/status.json"),
    "bitbucket.org": Source("Bitbucket Status", "https://bitbucket.status.atlassian.com/", "https://bitbucket.status.atlassian.com/api/v2/status.json"),
    "bitly.com": Source("Bitly Status", "https://status.bitly.com/", "https://status.bitly.com/api/v2/status.json"),
    "bitstamp.net": Source("Bitstamp Status", "https://status.bitstamp.net/", "https://status.bitstamp.net/api/v2/status.json"),
    "box.com": Source("Box Status", "https://status.box.com/", "https://status.box.com/api/v2/status.json"),
    "brevo.com": Source("Brevo Status", "https://status.brevo.com/", "https://status.brevo.com/api/v2/status.json"),
    "brex.com": Source("Brex Status", "https://status.brex.com/", "https://status.brex.com/api/v2/status.json"),
    "browserbase.com": Source("Browserbase Status", "https://status.browserbase.com/", "https://status.browserbase.com/api/v2/status.json"),
    "cerebras.ai": Source("Cerebras Status", "https://status.cerebras.ai/", "https://status.cerebras.ai/api/v2/status.json"),
    "circleci.com": Source("CircleCI Status", "https://status.circleci.com/", "https://status.circleci.com/api/v2/status.json"),
    "clickup.com": Source("ClickUp Status", "https://status.clickup.com/", "https://status.clickup.com/api/v2/status.json"),
    "cohere.com": Source("Cohere Status", "https://status.cohere.com/", "https://status.cohere.com/api/v2/status.json"),
    "coinbase.com": Source("Coinbase Status", "https://status.coinbase.com/", "https://status.coinbase.com/api/v2/status.json"),
    "crates.io": Source("crates.io Status (Rust project status page)", "https://status.crates.io/", "https://status.crates.io/api/v2/status.json"),
    "criteo.com": Source("Criteo Status", "https://status.criteo.com/", "https://status.criteo.com/api/v2/status.json"),
    "crossref.org": Source("Crossref Status", "https://status.crossref.org/", "https://status.crossref.org/api/v2/status.json"),
    "crypto.com": Source("Crypto.com Status", "https://status.crypto.com/", "https://status.crypto.com/api/v2/status.json"),
    "cursor.com": Source("Cursor Status", "https://status.cursor.com/", "https://status.cursor.com/api/v2/status.json"),
    "datacite.org": Source("DataCite Status", "https://status.datacite.org/", "https://status.datacite.org/api/v2/status.json"),
    "deepgram.com": Source("Deepgram Status", "https://status.deepgram.com/", "https://status.deepgram.com/api/v2/status.json"),
    "dev.to": Source("DEV Community Status", "https://status.dev.to/", "https://status.dev.to/api/v2/status.json"),
    "digicert.com": Source("DigiCert Status", "https://status.digicert.com/", "https://status.digicert.com/api/v2/status.json"),
    "docker.com": Source("Docker Status", "https://www.dockerstatus.com/", "https://www.dockerstatus.com/api/v2/status.json"),
    "elastic.co": Source("Elastic Cloud Status", "https://status.elastic.co/", "https://status.elastic.co/api/v2/status.json"),
    "elevenlabs.io": Source("ElevenLabs Status", "https://status.elevenlabs.io/", "https://status.elevenlabs.io/api/v2/status.json"),
    "figma.com": Source("Figma Status", "https://status.figma.com/", "https://status.figma.com/api/v2/status.json"),
    "figshare.com": Source("Figshare Status", "https://status.figshare.com/", "https://status.figshare.com/api/v2/status.json"),
    "fireworks.ai": Source("Fireworks AI Status", "https://status.fireworks.ai/", "https://status.fireworks.ai/api/v2/status.json"),
    "flutterwave.com": Source("Flutterwave Status", "https://status.flutterwave.com/", "https://status.flutterwave.com/api/v2/status.json"),
    "fly.io": Source("Fly.io Status", "https://status.flyio.net/", "https://status.flyio.net/api/v2/status.json"),
    "freshbooks.com": Source("FreshBooks Status", "https://status.freshbooks.com/", "https://status.freshbooks.com/api/v2/status.json"),
    "gandi.net": Source("Gandi Status", "https://status.gandi.net/", "https://status.gandi.net/api/v2/status.json"),
    "gemini.com": Source("Gemini Exchange Status", "https://status.gemini.com/", "https://status.gemini.com/api/v2/status.json"),
    "giphy.com": Source("GIPHY Status", "https://status.giphy.com/", "https://status.giphy.com/api/v2/status.json"),
    "gocardless.com": Source("GoCardless Status", "https://www.gocardless-status.com/", "https://www.gocardless-status.com/api/v2/status.json"),
    "goshippo.com": Source("Shippo Status", "https://status.goshippo.com/", "https://status.goshippo.com/api/v2/status.json"),
    "grafana.com": Source("Grafana Cloud Status", "https://status.grafana.com/", "https://status.grafana.com/api/v2/status.json"),
    "greenhouse.io": Source("Greenhouse Status", "https://status.greenhouse.io/", "https://status.greenhouse.io/api/v2/status.json"),
    "hashicorp.com": Source("HashiCorp Status", "https://status.hashicorp.com/", "https://status.hashicorp.com/api/v2/status.json"),
    "hubspot.com": Source("HubSpot Status", "https://status.hubspot.com/", "https://status.hubspot.com/api/v2/status.json"),
    "ideogram.ai": Source("Ideogram Status", "https://status.ideogram.ai/", "https://status.ideogram.ai/api/v2/status.json"),
    "ifttt.com": Source("IFTTT Status", "https://status.ifttt.com/", "https://status.ifttt.com/api/v2/status.json"),
    "imgur.com": Source("Imgur Status", "https://status.imgur.com/", "https://status.imgur.com/api/v2/status.json"),
    "kit.com": Source("Kit Status", "https://status.kit.com/", "https://status.kit.com/api/v2/status.json"),
    "klarna.com": Source("Klarna Status", "https://status.klarna.com/", "https://status.klarna.com/api/v2/status.json"),
    "klaviyo.com": Source("Klaviyo Status", "https://status.klaviyo.com/", "https://status.klaviyo.com/api/v2/status.json"),
    "kraken.com": Source("Kraken Status", "https://status.kraken.com/", "https://status.kraken.com/api/v2/status.json"),
    "launchdarkly.com": Source("LaunchDarkly Status", "https://status.launchdarkly.com/", "https://status.launchdarkly.com/api/v2/status.json"),
    "lever.co": Source("Lever Status", "https://status.lever.co/", "https://status.lever.co/api/v2/status.json"),
    "linear.app": Source("Linear Status", "https://linearstatus.com/", "https://linearstatus.com/api/v2/status.json"),
    "linkedin.com": Source("LinkedIn Status", "https://www.linkedin-status.com/", "https://www.linkedin-status.com/api/v2/status.json"),
    "linode.com": Source("Akamai Cloud (Linode) Status", "https://status.linode.com/", "https://status.linode.com/api/v2/status.json"),
    "login.gov": Source("Login.gov Status", "https://status.login.gov/", "https://status.login.gov/api/v2/status.json"),
    "mailgun.com": Source("Mailgun Status", "https://status.mailgun.com/", "https://status.mailgun.com/api/v2/status.json"),
    "mailjet.com": Source("Mailjet Status", "https://status.mailjet.com/", "https://status.mailjet.com/api/v2/status.json"),
    "make.com": Source("Make Status", "https://status.make.com/", "https://status.make.com/api/v2/status.json"),
    "mapbox.com": Source("Mapbox Status", "https://status.mapbox.com/", "https://status.mapbox.com/api/v2/status.json"),
    "maven.org": Source("Maven Central Status", "https://status.maven.org/", "https://status.maven.org/api/v2/status.json"),
    "medium.com": Source("Medium Status", "https://status.medium.com/", "https://status.medium.com/api/v2/status.json"),
    "mercadopago.com": Source("Mercado Pago Status", "https://status.mercadopago.com/", "https://status.mercadopago.com/api/v2/status.json"),
    "mercury.com": Source("Mercury Status", "https://status.mercury.com/", "https://status.mercury.com/api/v2/status.json"),
    "miro.com": Source("Miro Status", "https://status.miro.com/", "https://status.miro.com/api/v2/status.json"),
    "mixpanel.com": Source("Mixpanel Status", "https://www.mixpanelstatus.com/", "https://www.mixpanelstatus.com/api/v2/status.json"),
    "monday.com": Source("monday.com Status", "https://status.monday.com/", "https://status.monday.com/api/v2/status.json"),
    "mongodb.com": Source("MongoDB Cloud Status (Atlas and cloud services)", "https://status.mongodb.com/", "https://status.mongodb.com/api/v2/status.json"),
    "monzo.com": Source("Monzo Status", "https://monzo.statuspage.io/", "https://monzo.statuspage.io/api/v2/status.json"),
    "newrelic.com": Source("New Relic Status", "https://status.newrelic.com/", "https://status.newrelic.com/api/v2/status.json"),
    "ngrok.com": Source("ngrok Status", "https://status.ngrok.com/", "https://status.ngrok.com/api/v2/status.json"),
    "notion.com": Source("Notion Status", "https://www.notion-status.com/", "https://www.notion-status.com/api/v2/status.json"),
    "notion.so": Source("Notion Status", "https://www.notion-status.com/", "https://www.notion-status.com/api/v2/status.json"),
    "ouraring.com": Source("Oura Status", "https://status.ouraring.com/", "https://status.ouraring.com/api/v2/status.json"),
    "patreon.com": Source("Patreon Status", "https://status.patreon.com/", "https://status.patreon.com/api/v2/status.json"),
    "pinecone.io": Source("Pinecone Status", "https://status.pinecone.io/", "https://status.pinecone.io/api/v2/status.json"),
    "pinterest.com": Source("Pinterest Status", "https://www.pintereststatus.com/", "https://www.pintereststatus.com/api/v2/status.json"),
    "plaid.com": Source("Plaid Status", "https://status.plaid.com/", "https://status.plaid.com/api/v2/status.json"),
    "plivo.com": Source("Plivo Status", "https://status.plivo.com/", "https://status.plivo.com/api/v2/status.json"),
    "postman.com": Source("Postman Status", "https://status.postman.com/", "https://status.postman.com/api/v2/status.json"),
    "pypi.org": Source("Python Infrastructure Status", "https://status.python.org/", "https://status.python.org/api/v2/status.json"),
    "robinhood.com": Source("Robinhood Status", "https://status.robinhood.com/", "https://status.robinhood.com/api/v2/status.json"),
    "runwayml.com": Source("Runway Status", "https://status.runwayml.com/", "https://status.runwayml.com/api/v2/status.json"),
    "sanity.io": Source("Sanity Status", "https://www.sanity-status.com/", "https://www.sanity-status.com/api/v2/status.json"),
    "scaleway.com": Source("Scaleway Status", "https://status.scaleway.com/", "https://status.scaleway.com/api/v2/status.json"),
    "sendgrid.com": Source("Twilio Status (includes SendGrid; status.sendgrid.com redirects here)", "https://status.twilio.com/", "https://status.twilio.com/api/v2/status.json"),
    "sentry.io": Source("Sentry Status", "https://status.sentry.io/", "https://status.sentry.io/api/v2/status.json"),
    "serpapi.com": Source("SerpApi Status", "https://status.serpapi.com/", "https://status.serpapi.com/api/v2/status.json"),
    "snowflake.com": Source("Snowflake Status", "https://status.snowflake.com/", "https://status.snowflake.com/api/v2/status.json"),
    "squarespace.com": Source("Squarespace Status", "https://status.squarespace.com/", "https://status.squarespace.com/api/v2/status.json"),
    "squareup.com": Source("Square Status", "https://issquareup.com/", "https://issquareup.com/api/v2/status.json"),
    "stability.ai": Source("Stability AI Status", "https://status.stability.ai/", "https://status.stability.ai/api/v2/status.json"),
    "strava.com": Source("Strava Status", "https://status.strava.com/", "https://status.strava.com/api/v2/status.json"),
    "supabase.com": Source("Supabase Status", "https://status.supabase.com/", "https://status.supabase.com/api/v2/status.json"),
    "tastytrade.com": Source("tastytrade Status", "https://status.tastytrade.com/", "https://status.tastytrade.com/api/v2/status.json"),
    "tavily.com": Source("Tavily Status", "https://status.tavily.com/", "https://status.tavily.com/api/v2/status.json"),
    "terraform.io": Source("HashiCorp Status", "https://status.hashicorp.com/", "https://status.hashicorp.com/api/v2/status.json"),
    "tomorrow.io": Source("Tomorrow.io Status", "https://status.tomorrow.io/", "https://status.tomorrow.io/api/v2/status.json"),
    "tosspayments.com": Source("TossPayments Status", "https://status.tosspayments.com/", "https://status.tosspayments.com/api/v2/status.json"),
    "trello.com": Source("Trello Status", "https://trello.status.atlassian.com/", "https://trello.status.atlassian.com/api/v2/status.json"),
    "twilio.com": Source("Twilio Status", "https://status.twilio.com/", "https://status.twilio.com/api/v2/status.json"),
    "typeform.com": Source("Typeform Status", "https://status.typeform.com/", "https://status.typeform.com/api/v2/status.json"),
    "unsplash.com": Source("Unsplash Status", "https://status.unsplash.com/", "https://status.unsplash.com/api/v2/status.json"),
    "vimeo.com": Source("Vimeo Status", "https://www.vimeostatus.com/", "https://www.vimeostatus.com/api/v2/status.json"),
    "wikidata.org": Source("Wikimedia Status (major outages)", "https://www.wikimediastatus.net/", "https://www.wikimediastatus.net/api/v2/status.json"),
    "wikipedia.org": Source("Wikimedia Status (major outages)", "https://www.wikimediastatus.net/", "https://www.wikimediastatus.net/api/v2/status.json"),
    "worldpay.com": Source("Worldpay Status", "https://status.worldpay.com/", "https://status.worldpay.com/api/v2/status.json"),
    "xero.com": Source("Xero Status", "https://status.xero.com/", "https://status.xero.com/api/v2/status.json"),
    "zapier.com": Source("Zapier Status", "https://status.zapier.com/", "https://status.zapier.com/api/v2/status.json"),
    "zoom.us": Source("Zoom Status", "https://www.zoomstatus.com/", "https://www.zoomstatus.com/api/v2/status.json"),
}
# Publisher-owned pages for which this service has not verified a compatible
# machine-readable status feed. A link must never be converted to an indicator.
OFFICIAL_STATUS_PAGES: dict[str, tuple[str, str]] = {
    "netflix.com": ("Netflix Service Status", "https://help.netflix.com/en/is-netflix-down"),
    "webex.com": ("Webex Global Service Status", "https://status.webex.com/"),
    "adobe.com": ("Adobe Status", "https://status.adobe.com/"),
    "brave.com": ("Brave Status", "https://status.brave.com/"),
    "cdn77.org": ("CDN77 Status", "https://client.cdn77.com/support/status"),
    "fastly.net": ("Fastly Status", "https://www.fastlystatus.com/"),
    "jetbrains.com": ("JetBrains Services Status", "https://status.jetbrains.com/"),
    "office.com": ("Microsoft 365 Service Health", "https://status.cloud.microsoft/"),
    "office365.com": ("Microsoft 365 Service Health", "https://status.cloud.microsoft/"),
    "okta.com": ("Okta Status", "https://status.okta.com/"),
    "oraclecloud.com": ("Oracle Cloud Infrastructure Status", "https://ocistatus.oraclecloud.com/"),
    "roblox.com": ("Roblox Status", "https://status.roblox.com/"),
    "salesforce.com": ("Salesforce Trust Status", "https://status.salesforce.com/"),
    "sophos.com": ("Sophos Central Status", "https://centralstatus.sophos.com/"),
    "stripe.com": ("Stripe System Status", "https://status.stripe.com/"),
    "trendmicro.com": ("Trend Micro Status", "https://status.trendmicro.com/"),
    "unity3d.com": ("Unity Services Status", "https://status.unity.com/"),
    "xbox.com": ("Xbox Network Status", "https://support.xbox.com/en-US/xbox-live-status"),
    "tradingview.com": ("TradingView Status", "https://status.tradingview.com/"),
    "zendesk.com": ("Zendesk Status", "https://status.zendesk.com/"),
    "hostgator.com.br": ("HostGator Brasil Status", "https://status.hostgator.com.br/"),
    "craigslist.org": ("Craigslist System Status", "https://www.craigslist.org/about/help/system-status"),
    "playstation.net": ("PlayStation Network Status", "https://status.playstation.com/"),
    # Added from the official-path host list; the page answered 200 but no compatible feed was found.
    "adobe.io": ("Adobe Status", "https://status.adobe.com/"),
    "adyen.com": ("Adyen Status", "https://status.adyen.com/"),
    "algolia.com": ("Algolia Status", "https://status.algolia.com/"),
    "alibabacloud.com": ("Alibaba Cloud Status", "https://status.alibabacloud.com/"),
    "aliyun.com": ("Alibaba Cloud (China) Status", "https://status.aliyun.com/"),
    "amazonaws.com": ("AWS Health Dashboard (AWS services)", "https://health.aws.amazon.com/health/status"),
    "apple.com": ("Apple System Status (Apple services)", "https://www.apple.com/support/systemstatus/"),
    "arxiv.org": ("arXiv Status", "https://status.arxiv.org/"),
    "auth0.com": ("Auth0 Status", "https://status.auth0.com/"),
    "azure.com": ("Azure Status (Azure services)", "https://azure.status.microsoft/en-us/status"),
    "bitwarden.com": ("Bitwarden Status", "https://status.bitwarden.com/"),
    "braintreepayments.com": ("Braintree Status", "https://www.paypal-status.com/product/production"),
    "bsky.app": ("Bluesky Status", "https://status.bsky.app/"),
    "civitai.com": ("Civitai Status", "https://status.civitai.com/status/public"),
    "codeberg.org": ("Codeberg Status", "https://status.codeberg.org/status/codeberg"),
    "coingecko.com": ("CoinGecko API Status", "https://status.coingecko.com/"),
    "databricks.com": ("Databricks Status", "https://status.databricks.com/"),
    "datadoghq.com": ("Datadog Status (US1 page)", "https://status.datadoghq.com/"),
    "deepinfra.com": ("DeepInfra Status", "https://status.deepinfra.com/"),
    "deepl.com": ("DeepL Status", "https://status.deepl.com/"),
    "deepseek.com": ("DeepSeek Status", "https://status.deepseek.com/"),
    "docusign.com": ("DocuSign Status", "https://health.docusign.com/status"),
    "duffel.com": ("Duffel Status", "https://www.duffelstatus.com/"),
    "easypost.com": ("EasyPost Status", "https://www.easypoststatus.com/"),
    "eventbrite.com": ("Eventbrite Status", "https://www.eventbritestatus.com/"),
    "exa.ai": ("Exa Status", "https://status.exa.ai/"),
    "facebook.com": ("Meta Status (business products and platform)", "https://metastatus.com/"),
    "fastmail.com": ("Fastmail Status", "https://fastmailstatus.com/"),
    "firecrawl.dev": ("Firecrawl Status", "https://status.firecrawl.dev/"),
    "fitbit.com": ("Fitbit Status", "https://status.healthapp.google.com/"),
    "freshworks.com": ("Freshworks Status", "https://status.freshworks.com/"),
    "garmin.com": ("Garmin Connect Status", "https://connect.garmin.com/status/"),
    "ghost.org": ("Ghost Status", "https://ghoststatus.org/"),
    "gitlab.com": ("GitLab Status", "https://status.gitlab.com/"),
    "gumroad.com": ("Gumroad Status", "https://status.gumroad.com/"),
    "here.com": ("HERE Status", "https://status.here.com/status"),
    "heroku.com": ("Heroku Status", "https://status.heroku.com/"),
    "hetzner.com": ("Hetzner Status", "https://status.hetzner.com/"),
    "huggingface.co": ("Hugging Face Status", "https://status.huggingface.co/"),
    "ibm.com": ("IBM Cloud Status (IBM Cloud services only)", "https://cloud.ibm.com/status"),
    "icloud.com": ("Apple System Status (Apple services)", "https://www.apple.com/support/systemstatus/"),
    "instagram.com": ("Meta Status (business products and platform)", "https://metastatus.com/"),
    "intuit.com": ("Intuit Status", "https://status.intuit.com/"),
    "joinmastodon.org": ("Mastodon Status", "https://status.joinmastodon.org/"),
    "jsdelivr.com": ("jsDelivr Status", "https://status.jsdelivr.com/"),
    "kagi.com": ("Kagi Status", "https://status.kagi.com/"),
    "lemonsqueezy.com": ("Lemon Squeezy Status", "https://status.lemonsqueezy.com/"),
    "letsencrypt.org": ("Let's Encrypt Status", "https://letsencrypt.status.io/"),
    "mailchimp.com": ("Mailchimp Status", "https://status.mailchimp.com/"),
    "mastodon.social": ("Mastodon Status", "https://status.joinmastodon.org/"),
    "microsoftonline.com": ("Microsoft 365 Service Health", "https://status.cloud.microsoft/"),
    "mollie.com": ("Mollie Status", "https://status.mollie.com/"),
    "neon.tech": ("Neon Status", "https://neonstatus.com/"),
    "nuget.org": ("NuGet Status", "https://status.nuget.org/"),
    "okx.com": ("OKX Status", "https://www.okx.com/en-us/status"),
    "openalex.org": ("OpenAlex Status", "https://status.openalex.org/"),
    "openrouter.ai": ("OpenRouter Status", "https://status.openrouter.ai/"),
    "outlook.com": ("Microsoft 365 Service Health", "https://status.cloud.microsoft/"),
    "packagist.org": ("Packagist Status", "https://status.packagist.org/"),
    "paddle.com": ("Paddle Status", "https://paddlestatus.com/"),
    "pagerduty.com": ("PagerDuty Status", "https://status.pagerduty.com/"),
    "paypal.com": ("PayPal Status", "https://www.paypal-status.com/product/production"),
    "pipedrive.com": ("Pipedrive Status", "https://status.pipedrive.com/"),
    "porkbun.com": ("Porkbun Status", "https://status.porkbun.com/"),
    "posthog.com": ("PostHog Status", "https://www.posthogstatus.com/"),
    "postmarkapp.com": ("Postmark Status", "https://status.postmarkapp.com/"),
    "railway.com": ("Railway Status", "https://status.railway.com/"),
    "razorpay.com": ("Razorpay Status", "https://status.razorpay.com/"),
    "readthedocs.org": ("Read the Docs Status", "https://status.readthedocs.com/"),
    "redis.io": ("Redis Cloud Status", "https://status.redis.io/"),
    "ringcentral.com": ("RingCentral Status", "https://status.ringcentral.com/"),
    "riotgames.com": ("Riot Games Service Status", "https://status.riotgames.com/"),
    "rubygems.org": ("RubyGems Status", "https://status.rubygems.org/"),
    "sharepoint.com": ("Microsoft 365 Service Health", "https://status.cloud.microsoft/"),
    "sinch.com": ("Sinch Status", "https://status.sinch.com/"),
    "slack.com": ("Slack Status", "https://slack-status.com/"),
    "splunk.com": ("Splunk Cloud Status", "https://www.splunk.com/en_us/products/system-status.html"),
    "themoviedb.org": ("TMDB Status", "https://status.themoviedb.org/"),
    "tiingo.com": ("Tiingo Status", "https://status.tiingo.com/"),
    "together.ai": ("Together AI Status", "https://status.together.ai/"),
    "tomtom.com": ("TomTom Status", "https://status.tomtom.com/csm"),
    "unity.com": ("Unity Services Status", "https://status.unity.com/"),
    "upstage.ai": ("Upstage Status", "https://status.upstage.ai/"),
    "whatsapp.com": ("Meta Status (business products and platform)", "https://metastatus.com/"),
    "withings.com": ("Withings Status", "https://status.withings.io/"),
}
GMAIL_PRODUCT_ID = "prPt2Yra2CbGsbEm9cpC"
INDICATORS = frozenset({"none", "minor", "major", "critical", "unknown"})
_SOURCE_LOCKS = {host: threading.Lock() for host in SOURCES}
_STATUSPAGE_CNAME = re.compile(r"^([a-z0-9]{8,32})\.stspg-customer\.com\.$")


def _statuspage_target(site_host: str) -> tuple[str, str] | None:
    """Only accept a first-party status subdomain delegated to Statuspage."""
    status_host = f"status.{site_host}"
    resolver = dns.resolver.Resolver()
    resolver.timeout = 1.0
    resolver.lifetime = 1.5
    try:
        answers = resolver.resolve(status_host, "CNAME")
    except (dns.resolver.NXDOMAIN, dns.resolver.NoAnswer):
        return None  # the name does not exist or carries no CNAME: not delegated
    except (dns.exception.DNSException, OSError) as exc:
        # SERVFAIL, timeout, no reachable nameserver: the resolver failed, which says nothing about delegation.
        raise FeedFailure("dns_failed") from exc
    targets = {str(answer.target).lower() for answer in answers}
    if len(targets) != 1:
        return None
    target = targets.pop()
    match = _STATUSPAGE_CNAME.fullmatch(target)
    return (target.rstrip("."), match.group(1)) if match else None


def _read_discovered_statuspage(site_host: str) -> tuple[Source, str, datetime | None]:
    """Fetch a publisher-owned Statuspage endpoint using a pinned public IP."""
    target = _statuspage_target(site_host)
    if target is None:
        raise FeedFailure("no_cname")
    cname, page_id = target
    status_host = f"status.{site_host}"
    addresses = {item[4][0] for item in socket.getaddrinfo(cname, 443, type=socket.SOCK_STREAM)}
    if not addresses or len(addresses) > 16 or any(not ipaddress.ip_address(ip).is_global for ip in addresses):
        raise FeedFailure("not_public")
    pool = urllib3.HTTPSConnectionPool(
        sorted(addresses)[0], port=443, server_hostname=status_host,
        assert_hostname=status_host, timeout=urllib3.Timeout(connect=2.0, read=3.0), retries=False,
    )
    try:
        response = pool.request(
            "GET", "/api/v2/status.json", headers={"Host": status_host, "Accept": "application/json"},
            redirect=False, preload_content=False,
        )
        try:
            if response.status != 200:
                raise FeedFailure(f"http_{response.status}")
            body = response.read(32_769)
            if len(body) > 32_768:
                raise FeedFailure("too_large")
        finally:
            response.release_conn()
    finally:
        pool.close()
    payload = json.loads(body)
    page = payload.get("page") if isinstance(payload, dict) else None
    if not isinstance(page, dict) or page.get("id") != page_id:
        raise FeedFailure("id_mismatch")
    name = page.get("name")
    if not isinstance(name, str) or not 1 <= len(name) <= 72:
        raise FeedFailure("invalid_body")
    indicator, updated_at = parse_status(payload)
    source = Source(f"{name} Status", f"https://{status_host}/", f"https://{status_host}/api/v2/status.json", "discovered_statuspage")
    return source, indicator, updated_at


def discover_source(site_host: str) -> tuple[Source, str, datetime | None] | None:
    """Bounded best-effort discovery; unknown hosts remain unknown."""
    try:
        return _read_discovered_statuspage(site_host)
    except (ValueError, OSError, urllib3.exceptions.HTTPError, json.JSONDecodeError, RecursionError):
        return None


def parse_status(body: dict) -> tuple[str, datetime | None]:
    """Parse an Atlassian Statuspage page-level status response."""
    if not isinstance(body, dict) or not isinstance(body.get("status"), dict):
        raise ValueError("invalid provider status response")
    indicator = body["status"].get("indicator")
    if indicator not in INDICATORS:
        raise ValueError("unknown provider indicator")
    page = body.get("page")
    if page is not None and not isinstance(page, dict):
        raise ValueError("invalid provider page")
    raw_time = (page or {}).get("updated_at")
    updated_at = None
    if raw_time is not None:
        if not isinstance(raw_time, str):
            raise ValueError("invalid provider update time")
        updated_at = datetime.fromisoformat(raw_time.replace("Z", "+00:00"))
        if updated_at.tzinfo is None:
            raise ValueError("provider time has no timezone")
        updated_at = updated_at.astimezone(timezone.utc)
    return indicator, updated_at


def parse_google_gmail(body: list) -> tuple[str, datetime | None]:
    """Interpret only active Gmail incidents in Google's official history feed."""
    if not isinstance(body, list):
        raise ValueError("invalid Google Workspace incident feed")
    active: list[tuple[str, datetime | None]] = []
    severity = {"SERVICE_INFORMATION": "minor", "SERVICE_DISRUPTION": "major", "SERVICE_OUTAGE": "critical"}
    for incident in body:
        if not isinstance(incident, dict):
            raise ValueError("invalid Google Workspace incident")
        products = incident.get("affected_products")
        if not isinstance(products, list):
            raise ValueError("missing Google Workspace affected products")
        if not any(isinstance(product, dict) and product.get("id") == GMAIL_PRODUCT_ID for product in products):
            continue
        if incident.get("end"):
            continue
        raw_begin = incident.get("begin")
        if not isinstance(raw_begin, str):
            raise ValueError("missing Gmail incident start")
        begin = datetime.fromisoformat(raw_begin.replace("Z", "+00:00"))
        if begin.tzinfo is None:
            raise ValueError("Gmail incident start has no timezone")
        if begin > datetime.now(timezone.utc):
            continue
        impact = incident.get("status_impact")
        if impact not in severity:
            raise ValueError("unknown Gmail incident impact")
        raw_time = incident.get("modified") or incident.get("begin")
        updated_at = None
        if raw_time is not None:
            if not isinstance(raw_time, str):
                raise ValueError("invalid Gmail incident time")
            updated_at = datetime.fromisoformat(raw_time.replace("Z", "+00:00"))
            if updated_at.tzinfo is None:
                raise ValueError("Gmail incident time has no timezone")
        active.append((severity[impact], updated_at))
    if not active:
        return "none", None
    order = {"minor": 0, "major": 1, "critical": 2}
    indicator = max((item[0] for item in active), key=order.__getitem__)
    updated_times = [item[1] for item in active if item[1] is not None]
    return indicator, max(updated_times) if updated_times else None


def register_source(conn, site_host: str, source: Source | None = None) -> bool:
    """Persist a verified mapping only when a user looks up its host."""
    source = source or SOURCES.get(site_host)
    if source is None:
        row = conn.execute(
            "SELECT status_page_url,api_url,enabled FROM status_sources WHERE site_host=%s",
            (site_host,),
        ).fetchone()
        page_url = f"https://status.{site_host}/"
        return bool(row and row["enabled"] and row["status_page_url"] == page_url and row["api_url"] == page_url + "api/v2/status.json")
    row = conn.execute(
        """INSERT INTO status_sources (site_host,source_name,status_page_url,api_url)
           VALUES (%s,%s,%s,%s) ON CONFLICT (site_host) DO UPDATE SET
           source_name=EXCLUDED.source_name,status_page_url=EXCLUDED.status_page_url,
           api_url=EXCLUDED.api_url RETURNING enabled""",
        (site_host, source.name, source.page_url, source.api_url),
    ).fetchone()
    return bool(row["enabled"])


def _read_source(source: Source, client: httpx.Client) -> tuple[str, datetime | None]:
    if source.kind == "discovered_statuspage":
        host = urlparse(source.page_url).hostname
        if not host or not host.startswith("status."):
            raise FeedFailure("error")
        _, indicator, updated_at = _read_discovered_statuspage(host[7:])
        return indicator, updated_at
    # Follow no redirects, stream with a hard byte cap, and never send credentials.
    cap = 1_048_576 if source.kind == "google_gmail" else 32_768
    with client.stream("GET", source.api_url, headers={"Accept": "application/json"}) as response:
        if response.status_code != 200:
            raise FeedFailure(f"http_{response.status_code}")
        data = bytearray()
        for chunk in response.iter_bytes():
            data.extend(chunk)
            if len(data) > cap:
                raise FeedFailure("too_large")
    body = httpx.Response(200, content=bytes(data)).json()
    return parse_google_gmail(body) if source.kind == "google_gmail" else parse_status(body)


def _cached_ok(settings: Settings, site_host: str) -> bool | None:
    with connect(settings) as conn:
        row = conn.execute("SELECT fetched_at,last_error_at FROM status_cache WHERE site_host=%s", (site_host,)).fetchone()
    if row is None:
        return None
    return bool(row["fetched_at"] and (not row["last_error_at"] or row["fetched_at"] > row["last_error_at"]))


def refresh_one(settings: Settings, site_host: str, *, client: httpx.Client | None = None, min_interval: int = 15,
                wait: bool = False) -> bool:
    """Fetch one verified source now; retain prior data as stale on failure.

    Request-path callers leave wait=False: when another thread is already
    fetching this host, they return the cached verdict instead of queueing
    on the per-host lock. Only a host with no cache row yet waits, so the
    first lookup still answers fresh. The cron passes wait=True.
    """
    source = SOURCES.get(site_host)
    if source is None:
        with connect(settings) as conn:
            row = conn.execute(
                "SELECT source_name,status_page_url,api_url FROM status_sources WHERE site_host=%s AND enabled=true",
                (site_host,),
            ).fetchone()
        expected_page = f"https://status.{site_host}/"
        if row and row["status_page_url"] == expected_page and row["api_url"] == expected_page + "api/v2/status.json":
            source = Source(row["source_name"], row["status_page_url"], row["api_url"], "discovered_statuspage")
    if source is None:
        return False
    lock = _SOURCE_LOCKS.setdefault(site_host, threading.Lock())
    if not lock.acquire(blocking=wait):
        cached = _cached_ok(settings, site_host)
        if cached is not None:
            return cached
        lock.acquire()
    try:
        if min_interval:
            with connect(settings) as conn:
                previous = conn.execute(
                    "SELECT fetched_at,last_error_at FROM status_cache WHERE site_host=%s",
                    (site_host,),
                ).fetchone()
            if previous:
                fetched_at, error_at = previous["fetched_at"], previous["last_error_at"]
                latest = max((t for t in (fetched_at, error_at) if t is not None), default=None)
                if latest and latest >= datetime.now(timezone.utc) - timedelta(seconds=min_interval):
                    return bool(fetched_at and (not error_at or fetched_at > error_at))
        own_client = client is None
        client = client or httpx.Client(timeout=4.0, follow_redirects=False, trust_env=False)
        try:
            indicator, updated_at = _read_source(source, client)
            with connect(settings) as conn:
                conn.execute(
                    """INSERT INTO status_cache (site_host,indicator,source_updated_at,fetched_at,last_error_at,last_error_detail)
                       VALUES (%s,%s,%s,now(),NULL,NULL) ON CONFLICT (site_host) DO UPDATE SET
                       indicator=EXCLUDED.indicator,source_updated_at=EXCLUDED.source_updated_at,
                       fetched_at=EXCLUDED.fetched_at,last_error_at=NULL,last_error_detail=NULL""",
                    (site_host, indicator, updated_at),
                )
            return True
        except (httpx.HTTPError, urllib3.exceptions.HTTPError, OSError, ValueError, TypeError, KeyError, json.JSONDecodeError, RecursionError) as exc:
            # Keep why the read failed: a bare "stale" would show a 403 from the feed and a timeout as the same word.
            with connect(settings) as conn:
                conn.execute(
                    """INSERT INTO status_cache (site_host,indicator,fetched_at,last_error_at,last_error_detail)
                       VALUES (%s,'unknown',NULL,now(),%s) ON CONFLICT (site_host) DO UPDATE SET
                       last_error_at=now(),last_error_detail=EXCLUDED.last_error_detail""",
                    (site_host, failure_label(exc)),
                )
            return False
        finally:
            if own_client:
                client.close()
    finally:
        lock.release()


def refresh_batch(settings: Settings, *, client: httpx.Client | None = None, limit: int = 20) -> dict:
    """Periodically refresh only sources registered by an actual lookup."""
    if not 1 <= limit <= 20:
        raise ValueError("limit must be 1-20")
    with connect(settings) as conn:
        rows = conn.execute(
            """SELECT s.site_host FROM status_sources s
               JOIN queried_hosts q ON q.site_host=s.site_host
               LEFT JOIN status_cache c ON c.site_host=s.site_host
               WHERE s.enabled=true AND
                 COALESCE(GREATEST(c.fetched_at,c.last_error_at),'-infinity'::timestamptz)
                   < now() - interval '1 hour'
               ORDER BY GREATEST(c.fetched_at,c.last_error_at) NULLS FIRST,s.site_host LIMIT %s""",
            (limit,),
        ).fetchall()
    result = {"checked": 0, "updated": 0, "failed": 0}
    for row in rows:
        result["checked"] += 1
        if refresh_one(settings, row["site_host"], client=client, wait=True):
            result["updated"] += 1
        else:
            result["failed"] += 1
    return result


def main() -> None:
    database_url = os.environ.get("AHN_DATABASE_URL", "")
    if not database_url:
        raise RuntimeError("AHN_DATABASE_URL is required")
    settings = Settings(database_url, "unused-by-status-refresh")
    result = refresh_batch(settings)
    print(f"status_refresh checked={result['checked']} updated={result['updated']} failed={result['failed']}")


if __name__ == "__main__":
    main()
