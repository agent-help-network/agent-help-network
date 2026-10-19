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
    # Retry of the first batch's exclusions with scripts/discover_status_sources.py (2026-10-18); each shown by a same-domain page, the publisher's own status redirect, or a publisher documentation link.
    "canva.com": Source("Canva Connect APIs Status", "https://www.canvastatus.com/", "https://www.canvastatus.com/api/v2/status.json"),
    "etherscan.io": Source("Etherscan API Status", "https://status.etherscan.io/", "https://status.etherscan.io/api/v2/status.json"),
    "etsy.com": Source("Etsy Open API Status", "https://www.etsystatus.com/", "https://www.etsystatus.com/api/v2/status.json"),
    "groq.com": Source("Groq Status", "https://groqstatus.com/", "https://groqstatus.com/api/v2/status.json"),
    "intercom.com": Source("Fin Status (Intercom's status page)", "https://www.finstatus.com/", "https://www.finstatus.com/api/v2/status.json"),
    "line.me": Source("LINE Developers Status", "https://api.line-status.info/", "https://api.line-status.info/api/v2/status.json"),
    "llamaindex.ai": Source("LlamaIndex Docs Status", "https://llamaindex.statuspage.io/", "https://llamaindex.statuspage.io/api/v2/status.json"),
    "massive.com": Source("Massive Status (formerly Polygon.io)", "https://massive-status.com/", "https://massive-status.com/api/v2/status.json"),
    "npmjs.com": Source("npm registry Status", "https://status.npmjs.org/", "https://status.npmjs.org/api/v2/status.json"),
    "perplexity.ai": Source("Perplexity API Status", "https://status.perplexity.com/", "https://status.perplexity.com/api/v2/status.json"),
    "polygon.io": Source("Massive Status (formerly Polygon.io)", "https://massive-status.com/", "https://massive-status.com/api/v2/status.json"),
    "resend.com": Source("Resend Status", "https://resend-status.com/", "https://resend-status.com/api/v2/status.json"),
    "wise.com": Source("Wise Platform API Status", "https://status.wise.com/", "https://status.wise.com/api/v2/status.json"),
    "wix.com": Source("Wix Developers Status", "https://status.wix.com/", "https://status.wix.com/api/v2/status.json"),
    "yelp.com": Source("Yelp Fusion API Status", "https://status.developer.yelp.com/", "https://status.developer.yelp.com/api/v2/status.json"),
    # Seed hosts 547 onward, first pass with scripts/discover_status_sources.py (2026-10-19); each shown by a same-domain page or the publisher's own status redirect.
    "ancestry.com": Source("Ancestry Site Availability Status", "https://status.ancestry.com/", "https://status.ancestry.com/api/v2/status.json"),
    "anydesk.com": Source("AnyDesk Status", "https://status.anydesk.com/", "https://status.anydesk.com/api/v2/status.json"),
    "apache.org": Source("Apache Software Foundation Status", "https://status.apache.org/", "https://status.apache.org/api/v2/status.json"),
    "auvik.com": Source("Auvik Networks Inc. Status", "https://status.auvik.com/", "https://status.auvik.com/api/v2/status.json"),
    "branch.io": Source("Branch Status", "https://status.branch.io/", "https://status.branch.io/api/v2/status.json"),
    "chaturbate.com": Source("Chaturbate Status", "https://status.chaturbate.com/", "https://status.chaturbate.com/api/v2/status.json"),
    "checkpoint.com": Source("Check Point Services Status", "https://status.checkpoint.com/", "https://status.checkpoint.com/api/v2/status.json"),
    "cloudinary.com": Source("Cloudinary Status", "https://status.cloudinary.com/", "https://status.cloudinary.com/api/v2/status.json"),
    "conviva.com": Source("Conviva Status", "https://status.conviva.com/", "https://status.conviva.com/api/v2/status.json"),
    "cpanel.net": Source("cPanel Status", "https://status.cpanel.net/", "https://status.cpanel.net/api/v2/status.json"),
    "disqus.com": Source("Disqus Status", "https://status.disqus.com/", "https://status.disqus.com/api/v2/status.json"),
    "docker.io": Source("Docker Status", "https://www.dockerstatus.com/", "https://www.dockerstatus.com/api/v2/status.json"),
    "doubleverify.com": Source("Doubleverify DV Status", "https://status.doubleverify.com/", "https://status.doubleverify.com/api/v2/status.json"),
    "duolingo.com": Source("Duolingo Status", "https://status.duolingo.com/", "https://status.duolingo.com/api/v2/status.json"),
    "epicgames.com": Source("Epic Games Public Status", "https://status.epicgames.com/", "https://status.epicgames.com/api/v2/status.json"),
    "eset.com": Source("ESET Status", "https://status.eset.com/", "https://status.eset.com/api/v2/status.json"),
    "globalsign.com": Source("GMO GlobalSign Status", "https://status.globalsign.com/", "https://status.globalsign.com/api/v2/status.json"),
    "grammarly.com": Source("Grammarly Status", "https://status.grammarly.com/", "https://status.grammarly.com/api/v2/status.json"),
    "hotjar.com": Source("Hotjar Status", "https://status.hotjar.com/", "https://status.hotjar.com/api/v2/status.json"),
    "intercom.io": Source("Fin Status (Intercom's status page)", "https://www.finstatus.com/", "https://www.finstatus.com/api/v2/status.json"),
    "iso.org": Source("ISO Status", "https://status.iso.org/", "https://status.iso.org/api/v2/status.json"),
    "jotform.com": Source("Jotform Status", "https://status.jotform.com/", "https://status.jotform.com/api/v2/status.json"),
    "meraki.com": Source("Meraki Status", "https://status.meraki.net/", "https://status.meraki.net/api/v2/status.json"),
    "name.com": Source("Name.com Status", "https://status.name.com/", "https://status.name.com/api/v2/status.json"),
    "nexusmods.com": Source("Nexus Mods Status", "https://nexusmods.statuspage.io/", "https://nexusmods.statuspage.io/api/v2/status.json"),
    "onlyfans.com": Source("OnlyFans Status", "https://status.onlyfans.com/", "https://status.onlyfans.com/api/v2/status.json"),
    "optimizely.com": Source("Optimizely Service Status", "https://status.optimizely.com/", "https://status.optimizely.com/api/v2/status.json"),
    "oxylabs.io": Source("Oxylabs Network Status", "https://uptime.oxylabs.io/", "https://uptime.oxylabs.io/api/v2/status.json"),
    "paloaltonetworks.com": Source("Palo Alto Networks Cloud Services Status", "https://status.paloaltonetworks.com/", "https://status.paloaltonetworks.com/api/v2/status.json"),
    "pendo.io": Source("Pendo Status", "https://status.pendo.io/", "https://status.pendo.io/api/v2/status.json"),
    "python.org": Source("Python Infrastructure Status", "https://status.python.org/", "https://status.python.org/api/v2/status.json"),
    "qualtrics.com": Source("Qualtrics Status", "https://status.qualtrics.com/", "https://status.qualtrics.com/api/v2/status.json"),
    "redhat.com": Source("Red Hat Status", "https://status.redhat.com/", "https://status.redhat.com/api/v2/status.json"),
    "ring.com": Source("Ring.com Status", "https://status.ring.com/", "https://status.ring.com/api/v2/status.json"),
    "singular.net": Source("Singular Status", "https://status.singular.net/", "https://status.singular.net/api/v2/status.json"),
    "substack.com": Source("Substack Status", "https://substack.statuspage.io/", "https://substack.statuspage.io/api/v2/status.json"),
    "teamviewer.com": Source("TeamViewer Status", "https://status.teamviewer.com/", "https://status.teamviewer.com/api/v2/status.json"),
    "trustpilot.com": Source("Trustpilot Status", "https://status.trustpilot.com/", "https://status.trustpilot.com/api/v2/status.json"),
    "ui.com": Source("Ubiquiti System Status", "https://status.ui.com/", "https://status.ui.com/api/v2/status.json"),
    "wattpad.com": Source("Wattpad Status", "https://status.wattpad.com/", "https://status.wattpad.com/api/v2/status.json"),
    # Tranco ranks 1,001-2,000 (list of 2026-10-03), first pass with scripts/discover_status_sources.py (2026-10-19); each shown by a same-domain page or the publisher's own status redirect.
    "adroll.com": Source("NextRoll Status", "https://status.nextroll.com/", "https://status.nextroll.com/api/v2/status.json"),
    "archiveofourown.org": Source("Organization for Transformative Works Status", "https://www.otwstatus.org/", "https://www.otwstatus.org/api/v2/status.json"),
    "autotrader.co.uk": Source("Autotrader Status", "https://status.autotrader.co.uk/", "https://status.autotrader.co.uk/api/v2/status.json"),
    "bbb.org": Source("BBB System Status", "https://status.bbb.org/", "https://status.bbb.org/api/v2/status.json"),
    "classlink.com": Source("ClassLink Status", "https://status.classlink.com/", "https://status.classlink.com/api/v2/status.json"),
    "clever.com": Source("Clever Status", "https://status.clever.com/", "https://status.clever.com/api/v2/status.json"),
    "coursera.org": Source("Coursera Status", "https://status.coursera.org/", "https://status.coursera.org/api/v2/status.json"),
    "gofundme.com": Source("GoFundMe Pro Status", "https://status.gofundme.com/", "https://status.gofundme.com/api/v2/status.json"),
    "home.pl": Source("home.pl Status", "https://status.home.pl/", "https://status.home.pl/api/v2/status.json"),
    "hostinger.com": Source("Hostinger Status", "https://statuspage.hostinger.com/", "https://statuspage.hostinger.com/api/v2/status.json"),
    "id.me": Source("ID.me Status", "https://status.id.me/", "https://status.id.me/api/v2/status.json"),
    "instructure.com": Source("Instructure Status", "https://status.instructure.com/", "https://status.instructure.com/api/v2/status.json"),
    "kickstarter.com": Source("Kickstarter Status", "https://status.kickstarter.com/", "https://status.kickstarter.com/api/v2/status.json"),
    "one.com": Source("one.com Status", "https://status.one.com/", "https://status.one.com/api/v2/status.json"),
    "onesignal.com": Source("OneSignal Status", "https://status.onesignal.com/", "https://status.onesignal.com/api/v2/status.json"),
    "pbs.org": Source("PBS Public Status", "https://status.pbs.org/", "https://status.pbs.org/api/v2/status.json"),
    "plex.tv": Source("Plex Status", "https://status.plex.tv/", "https://status.plex.tv/api/v2/status.json"),
    "pusher.com": Source("Pusher Status", "https://status.pusher.com/", "https://status.pusher.com/api/v2/status.json"),
    "rapid7.com": Source("Rapid7 Status", "https://status.rapid7.com/", "https://status.rapid7.com/api/v2/status.json"),
    "tawk.to": Source("tawk.to Status", "https://status.tawk.to/", "https://status.tawk.to/api/v2/status.json"),
    "trustarc.com": Source("TrustArc Status", "https://status.trustarc.com/", "https://status.trustarc.com/api/v2/status.json"),
    "ubnt.com": Source("Ubiquiti System Status", "https://status.ui.com/", "https://status.ui.com/api/v2/status.json"),
    "ucsd.edu": Source("ITS Network & Systems Status", "https://status.ucsd.edu/", "https://status.ucsd.edu/api/v2/status.json"),
    "udemy.com": Source("Udemy Status", "https://status.udemy.com/", "https://status.udemy.com/api/v2/status.json"),
    # Tranco ranks 2,001-3,000 (list of 2026-10-03), first pass with scripts/discover_status_sources.py (2026-10-19); each shown by a same-domain page, the publisher's own status redirect or a publisher documentation link.
    "bazaarvoice.com": Source("Bazaarvoice Status", "https://status.bazaarvoice.com/", "https://status.bazaarvoice.com/api/v2/status.json"),
    "bunny.net": Source("bunny.net Status", "https://status.bunny.net/", "https://status.bunny.net/api/v2/status.json"),
    "callrail.com": Source("CallRail Status", "https://status.callrail.com/", "https://status.callrail.com/api/v2/status.json"),
    "colorado.edu": Source("University of Colorado Boulder Status", "https://status.colorado.edu/", "https://status.colorado.edu/api/v2/status.json"),
    "constantcontact.com": Source("Constant Contact Status", "https://status.constantcontact.com/", "https://status.constantcontact.com/api/v2/status.json"),
    "cookiebot.com": Source("Cookiebot Status", "https://status.cookiebot.com/", "https://status.cookiebot.com/api/v2/status.json"),
    "customer.io": Source("CustomerIO Status", "https://status.customerio.com/", "https://status.customerio.com/api/v2/status.json"),
    "datto.com": Source("Kaseya Status (Datto)", "https://status.kaseya.com/", "https://status.kaseya.com/api/v2/status.json"),
    "envato.com": Source("Envato Status", "https://status.envato.com/", "https://status.envato.com/api/v2/status.json"),
    "fullstory.com": Source("Fullstory Status", "https://status.fullstory.com/", "https://status.fullstory.com/api/v2/status.json"),
    "goguardian.com": Source("GoGuardian Status", "https://status.goguardian.com/", "https://status.goguardian.com/api/v2/status.json"),
    "intentiq.com": Source("Intent IQ Status", "https://status.intentiq.com/", "https://status.intentiq.com/api/v2/status.json"),
    "jwplayer.com": Source("JWX Status (JW Player)", "https://status.jwx.com/", "https://status.jwx.com/api/v2/status.json"),
    "khanacademy.org": Source("Khan Academy Status", "https://status.khanacademy.org/", "https://status.khanacademy.org/api/v2/status.json"),
    "libero.it": Source("Libero Mail Status", "https://status.libero.it/", "https://status.libero.it/api/v2/status.json"),
    "logitech.com": Source("Logitech Status", "https://status.logitech.com/", "https://status.logitech.com/api/v2/status.json"),
    "mathworks.com": Source("MathWorks Status", "https://status.mathworks.com/", "https://status.mathworks.com/api/v2/status.json"),
    "matterport.com": Source("Matterport Status", "https://status.matterport.com/", "https://status.matterport.com/api/v2/status.json"),
    "mindbox.ru": Source("Mindbox Status", "https://status.mindbox.ru/", "https://status.mindbox.ru/api/v2/status.json"),
    "moengage.com": Source("MoEngage Status", "https://status.moengage.com/", "https://status.moengage.com/api/v2/status.json"),
    "nodejs.org": Source("Node.js Status", "https://status.nodejs.org/", "https://status.nodejs.org/api/v2/status.json"),
    "npmjs.org": Source("Npmjs npm Status", "https://status.npmjs.org/", "https://status.npmjs.org/api/v2/status.json"),
    "optimove.net": Source("Optimove Service Status", "https://status.optimove.net/", "https://status.optimove.net/api/v2/status.json"),
    "prezi.com": Source("Prezi.com Status", "https://www.prezistatus.com/", "https://www.prezistatus.com/api/v2/status.json"),
    "proton.me": Source("Proton Services Status", "https://status.proton.me/", "https://status.proton.me/api/v2/status.json"),
    "quantummetric.com": Source("Quantum Metric Status", "https://status.quantummetric.com/", "https://status.quantummetric.com/api/v2/status.json"),
    "register.it": Source("Register IT Status", "https://status.register.it/", "https://status.register.it/api/v2/status.json"),
    "revenuecat.com": Source("RevenueCat Status", "https://status.revenuecat.com/", "https://status.revenuecat.com/api/v2/status.json"),
    "riskified.com": Source("Riskified Status", "https://status.riskified.com/", "https://status.riskified.com/api/v2/status.json"),
    "romarg.com": Source("ROMARG Status", "https://status.romarg.com/", "https://status.romarg.com/api/v2/status.json"),
    "segment.com": Source("Segment Status", "https://status.segment.com/", "https://status.segment.com/api/v2/status.json"),
    "sonos.com": Source("Sonos Status", "https://status.sonos.com/", "https://status.sonos.com/api/v2/status.json"),
    "splashtop.com": Source("Splashtop Status", "https://status.splashtop.com/", "https://status.splashtop.com/api/v2/status.json"),
    "split.io": Source("Harness Status (Split)", "https://status.harness.io/", "https://status.harness.io/api/v2/status.json"),
    "tenable.com": Source("Tenable Inc Status", "https://status.tenable.com/", "https://status.tenable.com/api/v2/status.json"),
    "ucdavis.edu": Source("UC Davis Status", "https://status.ucdavis.edu/", "https://status.ucdavis.edu/api/v2/status.json"),
    "verkada.com": Source("Verkada Command Status", "https://status.verkada.com/", "https://status.verkada.com/api/v2/status.json"),
    "webflow.com": Source("Webflow Status", "https://status.webflow.com/", "https://status.webflow.com/api/v2/status.json"),
    "wetransfer.com": Source("WeTransfer Status", "https://status.wetransfer.com/", "https://status.wetransfer.com/api/v2/status.json"),
    "yotpo.com": Source("Yotpo Status", "https://status.yotpo.com/", "https://status.yotpo.com/api/v2/status.json"),
    # Tranco ranks 3,001-4,000 (list of 2026-10-03), first pass with scripts/discover_status_sources.py (2026-10-19); each shown by a same-domain page, the publisher's own status redirect or a publisher documentation link.
    "ably.io": Source("Ably Status", "https://ably-realtime.statuspage.io/", "https://ably-realtime.statuspage.io/api/v2/status.json"),
    "arduino.cc": Source("Arduino Status", "https://status.arduino.cc/", "https://status.arduino.cc/api/v2/status.json"),
    "axis.com": Source("Axis Status", "https://status.axis.com/", "https://status.axis.com/api/v2/status.json"),
    "brown.edu": Source("Brown University Status", "https://status.brown.edu/", "https://status.brown.edu/api/v2/status.json"),
    "chartbeat.com": Source("Chartbeat Status", "https://status.chartbeat.com/", "https://status.chartbeat.com/api/v2/status.json"),
    "cribl.cloud": Source("Cribl Status", "https://status.cribl.cloud/", "https://status.cribl.cloud/api/v2/status.json"),
    "demandbase.com": Source("Demandbase Status", "https://status.demandbase.com/", "https://status.demandbase.com/api/v2/status.json"),
    "duosecurity.com": Source("Duo Status", "https://status.duo.com/", "https://status.duo.com/api/v2/status.json"),
    "ebsco.com": Source("EBSCO Status", "https://status.ebsco.com/", "https://status.ebsco.com/api/v2/status.json"),
    "egnyte.com": Source("Egnyte Platform Status", "https://status.egnyte.com/", "https://status.egnyte.com/api/v2/status.json"),
    "elementor.com": Source("Elementor Status", "https://status.elementor.com/", "https://status.elementor.com/api/v2/status.json"),
    "faceit.com": Source("FACEIT Status", "https://www.faceitstatus.com/", "https://www.faceitstatus.com/api/v2/status.json"),
    "feverup.com": Source("Fever Status", "https://status.feverup.com/", "https://status.feverup.com/api/v2/status.json"),
    "global-e.com": Source("Global-e Status", "https://status.global-e.com/", "https://status.global-e.com/api/v2/status.json"),
    "grindr.com": Source("Grindr Status", "https://status.grindr.com/", "https://status.grindr.com/api/v2/status.json"),
    "hootsuite.com": Source("Hootsuite Inc. Status", "https://status.hootsuite.com/", "https://status.hootsuite.com/api/v2/status.json"),
    "hudl.com": Source("Hudl Status", "https://status.hudl.com/", "https://status.hudl.com/api/v2/status.json"),
    "island.io": Source("Island Status", "https://status.island.io/", "https://status.island.io/api/v2/status.json"),
    "ispot.tv": Source("iSpot Status", "https://status.ispot.tv/", "https://status.ispot.tv/api/v2/status.json"),
    "iterable.com": Source("Iterable Status", "https://status.iterable.com/", "https://status.iterable.com/api/v2/status.json"),
    "jumpcloud.com": Source("JumpCloud Status", "https://status.jumpcloud.com/", "https://status.jumpcloud.com/api/v2/status.json"),
    "kaltura.com": Source("Kaltura Status", "https://status.kaltura.com/", "https://status.kaltura.com/api/v2/status.json"),
    "knowbe4.com": Source("KnowBe4 Status", "https://status.knowbe4.com/", "https://status.knowbe4.com/api/v2/status.json"),
    "konghq.com": Source("Kong Status", "https://status.konghq.com/", "https://status.konghq.com/api/v2/status.json"),
    "liveintent.com": Source("LiveIntent Status", "https://status.liveintent.com/", "https://status.liveintent.com/api/v2/status.json"),
    "lokalise.com": Source("Lokalise Status", "https://status.lokalise.com/", "https://status.lokalise.com/api/v2/status.json"),
    "lovable.dev": Source("Lovable Status", "https://status.lovable.dev/", "https://status.lovable.dev/api/v2/status.json"),
    "mercari.com": Source("Mercari Status", "https://status.mercari.com/", "https://status.mercari.com/api/v2/status.json"),
    "morningstar.com": Source("Morningstar Product Status", "https://status.morningstar.com/", "https://status.morningstar.com/api/v2/status.json"),
    "netbird.io": Source("NetBird Status", "https://status.netbird.io/", "https://status.netbird.io/api/v2/status.json"),
    "netsuite.com": Source("Oracle NetSuite Service Status", "https://status.netsuite.com/", "https://status.netsuite.com/api/v2/status.json"),
    "ntppool.org": Source("NTP Pool System Status", "https://status.ntppool.org/", "https://status.ntppool.org/api/v2/status.json"),
    "opsgenie.com": Source("Opsgenie Status", "https://opsgenie.status.atlassian.com/", "https://opsgenie.status.atlassian.com/api/v2/status.json"),
    "osano.com": Source("Osano Status", "https://status.osano.com/", "https://status.osano.com/api/v2/status.json"),
    "permutive.com": Source("Permutive Status", "https://status.permutive.com/", "https://status.permutive.com/api/v2/status.json"),
    "piano.io": Source("Piano Software Status", "https://status.piano.io/", "https://status.piano.io/api/v2/status.json"),
    "pitt.edu": Source("Pitt Digital Systems Status", "https://status.pitt.edu/", "https://status.pitt.edu/api/v2/status.json"),
    "razer.com": Source("Razer Status", "https://status.razer.com/", "https://status.razer.com/api/v2/status.json"),
    "ripe.net": Source("RIPE NCC Status", "https://status.ripe.net/", "https://status.ripe.net/api/v2/status.json"),
    "rudderstack.com": Source("Rudderstack Status", "https://status.rudderstack.com/", "https://status.rudderstack.com/api/v2/status.json"),
    "sardine.ai": Source("Sardine AI Status", "https://status.sardine.ai/", "https://status.sardine.ai/api/v2/status.json"),
    "seek.com": Source("SEEK API Status", "https://status.seek.com/", "https://status.seek.com/api/v2/status.json"),
    "smartsheet.com": Source("Smartsheet Status", "https://status.smartsheet.com/", "https://status.smartsheet.com/api/v2/status.json"),
    "smugmug.com": Source("SmugMug Status", "https://status.smugmug.com/", "https://status.smugmug.com/api/v2/status.json"),
    "splunkcloud.com": Source("Splunk Cloud Platform Status", "https://status.splunkcloud.com/", "https://status.splunkcloud.com/api/v2/status.json"),
    "sprinklr.com": Source("Sprinklr Status", "https://status.sprinklr.com/", "https://status.sprinklr.com/api/v2/status.json"),
    "sproutsocial.com": Source("Sprout Social Status", "https://www.sproutsocialstatus.com/", "https://www.sproutsocialstatus.com/api/v2/status.json"),
    "toasttab.com": Source("Toast System Status", "https://status.toasttab.com/", "https://status.toasttab.com/api/v2/status.json"),
    "twingate.com": Source("Twingate Status", "https://status.twingate.com/", "https://status.twingate.com/api/v2/status.json"),
    "ucsb.edu": Source("UCSB Status", "https://status.ucsb.edu/", "https://status.ucsb.edu/api/v2/status.json"),
    "unl.edu": Source("University of Nebraska IT Status", "https://status.nebraska.edu/", "https://status.nebraska.edu/api/v2/status.json"),
    "uwaterloo.ca": Source("University of Waterloo Status", "https://status.uwaterloo.ca/", "https://status.uwaterloo.ca/api/v2/status.json"),
    "voximplant.com": Source("Voximplant Status", "https://status.voximplant.com/", "https://status.voximplant.com/api/v2/status.json"),
    "walkme.com": Source("WalkMe US Status", "https://status.walkme.com/", "https://status.walkme.com/api/v2/status.json"),
    "watchguard.com": Source("WatchGuard Technologies Status", "https://status.watchguard.com/", "https://status.watchguard.com/api/v2/status.json"),
    "xe.com": Source("Xe Status", "https://status.xe.com/", "https://status.xe.com/api/v2/status.json"),
    "zeotap.com": Source("Zeotap Status", "https://status.zeotap.com/", "https://status.zeotap.com/api/v2/status.json"),
    # Tranco ranks 4,001-5,000 (list of 2026-10-03), first pass with scripts/discover_status_sources.py (2026-10-19); each shown by a same-domain page, the publisher's own status redirect or a publisher documentation link.
    "amplify.com": Source("Amplify Status", "https://status.amplify.com/", "https://status.amplify.com/api/v2/status.json"),
    "appboy.com": Source("Braze Status (Appboy)", "https://braze.statuspage.io/", "https://braze.statuspage.io/api/v2/status.json"),
    "aps.org": Source("APS Physics Status", "https://status.aps.org/", "https://status.aps.org/api/v2/status.json"),
    "bentley.com": Source("Bentley Systems, Inc Status", "https://status.bentley.com/", "https://status.bentley.com/api/v2/status.json"),
    "buffer.com": Source("Buffer Status", "https://status.buffer.com/", "https://status.buffer.com/api/v2/status.json"),
    "buildkite.com": Source("Buildkite Status", "https://www.buildkitestatus.com/", "https://www.buildkitestatus.com/api/v2/status.json"),
    "code42.com": Source("Code42 Software, Inc. Status", "https://status.code42.com/", "https://status.code42.com/api/v2/status.json"),
    "coveo.com": Source("Coveo Cloud Status", "https://status.cloud.coveo.com/", "https://status.cloud.coveo.com/api/v2/status.json"),
    "ecwid.com": Source("Ecwid Status", "https://status.ecwid.com/", "https://status.ecwid.com/api/v2/status.json"),
    "forticloud.com": Source("FortiCloud Status Hub Status", "https://status.forticloud.com/", "https://status.forticloud.com/api/v2/status.json"),
    "helpshift.com": Source("Helpshift Status", "https://status.helpshift.com/", "https://status.helpshift.com/api/v2/status.json"),
    "hosteurope.de": Source("Host Europe Status", "https://status.hosteurope.de/", "https://status.hosteurope.de/api/v2/status.json"),
    "impact.com": Source("impact.com Status", "https://status.impact.com/", "https://status.impact.com/api/v2/status.json"),
    "kandji.io": Source("Kandji Status", "https://status.kandji.io/", "https://status.kandji.io/api/v2/status.json"),
    "liquidweb.com": Source("Liquid Web - Services Status", "https://status.liquidweb.com/", "https://status.liquidweb.com/api/v2/status.json"),
    "livechat.com": Source("LiveChat Status", "https://status.livechat.com/", "https://status.livechat.com/api/v2/status.json"),
    "mailerlite.com": Source("MailerLite Status", "https://status.mailerlite.com/", "https://status.mailerlite.com/api/v2/status.json"),
    "mux.com": Source("mux Status", "https://status.mux.com/", "https://status.mux.com/api/v2/status.json"),
    "myfitnesspal.com": Source("MyFitnessPal Status", "https://status.myfitnesspal.com/", "https://status.myfitnesspal.com/api/v2/status.json"),
    "opensea.io": Source("OpenSea Status", "https://status.opensea.io/", "https://status.opensea.io/api/v2/status.json"),
    "parentsquare.com": Source("ParentSquare Status", "https://status.parentsquare.com/", "https://status.parentsquare.com/api/v2/status.json"),
    "poshmark.com": Source("Poshmark Status", "https://status.poshmark.com/", "https://status.poshmark.com/api/v2/status.json"),
    "rice.edu": Source("Rice University Status", "https://status.rice.edu/", "https://status.rice.edu/api/v2/status.json"),
    "seekingalpha.com": Source("Seeking Alpha Status", "https://status.seekingalpha.com/", "https://status.seekingalpha.com/api/v2/status.json"),
    "signifyd.com": Source("Signifyd Status", "https://status.signifyd.com/", "https://status.signifyd.com/api/v2/status.json"),
    "statsig.com": Source("Statsig Status", "https://status.statsig.com/", "https://status.statsig.com/api/v2/status.json"),
    "sumup.com": Source("SumUp Status", "https://status.sumup.com/", "https://status.sumup.com/api/v2/status.json"),
    "swrve.com": Source("Swrve - US Status", "https://status.swrve.com/", "https://status.swrve.com/api/v2/status.json"),
    "tunein.com": Source("TuneIn Status", "https://status.tunein.com/", "https://status.tunein.com/api/v2/status.json"),
    "upstart.com": Source("upstart Status", "https://status.upstart.com/", "https://status.upstart.com/api/v2/status.json"),
    "vanderbilt.edu": Source("Vanderbilt University Status", "https://status.vanderbilt.edu/", "https://status.vanderbilt.edu/api/v2/status.json"),
    "wolt.com": Source("Wolt Status", "https://status.wolt.com/", "https://status.wolt.com/api/v2/status.json"),
    "wufoo.com": Source("Wufoo Status", "https://status.wufoo.com/", "https://status.wufoo.com/api/v2/status.json"),
    "zoominfo.com": Source("ZoomInfo Status", "https://status.zoominfo.com/", "https://status.zoominfo.com/api/v2/status.json"),
    # Tranco ranks 5,001-6,000 (list of 2026-10-03), first pass with scripts/discover_status_sources.py (2026-10-19); each shown by a same-domain page, the publisher's own status redirect or a publisher documentation link.
    "afterpay.com": Source("Afterpay Status", "https://status.afterpay.com/", "https://status.afterpay.com/api/v2/status.json"),
    "aptrinsic.com": Source("Gainsight PX Status", "https://status.aptrinsic.com/", "https://status.aptrinsic.com/api/v2/status.json"),
    "arkoselabs.com": Source("Arkose Labs Status", "https://status.arkoselabs.com/", "https://status.arkoselabs.com/api/v2/status.json"),
    "atera.com": Source("Atera Status", "https://status.atera.com/", "https://status.atera.com/api/v2/status.json"),
    "avid.com": Source("Avid Status", "https://status.avid.com/", "https://status.avid.com/api/v2/status.json"),
    "barchart.com": Source("Barchart Status", "https://status.barchart.com/", "https://status.barchart.com/api/v2/status.json"),
    "barracuda.com": Source("Barracuda Networks Status", "https://status.barracuda.com/", "https://status.barracuda.com/api/v2/status.json"),
    "beehiiv.com": Source("beehiiv Status", "https://www.beehiivstatus.com/", "https://www.beehiivstatus.com/api/v2/status.json"),
    "blackboard.com": Source("Blackboard Inc. Status", "https://status.blackboard.com/", "https://status.blackboard.com/api/v2/status.json"),
    "blastapi.io": Source("Alchemy Status (Blast API)", "https://status.alchemy.com/", "https://status.alchemy.com/api/v2/status.json"),
    "brivo.com": Source("Brivo Status", "https://status.brivo.com/", "https://status.brivo.com/api/v2/status.json"),
    "cam4.com": Source("CAM4 Status", "https://status.cam4.com/", "https://status.cam4.com/api/v2/status.json"),
    "clover.com": Source("Clover Status", "https://status.clover.com/", "https://status.clover.com/api/v2/status.json"),
    "dartmouth.edu": Source("Dartmouth College Status", "https://status.dartmouth.edu/", "https://status.dartmouth.edu/api/v2/status.json"),
    "flippingbook.com": Source("FlippingBook Status", "https://status.flippingbook.com/", "https://status.flippingbook.com/api/v2/status.json"),
    "getpostman.com": Source("Postman Status", "https://status.postman.com/", "https://status.postman.com/api/v2/status.json"),
    "goodrx.com": Source("GoodRx API Status", "https://status.goodrx.com/", "https://status.goodrx.com/api/v2/status.json"),
    "goto.com": Source("GoTo Status", "https://status.goto.com/", "https://status.goto.com/api/v2/status.json"),
    "gotomeeting.com": Source("GoTo Status", "https://status.goto.com/", "https://status.goto.com/api/v2/status.json"),
    "infobip.com": Source("Infobip Status", "https://status.infobip.com/", "https://status.infobip.com/api/v2/status.json"),
    "kaufland.de": Source("Kaufland Status", "https://status.kaufland.de/", "https://status.kaufland.de/api/v2/status.json"),
    "localytics.com": Source("Localytics Status", "https://status.localytics.com/", "https://status.localytics.com/api/v2/status.json"),
    "logicmonitor.com": Source("LogicMonitor Status", "https://status.logicmonitor.com/", "https://status.logicmonitor.com/api/v2/status.json"),
    "mangadex.org": Source("MangaDex Status", "https://status.mangadex.org/", "https://status.mangadex.org/api/v2/status.json"),
    "missouri.edu": Source("UM IT Systems Status", "https://status.missouri.edu/", "https://status.missouri.edu/api/v2/status.json"),
    "mypurecloud.com": Source("Genesys Cloud Status", "https://status.mypurecloud.com/", "https://status.mypurecloud.com/api/v2/status.json"),
    "onepeloton.com": Source("Peloton Status", "https://status.onepeloton.com/", "https://status.onepeloton.com/api/v2/status.json"),
    "overdrive.com": Source("OverDrive Status", "https://status.overdrive.com/", "https://status.overdrive.com/api/v2/status.json"),
    "overleaf.com": Source("Overleaf Status", "https://status.overleaf.com/", "https://status.overleaf.com/api/v2/status.json"),
    "powerschool.com": Source("PowerSchool Products Status", "https://status.powerschool.com/", "https://status.powerschool.com/api/v2/status.json"),
    "pressreader.com": Source("PressReader Status", "https://status.pressreader.com/", "https://status.pressreader.com/api/v2/status.json"),
    "schoology.com": Source("PowerSchool Products Status (Schoology)", "https://status.powerschool.com/", "https://status.powerschool.com/api/v2/status.json"),
    "survicate.com": Source("Survicate Status", "https://status.survicate.com/", "https://status.survicate.com/api/v2/status.json"),
    "thinkific.com": Source("Thinkific Status", "https://status.thinkific.com/", "https://status.thinkific.com/api/v2/status.json"),
    "tritondigital.com": Source("Triton Digital Platform Status", "https://status.tritondigital.com/", "https://status.tritondigital.com/api/v2/status.json"),
    "uservoice.com": Source("UserVoice Status", "https://status.uservoice.com/", "https://status.uservoice.com/api/v2/status.json"),
    "warwick.ac.uk": Source("University of Warwick Status", "https://status.warwick.ac.uk/", "https://status.warwick.ac.uk/api/v2/status.json"),
    "zetaglobal.net": Source("Zeta Marketing Platform US Status", "https://status.zetaglobal.net/", "https://status.zetaglobal.net/api/v2/status.json"),
    # Tranco ranks 6,001-7,000 (list of 2026-10-03), first pass with scripts/discover_status_sources.py (2026-10-19); each shown by a same-domain page, the publisher's own status redirect or a publisher documentation link.
    "airbridge.io": Source("Airbridge Status", "https://status.airbridge.io/", "https://status.airbridge.io/api/v2/status.json"),
    "anota.ai": Source("Anota AI Status", "https://status.anota.ai/", "https://status.anota.ai/api/v2/status.json"),
    "basecamp.com": Source("37signals Status (Basecamp)", "https://www.37status.com/", "https://www.37status.com/api/v2/status.json"),
    "bidtheatre.com": Source("BidTheatre Status", "https://status.bidtheatre.com/", "https://status.bidtheatre.com/api/v2/status.json"),
    "contentful.com": Source("Contentful Status", "https://www.contentfulstatus.com/", "https://www.contentfulstatus.com/api/v2/status.json"),
    "controlup.com": Source("ControlUp Status", "https://status.controlup.com/", "https://status.controlup.com/api/v2/status.json"),
    "datagrail.io": Source("DataGrail Status", "https://status.datagrail.io/", "https://status.datagrail.io/api/v2/status.json"),
    "ecobee.com": Source("ecobee Status", "https://status.ecobee.com/", "https://status.ecobee.com/api/v2/status.json"),
    "expo.dev": Source("Expo Status", "https://status.expo.dev/", "https://status.expo.dev/api/v2/status.json"),
    "fareharbor.com": Source("FareHarbor Status", "https://status.fareharbor.com/", "https://status.fareharbor.com/api/v2/status.json"),
    "formstack.com": Source("Intellistack Status (Formstack)", "https://www.intellistackstatus.com/", "https://www.intellistackstatus.com/api/v2/status.json"),
    "genially.com": Source("Genially Status", "https://status.genially.com/", "https://status.genially.com/api/v2/status.json"),
    "gsu.edu": Source("GSU Technology Systems Status", "https://status.gsu.edu/", "https://status.gsu.edu/api/v2/status.json"),
    "heapanalytics.com": Source("Heap Status", "https://status.heap.io/", "https://status.heap.io/api/v2/status.json"),
    "jfrog.io": Source("JFrog Cloud Status", "https://status.jfrog.io/", "https://status.jfrog.io/api/v2/status.json"),
    "liveramp.com": Source("LiveRamp External Status", "https://status.liveramp.com/", "https://status.liveramp.com/api/v2/status.json"),
    "loggly.com": Source("Loggly Status", "https://status.loggly.com/", "https://status.loggly.com/api/v2/status.json"),
    "mattermost.com": Source("Mattermost Status", "https://status.mattermost.com/", "https://status.mattermost.com/api/v2/status.json"),
    "maze.co": Source("Maze Status", "https://status.maze.co/", "https://status.maze.co/api/v2/status.json"),
    "megaphone.fm": Source("Megaphone Status", "https://status.megaphone.fm/", "https://status.megaphone.fm/api/v2/status.json"),
    "mobileiron.com": Source("Ivanti Cloud Status (MobileIron)", "https://status.ivanticloud.com/", "https://status.ivanticloud.com/api/v2/status.json"),
    "noip.com": Source("No-IP Status", "https://status.noip.com/", "https://status.noip.com/api/v2/status.json"),
    "pubnub.com": Source("PubNub Status", "https://status.pubnub.com/", "https://status.pubnub.com/api/v2/status.json"),
    "razerzone.com": Source("Razer Status", "https://status.razer.com/", "https://status.razer.com/api/v2/status.json"),
    "secondlife.com": Source("Second Life Status", "https://status.secondlifegrid.net/", "https://status.secondlifegrid.net/api/v2/status.json"),
    "smartrecruiters.com": Source("SmartRecruiters Status", "https://status.smartrecruiters.com/", "https://status.smartrecruiters.com/api/v2/status.json"),
    "teachable.com": Source("Teachable Status", "https://www.teachablestatus.com/", "https://www.teachablestatus.com/api/v2/status.json"),
    "transcend.io": Source("Transcend Status", "https://status.transcend.io/", "https://status.transcend.io/api/v2/status.json"),
    "treasuredata.com": Source("Treasure AI Status (Treasure Data)", "https://status.treasure.ai/", "https://status.treasure.ai/api/v2/status.json"),
    "utk.edu": Source("University of Tennessee OneIT Status", "https://status.utk.edu/", "https://status.utk.edu/api/v2/status.json"),
    "uvic.ca": Source("UVic IT Status", "https://status.uvic.ca/", "https://status.uvic.ca/api/v2/status.json"),
    "whatnot.com": Source("Whatnot Status", "https://status.whatnot.com/", "https://status.whatnot.com/api/v2/status.json"),
    "whoop.com": Source("WHOOP Status", "https://status.whoop.com/", "https://status.whoop.com/api/v2/status.json"),
    "workspaceone.com": Source("Omnissa Workspace ONE Status", "https://status.workspaceone.com/", "https://status.workspaceone.com/api/v2/status.json"),
    "yoast.com": Source("Yoast Status", "https://status.yoast.com/", "https://status.yoast.com/api/v2/status.json"),
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
    # Retry of the first batch's exclusions with scripts/discover_status_sources.py (2026-10-18); each shown by a same-domain page, the publisher's own status redirect, or a publisher documentation link.
    "booking.com": ("Booking.com Connectivity API Status", "https://connect.booking.com/status"),
    "ebay.com": ("eBay API Status", "https://developer.ebay.com/support/api-status"),
    "fastly.com": ("Fastly API Status", "https://www.fastlystatus.com/"),
    "mastercard.com": ("Mastercard Developers API Status", "https://developer.mastercard.com/api-status"),
    "mistral.ai": ("Mistral AI Status", "https://status.mistral.ai/"),
    "replicate.com": ("Cloudflare Status (Replicate's own status address redirects here)", "https://www.cloudflarestatus.com/services"),
    "replit.com": ("Replit Status", "https://status.replit.com/"),
    "soundcloud.com": ("SoundCloud Status", "https://help.soundcloud.com/hc/en-us/articles/4413524848667-SoundCloud-Status"),
    "tencentcloud.com": ("Tencent Cloud API Status", "https://status.tencentcloud.com/"),
    "todoist.com": ("Todoist API Status", "https://status.todoist.net/"),
    "twelvedata.com": ("Twelve Data Status", "https://twelvedata.isitup.cloud/"),
    "twitter.com": ("X Developer Platform Status", "https://developer.x.com/status"),
    "writer.com": ("Writer Status", "https://status.writer.com/"),
    "x.ai": ("xAI Status", "https://status.x.ai/"),
    "x.com": ("X Developer Platform Status", "https://developer.x.com/status"),
    "zoho.com": ("Zoho Status (US data center view)", "https://us.zohostatus.com/"),
    # Seed hosts 547 onward, first pass with scripts/discover_status_sources.py (2026-10-19); each shown by a same-domain page or the publisher's own status redirect.
    "agora.io": ("Agora Status Page", "https://status.agora.io/"),
    "allegro.pl": ("Allegro Status Page", "https://www.allegrostatus.pl/"),
    "aol.com": ("AOL Status", "https://status.aol.com/"),
    "arcgis.com": ("ArcGIS Online Status", "https://status.arcgis.com/"),
    "b-cdn.net": ("B-CDN Status", "https://status.b-cdn.net/"),
    "bitrix24.ru": ("Bitrix24 Status", "https://status.bitrix24.ru/"),
    "curseforge.com": ("Curseforge Status Page", "https://status.curseforge.com/"),
    "discogs.com": ("Discogs Status", "https://status.discogs.com/"),
    "dreamhost.com": ("DreamHost Status", "https://www.dreamhoststatus.com/"),
    "dropcatch.com": ("DropCatch System Status", "https://status.dropcatch.com/"),
    "dynatrace.com": ("Dynatrace Health Status", "https://dynatrace.status.io/"),
    "fb.com": ("Meta Status (business products and platform)", "https://metastatus.com/"),
    "ip-api.com": ("ip-api.com Status", "https://ip-api.status.io/"),
    "mlb.com": ("MLB System Status", "https://status.mlb.com/"),
    "ngenix.net": ("NGENIX Status", "https://status.ngenix.net/"),
    "odoo.com": ("Odoo Status", "https://status.odoo.com/"),
    "ovh.net": ("OVHcloud Status", "https://www.status-ovhcloud.com/"),
    "php.net": ("PHP.net Status", "https://status.php.net/"),
    "playstation.com": ("PlayStation Service Status", "https://status.playstation.com/"),
    "scribd.com": ("Scribd Site Status", "https://support.scribd.com/hc/en-us/articles/360001202872-Site-Status"),
    "synology.com": ("Synology Service Status", "https://www.synology.com/en-global/support/synology_service"),
    "time.com": ("Time Status", "https://status.time.com/"),
    "zoom.com": ("Zoom Status", "https://www.zoomstatus.com/"),
    # Tranco ranks 1,001-2,000 (list of 2026-10-03), first pass with scripts/discover_status_sources.py (2026-10-19); each shown by a same-domain page or the publisher's own status redirect.
    "alura.com.br": ("Alura Status", "https://status.alura.com.br/"),
    "asurascans.com": ("Asura Scans Status", "https://status.asurascans.com/status/asura"),
    "aternos.org": ("Aternos Status", "https://status.aternos.gmbh/"),
    "datadome.co": ("DataDome Service Status", "https://status.datadome.co/"),
    "elegantthemes.com": ("Elegant Themes status", "https://status.elegantthemes.com/"),
    "fedoraproject.org": ("Fedora Infrastructure Status", "https://status.fedoraproject.org/"),
    "fetlife.com": ("FetLife Status Updates", "https://fetlifestatus.com/"),
    "furaffinity.net": ("Fur Affinity · Service status", "https://status.furaffinity.net/"),
    "ixl.com": ("IXL Status", "https://status.ixl.com/"),
    "janitorai.com": ("JanitorAI Status", "https://status.janitorai.com/"),
    "leboncoin.fr": ("Leboncoin Services Status", "https://status.leboncoin.fr/"),
    "overwolf.com": ("Overwolf Status Page", "https://status.overwolf.com/"),
    "ovh.com": ("OVHcloud Status", "https://www.status-ovhcloud.com/"),
    "ovhcloud.com": ("OVHcloud Status", "https://www.status-ovhcloud.com/"),
    "ox.ac.uk": ("University of Oxford Service Status", "https://status.ox.ac.uk/"),
    "pewresearch.org": ("Pew Research Center Platform Status", "https://status.pewresearch.org/"),
    "platform.sh": ("Upsun Status (Platform.sh)", "https://status.upsun.com/"),
    "service-now.com": ("ServiceNow Data Center Status", "https://status.service-now.com/"),
    "sportybet.com": ("SportyBet Status & Announcements", "https://status.sportybet.com/"),
    "theqoo.net": ("Theqoo Status", "https://status.theqoo.net/"),
    "ultradns.com": ("UltraDNS System Status", "https://status.ultradns.com/"),
    # Tranco ranks 2,001-3,000 (list of 2026-10-03), first pass with scripts/discover_status_sources.py (2026-10-19); each shown by a same-domain page, the publisher's own status redirect or a publisher documentation link.
    "2gis.ru": ("2Gis System Status", "https://status.2gis.ru/"),
    "a2hosting.com": ("hosting.com Status (A2 Hosting)", "https://status.hosting.com/"),
    "acrobat.com": ("Acrobat Adobe Status", "https://status.adobe.com/"),
    "adguard.com": ("AdGuard Status", "https://status.adguard.com/"),
    "admatic.de": ("Admatic.de Service Status", "https://status.admatic.de/"),
    "archlinux.org": ("status.archlinux.org", "https://status.archlinux.org/"),
    "atom.com": ("Atom.com status", "https://status.atom.com/"),
    "bigcartel.com": ("Big Cartel Status", "https://status.bigcartel.com/"),
    "broadcom.com": ("Broadcom Service Status", "https://status.broadcom.com/"),
    "check24.de": ("CHECK24 Platform Status", "https://status.check24.de/"),
    "cnil.fr": ("https://cnil.fr/ status page", "https://status.cnil.fr/"),
    "cookieyes.com": ("CookieYes Status Page", "https://status.cookieyes.com/"),
    "crazyegg.com": ("Crazyegg Status", "https://status.crazyegg.com/"),
    "crunchyroll.com": ("Crunchyroll Status", "https://status.crunchyroll.com/"),
    "cutt.ly": ("Cutt system status", "https://status.cutt.ly/"),
    "directnic.com": ("Directnic Status", "https://status.directnic.com/"),
    "evergage.com": ("Evergage Trust Status", "https://status.salesforce.com/products/MCPersonalization"),
    "evernote.com": ("Evernote Status", "https://status.evernote.com/"),
    "hover.com": ("Hover Status", "https://www.hoverstatus.com/"),
    "inmotionhosting.com": ("InMotion Hosting Status", "https://status.inmotionhosting.com/"),
    "innovid.com": ("Innovid Status Page", "https://status.innovid.com/"),
    "joinmassive.com": ("Massive Status Page", "https://status.joinmassive.com/"),
    "laravel.com": ("Laravel Status", "https://status.laravel.com/"),
    "malwarebytes.com": ("ThreatDown System Status (Malwarebytes)", "https://status.threatdown.com/"),
    "medal.tv": ("Medal Status", "https://status.medal.tv/"),
    "moodle.com": ("Moodle Status Page", "https://status.moodle.com/"),
    "mysecurecloudhost.com": ("Mysecurecloudhost Status", "https://status.mysecurecloudhost.com/"),
    "nest.com": ("Nest Status Page", "https://status.nest.com/"),
    "nordvpn.com": ("NordVPN status", "https://status.nordvpn.com/"),
    "padlet.com": ("Padlet Status", "https://status.padlet.help/"),
    "phantom.app": ("Phantom Status", "https://status.phantom.com/"),
    "polymarket.com": ("Polymarket Status", "https://status.polymarket.com/"),
    "rackspace.com": ("Rackspace Status", "https://rackspace.service-now.com/system_status"),
    "railway.app": ("Railway Status", "https://status.railway.com/"),
    "signal.org": ("Signal Status", "https://status.signal.org/"),
    "thousandeyes.com": ("ThousandEyes Status", "https://status.thousandeyes.com/"),
    "threema.ch": ("Threema System Status", "https://status.threema.ch/"),
    "typekit.com": ("Typekit Adobe Status", "https://status.adobe.com/products/503501"),
    "umbrella.com": ("Cisco Umbrella Status", "https://status.umbrella.cisco.com/"),
    "upwork.com": ("Upwork Status", "https://status.upwork.com/"),
    "vtex.com": ("VTEX Status", "https://status.vtex.com/"),
    # Tranco ranks 3,001-4,000 (list of 2026-10-03), first pass with scripts/discover_status_sources.py (2026-10-19); each shown by a same-domain page, the publisher's own status redirect or a publisher documentation link.
    "afrinic.net": ("Afrinic Status", "https://status.afrinic.net/"),
    "avg.com": ("AVG Cloudcare Status", "https://status.cloudcare.avg.com/"),
    "backblaze.com": ("Backblaze Status", "https://status.backblaze.com/"),
    "bitrix24.com": ("Bitrix24 Status", "https://status.bitrix24.com/"),
    "brightcove.com": ("Brightcove Status", "https://status.brightcove.com/"),
    "celtra.com": ("Celtra Status", "https://status.celtra.com/"),
    "code.org": ("Code Status", "https://status.code.org/"),
    "coindesk.com": ("Coindesk Status", "https://status.coindesk.com/"),
    "dashlane.com": ("Dashlane Status", "https://status.dashlane.com/"),
    "deere.com": ("Deere Status", "https://status.deere.com/"),
    "drpc.org": ("Drpc Status", "https://status.drpc.org/"),
    "feedly.com": ("Feedly Status", "https://status.feedly.com/"),
    "flightaware.com": ("Flightaware Status", "https://status.flightaware.com/"),
    "gamma.app": ("Gamma Status", "https://status.gamma.app/"),
    "gitea.com": ("Gitea Status", "https://status.gitea.com/"),
    "gnome.org": ("Gnome Status", "https://status.gnome.org/"),
    "heyzine.com": ("Heyzine Status", "https://status.heyzine.com/"),
    "hotmart.com": ("Hotmart Status", "https://status.hotmart.com/"),
    "leagueoflegends.com": ("Riot Games Service Status (League of Legends)", "https://status.riotgames.com/"),
    "libsyn.com": ("Libsyn Status", "https://status.libsyn.com/"),
    "mediavine.com": ("Mediavine Status", "https://status.mediavine.com/"),
    "merkle.io": ("Blink Labs Status (Merkle)", "https://status.blinklabs.xyz/"),
    "mimecast.com": ("Mimecast Status", "https://status.mimecast.com/"),
    "modrinth.com": ("Modrinth Status", "https://status.modrinth.com/"),
    "n-able.com": ("N-Able Status", "https://status.n-able.com/"),
    "namebright.com": ("Namebright Status", "https://status.namebright.com/"),
    "ning.com": ("Ning Status", "https://status.ning.com/"),
    "opensuse.org": ("Opensuse Status", "https://status.opensuse.org/"),
    "plausible.io": ("Plausible Status", "https://status.plausible.io/"),
    "proquest.com": ("Proquest Status", "https://status.proquest.com/"),
    "pushwoosh.com": ("Pushwoosh Status", "https://status.pushwoosh.com/"),
    "redislabs.com": ("Redis Status (Redis Labs)", "https://status.redis.io/"),
    "sketchup.com": ("Sketchup Status", "https://status.sketchup.com/"),
    "studocu.com": ("Studocu Status", "https://status.studocu.com/"),
    "telenet.be": ("Telenet Status", "https://status.telenet.be/"),
    "themegrill.com": ("Themegrill Status", "https://status.themegrill.com/en/"),
    "uga.edu": ("Uga Status", "https://status.uga.edu/"),
    "userway.org": ("Userway Status", "https://status.userway.org/"),
    "value-domain.com": ("Value-Domain Status", "https://status.value-domain.com/"),
    "wistia.com": ("Wistia Status", "https://status.wistia.com/"),
    "wordfence.com": ("Wordfence Status", "https://status.wordfence.com/"),
    "yumpu.com": ("Yumpu Status", "https://status.yumpu.com/"),
    # Tranco ranks 4,001-5,000 (list of 2026-10-03), first pass with scripts/discover_status_sources.py (2026-10-19); each shown by a same-domain page, the publisher's own status redirect or a publisher documentation link.
    "20minutes.fr": ("20Minutes Status", "https://status.20minutes.fr/en"),
    "a-ads.com": ("A-Ads Status", "https://status.aads.com/en/"),
    "abtasty.com": ("Abtasty Status", "https://status.abtasty.com/"),
    "adtarget.biz": ("Adtarget Status", "https://status.adtarget.biz/"),
    "bitdrift.io": ("Bitdrift Status", "https://status.bitdrift.io/"),
    "blender.org": ("Blender Status", "https://status.blender.org/status/public"),
    "byu.edu": ("Brigham Young University System Status", "https://support.byu.edu/it"),
    "carrd.co": ("Carrd Status", "https://status.carrd.co/"),
    "comodo.com": ("Comodo Status", "https://status.one.comodo.com/ui/"),
    "cyberark.com": ("Cyberark Status", "https://status.cyberark.com/"),
    "cyberfolks.pl": ("Cyberfolks Status", "https://status.cyberfolks.pl/"),
    "dayforcehcm.com": ("Dayforcehcm Status", "https://status.dayforcehcm.com/"),
    "dazn.com": ("Dazn Status", "https://status.dazn.com/"),
    "dmm.com": ("Dmm Status", "https://status.dmm.com/"),
    "draftkings.com": ("Draftkings Status", "https://status.draftkings.com/"),
    "eclipse.org": ("Eclipse Status", "https://www.eclipsestatus.io/"),
    "flipsnack.com": ("Flipsnack Status", "https://status.flipsnack.com/"),
    "growthbook.io": ("Growthbook Status", "https://status.growthbook.io/"),
    "helsinki.fi": ("University of Helsinki Status", "https://status.helsinki.fi/"),
    "higgsfield.ai": ("Higgsfield Status", "https://status.higgsfield.ai/"),
    "immersivelabs.com": ("Immersivelabs Status", "https://status.immersivelabs.com/"),
    "iu.edu": ("Indiana University Status", "https://status.iu.edu/status"),
    "mosyle.com": ("Mosyle Status", "https://status.mosyle.com/"),
    "nanit.com": ("Nanit Status", "https://status.nanit.com/"),
    "opentable.com": ("Opentable Status", "https://status.opentable.com/"),
    "optable.co": ("Optable Status", "https://status.optable.co/"),
    "phpbb.com": ("Phpbb Status", "https://status.phpbb.com/"),
    "picsart.com": ("Picsart Status", "https://status.picsart.com/"),
    "pixad.com.tr": ("Pixad Status", "https://status.pixad.com.tr/"),
    "preply.com": ("Preply Status", "https://status.preply.com/"),
    "qualys.com": ("Qualys Status", "https://status.qualys.com/"),
    "raspberrypi.org": ("Raspberrypi Status", "https://status.raspberrypi.org/"),
    "rei.com": ("Rei Status", "https://status.rei.com/"),
    "shutterfly.com": ("Shutterfly Status", "https://status.shutterfly.com/"),
    "sportskeeda.com": ("Sportskeeda Status", "https://status.sportskeeda.com/"),
    "strln.net": ("Strln Status", "https://status.strln.net/"),
    "torproject.org": ("Torproject Status", "https://torprojectstatus.org/"),
    "wsu.edu": ("Washington State University Status", "https://status.wsu.edu/"),
    # Tranco ranks 5,001-6,000 (list of 2026-10-03), first pass with scripts/discover_status_sources.py (2026-10-19); each shown by a same-domain page, the publisher's own status redirect or a publisher documentation link.
    "2miners.com": ("2Miners Status", "https://status.2miners.com/"),
    "cfr.org": ("Council on Foreign Relations Status", "https://status.cfr.org/"),
    "classdojo.com": ("Classdojo Status", "https://status.classdojo.com/"),
    "decodo.com": ("Decodo Status", "https://status.decodo.com/"),
    "desmos.com": ("Desmos Status", "https://status.desmos.com/"),
    "espressif.com": ("Espressif Status", "https://status.espressif.com/status/public"),
    "fabulous.com": ("Fabulous Status", "https://status.fabulous.com/"),
    "flashbots.net": ("Flashbots Status", "https://status.flashbots.net/"),
    "frameo.net": ("Frameo Status", "https://status.frameo.com/"),
    "framer.com": ("Framer Status", "https://www.framerstatus.com/"),
    "freeipapi.com": ("Freeipapi Status", "https://status.freeipapi.com/"),
    "freshchat.com": ("Freshchat Status", "https://status.freshchat.com/"),
    "fsf.org": ("Free Software Foundation Status", "https://status.fsf.org/status/fsf"),
    "gigya.com": ("Gigya Status", "https://status.gigya.com/"),
    "groupon.com": ("Groupon Status", "https://status.groupon.com/"),
    "hostgator.mx": ("Hostgator Status", "https://status.hostgator.mx/"),
    "hostnet.nl": ("Hostnet Status", "https://hostnet.status.io/"),
    "instana.io": ("Instana Status", "https://status.instana.io/"),
    "laravel-news.com": ("Laravel-News Status", "https://status.laravel-news.com/"),
    "larksuite.com": ("Larksuite Status", "https://status.larksuite.com/"),
    "lbl.gov": ("Berkeley Lab Status", "https://status.lbl.gov/"),
    "linkvertise.com": ("Linkvertise Status", "https://status.linkvertise.com/"),
    "lsu.edu": ("LSU IT Service Status", "https://www.lsu.edu/its/status/index.php"),
    "menlosecurity.com": ("Menlosecurity Status", "https://status.menlosecurity.com/"),
    "midjourney.com": ("Midjourney Status", "https://status.midjourney.com/"),
    "n8n.io": ("N8N Status", "https://status.n8n.cloud/"),
    "neocities.org": ("Neocities Status", "https://status.neocities.org/"),
    "nownodes.io": ("Nownodes Status", "https://status.nownodes.io/"),
    "pro32connect.ru": ("Pro32Connect Status", "https://status.pro32connect.ru/"),
    "ria.com": ("Ria Status", "https://status.ria.com/"),
    "roche.com": ("Roche Status", "https://status.roche.com/"),
    "securly.com": ("Securly Status", "https://status.securly.com/"),
    "smule.com": ("Smule Status", "https://status.smule.com/"),
    "tenjin.com": ("Tenjin Status", "https://status.tenjin.com/"),
    "threatdown.com": ("Threatdown Status", "https://status.threatdown.com/"),
    "tistory.com": ("Tistory Status", "https://status.tistory.com/"),
    "tracker.gg": ("Tracker Status", "https://status.tracker.gg/"),
    "up.pt": ("University of Porto Status", "https://status.up.pt/"),
    "yeastar.com": ("Yeastar Status", "https://status.yeastar.com/"),
    "zopim.com": ("Zendesk Status (Zopim)", "https://status.zendesk.com/"),
    # Tranco ranks 6,001-7,000 (list of 2026-10-03), first pass with scripts/discover_status_sources.py (2026-10-19); each shown by a same-domain page, the publisher's own status redirect or a publisher documentation link.
    "1rpc.io": ("1Rpc Status", "https://status.1rpc.io/"),
    "aalto.fi": ("Aalto University Status", "https://status.aalto.fi/"),
    "accompany.com": ("Accompany Status", "https://status.accompany.com/"),
    "ageverif.com": ("Ageverif Status", "https://status.ageverif.com/"),
    "appfolio.com": ("Appfolio Status", "https://status.appfolio.com/"),
    "cloudez.io": ("Cloudez Status", "https://status.cloudez.io/"),
    "datatables.net": ("Datatables Status", "https://status.datatables.net/"),
    "dm.de": ("Dm Status", "https://status.dm.de/"),
    "dnb.com": ("Dun & Bradstreet Status", "https://status.dnb.com/"),
    "dodois.io": ("Dodois Status", "https://status.dodois.io/"),
    "freecodecamp.org": ("Freecodecamp Status", "https://status.freecodecamp.org/"),
    "freeconvert.com": ("Freeconvert Status", "https://status.freeconvert.com/"),
    "funda.nl": ("Funda Status", "https://status.funda.nl/"),
    "godaddysites.com": ("Godaddysites Status", "https://status.godaddysites.com/"),
    "groupme.com": ("Groupme Status", "https://status.groupme.com/"),
    "monetixads.com": ("Monetixads Status", "https://status.monetixads.com/"),
    "musixmatch.com": ("Musixmatch Status", "https://status.musixmatch.com/status/main"),
    "narvar.com": ("Narvar Status", "https://status.narvar.com/"),
    "opencode.ai": ("Opencode Status", "https://status.opencode.ai/"),
    "pointclickcare.com": ("Pointclickcare Status", "https://status.pointclickcare.com/"),
    "sage.com": ("Sage Status", "https://status.sage.com/"),
    "signupgenius.com": ("Signupgenius Status", "https://status.signupgenius.com/"),
    "smooch.io": ("Zendesk Status (Smooch)", "https://status.smooch.io/"),
    "sniffies.com": ("Sniffies Status", "https://status.sniffies.com/"),
    "travian.com": ("Travian Status", "https://status.travian.com/"),
    "vivaldi.com": ("Vivaldi Status", "https://vivaldistatus.com/"),
    "wpzoom.com": ("Wpzoom Status", "https://status.wpzoom.com/"),
    "zoho.in": ("Zoho Status (India)", "https://in.zohostatus.com/"),
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
