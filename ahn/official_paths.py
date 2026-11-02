"""Curated official developer paths for well-known public hosts.

Every URL is a publisher-owned documentation entry point; nothing here is derived from a
user's hostname. Hosts without a public API carry a note instead of a guessed URL. A URL on a
domain other than the key's must be declared in scripts/verify_official_paths.py (PUBLISHER_DOMAINS).
Absence from the table means "not curated", not "no API".

Live check: python scripts/verify_official_paths.py (not run in CI). Full run 2026-10-18: every URL
answered 200 from a publisher-owned host except existing entries whose publisher refused the automated
check (bot wall or 403); those were kept because a refusal is not evidence that the page is gone.
"""

from __future__ import annotations

import copy
from datetime import date, datetime, timezone


def _entry(name: str, api_docs: str | None = None, developer_portal: str | None = None, note: str | None = None) -> dict:
    return {"name": name, "api_docs": api_docs, "developer_portal": developer_portal, "note": note}


_ATLASSIAN = "https://developer.atlassian.com/"
_META = "https://developers.facebook.com/"
_GOOGLE = "https://developers.google.com/"
_MS_LEARN = "https://learn.microsoft.com/"

OFFICIAL_PATHS: dict[str, dict] = {
    # Code hosting, OpenAI, Google, social Q&A and reference
    "github.com": _entry("GitHub", "https://docs.github.com/en/rest", "https://docs.github.com/"),
    "gitlab.com": _entry("GitLab", "https://docs.gitlab.com/api/", "https://docs.gitlab.com/"),
    "bitbucket.org": _entry("Bitbucket Cloud", "https://developer.atlassian.com/cloud/bitbucket/rest/", _ATLASSIAN),
    "openai.com": _entry("OpenAI", "https://developers.openai.com/api/reference/overview", "https://developers.openai.com/api/docs"),
    "chatgpt.com": _entry("OpenAI (ChatGPT)", "https://developers.openai.com/api/reference/overview", "https://developers.openai.com/api/docs"),
    "google.com": _entry("Google", "https://developers.google.com/apis-explorer", _GOOGLE,
                         "Maps Platform: https://developers.google.com/maps/documentation"),
    "google.dev": _entry("Google AI for Developers (Gemini API)", "https://ai.google.dev/api", "https://ai.google.dev/"),
    "youtube.com": _entry("YouTube Data API", "https://developers.google.com/youtube/v3", "https://developers.google.com/youtube"),
    "reddit.com": _entry("Reddit", "https://www.reddit.com/dev/api/", "https://developers.reddit.com/"),
    "x.com": _entry("X (Twitter) API", "https://docs.x.com/overview", "https://developer.x.com/"),
    "twitter.com": _entry("X (Twitter) API", "https://docs.x.com/overview", "https://developer.x.com/"),
    "stackoverflow.com": _entry("Stack Exchange API", "https://api.stackexchange.com/docs", "https://stackapps.com/"),
    "stackexchange.com": _entry("Stack Exchange API", "https://api.stackexchange.com/docs", "https://stackapps.com/"),
    "wikipedia.org": _entry("Wikimedia / MediaWiki API", "https://www.mediawiki.org/wiki/API:Main_page",
                               "https://www.mediawiki.org/wiki/Wikimedia_APIs"),
    "wikimedia.org": _entry("Wikimedia / MediaWiki API", "https://www.mediawiki.org/wiki/API:Main_page",
                               "https://www.mediawiki.org/wiki/Wikimedia_APIs"),
    # Commerce and payments
    "amazon.com": _entry("Amazon Selling Partner API", "https://developer-docs.amazon/sp-api/", "https://developer.amazon.com/",
                         "Product Advertising API 5.0 deprecation and the Creators API: "
                         "https://affiliate-program.amazon.com/creatorsapi/docs/en-us/paapiv5-deprecation"),
    "amazonaws.com": _entry("Amazon Web Services", "https://docs.aws.amazon.com/", "https://builder.aws.com/"),
    "ebay.com": _entry("eBay Developers Program", "https://developer.ebay.com/docs", "https://developer.ebay.com/"),
    "shopify.com": _entry("Shopify", "https://shopify.dev/docs/api", "https://shopify.dev/"),
    "etsy.com": _entry("Etsy Open API", "https://developers.etsy.com/documentation/", "https://developers.etsy.com/"),
    "walmart.com": _entry("Walmart Developer Portal", None, "https://developer.walmart.com/"),
    "stripe.com": _entry("Stripe", "https://docs.stripe.com/api", "https://docs.stripe.com/"),
    "paypal.com": _entry("PayPal", "https://developer.paypal.com/api/rest/", "https://developer.paypal.com/"),
    "squareup.com": _entry("Square", "https://developer.squareup.com/reference/square", "https://developer.squareup.com/"),
    "adyen.com": _entry("Adyen", "https://docs.adyen.com/api-explorer/", "https://docs.adyen.com/"),
    "plaid.com": _entry("Plaid", "https://plaid.com/docs/api/", "https://plaid.com/docs/"),
    # Messaging, collaboration and productivity
    "slack.com": _entry("Slack", "https://docs.slack.dev/reference/methods", "https://docs.slack.dev/"),
    "discord.com": _entry("Discord", "https://docs.discord.com/developers/intro", "https://discord.com/developers"),
    "telegram.org": _entry("Telegram Bot API", "https://core.telegram.org/bots/api", "https://core.telegram.org/"),
    "whatsapp.com": _entry("WhatsApp Business Platform", "https://developers.facebook.com/docs/whatsapp/", _META),
    "zoom.us": _entry("Zoom", "https://developers.zoom.us/docs/api/", "https://developers.zoom.us/"),
    "notion.so": _entry("Notion", "https://developers.notion.com/reference", "https://developers.notion.com/"),
    "notion.com": _entry("Notion", "https://developers.notion.com/reference", "https://developers.notion.com/"),
    "atlassian.com": _entry("Atlassian Cloud", "https://developer.atlassian.com/cloud/", _ATLASSIAN),
    "atlassian.net": _entry("Atlassian Cloud", "https://developer.atlassian.com/cloud/", _ATLASSIAN),
    "trello.com": _entry("Trello", "https://developer.atlassian.com/cloud/trello/rest/", _ATLASSIAN),
    "asana.com": _entry("Asana", "https://developers.asana.com/reference/rest-api-reference", "https://developers.asana.com/"),
    "airtable.com": _entry("Airtable", "https://airtable.com/developers/web/api/introduction", "https://airtable.com/developers"),
    "figma.com": _entry("Figma", "https://developers.figma.com/docs/rest-api/", "https://developers.figma.com/"),
    # Microsoft, Meta and social media
    "microsoft.com": _entry("Microsoft Graph", "https://learn.microsoft.com/en-us/graph/api/overview", _MS_LEARN),
    "azure.com": _entry("Microsoft Azure", "https://learn.microsoft.com/en-us/rest/api/azure/", "https://learn.microsoft.com/en-us/azure/"),
    "linkedin.com": _entry("LinkedIn", "https://learn.microsoft.com/en-us/linkedin/", "https://developer.linkedin.com/"),
    "facebook.com": _entry("Meta Graph API", "https://developers.facebook.com/docs/graph-api/", _META),
    "instagram.com": _entry("Instagram Platform (Meta)", "https://developers.facebook.com/docs/", _META),
    "tiktok.com": _entry("TikTok for Developers", "https://developers.tiktok.com/doc/overview", "https://developers.tiktok.com/"),
    "pinterest.com": _entry("Pinterest API", "https://developers.pinterest.com/docs/api/v5/", "https://developers.pinterest.com/"),
    "tumblr.com": _entry("Tumblr API", "https://www.tumblr.com/docs/en/api/v2", "https://www.tumblr.com/developers"),
    # Music, video and images
    "spotify.com": _entry("Spotify Web API", "https://developer.spotify.com/documentation/web-api", "https://developer.spotify.com/"),
    "soundcloud.com": _entry("SoundCloud API", "https://developers.soundcloud.com/docs/api/guide", "https://developers.soundcloud.com/"),
    "last.fm": _entry("Last.fm API", "https://www.last.fm/api", "https://www.last.fm/api"),
    "twitch.tv": _entry("Twitch", "https://dev.twitch.tv/docs/api/", "https://dev.twitch.tv/"),
    "vimeo.com": _entry("Vimeo API", "https://developer.vimeo.com/api/reference", "https://developer.vimeo.com/"),
    "flickr.com": _entry("Flickr API", "https://www.flickr.com/services/api/", "https://www.flickr.com/services/api/"),
    "unsplash.com": _entry("Unsplash API", "https://unsplash.com/documentation", "https://unsplash.com/developers"),
    "pexels.com": _entry("Pexels API", "https://www.pexels.com/api/documentation/", "https://www.pexels.com/api/"),
    "pixabay.com": _entry("Pixabay API", "https://pixabay.com/api/docs/", "https://pixabay.com/api/docs/"),
    "giphy.com": _entry("GIPHY API", "https://developers.giphy.com/docs/api/", "https://developers.giphy.com/"),
    "shutterstock.com": _entry("Shutterstock API", "https://api-reference.shutterstock.com/", None, "Requires an app token."),
    # Storage, CRM, messaging APIs and identity
    "dropbox.com": _entry("Dropbox", "https://www.dropbox.com/developers/documentation/http/documentation", "https://www.dropbox.com/developers"),
    "box.com": _entry("Box", "https://developer.box.com/reference/", "https://developer.box.com/"),
    "salesforce.com": _entry("Salesforce", "https://developer.salesforce.com/docs", "https://developer.salesforce.com/"),
    "hubspot.com": _entry("HubSpot", "https://developers.hubspot.com/docs", "https://developers.hubspot.com/"),
    "zendesk.com": _entry("Zendesk", "https://developer.zendesk.com/api-reference/", "https://developer.zendesk.com/"),
    "mailchimp.com": _entry("Mailchimp", "https://mailchimp.com/developer/marketing/api/", "https://mailchimp.com/developer/"),
    "twilio.com": _entry("Twilio", "https://www.twilio.com/docs/usage/api", "https://www.twilio.com/docs"),
    "sendgrid.com": _entry("SendGrid (Twilio)", "https://www.twilio.com/docs/sendgrid/api-reference", "https://www.twilio.com/docs/sendgrid"),
    "okta.com": _entry("Okta", "https://developer.okta.com/docs/reference/", "https://developer.okta.com/"),
    "auth0.com": _entry("Auth0", "https://auth0.com/docs/api", "https://auth0.com/docs"),
    # Cloud, hosting and monitoring
    "cloudflare.com": _entry("Cloudflare", "https://developers.cloudflare.com/api/", "https://developers.cloudflare.com/"),
    "vercel.com": _entry("Vercel", "https://vercel.com/docs/rest-api", "https://vercel.com/docs"),
    "netlify.com": _entry("Netlify", "https://docs.netlify.com/api/get-started/", "https://docs.netlify.com/"),
    "render.com": _entry("Render", "https://api-docs.render.com/", "https://render.com/docs"),
    "digitalocean.com": _entry("DigitalOcean", "https://docs.digitalocean.com/reference/api/", "https://docs.digitalocean.com/"),
    "heroku.com": _entry("Heroku Platform API", "https://devcenter.heroku.com/articles/platform-api-reference", "https://devcenter.heroku.com/"),
    "sentry.io": _entry("Sentry", "https://docs.sentry.io/api/", "https://docs.sentry.io/"),
    "datadoghq.com": _entry("Datadog", "https://docs.datadoghq.com/api/latest/", "https://docs.datadoghq.com/"),
    # Apple, package registries, research and books
    "apple.com": _entry("Apple Developer", "https://developer.apple.com/documentation/", "https://developer.apple.com/"),
    "ycombinator.com": _entry("Hacker News API (Firebase)", "https://github.com/HackerNews/API", "https://github.com/HackerNews/API"),
    "npmjs.com": _entry("npm registry", "https://github.com/npm/registry/blob/main/docs/REGISTRY-API.md", "https://docs.npmjs.com/"),
    "pypi.org": _entry("PyPI", "https://docs.pypi.org/api/", "https://docs.pypi.org/"),
    "huggingface.co": _entry("Hugging Face Hub", "https://huggingface.co/docs/hub/api", "https://huggingface.co/docs"),
    "kaggle.com": _entry("Kaggle API", "https://www.kaggle.com/docs/api", "https://www.kaggle.com/docs"),
    "arxiv.org": _entry("arXiv API", "https://info.arxiv.org/help/api/index.html", "https://info.arxiv.org/help/api/"),
    "crossref.org": _entry("Crossref REST API", "https://api.crossref.org/swagger-ui/index.html",
                           "https://www.crossref.org/documentation/retrieve-metadata/rest-api/"),
    "doi.org": _entry("DOI Foundation", None, "https://www.doi.org/",
                      "Resolution only; bibliographic metadata comes from the registration agency (e.g. Crossref REST API)."),
    "semanticscholar.org": _entry("Semantic Scholar API", "https://api.semanticscholar.org/api-docs/", "https://www.semanticscholar.org/product/api"),
    "openlibrary.org": _entry("Open Library API", "https://openlibrary.org/developers/api", "https://openlibrary.org/developers"),
    # US government data
    "nih.gov": _entry("NCBI E-utilities (NIH)", "https://www.ncbi.nlm.nih.gov/books/NBK25501/", "https://www.ncbi.nlm.nih.gov/home/develop/api/"),
    "nasa.gov": _entry("NASA Open APIs", "https://api.nasa.gov/", "https://api.nasa.gov/"),
    "weather.gov": _entry("NWS API", "https://www.weather.gov/documentation/services-web-api", "https://www.weather.gov/documentation/services-web-api"),
    "data.gov": _entry("api.data.gov", "https://api.data.gov/docs/developer-manual/", "https://api.data.gov/"),
    "sec.gov": _entry("SEC EDGAR APIs", "https://www.sec.gov/search-filings/edgar-application-programming-interfaces", None),
    "stlouisfed.org": _entry("FRED API", "https://fred.stlouisfed.org/docs/api/fred/", "https://fred.stlouisfed.org/docs/api/"),
    # Maps and weather
    "openweathermap.org": _entry("OpenWeather API", "https://openweathermap.org/api", "https://openweathermap.org/api"),
    "openstreetmap.org": _entry("OpenStreetMap API", "https://wiki.openstreetmap.org/wiki/API_v0.6", "https://wiki.openstreetmap.org/wiki/API"),
    "mapbox.com": _entry("Mapbox", "https://docs.mapbox.com/api/", "https://docs.mapbox.com/"),
    # News, reviews, entertainment and blogging
    "nytimes.com": _entry("NYTimes Developer Network", "https://developer.nytimes.com/apis", "https://developer.nytimes.com/"),
    "theguardian.com": _entry("The Guardian Open Platform", "https://open-platform.theguardian.com/documentation/", "https://open-platform.theguardian.com/"),
    "newsapi.org": _entry("NewsAPI", "https://newsapi.org/docs", "https://newsapi.org/"),
    "yelp.com": _entry("Yelp Fusion API", "https://docs.developer.yelp.com/docs/fusion-intro", "https://www.yelp.com/developers"),
    "themoviedb.org": _entry("TMDB API", "https://developer.themoviedb.org/docs", "https://developer.themoviedb.org/"),
    "imdb.com": _entry("IMDb", None, "https://data.imdb.com/", "No public web API; licensed datasets only."),
    "steampowered.com": _entry("Steam Web API", "https://partner.steamgames.com/doc/webapi_overview", "https://steamcommunity.com/dev"),
    "wordpress.com": _entry("WordPress.com REST API", "https://developer.wordpress.com/docs/api/", "https://developer.wordpress.com/"),
    "wordpress.org": _entry("WordPress REST API", "https://developer.wordpress.org/rest-api/", "https://developer.wordpress.org/"),
    "medium.com": _entry("Medium API", "https://github.com/Medium/medium-api-docs", None),
    "dev.to": _entry("Forem (dev.to) API", "https://developers.forem.com/api", "https://developers.forem.com/"),
    "producthunt.com": _entry("Product Hunt API", "https://api.producthunt.com/v2/docs", "https://api.producthunt.com/v2/docs"),
    # Crypto exchanges and market data
    "coinbase.com": _entry("Coinbase Developer Platform", "https://docs.cdp.coinbase.com/", "https://docs.cdp.coinbase.com/"),
    "binance.com": _entry("Binance API", "https://developers.binance.com/docs", "https://developers.binance.com/"),
    "kraken.com": _entry("Kraken API", "https://docs.kraken.com/api/", "https://docs.kraken.com/"),
    "okx.com": _entry("OKX API", "https://www.okx.com/docs-v5/en/", "https://www.okx.com/docs-v5/en/"),
    "bybit.com": _entry("Bybit API", "https://bybit-exchange.github.io/docs/", "https://bybit-exchange.github.io/docs/"),
    "coingecko.com": _entry("CoinGecko API", "https://docs.coingecko.com/", "https://docs.coingecko.com/"),
    "coinmarketcap.com": _entry("CoinMarketCap API", "https://coinmarketcap.com/api/documentation/v1/", "https://coinmarketcap.com/api/"),
    "alphavantage.co": _entry("Alpha Vantage", "https://www.alphavantage.co/documentation/", "https://www.alphavantage.co/"),
    "polygon.io": _entry("Massive (formerly Polygon.io)", "https://massive.com/docs", "https://massive.com/docs"),
    "massive.com": _entry("Massive (formerly Polygon.io)", "https://massive.com/docs", "https://massive.com/docs"),
    "finnhub.io": _entry("Finnhub", "https://finnhub.io/docs/api", "https://finnhub.io/docs/api"),
    "tradingview.com": _entry("TradingView", None, None, "No public REST API; charting library, widgets and alert webhooks only."),
    # AI model vendors
    "perplexity.ai": _entry("Perplexity API", "https://docs.perplexity.ai/", "https://docs.perplexity.ai/"),
    "mistral.ai": _entry("Mistral AI", "https://docs.mistral.ai/", "https://docs.mistral.ai/"),
    "cohere.com": _entry("Cohere", "https://docs.cohere.com/", "https://docs.cohere.com/"),
    "groq.com": _entry("Groq", "https://console.groq.com/docs", "https://console.groq.com/docs"),
    "replicate.com": _entry("Replicate", "https://replicate.com/docs", "https://replicate.com/docs"),
    "together.ai": _entry("Together AI", "https://docs.together.ai/", "https://docs.together.ai/"),
    "deepseek.com": _entry("DeepSeek API", "https://api-docs.deepseek.com/", "https://api-docs.deepseek.com/"),
    "x.ai": _entry("xAI API", "https://docs.x.ai/", "https://docs.x.ai/"),
    "openrouter.ai": _entry("OpenRouter", "https://openrouter.ai/docs", "https://openrouter.ai/docs"),
    "elevenlabs.io": _entry("ElevenLabs", "https://elevenlabs.io/docs", "https://elevenlabs.io/docs"),
    "stability.ai": _entry("Stability AI", "https://platform.stability.ai/docs", "https://platform.stability.ai/"),
    # Korean and Asian platforms
    "kakao.com": _entry("Kakao Developers", "https://developers.kakao.com/docs", "https://developers.kakao.com/"),
    "naver.com": _entry("NAVER Developers", None, "https://developers.naver.com/"),
    "line.me": _entry("LINE Developers", "https://developers.line.biz/en/docs/", "https://developers.line.biz/en/"),
    "coupang.com": _entry("Coupang Open API", None, "https://developers.coupang.com/"),
    "yahoo.com": _entry("Yahoo Developer Network", None, "https://developer.yahoo.com/"),
    # Travel, jobs and real estate (mostly partner-only)
    "booking.com": _entry("Booking.com", None, "https://developers.booking.com/", "Partner / affiliate programme access only; no open public API."),
    "expedia.com": _entry("Expedia Group", None, "https://developers.expediagroup.com/", "Expedia Partner Solutions (Rapid API) requires a partner agreement."),
    "airbnb.com": _entry("Airbnb", None, None, "No public API; partner programme only."),
    "indeed.com": _entry("Indeed", None, None, "No public job-search API; publisher and partner programmes only."),
    "glassdoor.com": _entry("Glassdoor", None, None, "No public API."),
    "zillow.com": _entry("Zillow", None, None, "Public Zillow API retired; listing data only via Bridge Interactive partner access."),
    "craigslist.org": _entry("Craigslist", None, None, "No public API; automated access is prohibited by the terms of use."),
    "viator.com": _entry("Viator Partner API", "https://docs.viator.com/partner-api/technical/", None, "Requires a Viator partner account."),
    "skyscanner.com": _entry("Skyscanner Partners API", "https://developers.skyscanner.net/docs/intro", None, "Requires partner approval."),
    "opentable.com": _entry("OpenTable API documentation", None, "https://docs.opentable.com/"),
    "trip.com": _entry("Trip.com Developer", None, "https://developer.trip.com/"),
    # US federal government and civic data
    "uscis.gov": _entry("USCIS Torch APIs", None, "https://developer.uscis.gov/"),
    "census.gov": _entry("U.S. Census Bureau Data API", "https://www.census.gov/data/developers/guidance/api-user-guide.html",
                         "https://www.census.gov/data/developers.html"),
    "bls.gov": _entry("BLS Public Data API", "https://www.bls.gov/developers/api_signature_v2.htm", "https://www.bls.gov/developers/"),
    "noaa.gov": _entry("NOAA NCEI data access", "https://www.ncei.noaa.gov/support/access-data-service-api-user-documentation",
                       "https://www.ncdc.noaa.gov/cdo-web/webservices/v2"),
    "usgs.gov": _entry("USGS web services", "https://earthquake.usgs.gov/fdsnws/event/1/", "https://api.waterdata.usgs.gov/"),
    "fda.gov": _entry("openFDA", "https://open.fda.gov/apis/", "https://open.fda.gov/"),
    "cdc.gov": _entry("CDC open data (data.cdc.gov)", None, "https://data.cdc.gov/"),
    "congress.gov": _entry("Congress.gov API", "https://api.congress.gov/", None),
    "loc.gov": _entry("Library of Congress APIs", "https://www.loc.gov/apis/json-and-yaml/", "https://www.loc.gov/apis/"),
    "govinfo.gov": _entry("GovInfo API", "https://api.govinfo.gov/docs/", "https://www.govinfo.gov/developers"),
    "usaspending.gov": _entry("USAspending API", "https://api.usaspending.gov/docs/endpoints", "https://api.usaspending.gov/"),
    "fec.gov": _entry("OpenFEC API", "https://api.open.fec.gov/developers/", None),
    "nps.gov": _entry("NPS Data API", "https://www.nps.gov/subjects/developer/api-documentation.htm",
                      "https://www.nps.gov/subjects/developer/index.htm"),
    "epa.gov": _entry("EPA Envirofacts Data Service API", "https://www.epa.gov/enviro/envirofacts-data-service-api", None),
    "treasury.gov": _entry("Treasury Fiscal Data API", "https://fiscaldata.treasury.gov/api-documentation/", "https://fiscaldata.treasury.gov/"),
    "gsa.gov": _entry("GSA open APIs", "https://open.gsa.gov/api/", "https://open.gsa.gov/"),
    "sam.gov": _entry("SAM.gov APIs (GSA)", "https://open.gsa.gov/api/entity-api/", "https://open.gsa.gov/api/"),
    "regulations.gov": _entry("Regulations.gov API (GSA)", "https://open.gsa.gov/api/regulationsgov/", None),
    "healthcare.gov": _entry("HealthCare.gov content API", "https://www.healthcare.gov/developers/", None),
    "cms.gov": _entry("CMS developer APIs", None, "https://developer.cms.gov/"),
    "medicare.gov": _entry("Medicare Blue Button 2.0 API (CMS)", "https://bluebutton.cms.gov/api-documentation/", "https://bluebutton.cms.gov/"),
    "va.gov": _entry("VA Lighthouse APIs", "https://developer.va.gov/explore", "https://developer.va.gov/"),
    "login.gov": _entry("Login.gov developer docs", "https://developers.login.gov/", "https://developers.login.gov/"),
    "usajobs.gov": _entry("USAJOBS API", "https://developer.usajobs.gov/api-reference/", "https://developer.usajobs.gov/"),
    "usps.com": _entry("USPS APIs", "https://developers.usps.com/apis", "https://developers.usps.com/"),
    "finra.org": _entry("FINRA API Platform", None, "https://developer.finra.org/"),
    "irs.gov": _entry("IRS bulk data downloads", "https://www.irs.gov/charities-non-profits/tax-exempt-organization-search-bulk-data-downloads", None),
    "justice.gov": _entry("Justice.gov API", "https://www.justice.gov/developer/api-documentation/api_v1", "https://www.justice.gov/developer"),
    "archives.gov": _entry("National Archives Catalog API", "https://www.archives.gov/research/catalog/help/api", None, "Requires an API key."),
    "recreation.gov": _entry("Recreation Information Database (RIDB) API", "https://ridb.recreation.gov/docs", "https://ridb.recreation.gov/",
                             "Requires an API key."),
    "ed.gov": _entry("College Scorecard API (U.S. Department of Education)", "https://collegescorecard.ed.gov/data/api-documentation", "https://collegescorecard.ed.gov/data/",
                     "Requires an api.data.gov key."),
    # UK, Canada, international bodies, Korean and Japanese public data
    "service.gov.uk": _entry("UK government API catalogue", None, "https://www.api.gov.uk/",
                             "HMRC APIs: https://developer.service.hmrc.gov.uk/api-documentation"),
    "hmrc.gov.uk": _entry("HMRC Developer Hub", "https://developer.service.hmrc.gov.uk/api-documentation",
                          "https://developer.service.hmrc.gov.uk/"),
    "ons.gov.uk": _entry("ONS Developer Hub", None, "https://developer.ons.gov.uk/"),
    "tfl.gov.uk": _entry("TfL Unified API", "https://api.tfl.gov.uk/", "https://api-portal.tfl.gov.uk/"),
    "legislation.gov.uk": _entry("legislation.gov.uk API", "https://www.legislation.gov.uk/developer", None),
    "data.police.uk": _entry("data.police.uk API", "https://data.police.uk/docs/", "https://data.police.uk/"),
    "metoffice.gov.uk": _entry("Met Office Weather DataHub", None, "https://datahub.metoffice.gov.uk/"),
    "canada.ca": _entry("Government of Canada open data API", "https://open.canada.ca/en/access-our-application-programming-interface-api", None),
    "who.int": _entry("WHO GHO OData API", "https://www.who.int/data/gho/info/gho-odata-api", None),
    "worldbank.org": _entry("World Bank Indicators API",
                            "https://datahelpdesk.worldbank.org/knowledgebase/articles/889392-about-the-indicators-api-documentation", None),
    "abs.gov.au": _entry("ABS Data API",
                         "https://www.abs.gov.au/about/data-services/application-programming-interfaces-apis/data-api-user-guide", None),
    "e-stat.go.jp": _entry("e-Stat API (Statistics Japan)", "https://www.e-stat.go.jp/api/", None),
    "gov.kr": _entry("Government24 (gov.kr)", None, "https://www.data.go.kr/",
                     "Korean government open APIs are published through the Public Data Portal (data.go.kr)."),
    "data.go.kr": _entry("Public Data Portal (data.go.kr)", None, "https://www.data.go.kr/"),
    "fss.or.kr": _entry("OpenDART (Financial Supervisory Service)", "https://opendart.fss.or.kr/guide/main.do", "https://opendart.fss.or.kr/"),
    "bok.or.kr": _entry("Bank of Korea ECOS Open API", "https://ecos.bok.or.kr/api/", None),
    "kosis.kr": _entry("KOSIS Open API (Statistics Korea)", "https://kosis.kr/openapi/", None),
    "kma.go.kr": _entry("KMA API Hub (Korea Meteorological Administration)", None, "https://apihub.kma.go.kr/"),
    "seoul.go.kr": _entry("Seoul Open Data Plaza", None, "https://data.seoul.go.kr/"),
    "law.go.kr": _entry("Korean National Law Information open API", None, "https://open.law.go.kr/LSO/main.do"),
    # Airlines, travel and mobility
    "lufthansa.com": _entry("Lufthansa Open API", "https://developer.lufthansa.com/docs", "https://developer.lufthansa.com/"),
    "klm.com": _entry("Air France-KLM Developer", None, "https://developer.airfranceklm.com/"),
    "airfrance.com": _entry("Air France-KLM Developer", None, "https://developer.airfranceklm.com/"),
    "amadeus.com": _entry("Amadeus for Developers", None, "https://developers.amadeus.com/"),
    "sabre.com": _entry("Sabre Dev Studio", None, "https://developer.sabre.com/"),
    "skyscanner.net": _entry("Skyscanner Partners API", "https://developers.skyscanner.net/docs/intro", "https://developers.skyscanner.net/"),
    "hotels.com": _entry("Expedia Group (Hotels.com)", None, "https://developers.expediagroup.com/",
                         "Expedia Partner Solutions (Rapid API) requires a partner agreement."),
    "vrbo.com": _entry("Expedia Group (Vrbo)", None, "https://developers.expediagroup.com/",
                       "Expedia Partner Solutions (Rapid API) requires a partner agreement."),
    "hotelbeds.com": _entry("Hotelbeds APItude", None, "https://developer.hotelbeds.com/"),
    "duffel.com": _entry("Duffel API", "https://duffel.com/docs/api", "https://duffel.com/docs"),
    "uber.com": _entry("Uber Developers", "https://developer.uber.com/docs", "https://developer.uber.com/"),
    "ubereats.com": _entry("Uber Eats APIs (Uber Developers)", "https://developer.uber.com/docs/eats/introduction", "https://developer.uber.com/"),
    "grab.com": _entry("Grab Developer", None, "https://developer.grab.com/"),
    "flightaware.com": _entry("FlightAware AeroAPI", "https://www.flightaware.com/aeroapi/portal/documentation", "https://www.flightaware.com/commercial/aeroapi/",
                              "Requires an API key; paid tiers."),
    "turkishairlines.com": _entry("Turkish Airlines Developer Portal", None, "https://developer.turkishairlines.com/"),
    "mbta.com": _entry("MBTA V3 API", "https://www.mbta.com/developers/v3-api", "https://www.mbta.com/developers"),
    # Banks, card networks, brokerages and fintech
    "chase.com": _entry("Chase Developer", None, "https://developer.chase.com/"),
    "jpmorgan.com": _entry("J.P. Morgan Payments Developer", None, "https://developer.payments.jpmorgan.com/"),
    "wellsfargo.com": _entry("Wells Fargo Developer", None, "https://developer.wellsfargo.com/"),
    "citi.com": _entry("Citi Developer", None, "https://developer.citi.com/"),
    "capitalone.com": _entry("Capital One DevExchange", None, "https://developer.capitalone.com/"),
    "americanexpress.com": _entry("American Express Developers", None, "https://developer.americanexpress.com/"),
    "visa.com": _entry("Visa Developer", None, "https://developer.visa.com/"),
    "mastercard.com": _entry("Mastercard Developers", None, "https://developer.mastercard.com/"),
    "discover.com": _entry("Discover Developer", None, "https://developer.discover.com/"),
    "venmo.com": _entry("Venmo (PayPal)", "https://developer.paypal.com/docs/checkout/pay-with-venmo/", "https://developer.paypal.com/"),
    "robinhood.com": _entry("Robinhood Crypto Trading API", "https://docs.robinhood.com/crypto/trading/", "https://docs.robinhood.com/"),
    "interactivebrokers.com": _entry("Interactive Brokers API", "https://www.interactivebrokers.com/campus/ibkr-api-page/ibkr-api-home/", None),
    "alpaca.markets": _entry("Alpaca", "https://docs.alpaca.markets/reference", "https://docs.alpaca.markets/"),
    "tradestation.com": _entry("TradeStation API", "https://api.tradestation.com/docs/", None),
    "tastytrade.com": _entry("tastytrade Open API", None, "https://developer.tastytrade.com/"),
    "wise.com": _entry("Wise Platform API", "https://docs.wise.com/api-reference", "https://docs.wise.com/"),
    "monzo.com": _entry("Monzo API", "https://docs.monzo.com/", None),
    "intuit.com": _entry("Intuit Developer (QuickBooks)", None, "https://developer.intuit.com/"),
    "xero.com": _entry("Xero Developer", "https://developer.xero.com/documentation/", "https://developer.xero.com/"),
    "freshbooks.com": _entry("FreshBooks API", "https://www.freshbooks.com/api/start", None),
    "brex.com": _entry("Brex Developer", None, "https://developer.brex.com/"),
    "ramp.com": _entry("Ramp Developer", None, "https://docs.ramp.com/"),
    "mercury.com": _entry("Mercury API", "https://docs.mercury.com/reference", "https://docs.mercury.com/"),
    "nasdaq.com": _entry("Nasdaq Data Link", None, "https://data.nasdaq.com/"),
    "twelvedata.com": _entry("Twelve Data", "https://twelvedata.com/docs", None),
    "tiingo.com": _entry("Tiingo API", "https://www.tiingo.com/documentation/general/overview", None),
    "eodhd.com": _entry("EODHD APIs", "https://eodhd.com/financial-apis/", None),
    # Payment processors and commerce billing
    "braintreepayments.com": _entry("Braintree (PayPal)", "https://developer.paypal.com/braintree/docs", None),
    "checkout.com": _entry("Checkout.com", "https://api-reference.checkout.com/", "https://www.checkout.com/docs"),
    "worldpay.com": _entry("Worldpay Developer", None, "https://docs.worldpay.com/access/"),
    "authorize.net": _entry("Authorize.net Developer", "https://developer.authorize.net/api/reference/", "https://developer.authorize.net/"),
    "klarna.com": _entry("Klarna Docs", None, "https://docs.klarna.com/"),
    "affirm.com": _entry("Affirm Docs", None, "https://docs.affirm.com/"),
    "razorpay.com": _entry("Razorpay", "https://razorpay.com/docs/api/", "https://razorpay.com/docs/"),
    "flutterwave.com": _entry("Flutterwave", None, "https://developer.flutterwave.com/"),
    "mollie.com": _entry("Mollie", "https://docs.mollie.com/reference/overview", "https://docs.mollie.com/"),
    "gocardless.com": _entry("GoCardless", "https://docs.gocardless.com/docs/api-reference", "https://docs.gocardless.com/"),
    "paddle.com": _entry("Paddle", "https://developer.paddle.com/api-reference/overview", "https://developer.paddle.com/"),
    "lemonsqueezy.com": _entry("Lemon Squeezy", "https://docs.lemonsqueezy.com/api", "https://docs.lemonsqueezy.com/"),
    "mercadopago.com": _entry("Mercado Pago Developers", None, "https://www.mercadopago.com.ar/developers/en"),
    # Crypto exchanges and blockchain data
    "gemini.com": _entry("Gemini Exchange API", "https://developer.gemini.com/", None),
    "bitstamp.net": _entry("Bitstamp API", "https://www.bitstamp.net/api/", None),
    "bitfinex.com": _entry("Bitfinex API", "https://docs.bitfinex.com/docs", "https://docs.bitfinex.com/"),
    "kucoin.com": _entry("KuCoin API", "https://www.kucoin.com/docs-new", None),
    "crypto.com": _entry("Crypto.com Exchange API", "https://exchange-developer.crypto.com/exchange/v1", None),
    "bitget.com": _entry("Bitget API", "https://www.bitget.com/api-doc/common/intro", None),
    "gate.com": _entry("Gate API v4", "https://www.gate.com/docs/developers/apiv4/", None),
    "upbit.com": _entry("Upbit Open API", "https://docs.upbit.com/", None),
    "bithumb.com": _entry("Bithumb API", "https://apidocs.bithumb.com/", None),
    "etherscan.io": _entry("Etherscan API", "https://docs.etherscan.io/", None),
    "alchemy.com": _entry("Alchemy", "https://www.alchemy.com/docs", None),
    # Korean finance and payments
    "koreainvestment.com": _entry("Korea Investment & Securities KIS Developers", None, "https://apiportal.koreainvestment.com/"),
    "kiwoom.com": _entry("Kiwoom Securities REST API", None, "https://openapi.kiwoom.com/"),
    "tosspayments.com": _entry("Toss Payments", "https://docs.tosspayments.com/reference", "https://developers.tosspayments.com/"),
    "kakaopay.com": _entry("KakaoPay Developers", None, "https://developers.kakaopay.com/"),
    "portone.io": _entry("PortOne", None, "https://developers.portone.io/"),
    # Ticketing and events
    "ticketmaster.com": _entry("Ticketmaster Developer", "https://developer.ticketmaster.com/products-and-docs/apis/getting-started/",
                               "https://developer.ticketmaster.com/"),
    "livenation.com": _entry("Ticketmaster Developer (Live Nation)", "https://developer.ticketmaster.com/products-and-docs/apis/getting-started/",
                             "https://developer.ticketmaster.com/"),
    "stubhub.com": _entry("StubHub Developer", None, "https://developer.stubhub.com/"),
    "eventbrite.com": _entry("Eventbrite API", "https://www.eventbrite.com/platform/api", "https://www.eventbrite.com/platform/"),
    "seatgeek.com": _entry("SeatGeek Platform", None, "https://developer.seatgeek.com/"),
    "meetup.com": _entry("Meetup API", "https://www.meetup.com/api/guide/", None),
    # Retail, marketplaces and delivery
    "bestbuy.com": _entry("Best Buy Developer", None, "https://developer.bestbuy.com/"),
    "kroger.com": _entry("Kroger Developers", None, "https://developer.kroger.com/"),
    "walgreens.com": _entry("Walgreens Developer", None, "https://developer.walgreens.com/"),
    "aliexpress.com": _entry("AliExpress Open Platform", None, "https://openservice.aliexpress.com/"),
    "taobao.com": _entry("Taobao Open Platform", None, "https://open.taobao.com/"),
    "jd.com": _entry("JD Open Platform", None, "https://open.jd.com/"),
    "rakuten.co.jp": _entry("Rakuten Web Service", "https://webservice.rakuten.co.jp/documentation", "https://webservice.rakuten.co.jp/"),
    "mercadolibre.com": _entry("Mercado Libre Developers", None, "https://developers.mercadolibre.com.ar/"),
    "11st.co.kr": _entry("11st Open API", None, "https://openapi.11st.co.kr/"),
    "bigcommerce.com": _entry("BigCommerce Developer", None, "https://developer.bigcommerce.com/"),
    "woocommerce.com": _entry("WooCommerce REST API", "https://woocommerce.github.io/woocommerce-rest-api-docs/",
                              "https://developer.woocommerce.com/docs/"),
    "wix.com": _entry("Wix Developers", "https://dev.wix.com/docs/rest", "https://dev.wix.com/"),
    "squarespace.com": _entry("Squarespace Developers", None, "https://developers.squarespace.com/"),
    "instacart.com": _entry("Instacart Developer Platform", "https://docs.instacart.com/", None),
    "doordash.com": _entry("DoorDash Developer", None, "https://developer.doordash.com/"),
    "deliveroo.co.uk": _entry("Deliveroo API", "https://api-docs.deliveroo.com/", None),
    "printful.com": _entry("Printful API", "https://developers.printful.com/docs/", None),
    "gumroad.com": _entry("Gumroad API", "https://gumroad.com/api", None),
    "wayfair.com": _entry("Wayfair Developer Portal", None, "https://developer.wayfair.com/"),
    "newegg.com": _entry("Newegg Developer Portal", None, "https://developer.newegg.com/"),
    "grubhub.com": _entry("Grubhub Developer", None, "https://developer.grubhub.com/"),
    "thumbtack.com": _entry("Thumbtack Developers", None, "https://developers.thumbtack.com/"),
    "orbitz.com": _entry("Expedia Group (Orbitz)", None, "https://developers.expediagroup.com/",
                         "Expedia Partner Solutions (Rapid API) requires a partner agreement."),
    "travelocity.com": _entry("Expedia Group (Travelocity)", None, "https://developers.expediagroup.com/",
                              "Expedia Partner Solutions (Rapid API) requires a partner agreement."),
    "shein.com": _entry("SHEIN Open Platform", None, "https://open.sheincorp.com/"),
    # Jobs and freelancing
    "greenhouse.io": _entry("Greenhouse", "https://docs.greenhouse.io/harvest.html", "https://developers.greenhouse.io/"),
    "lever.co": _entry("Lever", "https://hire.lever.co/developer/documentation", None),
    "freelancer.com": _entry("Freelancer.com API", None, "https://developers.freelancer.com/"),
    "adzuna.com": _entry("Adzuna API", "https://developer.adzuna.com/overview", "https://developer.adzuna.com/"),
    # Social, community and creator platforms
    "threads.net": _entry("Threads API (Meta)", "https://developers.facebook.com/docs/threads/", _META),
    "threads.com": _entry("Threads API (Meta)", "https://developers.facebook.com/docs/threads/", _META),
    "meta.com": _entry("Meta for Developers", None, "https://developers.meta.com/"),
    "bsky.app": _entry("Bluesky / AT Protocol", "https://bsky.network/docs/category/http-reference/", "https://bsky.network/"),
    "joinmastodon.org": _entry("Mastodon API", "https://docs.joinmastodon.org/api/", "https://docs.joinmastodon.org/"),
    "mastodon.social": _entry("Mastodon API", "https://docs.joinmastodon.org/api/", "https://docs.joinmastodon.org/"),
    "snapchat.com": _entry("Snap for Developers", None, "https://developers.snap.com/"),
    "snap.com": _entry("Snap for Developers", None, "https://developers.snap.com/"),
    "weibo.com": _entry("Weibo Open Platform", None, "https://open.weibo.com/"),
    "patreon.com": _entry("Patreon API", "https://docs.patreon.com/", None),
    "kick.com": _entry("Kick Developer Docs", "https://docs.kick.com/", None),
    "dailymotion.com": _entry("Dailymotion for Developers", None, "https://developers.dailymotion.com/"),
    "imgur.com": _entry("Imgur API", "https://apidocs.imgur.com/", None),
    "dribbble.com": _entry("Dribbble API", "https://developer.dribbble.com/v2/", "https://developer.dribbble.com/"),
    "ghost.org": _entry("Ghost APIs", None, "https://docs.ghost.org/"),
    "blogger.com": _entry("Blogger API", "https://developers.google.com/blogger", _GOOGLE),
    "blogspot.com": _entry("Blogger API", "https://developers.google.com/blogger", _GOOGLE),
    "gravatar.com": _entry("Gravatar", "https://docs.gravatar.com/", None),
    "qiita.com": _entry("Qiita API", "https://qiita.com/api/v2/docs", None),
    "hatena.ne.jp": _entry("Hatena Developer Center", None, "https://developer.hatena.ne.jp/"),
    "yahoo.co.jp": _entry("Yahoo! JAPAN Developer Network", None, "https://developer.yahoo.co.jp/"),
    "daum.net": _entry("Daum search API (Kakao Developers)", "https://developers.kakao.com/docs/latest/ko/daum-search/common",
                       "https://developers.kakao.com/"),
    # Music, fitness, games and media data
    "genius.com": _entry("Genius API", "https://docs.genius.com/", None),
    "musicbrainz.org": _entry("MusicBrainz API", "https://musicbrainz.org/doc/MusicBrainz_API", None),
    "deezer.com": _entry("Deezer for Developers", None, "https://developers.deezer.com/"),
    "tidal.com": _entry("TIDAL Developer", None, "https://developer.tidal.com/"),
    "anilist.co": _entry("AniList API", "https://docs.anilist.co/", None),
    "myanimelist.net": _entry("MyAnimeList API", "https://myanimelist.net/apiconfig/references/api/v2", None),
    "strava.com": _entry("Strava API", "https://developers.strava.com/docs/reference/", "https://developers.strava.com/"),
    "fitbit.com": _entry("Fitbit Web API", "https://dev.fitbit.com/build/reference/web-api/", "https://dev.fitbit.com/"),
    "garmin.com": _entry("Garmin Developer", None, "https://developer.garmin.com/"),
    "withings.com": _entry("Withings Developer", "https://developer.withings.com/api-reference", "https://developer.withings.com/"),
    "ouraring.com": _entry("Oura API", "https://cloud.ouraring.com/v2/docs", "https://cloud.ouraring.com/docs/"),
    "roblox.com": _entry("Roblox Open Cloud", "https://create.roblox.com/docs/cloud", "https://create.roblox.com/docs"),
    "unity.com": _entry("Unity Docs", None, "https://docs.unity.com/"),
    "unity3d.com": _entry("Unity Scripting API", "https://docs.unity3d.com/ScriptReference/", "https://docs.unity3d.com/"),
    "riotgames.com": _entry("Riot Developer Portal", "https://developer.riotgames.com/apis", "https://developer.riotgames.com/"),
    "battle.net": _entry("Blizzard Battle.net API", "https://community.developer.battle.net/documentation",
                            "https://community.developer.battle.net/"),
    "blizzard.com": _entry("Blizzard Battle.net API", "https://community.developer.battle.net/documentation",
                              "https://community.developer.battle.net/"),
    "steamcommunity.com": _entry("Steam Web API", "https://partner.steamgames.com/doc/webapi_overview", "https://steamcommunity.com/dev"),
    "igdb.com": _entry("IGDB API", "https://api-docs.igdb.com/", None),
    "lichess.org": _entry("Lichess API", "https://lichess.org/api", None),
    "wolframalpha.com": _entry("Wolfram|Alpha APIs", "https://products.wolframalpha.com/api/documentation", "https://developer.wolframalpha.com/"),
    "netflix.com": _entry("Netflix", None, None,
                          "No public API (the open API was retired in 2014); service status: https://help.netflix.com/en/is-netflix-down"),
    "leagueoflegends.com": _entry("Riot Games Developer Portal", "https://developer.riotgames.com/apis", "https://developer.riotgames.com/",
                                  "Requires a Riot developer key."),
    # News publishers
    "ft.com": _entry("FT Developer", None, "https://developer.ft.com/portal"),
    "apnews.com": _entry("AP Developer", None, "https://developer.ap.org/"),
    # Search APIs
    "bing.com": _entry("Bing Webmaster API (Microsoft)", "https://learn.microsoft.com/en-us/bingwebmaster/", None,
                       "The Bing Search APIs were retired on 2025-08-11."),
    "brave.com": _entry("Brave Search API", "https://api-dashboard.search.brave.com/app/documentation", None),
    "kagi.com": _entry("Kagi API", "https://help.kagi.com/kagi/api/overview.html", None),
    "you.com": _entry("You.com API", "https://you.com/docs/welcome", None),
    "exa.ai": _entry("Exa API", "https://exa.ai/docs", None),
    "tavily.com": _entry("Tavily API", "https://docs.tavily.com/", None),
    "serpapi.com": _entry("SerpApi", "https://serpapi.com/search-api", None),
    # Mail, Microsoft 365 and Apple services
    "gmail.com": _entry("Gmail API", "https://developers.google.com/workspace/gmail/api/guides", _GOOGLE),
    "outlook.com": _entry("Outlook mail via Microsoft Graph", "https://learn.microsoft.com/en-us/graph/outlook-mail-concept-overview", _MS_LEARN),
    "live.com": _entry("Microsoft Graph (Microsoft account services)", "https://learn.microsoft.com/en-us/graph/api/overview", _MS_LEARN),
    "office.com": _entry("Microsoft 365 via Microsoft Graph", "https://learn.microsoft.com/en-us/graph/api/overview", _MS_LEARN),
    "sharepoint.com": _entry("SharePoint development", "https://learn.microsoft.com/en-us/sharepoint/dev/", _MS_LEARN),
    "microsoftonline.com": _entry("Microsoft identity platform", "https://learn.microsoft.com/en-us/entra/identity-platform/", _MS_LEARN),
    "icloud.com": _entry("Apple CloudKit", "https://developer.apple.com/documentation/cloudkit", "https://developer.apple.com/icloud/"),
    "fastmail.com": _entry("Fastmail JMAP API", "https://www.fastmail.com/dev/", None),
    "zoho.com": _entry("Zoho Developer", None, "https://www.zoho.com/developer/"),
    # Email delivery and marketing
    "mailgun.com": _entry("Mailgun", None, "https://documentation.mailgun.com/"),
    "postmarkapp.com": _entry("Postmark", "https://postmarkapp.com/developer", None),
    "resend.com": _entry("Resend", "https://resend.com/docs/api-reference/introduction", "https://resend.com/docs"),
    "brevo.com": _entry("Brevo API", "https://developers.brevo.com/reference", "https://developers.brevo.com/"),
    "mailjet.com": _entry("Mailjet API", "https://dev.mailjet.com/email/reference/", "https://dev.mailjet.com/"),
    "klaviyo.com": _entry("Klaviyo Developers", None, "https://developers.klaviyo.com/"),
    "kit.com": _entry("Kit (ConvertKit) API", None, "https://developers.kit.com/"),
    # Cloud, data and infrastructure
    "oracle.com": _entry("Oracle Cloud Infrastructure", "https://docs.oracle.com/en-us/iaas/api/", "https://docs.oracle.com/en/"),
    "ibm.com": _entry("IBM Cloud Docs", "https://cloud.ibm.com/docs", "https://developer.ibm.com/"),
    "alibabacloud.com": _entry("Alibaba Cloud", "https://api.alibabacloud.com/", "https://www.alibabacloud.com/help/en/"),
    "aliyun.com": _entry("Alibaba Cloud (Aliyun)", "https://api.aliyun.com/", "https://help.aliyun.com/"),
    "tencentcloud.com": _entry("Tencent Cloud API", "https://www.tencentcloud.com/document/api", None),
    "ncloud.com": _entry("NAVER Cloud Platform API", "https://api.ncloud-docs.com/docs/en/home", None),
    "akamai.com": _entry("Akamai TechDocs", None, "https://techdocs.akamai.com/"),
    "linode.com": _entry("Linode API (Akamai)", "https://techdocs.akamai.com/linode-api/reference/api", "https://techdocs.akamai.com/"),
    "hetzner.com": _entry("Hetzner Cloud API", "https://docs.hetzner.cloud/", None),
    "scaleway.com": _entry("Scaleway APIs", "https://www.scaleway.com/en/developers/api/", None),
    "fly.io": _entry("Fly.io Machines API", "https://docs.fly.io/machines/api", "https://docs.fly.io/"),
    "railway.com": _entry("Railway Public API", "https://docs.railway.com/reference/public-api", "https://docs.railway.com/"),
    "supabase.com": _entry("Supabase", "https://supabase.com/docs/reference/api/introduction", "https://supabase.com/docs"),
    "neon.tech": _entry("Neon API", "https://api-docs.neon.tech/reference/getting-started-with-neon-api", None),
    "mongodb.com": _entry("MongoDB Atlas Administration API", "https://www.mongodb.com/docs/atlas/api/", "https://www.mongodb.com/docs/"),
    "snowflake.com": _entry("Snowflake SQL API", "https://docs.snowflake.com/en/developer-guide/sql-api/index", "https://docs.snowflake.com/"),
    "databricks.com": _entry("Databricks REST API", "https://docs.databricks.com/api/workspace/introduction", "https://docs.databricks.com/"),
    "elastic.co": _entry("Elastic APIs", "https://www.elastic.co/docs/api/", "https://www.elastic.co/docs"),
    "redis.io": _entry("Redis Cloud API", "https://redis.io/docs/latest/operate/rc/api/", "https://redis.io/docs/latest/"),
    "confluent.io": _entry("Confluent Cloud APIs", "https://docs.confluent.io/cloud/current/api.html", "https://docs.confluent.io/"),
    "docker.com": _entry("Docker APIs", "https://docs.docker.com/reference/api/engine/", "https://docs.docker.com/"),
    "kubernetes.io": _entry("Kubernetes API reference", "https://kubernetes.io/docs/reference/kubernetes-api/", "https://kubernetes.io/docs/"),
    "hashicorp.com": _entry("HashiCorp Developer", None, "https://developer.hashicorp.com/"),
    "terraform.io": _entry("HCP Terraform API (HashiCorp)", "https://developer.hashicorp.com/terraform/cloud-docs/api-docs",
                           "https://developer.hashicorp.com/terraform"),
    # Observability, DevOps and developer tooling
    "grafana.com": _entry("Grafana HTTP API", "https://grafana.com/docs/grafana/latest/developers/http_api/", "https://grafana.com/docs/"),
    "newrelic.com": _entry("New Relic APIs", "https://docs.newrelic.com/docs/apis/intro-apis/introduction-new-relic-apis/",
                           "https://docs.newrelic.com/"),
    "pagerduty.com": _entry("PagerDuty Developer", "https://docs.pagerduty.com/developer/api/reference", "https://docs.pagerduty.com/developer"),
    "statuspage.io": _entry("Atlassian Statuspage API", "https://developer.statuspage.io/", None),
    "splunk.com": _entry("Splunk Developer", None, "https://dev.splunk.com/"),
    "circleci.com": _entry("CircleCI API v2", "https://circleci.com/docs/api/v2/", "https://circleci.com/docs/"),
    "jenkins.io": _entry("Jenkins remote access API", "https://www.jenkins.io/doc/book/using/remote-access-api/", "https://www.jenkins.io/doc/"),
    "postman.com": _entry("Postman API", "https://learning.postman.com/docs/developer/postman-api/intro-api/", "https://learning.postman.com/docs/"),
    "launchdarkly.com": _entry("LaunchDarkly API", "https://apidocs.launchdarkly.com/", "https://launchdarkly.com/docs/"),
    "codeberg.org": _entry("Codeberg (Forgejo) API", "https://codeberg.org/api/swagger", "https://docs.codeberg.org/"),
    "sourceforge.net": _entry("SourceForge API", "https://sourceforge.net/p/forge/documentation/API/", None),
    "launchpad.net": _entry("Launchpad web service API", "https://ubuntu.com/docs/launchpad/user/API/", None),
    "gitee.com": _entry("Gitee OpenAPI", "https://gitee.com/api/v5/swagger", None),
    "readthedocs.org": _entry("Read the Docs API", "https://docs.readthedocs.com/platform/stable/api/v3.html", "https://docs.readthedocs.com/"),
    "android.com": _entry("Android Developers", "https://developer.android.com/reference", "https://developer.android.com/"),
    "chrome.com": _entry("Chrome for Developers", "https://developer.chrome.com/docs/extensions/reference/api", "https://developer.chrome.com/"),
    "mozilla.org": _entry("MDN Web Docs (Mozilla)", None, "https://developer.mozilla.org/"),
    "adobe.com": _entry("Adobe Developer", None, "https://developer.adobe.com/",
                        "Adobe Commerce (Magento) web APIs: https://developer.adobe.com/commerce/webapi/"),
    "adobe.io": _entry("Adobe Developer", None, "https://developer.adobe.com/"),
    "autodesk.com": _entry("Autodesk Platform Services", None, "https://aps.autodesk.com/"),
    "canva.com": _entry("Canva Connect APIs", "https://www.canva.dev/docs/connect/", "https://www.canva.dev/"),
    "sanity.io": _entry("Sanity HTTP API", "https://www.sanity.io/docs/http-api", "https://www.sanity.io/docs"),
    "algolia.com": _entry("Algolia", "https://www.algolia.com/doc/rest-api/search/", "https://www.algolia.com/doc/"),
    # Productivity, CRM and communications APIs
    "linear.app": _entry("Linear API", "https://linear.app/developers", None),
    "clickup.com": _entry("ClickUp API", "https://developer.clickup.com/reference", "https://developer.clickup.com/"),
    "monday.com": _entry("monday.com API", "https://developer.monday.com/api-reference/", "https://developer.monday.com/"),
    "todoist.com": _entry("Todoist API", "https://developer.todoist.com/", None),
    "miro.com": _entry("Miro Developer Platform", "https://developers.miro.com/reference", "https://developers.miro.com/"),
    "calendly.com": _entry("Calendly API", None, "https://developer.calendly.com/"),
    "typeform.com": _entry("Typeform Developers", None, "https://www.typeform.com/developers/"),
    "surveymonkey.com": _entry("SurveyMonkey API", "https://api.surveymonkey.com/v3/docs", None),
    "docusign.com": _entry("Docusign Developer", "https://developers.docusign.com/docs/", "https://developers.docusign.com/"),
    "intercom.com": _entry("Intercom Developers", "https://developers.intercom.com/docs", "https://developers.intercom.com/"),
    "freshdesk.com": _entry("Freshdesk API", "https://developers.freshdesk.com/api/", None),
    "freshworks.com": _entry("Freshworks Developers", None, "https://developers.freshworks.com/"),
    "servicenow.com": _entry("ServiceNow Developer", None, "https://developer.servicenow.com/"),
    "pipedrive.com": _entry("Pipedrive API", "https://developers.pipedrive.com/docs/api/v1", "https://developers.pipedrive.com/"),
    "zapier.com": _entry("Zapier Developer Platform", None, "https://docs.zapier.com/"),
    "make.com": _entry("Make Developer Hub", None, "https://developers.make.com/"),
    "ifttt.com": _entry("IFTTT Developer", None, "https://ifttt.com/docs"),
    "tableau.com": _entry("Tableau REST API", "https://help.tableau.com/current/api/rest_api/en-us/REST/rest_api.htm", None),
    "sap.com": _entry("SAP Business Accelerator Hub", None, "https://api.sap.com/"),
    "webex.com": _entry("Webex for Developers", None, "https://developer.webex.com/"),
    "ringcentral.com": _entry("RingCentral Developers", "https://developers.ringcentral.com/api-reference", "https://developers.ringcentral.com/"),
    "vonage.com": _entry("Vonage API Developer", None, "https://developer.vonage.com/"),
    "plivo.com": _entry("Plivo", "https://www.plivo.com/docs/", None),
    "sinch.com": _entry("Sinch Developers", None, "https://developers.sinch.com/"),
    "g2.com": _entry("G2 API", "https://data.g2.com/api/docs", None, "Requires an API token."),
    # Package registries, web standards and archives
    "crates.io": _entry("crates.io", "https://crates.io/data-access", "https://doc.rust-lang.org/cargo/reference/registry-web-api.html"),
    "rubygems.org": _entry("RubyGems.org API", "https://guides.rubygems.org/rubygems-org-api/", "https://guides.rubygems.org/"),
    "packagist.org": _entry("Packagist API", "https://packagist.org/apidoc", None),
    "nuget.org": _entry("NuGet Server API", "https://learn.microsoft.com/en-us/nuget/api/overview", None),
    "maven.org": _entry("Maven Central Search API (Sonatype)", "https://central.sonatype.org/search/rest-api-guide/",
                        "https://central.sonatype.org/"),
    "go.dev": _entry("Go module proxy protocol", "https://go.dev/ref/mod#goproxy-protocol", "https://go.dev/doc/"),
    "jsdelivr.com": _entry("jsDelivr data API", "https://www.jsdelivr.com/docs/data.jsdelivr.com", None),
    "cdnjs.com": _entry("cdnjs API", "https://cdnjs.com/api", None),
    "w3.org": _entry("W3C API", "https://api.w3.org/doc", None),
    "ietf.org": _entry("IETF Datatracker API", "https://datatracker.ietf.org/api/", None),
    "archive.org": _entry("Internet Archive APIs", "https://archive.org/developers/", None,
                          "Wayback Machine availability API: https://archive.org/help/wayback_api.php"),
    "wikidata.org": _entry("Wikidata data access", "https://www.wikidata.org/wiki/Wikidata:Data_access", None),
    "haskell.org": _entry("Hackage API", "https://hackage.haskell.org/api", "https://hackage.haskell.org/"),
    "metacpan.org": _entry("MetaCPAN API", "https://github.com/metacpan/metacpan-api/blob/master/docs/API-docs.md", None),
    "npmjs.org": _entry("npm registry", "https://github.com/npm/registry/blob/main/docs/REGISTRY-API.md", "https://docs.npmjs.com/"),
    "packagecloud.io": _entry("packagecloud API", "https://packagecloud.io/docs/api", "https://packagecloud.io/docs", "Requires an API token."),
    "pub.dev": _entry("pub.dev API", "https://pub.dev/help/api", None),
    "pypa.io": _entry("Python Packaging Authority specifications", "https://packaging.python.org/en/latest/specifications/simple-repository-api/", "https://packaging.python.org/"),
    "schemastore.org": _entry("SchemaStore catalog", "https://www.schemastore.org/api/json/catalog.json", "https://www.schemastore.org/"),
    "golang.org": _entry("Go module proxy protocol", "https://go.dev/ref/mod#goproxy-protocol", "https://pkg.go.dev/"),
    "ghcr.io": _entry("GitHub Container registry", "https://docs.github.com/en/packages/working-with-a-github-packages-registry/working-with-the-container-registry", "https://docs.github.com/en/packages"),
    "quay.io": _entry("Quay API", "https://docs.quay.io/api/", "https://docs.quay.io/"),
    "archlinux.org": _entry("Arch User Repository RPC interface", "https://aur.archlinux.org/rpc", None),
    "fedoraproject.org": _entry("Fedora Bodhi REST API", "https://bodhi.fedoraproject.org/docs/", None),
    "spring.io": _entry("Spring Initializr API", "https://docs.spring.io/initializr/docs/current/reference/html/", None),
    # Domains, certificates, networking and security
    "godaddy.com": _entry("GoDaddy Developer", None, "https://developer.godaddy.com/"),
    "porkbun.com": _entry("Porkbun API", "https://porkbun.com/api/json/v3/documentation", None),
    "gandi.net": _entry("Gandi API", "https://api.gandi.net/docs/", None),
    "fastly.com": _entry("Fastly API", "https://www.fastly.com/documentation/reference/api/", "https://www.fastly.com/documentation/"),
    "digicert.com": _entry("DigiCert Developer", None, "https://dev.digicert.com/"),
    "letsencrypt.org": _entry("Let's Encrypt (ACME)", None, "https://letsencrypt.org/docs/"),
    "ngrok.com": _entry("ngrok API", "https://ngrok.com/docs/api/", "https://ngrok.com/docs/"),
    "tailscale.com": _entry("Tailscale API", "https://tailscale.com/api", "https://tailscale.com/kb/"),
    "1password.com": _entry("1Password Developer", None, "https://www.1password.dev/"),
    "bitwarden.com": _entry("Bitwarden Public API", "https://bitwarden.com/help/api/", None),
    "virustotal.com": _entry("VirusTotal API", None, "https://docs.virustotal.com/"),
    "shodan.io": _entry("Shodan API", "https://developer.shodan.io/api", "https://developer.shodan.io/"),
    "haveibeenpwned.com": _entry("Have I Been Pwned API", "https://haveibeenpwned.com/API/v3", None),
    "abuseipdb.com": _entry("AbuseIPDB API", "https://docs.abuseipdb.com/", None),
    "ipinfo.io": _entry("IPinfo API", "https://ipinfo.io/developers", None),
    "urlscan.io": _entry("urlscan.io API", "https://urlscan.io/docs/api/", None),
    # Logistics and telecom
    "fedex.com": _entry("FedEx Developer", None, "https://developer.fedex.com/"),
    "dhl.com": _entry("DHL Developer Portal", "https://developer.dhl.com/api-catalog", "https://developer.dhl.com/"),
    "goshippo.com": _entry("Shippo API", None, "https://docs.goshippo.com/"),
    "easypost.com": _entry("EasyPost API", None, "https://docs.easypost.com/"),
    "maersk.com": _entry("Maersk Developer Portal", None, "https://developer.maersk.com/"),
    "att.com": _entry("AT&T Developer", None, "https://developer.att.com/"),
    "verizon.com": _entry("Verizon ThingSpace", None, "https://thingspace.verizon.com/"),
    # Health
    "epic.com": _entry("Epic on FHIR", "https://fhir.epic.com/Documentation", "https://fhir.epic.com/"),
    "athenahealth.com": _entry("athenahealth Developer Portal", "https://docs.athenahealth.com/api/", None),
    # Research and open data
    "openalex.org": _entry("OpenAlex API", "https://docs.openalex.org/", None),
    "core.ac.uk": _entry("CORE API", "https://core.ac.uk/services/api", None),
    "zenodo.org": _entry("Zenodo REST API", "https://developers.zenodo.org/", None),
    "figshare.com": _entry("Figshare API", "https://docs.figshare.com/", None),
    "datacite.org": _entry("DataCite REST API", "https://support.datacite.org/docs/api", None),
    "unpaywall.org": _entry("Unpaywall API", "https://unpaywall.org/products/api", None),
    "springernature.com": _entry("Springer Nature API", None, "https://dev.springernature.com/"),
    "springer.com": _entry("Springer Nature API", None, "https://dev.springernature.com/"),
    "nature.com": _entry("Springer Nature API", None, "https://dev.springernature.com/"),
    "elsevier.com": _entry("Elsevier Developer Portal", "https://dev.elsevier.com/api_docs.html", "https://dev.elsevier.com/"),
    "sciencedirect.com": _entry("Elsevier Developer Portal", "https://dev.elsevier.com/api_docs.html", "https://dev.elsevier.com/"),
    "ieee.org": _entry("IEEE Xplore API", None, "https://developer.ieee.org/"),
    "biorxiv.org": _entry("bioRxiv API", "https://api.biorxiv.org/", None),
    "gbif.org": _entry("GBIF API", "https://techdocs.gbif.org/en/openapi/", None),
    "inaturalist.org": _entry("iNaturalist API", "https://api.inaturalist.org/v1/docs/", None),
    "opencorporates.com": _entry("OpenCorporates API", "https://api.opencorporates.com/documentation/API-Reference", None),
    "gleif.org": _entry("GLEIF API", "https://www.gleif.org/en/lei-data/gleif-api", None),
    "biomedcentral.com": _entry("Springer Nature API Portal (BMC)", "https://dev.springernature.com/", "https://dev.springernature.com/",
                                "Requires an API key."),
    "dblp.org": _entry("dblp XML data", None, "https://dblp.org/xml/", "Full XML dump of the dblp dataset."),
    "plos.org": _entry("PLOS Search API", "https://api.plos.org/solr/faq/", "https://api.plos.org/"),
    "si.edu": _entry("Smithsonian Open Access API", "https://edan.si.edu/openaccess/apidocs/", None, "Requires an api.data.gov key."),
    "gutenberg.org": _entry("Project Gutenberg catalog feeds", None, "https://www.gutenberg.org/ebooks/offline_catalogs.html",
                            "Official OPDS and RDF catalog downloads."),
    # Maps, places and weather
    "here.com": _entry("HERE Developer", None, "https://docs.here.com/"),
    "tomtom.com": _entry("TomTom Developer Documentation", None, "https://docs.tomtom.com/"),
    "foursquare.com": _entry("Foursquare Developer", None, "https://docs.foursquare.com/"),
    "what3words.com": _entry("what3words API", None, "https://developer.what3words.com/"),
    "weatherapi.com": _entry("WeatherAPI.com", "https://www.weatherapi.com/docs/", None),
    "tomorrow.io": _entry("Tomorrow.io API", "https://docs.tomorrow.io/", None),
    "accuweather.com": _entry("AccuWeather APIs", None, "https://developer.accuweather.com/"),
    "open-meteo.com": _entry("Open-Meteo", "https://open-meteo.com/en/docs", None),
    # Analytics and adtech
    "google-analytics.com": _entry("Google Analytics APIs", "https://developers.google.com/analytics", _GOOGLE),
    "mixpanel.com": _entry("Mixpanel API", "https://docs.mixpanel.com/reference/overview", None),
    "amplitude.com": _entry("Amplitude APIs", "https://amplitude.com/docs/apis", "https://amplitude.com/docs"),
    "posthog.com": _entry("PostHog API", "https://posthog.com/docs/api", "https://posthog.com/docs"),
    "criteo.com": _entry("Criteo Developers", None, "https://developers.criteo.com/"),
    "appsflyer.com": _entry("AppsFlyer Dev Hub", None, "https://dev.appsflyer.com/"),
    "adjust.com": _entry("Adjust Developer Hub", None, "https://dev.adjust.com/"),
    # Device makers, Chinese and Russian platforms
    "samsung.com": _entry("Samsung Developer", None, "https://developer.samsung.com/"),
    "smartthings.com": _entry("SmartThings API", "https://developer.smartthings.com/docs/api/public", "https://developer.smartthings.com/"),
    "lg.com": _entry("LG webOS TV Developer", None, "https://webostv.developer.lge.com/"),
    "huawei.com": _entry("HUAWEI Developers", None, "https://developer.huawei.com/consumer/en/"),
    "xiaomi.com": _entry("Xiaomi Developer", None, "https://dev.mi.com/"),
    "nvidia.com": _entry("NVIDIA Developer", "https://docs.nvidia.com/", "https://developer.nvidia.com/"),
    "cisco.com": _entry("Cisco DevNet", None, "https://developer.cisco.com/"),
    "tesla.com": _entry("Tesla Fleet API", "https://developer.tesla.com/docs/fleet-api", "https://developer.tesla.com/"),
    "home-assistant.io": _entry("Home Assistant developer docs", "https://contributors.home-assistant.io/core/internals/rest-api/",
                                "https://contributors.home-assistant.io/"),
    "qq.com": _entry("Tencent Open Platform (QQ / WeChat)", "https://developers.weixin.qq.com/doc/", "https://open.tencent.com/"),
    "wechat.com": _entry("WeChat Open Platform", "https://developers.weixin.qq.com/doc/", None),
    "baidu.com": _entry("Baidu AI Cloud", "https://cloud.baidu.com/doc/index.html", "https://ai.baidu.com/"),
    "yandex.com": _entry("Yandex for Developers", None, "https://yandex.com/dev/"),
    "yandex.ru": _entry("Yandex for Developers", None, "https://yandex.ru/dev/"),
    # AI model vendors and AI tooling
    "anthropic.com": _entry("Claude API (Anthropic)", "https://platform.claude.com/docs/en/api/overview", "https://platform.claude.com/docs/en/home"),
    "claude.ai": _entry("Claude API (Anthropic)", "https://platform.claude.com/docs/en/api/overview", "https://platform.claude.com/docs/en/home"),
    "claude.com": _entry("Claude API (Anthropic)", "https://platform.claude.com/docs/en/api/overview", "https://platform.claude.com/docs/en/home"),
    "deepmind.google": _entry("Gemini API (Google)", "https://ai.google.dev/api", "https://ai.google.dev/"),
    "llama.com": _entry("Llama (Meta AI developer platform)", None, "https://dev.meta.ai/"),
    "meta.ai": _entry("Meta AI developer platform", "https://dev.meta.ai/docs/overview", "https://dev.meta.ai/"),
    "ai21.com": _entry("AI21 Studio", None, "https://docs.ai21.com/"),
    "fireworks.ai": _entry("Fireworks AI", "https://docs.fireworks.ai/api-reference/introduction", "https://docs.fireworks.ai/"),
    "cerebras.ai": _entry("Cerebras Inference", None, "https://inference-docs.cerebras.ai/"),
    "deepinfra.com": _entry("DeepInfra", "https://docs.deepinfra.com/", None),
    "runwayml.com": _entry("Runway API", "https://docs.dev.runwayml.com/", None),
    "leonardo.ai": _entry("Leonardo.Ai API", "https://docs.leonardo.ai/", None),
    "ideogram.ai": _entry("Ideogram API", "https://developer.ideogram.ai/", None),
    "assemblyai.com": _entry("AssemblyAI", None, "https://www.assemblyai.com/docs"),
    "deepgram.com": _entry("Deepgram", None, "https://developers.deepgram.com/"),
    "deepl.com": _entry("DeepL API", None, "https://developers.deepl.com/"),
    "pinecone.io": _entry("Pinecone", None, "https://docs.pinecone.io/"),
    "weaviate.io": _entry("Weaviate", None, "https://docs.weaviate.io/"),
    "qdrant.tech": _entry("Qdrant", "https://api.qdrant.tech/", "https://qdrant.tech/documentation/"),
    "langchain.com": _entry("LangChain Docs", None, "https://docs.langchain.com/"),
    "llamaindex.ai": _entry("LlamaIndex Docs", None, "https://developers.llamaindex.ai/python/framework/"),
    "firecrawl.dev": _entry("Firecrawl", None, "https://docs.firecrawl.dev/"),
    "browserbase.com": _entry("Browserbase", None, "https://docs.browserbase.com/"),
    "poe.com": _entry("Poe Creator Platform", None, "https://creator.poe.com/docs"),
    "moonshot.ai": _entry("Moonshot AI (Kimi API Platform)", None, "https://platform.kimi.ai/docs/overview"),
    "z.ai": _entry("Z.ai API", None, "https://docs.z.ai/"),
    "upstage.ai": _entry("Upstage Console Docs", None, "https://console.upstage.ai/docs"),
    "writer.com": _entry("Writer Developer", None, "https://dev.writer.com/"),
    "civitai.com": _entry("Civitai Developer", None, "https://developer.civitai.com/"),
    "cursor.com": _entry("Cursor Docs", None, "https://cursor.com/docs"),
    "replit.com": _entry("Replit Docs", None, "https://docs.replit.com/"),
    # Short links and real alias hosts of platforms listed above
    "youtu.be": _entry("YouTube Data API", "https://developers.google.com/youtube/v3", "https://developers.google.com/youtube"),
    "wa.me": _entry("WhatsApp Business Platform", "https://developers.facebook.com/docs/whatsapp/", _META),
    "t.me": _entry("Telegram Bot API", "https://core.telegram.org/bots/api", "https://core.telegram.org/"),
    "discord.gg": _entry("Discord", "https://docs.discord.com/developers/intro", "https://discord.com/developers"),
    "bitly.com": _entry("Bitly API", "https://dev.bitly.com/api-reference", "https://dev.bitly.com/"),
    "bit.ly": _entry("Bitly API", "https://dev.bitly.com/api-reference", "https://dev.bitly.com/"),
    "tinyurl.com": _entry("TinyURL API", "https://tinyurl.com/app/dev", None),
    # Travel, banks, retail, telecom and logistics developer portals
    "bankofamerica.com": _entry("Bank of America Developer", None, "https://developer.bankofamerica.com/"),
    "target.com": _entry("Target Developer", None, "https://developer.target.com/"),
    "bloomberg.com": _entry("Bloomberg API Library", "https://www.bloomberg.com/professional/support/api-library/", None),
    "mychart.com": _entry("Epic on FHIR (MyChart)", "https://fhir.epic.com/Documentation", "https://fhir.epic.com/"),
    "dell.com": _entry("Dell Technologies Developer", "https://developer.dell.com/apis", "https://developer.dell.com/"),
    "intel.com": _entry("Intel Developer Zone", None, "https://www.intel.com/content/www/us/en/developer/overview.html"),
    "synology.com": _entry("Synology Developer", None, "https://www.synology.com/en-global/support/developer"),
    "redhat.com": _entry("Red Hat Developer", None, "https://developers.redhat.com/"),
    # Government and public data
    "ny.gov": _entry("New York State Open Data", None, "https://data.ny.gov/developers"),
    "europa.eu": _entry("data.europa.eu (EU Open Data Portal)", "https://data.europa.eu/api/hub/search/", "https://data.europa.eu/"),
    "nist.gov": _entry("NVD API (NIST)", "https://nvd.nist.gov/developers/vulnerabilities", "https://nvd.nist.gov/developers"),
    "usda.gov": _entry("USDA FoodData Central API", "https://fdc.nal.usda.gov/api-guide", "https://fdc.nal.usda.gov/"),
    "unesco.org": _entry("UNESCO Institute for Statistics API", "https://api.uis.unesco.org/api/public/documentation/", None),
    "arcgis.com": _entry("ArcGIS Developers (Esri)", "https://developers.arcgis.com/rest/", "https://developers.arcgis.com/"),
    "flightradar24.com": _entry("Flightradar24 API", "https://fr24api.flightradar24.com/docs", "https://fr24api.flightradar24.com/"),
    "debian.org": _entry("Debian Sources API", "https://sources.debian.org/doc/api/", None),
    "ubuntu.com": _entry("Ubuntu Security API", "https://ubuntu.com/security/api/docs", "https://documentation.ubuntu.com/"),
    "merriam-webster.com": _entry("Merriam-Webster Dictionary API", "https://dictionaryapi.com/products/index", "https://dictionaryapi.com/"),
    # Amazon, Google, Microsoft, Meta and eBay regional and alias hosts
    "amazon.co.uk": _entry("Amazon Selling Partner API", "https://developer-docs.amazon/sp-api/", "https://developer.amazon.com/"),
    "amazon.de": _entry("Amazon Selling Partner API", "https://developer-docs.amazon/sp-api/", "https://developer.amazon.com/"),
    "amazon.fr": _entry("Amazon Selling Partner API", "https://developer-docs.amazon/sp-api/", "https://developer.amazon.com/"),
    "amazon.es": _entry("Amazon Selling Partner API", "https://developer-docs.amazon/sp-api/", "https://developer.amazon.com/"),
    "amazon.it": _entry("Amazon Selling Partner API", "https://developer-docs.amazon/sp-api/", "https://developer.amazon.com/"),
    "amazon.ca": _entry("Amazon Selling Partner API", "https://developer-docs.amazon/sp-api/", "https://developer.amazon.com/"),
    "amazon.in": _entry("Amazon Selling Partner API", "https://developer-docs.amazon/sp-api/", "https://developer.amazon.com/"),
    "amazon.co.jp": _entry("Amazon Selling Partner API", "https://developer-docs.amazon/sp-api/", "https://developer.amazon.com/"),
    "amazon.com.br": _entry("Amazon Selling Partner API", "https://developer-docs.amazon/sp-api/", "https://developer.amazon.com/"),
    "amazon.com.au": _entry("Amazon Selling Partner API", "https://developer-docs.amazon/sp-api/", "https://developer.amazon.com/"),
    "amazon.co.za": _entry("Amazon Selling Partner API", "https://developer-docs.amazon/sp-api/", "https://developer.amazon.com/"),
    "amazonalexa.com": _entry("Alexa Skills Kit",
                              "https://developer.amazon.com/en-US/docs/alexa/ask-overviews/what-is-the-alexa-skills-kit.html", "https://developer.amazon.com/en-US/alexa"),
    "google.de": _entry("Google", "https://developers.google.com/apis-explorer", "https://developers.google.com/"),
    "google.co.uk": _entry("Google", "https://developers.google.com/apis-explorer", "https://developers.google.com/"),
    "google.com.hk": _entry("Google", "https://developers.google.com/apis-explorer", "https://developers.google.com/"),
    "google.com.br": _entry("Google", "https://developers.google.com/apis-explorer", "https://developers.google.com/"),
    "googleapis.com": _entry("Google Cloud APIs", "https://cloud.google.com/apis/docs/overview", "https://cloud.google.com/apis"),
    "office365.com": _entry("Microsoft 365 via Microsoft Graph",
                            "https://learn.microsoft.com/en-us/graph/api/overview", "https://learn.microsoft.com/"),
    "visualstudio.com": _entry("Azure DevOps Services REST API",
                               "https://learn.microsoft.com/en-us/rest/api/azure/devops/", "https://learn.microsoft.com/en-us/azure/devops/"),
    "zoom.com": _entry("Zoom", "https://developers.zoom.us/docs/api/", "https://developers.zoom.us/"),
    "docker.io": _entry("Docker Hub API", "https://docs.docker.com/reference/api/hub/latest/", "https://docs.docker.com/"),
    "cloudflare-dns.com": _entry("Cloudflare DNS over HTTPS",
                                 "https://developers.cloudflare.com/1.1.1.1/encryption/dns-over-https/", "https://developers.cloudflare.com/1.1.1.1/"),
    "oraclecloud.com": _entry("Oracle Cloud Infrastructure", "https://docs.oracle.com/en-us/iaas/api/", "https://docs.oracle.com/en/"),
    "tencent.com": _entry("Tencent Cloud API", "https://www.tencentcloud.com/document/api", None),
    "mi.com": _entry("Xiaomi Developer", None, "https://dev.mi.com/"),
    # Security, networking and hosting vendor developer portals
    "meraki.com": _entry("Cisco Meraki Dashboard API", "https://developer.cisco.com/meraki/api-v1/", "https://developer.cisco.com/meraki/"),
    "opendns.com": _entry("Cisco Umbrella API",
                          "https://developer.cisco.com/docs/cloud-security/", "https://developer.cisco.com/docs/cloud-security/"),
    "arubanetworks.com": _entry("HPE Aruba Networking Developer Hub", None, "https://developer.arubanetworks.com/"),
    "ui.com": _entry("Ubiquiti Developer", None, "https://developer.ui.com/"),
    "mikrotik.com": _entry("MikroTik RouterOS REST API",
                           "https://help.mikrotik.com/docs/spaces/ROS/pages/47579162/REST+API", "https://help.mikrotik.com/docs/"),
    "checkpoint.com": _entry("Check Point Management API", "https://sc1.checkpoint.com/documents/latest/APIs/", None),
    "paloaltonetworks.com": _entry("Palo Alto Networks Developer Docs", None, "https://pan.dev/"),
    "sophos.com": _entry("Sophos Developer", None, "https://developer.sophos.com/"),
    "trendmicro.com": _entry("Trend Vision One Automation Center", None, "https://automation.trendmicro.com/"),
    "onetrust.com": _entry("OneTrust Developer Portal", None, "https://developer.onetrust.com/"),
    "name.com": _entry("Name.com API", "https://docs.name.com/", None),
    "dreamhost.com": _entry("DreamHost API", "https://help.dreamhost.com/hc/en-us/articles/217560167-API-overview", None),
    "cpanel.net": _entry("cPanel API", "https://api.docs.cpanel.net/", None),
    "plesk.com": _entry("Plesk API", "https://docs.plesk.com/en-US/obsidian/api-rpc/", None),
    "reg.ru": _entry("REG.RU API", "https://www.reg.ru/support/help/api2", None),
    "no-ip.com": _entry("No-IP Dynamic DNS Update API", "https://www.noip.com/integrate/request", None),
    "duckdns.org": _entry("Duck DNS spec", "https://www.duckdns.org/spec.jsp", None),
    "dnsmadeeasy.com": _entry("DNS Made Easy API", "https://api-docs.dnsmadeeasy.com/", None),
    "cloudns.net": _entry("ClouDNS API", "https://www.cloudns.net/wiki/article/41/", None),
    "teamviewer.com": _entry("TeamViewer Web API", "https://webapi.teamviewer.com/api/v1/docs/index", None),
    # Ads, analytics, marketing, support and SaaS developer docs
    "applovin.com": _entry("AppLovin Developers", None, "https://developers.applovin.com/"),
    "braze.com": _entry("Braze Developer Guide", "https://www.braze.com/docs/api/home", "https://www.braze.com/docs/"),
    "optimizely.com": _entry("Optimizely Developer Docs", None, "https://docs.developers.optimizely.com/"),
    "qualtrics.com": _entry("Qualtrics API", "https://api.qualtrics.com/", None),
    "pendo.io": _entry("Pendo Engage API", "https://engageapi.pendo.io/", None),
    "dynatrace.com": _entry("Dynatrace API", "https://docs.dynatrace.com/docs/dynatrace-api", "https://docs.dynatrace.com/"),
    "bugsnag.com": _entry("Bugsnag API", None, "https://docs.bugsnag.com/"),
    "jotform.com": _entry("Jotform API", "https://api.jotform.com/docs/", None),
    "disqus.com": _entry("Disqus API", "https://disqus.com/api/docs/", None),
    "cloudinary.com": _entry("Cloudinary", "https://cloudinary.com/documentation/image_upload_api_reference", "https://cloudinary.com/documentation"),
    "agora.io": _entry("Agora Docs", None, "https://docs.agora.io/en/"),
    "fontawesome.com": _entry("Font Awesome GraphQL API", "https://docs.fontawesome.com/apis/graphql/get-started", "https://docs.fontawesome.com/"),
    "taboola.com": _entry("Taboola Backstage API", "https://developers.taboola.com/backstage-api/reference", "https://developers.taboola.com/"),
    "stackadapt.com": _entry("StackAdapt API Docs", None, "https://docs.stackadapt.com/"),
    "trustpilot.com": _entry("Trustpilot Developers", "https://developers.trustpilot.com/", "https://developers.trustpilot.com/"),
    "mega.co.nz": _entry("MEGA Developers", None, "https://mega.io/developers"),
    # Marketplaces and platforms in Russia, Eastern Europe, Turkey and Latin America
    "vk.com": _entry("VK for Developers", "https://dev.vk.com/en/api/overview", None),
    "vk.ru": _entry("VK for Developers", "https://dev.vk.com/en/api/overview", None),
    "ok.ru": _entry("OK API", "https://apiok.ru/en/dev/", "https://apiok.ru/en/"),
    "yandexcloud.net": _entry("Yandex Cloud API", "https://yandex.cloud/en/docs/api-design-guide/", "https://yandex.cloud/en/docs/"),
    "2gis.com": _entry("2GIS API", None, "https://docs.2gis.com/"),
    "sberbank.ru": _entry("SberDevices / Sber for Developers", None, "https://developers.sber.ru/"),
    "bitrix24.ru": _entry("Bitrix24 REST API", "https://apidocs.bitrix24.com/", None),
    "allegro.pl": _entry("Allegro Developer", "https://developer.allegro.pl/documentation", "https://developer.allegro.pl/"),
    "trendyol.com": _entry("Trendyol Developers", None, "https://developers.trendyol.com/"),
    "mobile.de": _entry("mobile.de Seller API", "https://services.mobile.de/docs/seller-api.html", "https://services.mobile.de/"),
    "otto.de": _entry("OTTO Market API", "https://api.otto.market/", None),
    "mercadolivre.com.br": _entry("Mercado Livre Developers", None, "https://developers.mercadolivre.com.br/"),
    "mercadolibre.com.ar": _entry("Mercado Libre Developers", None, "https://developers.mercadolibre.com.ar/"),
    "shopee.com.br": _entry("Shopee Open Platform", None, "https://open.shopee.com/"),
    "shopee.co.id": _entry("Shopee Open Platform", None, "https://open.shopee.com/"),
    "alibaba.com": _entry("Alibaba.com Open Platform", None, "https://open.alibaba.com/"),
    # Games, creators, music and content platforms
    "epicgames.com": _entry("Epic Online Services Web API", "https://dev.epicgames.com/docs/web-api-ref", None),
    "supercell.com": _entry("Supercell game APIs", "https://developer.clashofclans.com/", "https://developer.brawlstars.com/"),
    "itch.io": _entry("itch.io API", "https://itch.io/docs/api/overview", "https://itch.io/docs"),
    "curseforge.com": _entry("CurseForge API", "https://docs.curseforge.com/", None),
    "bandcamp.com": _entry("Bandcamp API", "https://bandcamp.com/developer", None),
    "chess.com": _entry("Chess.com Published-Data API", "https://www.chess.com/news/view/published-data-api", None),
    "issuu.com": _entry("Issuu Developers", None, "https://developers.issuu.com/"),
    "ip-api.com": _entry("ip-api.com documentation", "https://ip-api.com/docs/", None),
    "ipify.org": _entry("ipify API", "https://www.ipify.org/", None),
    # State open data, standards bodies and reference data
    "un.org": _entry("UN Data API", "https://data.un.org/Host.aspx?Content=API", None),
    "ca.gov": _entry("California Open Data", None, "https://data.ca.gov/", "Datasets expose JSON APIs."),
    "texas.gov": _entry("Texas Open Data Portal", None, "https://data.texas.gov/", "Datasets expose JSON APIs."),
    "virginia.gov": _entry("Virginia Open Data Portal", None, "https://data.virginia.gov/", "Datasets expose JSON APIs."),
    "mo.gov": _entry("Missouri Open Data", None, "https://data.mo.gov/", "Datasets expose JSON APIs."),
    # Browser, networking and operating-system vendors
    "f5.com": _entry("F5 Cloud Docs API", "https://clouddocs.f5.com/api/", "https://clouddocs.f5.com/"),
    # Yandex and VK alias hosts
    "ya.ru": _entry("Yandex for Developers", None, "https://yandex.ru/dev/"),
    "vkontakte.ru": _entry("VK for Developers", "https://dev.vk.com/en/api/overview", None),
    # Agent-demand slice 2: open-data portals, publishers, travel, commerce and developer tools
    "wa.gov": _entry("Washington State Open Data Portal", None, "https://data.wa.gov/", "Datasets expose JSON APIs."),
    "idaho.gov": _entry("Idaho Open Data Portal", None, "https://data.idaho.gov/", "Datasets expose JSON APIs."),
    "illinois.gov": _entry("Illinois Data Portal", None, "https://data.illinois.gov/"),
    "delaware.gov": _entry("Delaware Open Data Portal", None, "https://data.delaware.gov/", "Datasets expose JSON APIs."),
    "dc.gov": _entry("DC Open Data", None, "https://opendata.dc.gov/"),
    "alberta.ca": _entry("Alberta Open Government Program", None, "https://open.alberta.ca/"),
    "admin.ch": _entry("Swiss federal open government data", None, "https://opendata.swiss/en/"),
    "medlineplus.gov": _entry("MedlinePlus Web Service", "https://medlineplus.gov/about/developers/webservices/", "https://medlineplus.gov/about/developers/"),
    "ftc.gov": _entry("FTC developer resources", None, "https://www.ftc.gov/developer"),
    "uscourts.gov": _entry("PACER developer resources", None, "https://pacer.uscourts.gov/file-case/developer-resources"),
    "datausa.io": _entry("Data USA API", "https://datausa.io/about/api/", None),
    "censusreporter.org": _entry("Census Reporter API", "https://github.com/censusreporter/census-api/blob/master/API.md", None),
    "ensembl.org": _entry("Ensembl REST API", "https://rest.ensembl.org/", None),
    "aclanthology.org": _entry("ACL Anthology data", None, "https://aclanthology.org/info/development/"),
    "ebsco.com": _entry("EBSCO Developer Portal", None, "https://developer.ebsco.com/"),
    "gettyimages.com": _entry("Getty Images API", "https://developer.gettyimages.com/api/", "https://developer.gettyimages.com/"),
    "freepik.com": _entry("Freepik API", "https://docs.freepik.com/", None),
    "dreamstime.com": _entry("Dreamstime API", None, "https://www.dreamstime.com/api.php"),
    "overleaf.com": _entry("Overleaf developer resources", None, "https://www.overleaf.com/devs"),
    "nextdoor.com": _entry("Nextdoor Developer", None, "https://developer.nextdoor.com/"),
    "globalgiving.org": _entry("GlobalGiving API", "https://www.globalgiving.org/api/", None),
    "crunchbase.com": _entry("Crunchbase API", "https://data.crunchbase.com/docs/using-the-api", "https://data.crunchbase.com/docs"),
    "ashbyhq.com": _entry("Ashby API", "https://developers.ashbyhq.com/reference", "https://developers.ashbyhq.com/"),
    "greenhouse.com": _entry("Greenhouse", "https://docs.greenhouse.io/harvest.html", "https://developers.greenhouse.io/"),
    "fiserv.com": _entry("Fiserv Developer Studio", None, "https://developer.fiserv.com/"),
    "honeycomb.io": _entry("Honeycomb API", "https://docs.honeycomb.io/api/", "https://docs.honeycomb.io/"),
    "datadoghq.eu": _entry("Datadog", "https://docs.datadoghq.com/api/latest/", "https://docs.datadoghq.com/"),
    "discourse.org": _entry("Discourse API", "https://docs.discourse.org/", None),
    "api.aws": _entry("Amazon Web Services", "https://docs.aws.amazon.com/", "https://builder.aws.com/"),
    "adoptium.net": _entry("Adoptium API", "https://api.adoptium.net/q/swagger-ui/", "https://adoptium.net/docs/"),
    "bun.sh": _entry("Bun docs", None, "https://bun.sh/docs"),
    "godotengine.org": _entry("Godot Engine docs", None, "https://docs.godotengine.org/en/stable/"),
    "dartlang.org": _entry("Dart API reference", "https://api.dart.dev/", "https://dart.dev/"),
    "gog.com": _entry("GOG developer docs", None, "https://docs.gog.com/"),
    "eventbrite.co.uk": _entry("Eventbrite API", "https://www.eventbrite.com/platform/api", "https://www.eventbrite.com/platform/"),
    "eventbrite.com.au": _entry("Eventbrite API", "https://www.eventbrite.com/platform/api", "https://www.eventbrite.com/platform/"),
    "aliexpress.us": _entry("AliExpress Open Platform", None, "https://openservice.aliexpress.com/"),
    "cheaptickets.com": _entry("Expedia Group (CheapTickets)", None, "https://developers.expediagroup.com/",
                               "Expedia Partner Solutions (Rapid API) requires a partner agreement."),
    "fb.com": _entry("Meta Graph API", "https://developers.facebook.com/docs/graph-api/", _META),
    "google.ca": _entry("Google", "https://developers.google.com/apis-explorer", "https://developers.google.com/"),
    "google.co.in": _entry("Google", "https://developers.google.com/apis-explorer", "https://developers.google.com/"),
    "accor.com": _entry("Accor Developer Portal", None, "https://developer.accor.com/"),
    "bahn.de": _entry("Deutsche Bahn API Marketplace", None, "https://developers.deutschebahn.com/db-api-marketplace/apis/frontpage"),
    "autotrader.co.uk": _entry("Auto Trader Developers", None, "https://developers.autotrader.co.uk/"),
    "getyourguide.com": _entry("GetYourGuide Partner API", "https://code.getyourguide.com/partner-api-spec/", None),
    "ford.com": _entry("Ford Developer", None, "https://developer.ford.com/"),
    # Agent-demand slice 3: agent protocols and developer tools, open-data portals, travel and commerce
    "modelcontextprotocol.io": _entry("Model Context Protocol", "https://modelcontextprotocol.io/specification/latest",
                                      "https://modelcontextprotocol.io/docs/getting-started/intro"),
    "playwright.dev": _entry("Playwright", "https://playwright.dev/docs/api/class-playwright", "https://playwright.dev/docs/intro"),
    "jsr.io": _entry("JSR API", "https://jsr.io/docs/api", "https://jsr.io/docs"),
    "kotlinlang.org": _entry("Kotlin documentation", "https://kotlinlang.org/api/latest/jvm/stdlib/", "https://kotlinlang.org/docs/home.html"),
    "stainless.com": _entry("Stainless docs", None, "https://www.stainless.com/docs/"),
    "snapcraft.io": _entry("Snap Store API", "https://api.snapcraft.io/docs/", None),
    "opensuse.org": _entry("Open Build Service API", "https://build.opensuse.org/apidocs/index", None),
    "sonatype.org": _entry("Sonatype Central", "https://central.sonatype.org/publish/publish-portal-api/", "https://central.sonatype.org/"),
    "mega.io": _entry("MEGA developers", None, "https://mega.io/developers"),
    "instructure.com": _entry("Canvas LMS API", "https://developerdocs.instructure.com/services/canvas", None),
    "luma.com": _entry("Luma API", "https://docs.luma.com/reference/getting-started-with-your-api", "https://docs.luma.com/",
                       "API access needs a Luma Plus subscription on the calendar."),
    "splitit.com": _entry("Splitit Developer Portal", None, "https://developers.splitit.com/"),
    "springeropen.com": _entry("Springer Nature API Portal (SpringerOpen)", "https://dev.springernature.com/", "https://dev.springernature.com/"),
    "superuser.com": _entry("Stack Exchange API", "https://api.stackexchange.com/docs", None),
    "jstor.org": _entry("JSTOR Data for Research", None, "https://www.jstor.org/dfr/"),
    "jhu.edu": _entry("Johns Hopkins SIS API", None, "https://sis.jhu.edu/api"),
    "metmuseum.org": _entry("The Met Collection API", "https://metmuseum.github.io/", None),
    "ourworldindata.org": _entry("Our World in Data ETL and API", "https://docs.owid.io/projects/etl/api/", None),
    "in.gov": _entry("Indiana Management Performance Hub", None, "https://hub.mph.in.gov/"),
    "maryland.gov": _entry("Maryland Open Data Portal", None, "https://opendata.maryland.gov/"),
    "michigan.gov": _entry("Michigan Open Data", None, "https://data.michigan.gov/"),
    "oregon.gov": _entry("Oregon Open Data Portal", None, "https://data.oregon.gov/"),
    "pa.gov": _entry("Pennsylvania Open Data Portal", None, "https://data.pa.gov/"),
    "nyc.gov": _entry("NYC API Developer Portal", None, "https://api-portal.nyc.gov/"),
    "mta.info": _entry("MTA developer resources", None, "https://www.mta.info/developers"),
    "senate.gov": _entry("Lobbying Disclosure Act REST API", "https://lda.gov/api/redoc/v1/", None),
    "nsf.gov": _entry("NSF Awards API", "https://resources.research.gov/common/webapi/awardapisearch-v1.htm",
                      "https://www.nsf.gov/awardsearch/download.jsp"),
    "mapquest.com": _entry("MapQuest Developer", None, "https://developer.mapquest.com/"),
    "singaporeair.com": _entry("Singapore Airlines developer portal", None, "https://developer.singaporeair.com/"),
    "sncf.com": _entry("SNCF open data and APIs", "https://numerique.sncf.com/startup/api/", "https://data.sncf.com/"),
    "sncf-connect.com": _entry("SNCF open data and APIs (SNCF Connect)", "https://numerique.sncf.com/startup/api/", "https://data.sncf.com/"),
    "stubhub.ca": _entry("StubHub Developer Portal", None, "https://developer.stubhub.com/"),
    "porsche.com": _entry("Porsche Developer Portal", None, "https://developer.porsche.com/"),
    "reed.co.uk": _entry("Reed.co.uk API", None, "https://www.reed.co.uk/developers"),
    "shopee.com": _entry("Shopee Open Platform", None, "https://open.shopee.com/"),
    "shopee.ph": _entry("Shopee Open Platform", None, "https://open.shopee.com/"),
    # Korean services: public bodies through the Public Data Portal dataset page the body owns, others through their own open-API portal
    "airkorea.or.kr": _entry("한국환경공단 에어코리아 대기오염정보 (AirKorea air quality API)", None,
                             "https://www.data.go.kr/data/15073861/openapi.do"),
    "airport.kr": _entry("인천국제공항공사 항공기 운항 현황 상세 조회 (Incheon Airport flight status API)", None,
                         "https://www.data.go.kr/data/15140153/openapi.do"),
    "bokjiro.go.kr": _entry("한국사회보장정보원 중앙부처복지서비스 (Bokjiro central-government welfare services API)", None,
                            "https://www.data.go.kr/data/15090532/openapi.do"),
    "hira.or.kr": _entry("건강보험심사평가원 병원정보서비스 (HIRA hospital information API)", None,
                         "https://www.data.go.kr/data/15001698/openapi.do"),
    "hometax.go.kr": _entry("국세청 사업자등록정보 진위확인 및 상태조회 서비스 (NTS business registration status API)", None,
                            "https://www.data.go.kr/data/15081808/openapi.do"),
    "nts.go.kr": _entry("국세청 사업자등록정보 진위확인 및 상태조회 서비스 (NTS business registration status API)", None,
                        "https://www.data.go.kr/data/15081808/openapi.do"),
    "mfds.go.kr": _entry("식품의약품안전처 의약품 제품 허가정보 (MFDS drug approval API)", None,
                         "https://www.data.go.kr/data/15095677/openapi.do"),
    "nps.or.kr": _entry("국민연금공단 국민연금 가입 사업장 내역 (National Pension Service workplace API)", None,
                        "https://www.data.go.kr/data/3046071/openapi.do"),
    "police.go.kr": _entry("경찰청 분실물정보 조회 서비스 (Korean National Police Agency lost-property API)", None,
                           "https://www.data.go.kr/data/15000799/openapi.do"),
    "korail.com": _entry("한국철도공사 공통기준 역상세 정보 (Korail station details)", None,
                         "https://www.data.go.kr/data/15153852/fileData.do"),
    "letskorail.com": _entry("한국철도공사 공통기준 역상세 정보 (Korail station details)", None,
                             "https://www.data.go.kr/data/15153852/fileData.do"),
    "minwon.go.kr": _entry("Government24 (formerly Minwon24)", None, "https://www.data.go.kr/",
                           "Korean government open APIs are published through the Public Data Portal (data.go.kr)."),
    "kepco.co.kr": _entry("한국전력공사 전력데이터 개방 포털 (KEPCO Power Data Open Portal)", None, "https://bigdata.kepco.co.kr/"),
    "krx.co.kr": _entry("KRX Open API", None, "https://openapi.krx.co.kr/"),
    "visitkorea.or.kr": _entry("한국관광공사 TourAPI (Korea Tourism Organization)", None, "https://api.visitkorea.or.kr/"),
    "work24.go.kr": _entry("고용24 OPEN-API (Work24 Open API)", None, "https://www.work24.go.kr/cm/e/a/0110/selectOpenApiIntro.do"),
    "saramin.co.kr": _entry("Saramin Open API", None, "https://oapi.saramin.co.kr/"),
    "kbstar.com": _entry("KB국민은행 기업 Open API 포털 (KB Kookmin Bank corporate Open API)", None, "https://obizapi.kbstar.com/"),
    "shinhan.com": _entry("신한 Open API (Shinhan Open API)", None, "https://openapi.shinhan.com/"),
    "sktelecom.com": _entry("SK Open API", None, "https://openapi.sk.com/"),
    # Media, avatar and video-delivery hosts of platforms listed above
}


# How to use an official path: optional `access` bundles for some table keys, answered as official_paths.access.
# Every field is optional except verified_at, and a field is left out (never null) when it is unknown. Every URL is
# on a publisher-owned domain (the key's, one declared in PUBLISHER_DOMAINS, or the publisher's own GitHub
# organisation declared in PUBLISHER_GITHUB_ORGS; see scripts/verify_official_paths.py) and was read on verified_at.
# Quotes are copied verbatim from the page at source_url / terms.url and are at most ACCESS_QUOTE_MAX characters.
# free_tier quotes only a sentence that states how much can be used free or on what condition (a default quota); pricing
# slogans, free-trial adverts and price-list headers do not count.
# Terms are a link and a quote only: this service does not interpret them or say whether automation is allowed.
# A page that refused the read (401/403/challenge) leaves its field out. Procedure: scripts/README.md.
ACCESS_AUTH_METHODS = ("none", "api_key", "oauth1", "oauth2", "device_code", "pat")
ACCESS_QUOTE_MAX = 200
# An access bundle older than this is left out of answers until it is verified again.
ACCESS_MAX_AGE_DAYS = 90

_GH_DOCS = "https://docs.github.com/en/"
_OPENAI = "https://developers.openai.com/"
_YT = "https://developers.google.com/youtube/"
_NOTION_AUTH = "https://developers.notion.com/guides/get-started/authorization"
_X_AUTH = "https://docs.x.com/fundamentals/authentication/overview"

OFFICIAL_ACCESS: dict[str, dict] = {
    "github.com": {
        "auth": [
            {"method": "pat", "source_url": _GH_DOCS + "rest/authentication/authenticating-to-the-rest-api"},
            {"method": "oauth2", "source_url": _GH_DOCS + "apps/oauth-apps/building-oauth-apps/authorizing-oauth-apps"},
            {"method": "device_code", "source_url": _GH_DOCS + "apps/oauth-apps/building-oauth-apps/authorizing-oauth-apps"},
            {"method": "none", "source_url": _GH_DOCS + "rest/using-the-rest-api/rate-limits-for-the-rest-api"},
        ],
        "key_signup_url": _GH_DOCS + "authentication/keeping-your-account-and-data-secure/managing-your-personal-access-tokens",
        "rate_limits_url": _GH_DOCS + "rest/using-the-rest-api/rate-limits-for-the-rest-api",
        "openapi_url": "https://raw.githubusercontent.com/github/rest-api-description/main/descriptions/api.github.com/api.github.com.json",
        "official_mcp": [{"endpoint": "https://api.githubcopilot.com/mcp/",
                          "doc_url": _GH_DOCS + "copilot/how-tos/copilot-in-your-ide/customize-copilot/"
                                                "extend-copilot-with-tools-and-context/set-up-the-github-mcp-server"}],
        "terms": {"url": _GH_DOCS + "site-policy/github-terms/github-terms-of-service",
                  "quote": "Abuse or excessively frequent requests to GitHub via the API may result in the temporary or "
                           "permanent suspension of your Account's access to the API."},
        "verified_at": "2026-11-01",
    },
    "stripe.com": {
        "auth": [{"method": "api_key", "source_url": "https://docs.stripe.com/api/authentication"}],
        "key_signup_url": "https://docs.stripe.com/keys",
        "rate_limits_url": "https://docs.stripe.com/rate-limits",
        "sandbox_url": "https://docs.stripe.com/sandboxes",
        "openapi_url": "https://raw.githubusercontent.com/stripe/openapi/master/openapi/spec3.json",
        "official_mcp": [{"endpoint": "https://mcp.stripe.com", "doc_url": "https://docs.stripe.com/mcp"}],
        "terms": {"url": "https://stripe.com/legal/ssa",
                  "quote": "If User uses an AI Agent to access the Stripe Services, User is solely responsible for each "
                           "action initiated by or through the AI Agent."},
        "verified_at": "2026-11-01",
    },
    "openai.com": {
        "auth": [{"method": "api_key", "source_url": _OPENAI + "api/reference/overview"}],
        "key_signup_url": _OPENAI + "api/docs/quickstart",
        "rate_limits_url": _OPENAI + "api/docs/guides/rate-limits",
        "openapi_url": "https://raw.githubusercontent.com/openai/openai-openapi/main/openapi.yaml",
        "official_mcp": [{"endpoint": "https://developers.openai.com/mcp", "doc_url": _OPENAI + "learn/docs-mcp"}],
        "verified_at": "2026-11-01",
    },
    "youtube.com": {
        "auth": [
            {"method": "api_key", "source_url": _YT + "data/getting-started"},
            {"method": "oauth2", "source_url": _YT + "data/guides/authentication"},
            {"method": "device_code", "source_url": _YT + "data/guides/auth/devices"},
        ],
        "key_signup_url": _YT + "registering_an_application",
        "free_tier": {"quote": "Projects that enable the YouTube Data API have a default quota allocation of 100 search.list "
                               "calls, 100 videos.insert calls, and 10,000 units per day combined for all other endpoints.",
                      "source_url": _YT + "data/getting-started"},
        "rate_limits_url": _YT + "data/determine_quota_cost",
        "terms": {"url": _YT + "terms/api-services-terms-of-service",
                  "quote": "You and your API Client(s) will not, and will not attempt to, exceed or circumvent use or "
                           "quota restrictions."},
        "verified_at": "2026-11-01",
    },
    "slack.com": {
        "auth": [{"method": "oauth2", "source_url": "https://docs.slack.dev/authentication/installing-with-oauth/"}],
        "key_signup_url": "https://docs.slack.dev/quickstart/",
        "rate_limits_url": "https://docs.slack.dev/apis/web-api/rate-limits/",
        "sandbox_url": "https://docs.slack.dev/tools/developer-sandboxes/",
        "official_mcp": [{"endpoint": "https://mcp.slack.com/mcp", "doc_url": "https://docs.slack.dev/ai/slack-mcp-server/"}],
        "terms": {"url": "https://slack.com/terms-of-service/api",
                  "quote": "You may not Commercially Distribute an Application that integrates with the Slack APIs unless "
                           "you are authorized to do so under a separate agreement with Slack or our parent company, Salesforce."},
        "verified_at": "2026-11-01",
    },
    "notion.so": {
        "auth": [{"method": "api_key", "source_url": _NOTION_AUTH}, {"method": "pat", "source_url": _NOTION_AUTH},
                 {"method": "oauth2", "source_url": _NOTION_AUTH}],
        "key_signup_url": "https://developers.notion.com/guides/get-started/quick-start",
        "rate_limits_url": "https://developers.notion.com/reference/request-limits",
        "official_mcp": [{"endpoint": "https://mcp.notion.com/mcp",
                          "doc_url": "https://developers.notion.com/guides/mcp/get-started-with-mcp"}],
        "verified_at": "2026-11-01",
    },
    "reddit.com": {
        "auth": [{"method": "oauth2", "source_url": "https://www.reddit.com/dev/api/"}],
        "terms": {"url": "https://redditinc.com/policies/data-api-terms",
                  "quote": "Reddit reserves the right to charge fees for future use or access to the Data APIs, rates to be "
                           "determined at Reddit's sole discretion."},
        "verified_at": "2026-11-01",
    },
    "x.com": {
        "auth": [{"method": "oauth2", "source_url": _X_AUTH}, {"method": "oauth1", "source_url": _X_AUTH},
                 {"method": "api_key", "source_url": _X_AUTH}],
        "key_signup_url": "https://docs.x.com/x-api/getting-started/getting-access",
        "rate_limits_url": "https://docs.x.com/x-api/fundamentals/rate-limits",
        "openapi_url": "https://api.x.com/2/openapi.json",
        "official_mcp": [{"endpoint": "https://api.x.com/mcp", "doc_url": "https://docs.x.com/tools/mcp"},
                         {"endpoint": "https://docs.x.com/mcp", "doc_url": "https://docs.x.com/tools/mcp"}],
        "terms": {"url": "https://docs.x.com/developer-terms/policy",
                  "quote": "Services that perform write actions, including posting Posts, following accounts, or sending "
                           "Direct Messages, must follow the Automation Rules."},
        "verified_at": "2026-11-01",
    },
    "wikipedia.org": {
        "auth": [{"method": "oauth2", "source_url": "https://www.mediawiki.org/wiki/OAuth/For_Developers"}],
        "rate_limits_url": "https://www.mediawiki.org/wiki/API:Etiquette",
        "terms": {"url": "https://foundation.wikimedia.org/wiki/Policy:Wikimedia_Foundation_User-Agent_Policy",
                  "quote": "Scripts should use an informative User-Agent string with contact information, or they may be "
                           "blocked without notice."},
        "verified_at": "2026-11-01",
    },
    "cloudflare.com": {
        "auth": [{"method": "api_key", "source_url": "https://developers.cloudflare.com/fundamentals/api/get-started/create-token/"}],
        "key_signup_url": "https://developers.cloudflare.com/fundamentals/api/get-started/create-token/",
        "rate_limits_url": "https://developers.cloudflare.com/fundamentals/api/reference/limits/",
        "openapi_url": "https://raw.githubusercontent.com/cloudflare/api-schemas/main/openapi.json",
        "official_mcp": [{"endpoint": "https://mcp.cloudflare.com/mcp",
                          "doc_url": "https://developers.cloudflare.com/agents/model-context-protocol/cloudflare/servers-for-cloudflare/"},
                         {"endpoint": "https://docs.mcp.cloudflare.com/mcp",
                          "doc_url": "https://developers.cloudflare.com/agents/model-context-protocol/cloudflare/servers-for-cloudflare/"}],
        "terms": {"url": "https://www.cloudflare.com/terms/",
                  "quote": "If you permit third parties to access your Cloudflare account (e.g., by providing your API token "
                           "or using OAuth), you do so at your sole risk"},
        "verified_at": "2026-11-01",
    },
    "gitlab.com": {
        "auth": [
            {"method": "oauth2", "source_url": "https://docs.gitlab.com/api/rest/authentication/"},
            {"method": "pat", "source_url": "https://docs.gitlab.com/api/rest/authentication/"},
            {"method": "none", "source_url": "https://docs.gitlab.com/api/rest/authentication/"},
        ],
        "key_signup_url": "https://docs.gitlab.com/user/profile/personal_access_tokens/",
        "rate_limits_url": "https://docs.gitlab.com/user/gitlab_com/rate_limits/",
        "terms": {"url": "https://handbook.gitlab.com/handbook/legal/api-terms/",
                  "quote": "GitLab may place limits on your use of the GitLab APIs (like limits on call volumes or requests "
                           "that can be sent and received by the GitLab APIs during a defined period of time)"},
        "verified_at": "2026-11-01",
    },
    "bitbucket.org": {
        "auth": [
            {"method": "pat", "source_url": "https://developer.atlassian.com/cloud/bitbucket/rest/intro/"},
            {"method": "api_key", "source_url": "https://developer.atlassian.com/cloud/bitbucket/rest/intro/"},
            {"method": "oauth2", "source_url": "https://developer.atlassian.com/cloud/bitbucket/rest/intro/"},
        ],
        "key_signup_url": "https://support.atlassian.com/bitbucket-cloud/docs/api-tokens/",
        "rate_limits_url": "https://support.atlassian.com/bitbucket-cloud/docs/api-request-limits/",
        "openapi_url": "https://dac-static.atlassian.com/cloud/bitbucket/swagger.v3.json?_v=2.300.203",
        "terms": {"url": "https://developer.atlassian.com/platform/marketplace/atlassian-developer-terms/",
                  "quote": "Atlassian may place limits on access to the Atlassian Platform (e.g., limits on numbers of calls or "
                           "requests, or on points that reflect the work performed in response to an API request)."},
        "verified_at": "2026-11-01",
    },
    "google.dev": {
        "auth": [
            {"method": "api_key", "source_url": "https://ai.google.dev/gemini-api/docs/api-key"},
            {"method": "oauth2", "source_url": "https://ai.google.dev/gemini-api/docs/oauth"},
        ],
        "key_signup_url": "https://ai.google.dev/gemini-api/docs/api-key",
        "rate_limits_url": "https://ai.google.dev/gemini-api/docs/rate-limits",
        "terms": {"url": "https://ai.google.dev/gemini-api/terms",
                  "quote": "You will not automatically bypass any requests for human confirmation."},
        "verified_at": "2026-11-01",
    },
    "stackexchange.com": {
        "auth": [
            {"method": "oauth2", "source_url": "https://api.stackexchange.com/docs/authentication"},
            {"method": "api_key", "source_url": "https://api.stackexchange.com/docs"},
            {"method": "none", "source_url": "https://api.stackexchange.com/docs"},
        ],
        "key_signup_url": "https://api.stackexchange.com/docs",
        "free_tier": {"quote": "If an application does have an access_token, then the application is on a distinct user/app pair "
                               "daily quota (default size of 10,000).",
                      "source_url": "https://api.stackexchange.com/docs/throttle"},
        "rate_limits_url": "https://api.stackexchange.com/docs/throttle",
        "verified_at": "2026-11-01",
    },
    "discord.com": {
        "auth": [
            {"method": "api_key", "source_url": "https://docs.discord.com/developers/reference"},
            {"method": "oauth2", "source_url": "https://docs.discord.com/developers/reference"},
        ],
        "key_signup_url": "https://docs.discord.com/developers/bots/overview",
        "rate_limits_url": "https://docs.discord.com/developers/topics/rate-limits",
        "verified_at": "2026-11-01",
    },
    "telegram.org": {
        "auth": [{"method": "api_key", "source_url": "https://core.telegram.org/bots/api"}],
        "key_signup_url": "https://core.telegram.org/bots",
        "free_tier": {"quote": "Each message broadcasted over the free amount of 30 per second incurs a cost of 0.1 Stars per "
                               "message, paid with Telegram Stars from the bot's balance.",
                      "source_url": "https://core.telegram.org/bots/faq"},
        "rate_limits_url": "https://core.telegram.org/bots/faq",
        "terms": {"url": "https://telegram.org/tos/bot-developers",
                  "quote": "Your TPA must not attempt to circumvent or otherwise undermine Telegram rate limits and moderation."},
        "verified_at": "2026-11-01",
    },
    "zoom.us": {
        "auth": [{"method": "oauth2", "source_url": "https://developers.zoom.us/docs/api/using-zoom-apis/"}],
        "key_signup_url": "https://developers.zoom.us/docs/integrations/oauth/",
        "rate_limits_url": "https://developers.zoom.us/docs/api/rate-limits/",
        "terms": {"url": "https://explore.zoom.us/docs/en-us/zoom_api_license_and_tou.html",
                  "quote": "Limitations may include technical limitations, such as call, volume and/or rate limits, or limits "
                           "on your access to some or all of the Zoom APIs."},
        "verified_at": "2026-11-01",
    },
    "asana.com": {
        "auth": [
            {"method": "pat", "source_url": "https://developers.asana.com/docs/authentication"},
            {"method": "oauth2", "source_url": "https://developers.asana.com/docs/authentication"},
            {"method": "api_key", "source_url": "https://developers.asana.com/docs/authentication"},
        ],
        "key_signup_url": "https://developers.asana.com/docs/personal-access-token",
        "rate_limits_url": "https://developers.asana.com/docs/rate-limits",
        "sandbox_url": "https://developers.asana.com/docs/developer-sandbox",
        "openapi_url": "https://raw.githubusercontent.com/Asana/openapi/master/defs/asana_oas.yaml",
        "official_mcp": [{"endpoint": "https://mcp.asana.com/v2/mcp", "doc_url": "https://developers.asana.com/docs/using-asanas-mcp-server"}],
        "terms": {"url": "https://asana.com/terms/api-terms",
                  "quote": "Asana may monitor and limit the User Content that may be accessed, the rate at which such User Content "
                           "may be accessed, and/or the number of network calls that your App may make to our APIs."},
        "verified_at": "2026-11-01",
    },
    "figma.com": {
        "auth": [
            {"method": "pat", "source_url": "https://developers.figma.com/docs/rest-api/authentication/"},
            {"method": "oauth2", "source_url": "https://developers.figma.com/docs/rest-api/authentication/"},
        ],
        "key_signup_url": "https://developers.figma.com/docs/rest-api/personal-access-tokens/",
        "rate_limits_url": "https://developers.figma.com/docs/rest-api/rate-limits/",
        "openapi_url": "https://raw.githubusercontent.com/figma/rest-api-spec/main/openapi/openapi.yaml",
        "official_mcp": [{"endpoint": "https://mcp.figma.com/mcp",
                          "doc_url": "https://developers.figma.com/docs/figma-mcp-server/remote-server-installation/"}],
        "terms": {"url": "https://www.figma.com/legal/developer-terms/",
                  "quote": "Figma may change the fees it charges for access to or use of the Developer Resources (including by "
                           "introducing or modifying rate limits) on thirty days' written notice"},
        "verified_at": "2026-11-01",
    },
    "twilio.com": {
        "auth": [{"method": "api_key", "source_url": "https://www.twilio.com/docs/usage/requests-to-twilio"}],
        "key_signup_url": "https://www.twilio.com/docs/usage/requests-to-twilio",
        "rate_limits_url": "https://www.twilio.com/docs/usage/rest-api-best-practices",
        "official_mcp": [{"endpoint": "https://mcp.twilio.com/docs", "doc_url": "https://www.twilio.com/docs/ai/mcp"}],
        "terms": {"url": "https://www.twilio.com/en-us/legal/aup",
                  "quote": "Transmitting code, files, scripts, agents, or programs intended to do harm, including viruses or "
                           "malware, or using automated means, such as bots, to gain access to or use the Services."},
        "verified_at": "2026-11-01",
    },
    "vercel.com": {
        "auth": [{"method": "api_key", "source_url": "https://vercel.com/docs/rest-api"}],
        "key_signup_url": "https://vercel.com/docs/rest-api/getting-started",
        "rate_limits_url": "https://vercel.com/docs/rest-api",
        "official_mcp": [{"endpoint": "https://mcp.vercel.com", "doc_url": "https://vercel.com/docs/agent-resources/vercel-mcp"}],
        "verified_at": "2026-11-01",
    },
    "netlify.com": {
        "auth": [
            {"method": "pat", "source_url": "https://docs.netlify.com/api-and-cli-guides/api-guides/get-started-with-api/"},
            {"method": "oauth2", "source_url": "https://docs.netlify.com/api-and-cli-guides/api-guides/get-started-with-api/"},
        ],
        "key_signup_url": "https://docs.netlify.com/api-and-cli-guides/api-guides/get-started-with-api/",
        "rate_limits_url": "https://docs.netlify.com/api-and-cli-guides/api-guides/get-started-with-api/",
        "openapi_url": "https://raw.githubusercontent.com/netlify/open-api/master/swagger.yml",
        "verified_at": "2026-11-01",
    },
    "digitalocean.com": {
        "auth": [
            {"method": "pat", "source_url": "https://docs.digitalocean.com/reference/api/create-personal-access-token/"},
            {"method": "oauth2", "source_url": "https://docs.digitalocean.com/reference/api/oauth/"},
        ],
        "key_signup_url": "https://docs.digitalocean.com/reference/api/create-personal-access-token/",
        "official_mcp": [{"endpoint": "https://mcp.digitalocean.com/mcp",
                          "doc_url": "https://docs.digitalocean.com/reference/mcp/configure-mcp/"}],
        "verified_at": "2026-11-01",
    },
    "sentry.io": {
        "auth": [
            {"method": "pat", "source_url": "https://docs.sentry.io/api/auth/"},
            {"method": "api_key", "source_url": "https://docs.sentry.io/api/auth/"},
            {"method": "oauth2", "source_url": "https://docs.sentry.io/api/auth/"},
        ],
        "key_signup_url": "https://docs.sentry.io/api/guides/create-auth-token/",
        "rate_limits_url": "https://docs.sentry.io/api/ratelimits/",
        "verified_at": "2026-11-01",
    },
    "datadoghq.com": {
        "auth": [{"method": "api_key", "source_url": "https://docs.datadoghq.com/api/latest/"}],
        "key_signup_url": "https://docs.datadoghq.com/account_management/api-app-keys/",
        "rate_limits_url": "https://docs.datadoghq.com/api/latest/rate-limits/",
        "verified_at": "2026-11-01",
    },
    "huggingface.co": {
        "auth": [
            {"method": "pat", "source_url": "https://huggingface.co/docs/hub/security-tokens"},
            {"method": "oauth2", "source_url": "https://huggingface.co/docs/hub/oauth"},
        ],
        "key_signup_url": "https://huggingface.co/docs/hub/security-tokens",
        "rate_limits_url": "https://huggingface.co/docs/hub/rate-limits",
        "openapi_url": "https://huggingface.co/.well-known/openapi.json",
        "official_mcp": [{"endpoint": "https://huggingface.co/mcp", "doc_url": "https://huggingface.co/docs/hub/agents-mcp"}],
        "verified_at": "2026-11-01",
    },
    "anthropic.com": {
        "auth": [{"method": "api_key", "source_url": "https://platform.claude.com/docs/en/manage-claude/authentication"}],
        "key_signup_url": "https://platform.claude.com/docs/en/get-api-key",
        "rate_limits_url": "https://platform.claude.com/docs/en/api/rate-limits",
        "terms": {"url": "https://www.anthropic.com/legal/commercial-terms",
                  "quote": "Customer is responsible for all activity under its account."},
        "verified_at": "2026-11-01",
    },
    "perplexity.ai": {
        "auth": [{"method": "api_key", "source_url": "https://docs.perplexity.ai/docs/getting-started/quickstart"}],
        "key_signup_url": "https://docs.perplexity.ai/docs/getting-started/quickstart",
        "rate_limits_url": "https://docs.perplexity.ai/docs/admin/rate-limits-usage-tiers",
        "official_mcp": [{"endpoint": "https://api.perplexity.ai/mcp",
                          "doc_url": "https://docs.perplexity.ai/docs/getting-started/integrations/mcp-server"}],
        "verified_at": "2026-11-01",
    },
    "mistral.ai": {
        "auth": [{"method": "api_key", "source_url": "https://docs.mistral.ai/getting-started/quickstarts/studio/activate-and-generate-api-key"}],
        "key_signup_url": "https://docs.mistral.ai/getting-started/quickstarts/studio/activate-and-generate-api-key",
        "free_tier": {"quote": "Free mode: API access is enabled by default with no credit card required. Usage and rate limits apply.",
                      "source_url": "https://docs.mistral.ai/getting-started/quickstarts/studio/activate-and-generate-api-key"},
        "terms": {"url": "https://legal.mistral.ai/terms/commercial-terms-of-service/",
                  "quote": "Customer is solely responsible for all use of the Outputs and evaluating the Output for improper "
                           "bias, accuracy, responsiveness, and appropriateness for Customer’s use case."},
        "verified_at": "2026-11-01",
    },
    "groq.com": {
        "auth": [{"method": "api_key", "source_url": "https://console.groq.com/docs/quickstart"}],
        "key_signup_url": "https://console.groq.com/docs/quickstart",
        "rate_limits_url": "https://console.groq.com/docs/rate-limits",
        "terms": {"url": "https://console.groq.com/docs/legal/services-agreement",
                  "quote": "Customer may not resell or lease access to its Account."},
        "verified_at": "2026-11-01",
    },
    "together.ai": {
        "auth": [{"method": "api_key", "source_url": "https://docs.together.ai/docs/api-keys-authentication"}],
        "key_signup_url": "https://docs.together.ai/docs/api-keys-authentication",
        "rate_limits_url": "https://docs.together.ai/docs/serverless/rate-limits",
        "verified_at": "2026-11-01",
    },
    "deepseek.com": {
        "auth": [{"method": "api_key", "source_url": "https://api-docs.deepseek.com/"}],
        "key_signup_url": "https://api-docs.deepseek.com/",
        "rate_limits_url": "https://api-docs.deepseek.com/quick_start/rate_limit/",
        "verified_at": "2026-11-01",
    },
    "x.ai": {
        "auth": [{"method": "api_key", "source_url": "https://docs.x.ai/developers/quickstart"}],
        "key_signup_url": "https://docs.x.ai/developers/quickstart",
        "rate_limits_url": "https://docs.x.ai/developers/rate-limits",
        "official_mcp": [{"endpoint": "https://docs.x.ai/api/mcp", "doc_url": "https://docs.x.ai/developers/docs-mcp"}],
        "verified_at": "2026-11-01",
    },
    "openrouter.ai": {
        "auth": [
            {"method": "api_key", "source_url": "https://openrouter.ai/docs/api_reference/authentication"},
            {"method": "oauth2", "source_url": "https://openrouter.ai/docs/guides/overview/auth/oauth"},
        ],
        "key_signup_url": "https://openrouter.ai/docs/api_reference/authentication",
        "rate_limits_url": "https://openrouter.ai/docs/api_reference/limits",
        "openapi_url": "https://openrouter.ai/openapi.json",
        "official_mcp": [{"endpoint": "https://mcp.openrouter.ai/mcp", "doc_url": "https://openrouter.ai/docs/guides/overview/mcp-server"}],
        "verified_at": "2026-11-01",
    },
    "elevenlabs.io": {
        "auth": [{"method": "api_key", "source_url": "https://elevenlabs.io/docs/overview/administration/workspaces/api-keys"}],
        "key_signup_url": "https://elevenlabs.io/docs/eleven-api/quickstart",
        "verified_at": "2026-11-01",
    },
    "supabase.com": {
        "auth": [
            {"method": "pat", "source_url": "https://supabase.com/docs/reference/api/introduction"},
            {"method": "oauth2", "source_url": "https://supabase.com/docs/reference/api/introduction"},
        ],
        "key_signup_url": "https://supabase.com/docs/guides/platform/personal-access-tokens",
        "rate_limits_url": "https://supabase.com/docs/reference/api/introduction",
        "openapi_url": "https://api.supabase.com/api/v1-json",
        "official_mcp": [{"endpoint": "https://mcp.supabase.com/mcp", "doc_url": "https://supabase.com/docs/guides/ai-tools/mcp"}],
        "terms": {"url": "https://supabase.com/terms",
                  "quote": "Customer shall not use the Services for any purposes beyond the scope of the access granted in this Agreement."},
        "verified_at": "2026-11-01",
    },
    "linear.app": {
        "auth": [
            {"method": "pat", "source_url": "https://linear.app/developers/graphql"},
            {"method": "oauth2", "source_url": "https://linear.app/developers/graphql"},
        ],
        "key_signup_url": "https://linear.app/developers/graphql",
        "rate_limits_url": "https://linear.app/developers/rate-limiting",
        "official_mcp": [{"endpoint": "https://mcp.linear.app/mcp", "doc_url": "https://linear.app/docs/mcp"}],
        "verified_at": "2026-11-01",
    },
    "shopify.com": {
        "auth": [
            {"method": "oauth2", "source_url": "https://shopify.dev/docs/api/usage/authentication"},
            {"method": "api_key", "source_url": "https://shopify.dev/docs/api/usage/authentication"},
        ],
        "key_signup_url": "https://shopify.dev/docs/api/usage/authentication",
        "rate_limits_url": "https://shopify.dev/docs/api/usage/limits",
        "sandbox_url": "https://shopify.dev/docs/apps/build/stores/development-stores",
        "terms": {"url": "https://www.shopify.com/legal/api-terms",
                  "quote": "Stay within the limits we set for API calls, and keep your app up to date with API changes."},
        "verified_at": "2026-11-01",
    },
    "paypal.com": {
        "auth": [{"method": "oauth2", "source_url": "https://developer.paypal.com/api/rest/authentication"}],
        "key_signup_url": "https://developer.paypal.com/api/rest/authentication",
        "sandbox_url": "https://developer.paypal.com/sandbox-testing/overview",
        "official_mcp": [{"endpoint": "https://mcp.paypal.com/http", "doc_url": "https://developer.paypal.com/ai-tools/mcp-server"},
                         {"endpoint": "https://mcp.paypal.com/sse", "doc_url": "https://developer.paypal.com/ai-tools/mcp-server"}],
        "terms": {"url": "https://www.paypal.com/us/legalhub/paypal/xdeveloper-full",
                  "quote": "PayPal may also impose limits on certain features and services or restrict your access to parts of "
                           "or all of the Developer’s Tools without notice or liability."},
        "verified_at": "2026-11-01",
    },
    "squareup.com": {
        "auth": [
            {"method": "pat", "source_url": "https://developer.squareup.com/docs/build-basics/access-tokens"},
            {"method": "oauth2", "source_url": "https://developer.squareup.com/docs/build-basics/access-tokens"},
        ],
        "key_signup_url": "https://developer.squareup.com/docs/get-started/create-account-and-application",
        "sandbox_url": "https://developer.squareup.com/docs/devtools/sandbox/overview",
        "official_mcp": [{"endpoint": "https://mcp.squareup.com/mcp", "doc_url": "https://developer.squareup.com/docs/mcp"}],
        "terms": {"url": "https://squareup.com/us/en/legal/general/developers",
                  "quote": "If Square believes that you have attempted to exceed or circumvent these limitations, your ability to "
                           "use the Developer Tools and Content may be temporarily or permanently blocked."},
        "verified_at": "2026-11-01",
    },
    "spotify.com": {
        "auth": [{"method": "oauth2", "source_url": "https://developer.spotify.com/documentation/web-api/concepts/authorization"}],
        "key_signup_url": "https://developer.spotify.com/documentation/web-api/concepts/apps",
        "rate_limits_url": "https://developer.spotify.com/documentation/web-api/concepts/rate-limits",
        "terms": {"url": "https://developer.spotify.com/terms",
                  "quote": "Except as otherwise set out in these Developer Terms, you may not store, aggregate or create "
                           "compilations or databases of Spotify Content, other than as strictly necessary to operate your SDA."},
        "verified_at": "2026-11-01",
    },
    "twitch.tv": {
        "auth": [
            {"method": "oauth2", "source_url": "https://dev.twitch.tv/docs/authentication/"},
            {"method": "device_code", "source_url": "https://dev.twitch.tv/docs/authentication/"},
        ],
        "key_signup_url": "https://dev.twitch.tv/docs/authentication/register-app/",
        "rate_limits_url": "https://dev.twitch.tv/docs/api/guide/",
        "verified_at": "2026-11-01",
    },
    "kakao.com": {
        "auth": [
            {"method": "api_key", "source_url": "https://developers.kakao.com/docs/ko/getting-started/app-key-migration"},
            {"method": "oauth2", "source_url": "https://developers.kakao.com/docs/ko/kakaologin/rest-api"},
        ],
        "key_signup_url": "https://developers.kakao.com/docs/ko/getting-started/kakao-developers",
        "rate_limits_url": "https://developers.kakao.com/docs/ko/getting-started/quota",
        "verified_at": "2026-11-01",
    },
    "tosspayments.com": {
        "auth": [{"method": "api_key", "source_url": "https://docs.tosspayments.com/reference/using-api/authorization"}],
        "key_signup_url": "https://docs.tosspayments.com/reference/using-api/api-keys",
        "verified_at": "2026-11-01",
    },
    "replicate.com": {
        "auth": [{"method": "api_key", "source_url": "https://replicate.com/docs/topics/security/api-tokens"}],
        "key_signup_url": "https://replicate.com/docs/topics/security/api-tokens",
        "rate_limits_url": "https://replicate.com/docs/topics/predictions/rate-limits",
        "openapi_url": "https://api.replicate.com/openapi.json",
        "terms": {"url": "https://replicate.com/terms",
                  "quote": "You are responsible for any and all activities or actions that occur under your Account and/or Login Credentials"},
        "verified_at": "2026-11-01",
    },
    "coinbase.com": {
        "auth": [{"method": "api_key", "source_url": "https://docs.cdp.coinbase.com/api-reference/v2/authentication"}],
        "key_signup_url": "https://docs.cdp.coinbase.com/get-started/initial-setup",
        "rate_limits_url": "https://docs.cdp.coinbase.com/api-reference/v2/rate-limits",
        "openapi_url": "https://docs.cdp.coinbase.com/api-reference/v2/cdp-api-v2.yaml",
        "official_mcp": [{"endpoint": "https://agents.coinbase.com/mcp",
                          "doc_url": "https://docs.cdp.coinbase.com/ai-agents/coinbase-for-agents/coinbase-mcp"}],
        "verified_at": "2026-11-01",
    },
    "plaid.com": {
        "auth": [{"method": "api_key", "source_url": "https://plaid.com/docs/api/"}],
        "key_signup_url": "https://plaid.com/docs/api/",
        "sandbox_url": "https://plaid.com/docs/api/sandbox/",
        "openapi_url": "https://raw.githubusercontent.com/plaid/plaid-openapi/master/2020-09-14.yml",
        "verified_at": "2026-11-01",
    },
    "mongodb.com": {
        "auth": [
            {"method": "oauth2", "source_url": "https://www.mongodb.com/docs/atlas/api/api-authentication/"},
            {"method": "api_key", "source_url": "https://www.mongodb.com/docs/atlas/api/api-authentication/"},
        ],
        "key_signup_url": "https://www.mongodb.com/docs/atlas/api/api-authentication/",
        "rate_limits_url": "https://www.mongodb.com/docs/atlas/api/api-rate-limit/",
        "verified_at": "2026-11-01",
    },
    "sendgrid.com": {
        "auth": [{"method": "api_key", "source_url": "https://www.twilio.com/docs/sendgrid/api-reference/how-to-use-the-sendgrid-v3-api/authentication"}],
        "key_signup_url": "https://www.twilio.com/docs/sendgrid/api-reference/how-to-use-the-sendgrid-v3-api/authentication",
        "rate_limits_url": "https://www.twilio.com/docs/sendgrid/api-reference/how-to-use-the-sendgrid-v3-api/rate-limits",
        "verified_at": "2026-11-01",
    },
    "render.com": {
        "auth": [{"method": "api_key", "source_url": "https://api-docs.render.com/reference/authentication"}],
        "key_signup_url": "https://api-docs.render.com/reference/authentication",
        "rate_limits_url": "https://api-docs.render.com/reference/rate-limiting",
        "verified_at": "2026-11-01",
    },
    # Hosts below carry only `official_mcp`: the endpoint was listed under a domain-verified name in the official MCP
    # registry (candidate finding only) and is printed on the publisher's own documentation page at `doc_url`.
    "atlassian.com": {
        "official_mcp": [{"endpoint": "https://mcp.atlassian.com/v2/mcp",
                          "doc_url": "https://support.atlassian.com/atlassian-ai-gateway/docs/get-started-with-the-atlassian-remote-mcp-server/"}],
        "verified_at": "2026-11-01",
    },
    "zapier.com": {
        "official_mcp": [{"endpoint": "https://mcp.zapier.com/api/v1/connect",
                          "doc_url": "https://docs.zapier.com/mcp/get-started/connect/other"}],
        "verified_at": "2026-11-01",
    },
    "airtable.com": {
        "official_mcp": [{"endpoint": "https://mcp.airtable.com/mcp", "doc_url": "https://airtable.com/developers/agents/mcp/getting-started"}],
        "verified_at": "2026-11-01",
    },
    "alchemy.com": {
        "official_mcp": [{"endpoint": "https://mcp.alchemy.com/mcp", "doc_url": "https://www.alchemy.com/docs/alchemy-mcp-server"}],
        "verified_at": "2026-11-01",
    },
    "amplitude.com": {
        "official_mcp": [{"endpoint": "https://mcp.amplitude.com/mcp", "doc_url": "https://amplitude.com/docs/amplitude-ai/amplitude-mcp"},
                         {"endpoint": "https://mcp.eu.amplitude.com/mcp", "doc_url": "https://amplitude.com/docs/amplitude-ai/amplitude-mcp"}],
        "verified_at": "2026-11-01",
    },
    "bitly.com": {
        "official_mcp": [{"endpoint": "https://api-ssl.bitly.com/v4/mcp", "doc_url": "https://dev.bitly.com/bitly-mcp/"}],
        "verified_at": "2026-11-01",
    },
    "circleci.com": {
        "official_mcp": [{"endpoint": "https://mcp.circleci.com/v1/mcp",
                          "doc_url": "https://circleci.com/docs/guides/toolkit/connecting-to-the-circleci-mcp-server/"}],
        "verified_at": "2026-11-01",
    },
    "coinmarketcap.com": {
        "official_mcp": [{"endpoint": "https://mcp.coinmarketcap.com/mcp", "doc_url": "https://coinmarketcap.com/api/mcp/"}],
        "verified_at": "2026-11-01",
    },
    "exa.ai": {
        "official_mcp": [{"endpoint": "https://mcp.exa.ai/mcp", "doc_url": "https://exa.ai/docs/get-started/exa-mcp"}],
        "verified_at": "2026-11-01",
    },
    "gocardless.com": {
        "official_mcp": [{"endpoint": "https://mcp.gocardless.com", "doc_url": "https://docs.gocardless.com/docs/developer-resources/mcp"}],
        "verified_at": "2026-11-01",
    },
    "jotform.com": {
        "official_mcp": [{"endpoint": "https://mcp.jotform.com", "doc_url": "https://www.jotform.com/developers/mcp/"}],
        "verified_at": "2026-11-01",
    },
    "microsoft.com": {
        "official_mcp": [{"endpoint": "https://learn.microsoft.com/api/mcp",
                          "doc_url": "https://learn.microsoft.com/en-us/training/support/mcp-developer-reference"}],
        "verified_at": "2026-11-01",
    },
    "monday.com": {
        "official_mcp": [{"endpoint": "https://mcp.monday.com/mcp",
                          "doc_url": "https://developer.monday.com/api-reference/docs/integrate-with-monday-mcp"}],
        "verified_at": "2026-11-01",
    },
    "newrelic.com": {
        "official_mcp": [{"endpoint": "https://mcp.newrelic.com/mcp/", "doc_url": "https://docs.newrelic.com/docs/agentic-ai/mcp/setup/"},
                         {"endpoint": "https://mcp.eu.newrelic.com/mcp/", "doc_url": "https://docs.newrelic.com/docs/agentic-ai/mcp/setup/"},
                         {"endpoint": "https://mcp.jp.newrelic.com/mcp/", "doc_url": "https://docs.newrelic.com/docs/agentic-ai/mcp/setup/"}],
        "verified_at": "2026-11-01",
    },
    "paddle.com": {
        "official_mcp": [{"endpoint": "https://mcp.paddle.com/mcp", "doc_url": "https://developer.paddle.com/sdks/ai/paddle-mcp/"},
                         {"endpoint": "https://sandbox-mcp.paddle.com/mcp", "doc_url": "https://developer.paddle.com/sdks/ai/paddle-mcp/"}],
        "verified_at": "2026-11-01",
    },
    "postman.com": {
        "official_mcp": [{"endpoint": "https://mcp.postman.com/minimal", "doc_url": "https://www.postman.com/product/mcp-server/"}],
        "verified_at": "2026-11-01",
    },
    "railway.com": {
        "official_mcp": [{"endpoint": "https://mcp.railway.com", "doc_url": "https://docs.railway.com/ai/mcp-server"}],
        "verified_at": "2026-11-01",
    },
    "twelvedata.com": {
        "official_mcp": [{"endpoint": "https://mcp.twelvedata.com/mcp", "doc_url": "https://twelvedata.com/docs/llms/ai/mcp-server.md"}],
        "verified_at": "2026-11-01",
    },
    "typeform.com": {
        "official_mcp": [{"endpoint": "https://api.typeform.com/mcp", "doc_url": "https://www.typeform.com/developers/mcp/"}],
        "verified_at": "2026-11-01",
    },
    "etherscan.io": {
        "auth": [{"method": "api_key", "source_url": "https://docs.etherscan.io/set-up-your-api-key"}],
        "key_signup_url": "https://docs.etherscan.io/set-up-your-api-key",
        "free_tier": {"source_url": "https://docs.etherscan.io/set-up-your-api-key",
                      "quote": "A free API plan allows 3 calls per second and up to 100,000 calls per day on most chains, cumulative across all the keys you have created."},
        "rate_limits_url": "https://docs.etherscan.io/rate-limits",
        "official_mcp": [{"endpoint": "https://mcp.etherscan.io/mcp", "doc_url": "https://docs.etherscan.io/build-with-ai/mcp"},
                         {"endpoint": "https://mcp-keyless.etherscan.io/mcp", "doc_url": "https://docs.etherscan.io/build-with-ai/mcp"}],
        "verified_at": "2026-11-01",
    },
    "porkbun.com": {
        "auth": [{"method": "api_key", "source_url": "https://porkbun.com/api/json/v3/documentation"}],
        "key_signup_url": "https://porkbun.com/llms/guides/getting-started",
        "sandbox_url": "https://porkbun.com/llms/sandbox",
        "openapi_url": "https://porkbun.com/api/json/v3/spec",
        "official_mcp": [{"endpoint": "https://mcp.porkbun.com/mcp", "doc_url": "https://porkbun.com/api/json/v3/documentation"}],
        "verified_at": "2026-11-01",
    },
    "googleapis.com": {
        "auth": [{"method": "oauth2", "source_url": "https://docs.cloud.google.com/mcp/authenticate-mcp"}],
        "key_signup_url": "https://docs.cloud.google.com/mcp/set-up-authentication-mcp-servers",
        "official_mcp": [{"endpoint": f"https://{_n}.googleapis.com/mcp", "doc_url": "https://docs.cloud.google.com/mcp/supported-products"}
                         for _n in ("bigquery", "compute", "container", "firestore", "sqladmin", "monitoring", "logging",
                                    "cloudresourcemanager", "run", "developerknowledge")],
        "verified_at": "2026-11-01",
    },
    "kraken.com": {
        "auth": [{"method": "api_key", "source_url": "https://docs.kraken.com/home/guides/quickstart"},
                 {"method": "none", "source_url": "https://docs.kraken.com/home/guides/quickstart"}],
        "key_signup_url": "https://docs.kraken.com/home/guides/quickstart",
        "verified_at": "2026-11-01",
    },
    "kagi.com": {
        "auth": [{"method": "api_key", "source_url": "https://help.kagi.com/kagi/api/quick-start.html"}],
        "key_signup_url": "https://help.kagi.com/kagi/api/quick-start.html",
        "openapi_url": "https://kagi.com/api/docs/_spec/openapi.yaml",
        "verified_at": "2026-11-01",
    },
    "rubygems.org": {
        "auth": [{"method": "api_key", "source_url": "https://guides.rubygems.org/rubygems-org-api/"}],
        "key_signup_url": "https://guides.rubygems.org/rubygems-org-api/",
        "rate_limits_url": "https://guides.rubygems.org/rubygems-org-rate-limits/",
        "verified_at": "2026-11-01",
    },
    "yelp.com": {
        "auth": [{"method": "api_key", "source_url": "https://docs.developer.yelp.com/docs/places-authentication"}],
        "key_signup_url": "https://docs.developer.yelp.com/docs/places-authentication",
        "rate_limits_url": "https://docs.developer.yelp.com/docs/places-rate-limiting",
        "terms": {"url": "https://www.yelp.com/developers/api_terms",
                  "quote": "All queries sent to the API requesting data must reference your valid login credentials."},
        "verified_at": "2026-11-01",
    },
    "pagerduty.com": {
        "auth": [{"method": "api_key", "source_url": "https://docs.pagerduty.com/developer/authentication"},
                 {"method": "oauth2", "source_url": "https://docs.pagerduty.com/developer/mcp-tooling-remote-server"}],
        "key_signup_url": "https://docs.pagerduty.com/account-admin/api-access-keys#generate-a-general-access-rest-api-key",
        "rate_limits_url": "https://docs.pagerduty.com/developer/rest-api-rate-limits",
        "official_mcp": [{"endpoint": "https://mcp.pagerduty.com/mcp", "doc_url": "https://docs.pagerduty.com/developer/mcp-tooling-remote-server"}],
        "terms": {"url": "https://docs.pagerduty.com/developer/pagerduty-developer-agreement",
                  "quote": "Sets out the legal terms governing your use of PagerDuty's APIs, MCP servers, and related developer service documentation."},
        "verified_at": "2026-11-01",
    },
    "etsy.com": {
        "auth": [{"method": "api_key", "source_url": "https://developers.etsy.com/documentation/essentials/authentication"},
                 {"method": "oauth2", "source_url": "https://developers.etsy.com/documentation/essentials/authentication"}],
        "key_signup_url": "https://developers.etsy.com/documentation/essentials/authentication",
        "rate_limits_url": "https://developers.etsy.com/documentation/essentials/rate-limits",
        "verified_at": "2026-11-01",
    },
    "ticketmaster.com": {
        "auth": [{"method": "api_key", "source_url": "https://developer.ticketmaster.com/products-and-docs/apis/getting-started/"}],
        "key_signup_url": "https://developer.ticketmaster.com/products-and-docs/apis/getting-started/",
        "free_tier": {"source_url": "https://developer.ticketmaster.com/products-and-docs/apis/getting-started/",
                      "quote": "All API keys are issued with a default quota of 5000 API calls per day and rate limitation of 5 requests per second."},
        "rate_limits_url": "https://developer.ticketmaster.com/products-and-docs/apis/getting-started/",
        "terms": {"url": "https://developer.ticketmaster.com/support/terms-of-use/",
                  "quote": "we reserve the right to rate limit or block applications that make a large number of calls to the API that are not primarily in response to direct user actions."},
        "verified_at": "2026-11-01",
    },
    "dev.to": {
        "auth": [{"method": "api_key", "source_url": "https://developers.forem.com/api"}],
        "key_signup_url": "https://developers.forem.com/api",
        "verified_at": "2026-11-01",
    },
    "data.police.uk": {
        "auth": [{"method": "none", "source_url": "https://data.police.uk/docs/authentication/"}],
        "rate_limits_url": "https://data.police.uk/docs/api-call-limits/",
        "verified_at": "2026-11-01",
    },
    "ietf.org": {
        "auth": [{"method": "none", "source_url": "https://datatracker.ietf.org/api/"}],
        "verified_at": "2026-11-01",
    },
    "quay.io": {
        "auth": [{"method": "oauth2", "source_url": "https://docs.quay.io/api/"}],
        "key_signup_url": "https://docs.quay.io/api/",
        "verified_at": "2026-11-01",
    },
    "flutterwave.com": {
        "auth": [{"method": "api_key", "source_url": "https://developer.flutterwave.com/docs/authentication"}],
        "key_signup_url": "https://developer.flutterwave.com/docs/authentication",
        "rate_limits_url": "https://developer.flutterwave.com/docs/rate-limit",
        "sandbox_url": "https://developer.flutterwave.com/docs/testing",
        "verified_at": "2026-11-01",
    },
    "hmrc.gov.uk": {
        "auth": [{"method": "oauth2", "source_url": "https://developer.service.hmrc.gov.uk/api-documentation/docs/authorisation"},
                 {"method": "none", "source_url": "https://developer.service.hmrc.gov.uk/api-documentation/docs/authorisation"}],
        "sandbox_url": "https://developer.service.hmrc.gov.uk/api-documentation/docs/testing",
        "terms": {"url": "https://developer.service.hmrc.gov.uk/api-documentation/docs/terms-of-use",
                  "quote": "We reserve the right to remove or revoke your access to the Developer Hub and its APIs temporarily or permanently."},
        "verified_at": "2026-11-01",
    },
    "1password.com": {
        "auth": [{"method": "api_key", "source_url": "https://www.1password.dev/service-accounts/get-started"}],
        "key_signup_url": "https://www.1password.dev/service-accounts/get-started",
        "rate_limits_url": "https://www.1password.dev/service-accounts/rate-limits",
        "verified_at": "2026-11-01",
    },
    "godaddy.com": {
        "auth": [{"method": "pat", "source_url": "https://developer.godaddy.com/docs/api-users/auth"},
                 {"method": "api_key", "source_url": "https://developer.godaddy.com/docs/api-users/auth"}],
        "key_signup_url": "https://developer.godaddy.com/docs/api-users/auth/how-to",
        "rate_limits_url": "https://developer.godaddy.com/docs/api-users/rate-limits",
        "openapi_url": "https://developer.godaddy.com/openapi/domains-v3.json",
        "official_mcp": [{"endpoint": "https://mcp.godaddy.com", "doc_url": "https://developer.godaddy.com/docs/api-users/mcp"}],
        "verified_at": "2026-11-01",
    },
    "usps.com": {
        "auth": [{"method": "oauth2", "source_url": "https://developers.usps.com/getting-started"}],
        "key_signup_url": "https://developers.usps.com/getting-started",
        "terms": {"url": "https://developers.usps.com/terms-and-conditions",
                  "quote": "ANY USE OF THE SERVICES, CREDENTIALS, AND/OR DATA NOT SPECIFICALLY PERMITTED UNDER THIS AGREEMENT IS STRICTLY PROHIBITED."},
        "verified_at": "2026-11-01",
    },
    "fireworks.ai": {
        "auth": [{"method": "api_key", "source_url": "https://docs.fireworks.ai/api-reference/introduction"}],
        "key_signup_url": "https://docs.fireworks.ai/api-reference/introduction",
        "rate_limits_url": "https://docs.fireworks.ai/serverless/rate-limits",
        "official_mcp": [{"endpoint": "https://docs.fireworks.ai/mcp", "doc_url": "https://docs.fireworks.ai/ecosystem/integrations/development-setup"}],
        "verified_at": "2026-11-01",
    },
    "cerebras.ai": {
        "auth": [{"method": "api_key", "source_url": "https://inference-docs.cerebras.ai/api-reference/authentication"}],
        "key_signup_url": "https://inference-docs.cerebras.ai/console/api-keys",
        "rate_limits_url": "https://inference-docs.cerebras.ai/support/rate-limits",
        "verified_at": "2026-11-01",
    },
    "deepinfra.com": {
        "auth": [{"method": "api_key", "source_url": "https://docs.deepinfra.com/account/authentication"}],
        "key_signup_url": "https://docs.deepinfra.com/account/authentication",
        "rate_limits_url": "https://docs.deepinfra.com/account/rate-limits",
        "verified_at": "2026-11-01",
    },
    "assemblyai.com": {
        "auth": [{"method": "api_key", "source_url": "https://www.assemblyai.com/docs/sync-stt/getting-started/quickstart"}],
        "key_signup_url": "https://www.assemblyai.com/docs/sync-stt/getting-started/quickstart",
        "rate_limits_url": "https://www.assemblyai.com/docs/pre-recorded-audio/rate-limits",
        "verified_at": "2026-11-01",
    },
    "deepgram.com": {
        "auth": [{"method": "api_key", "source_url": "https://developers.deepgram.com/guides/fundamentals/authenticating"}],
        "key_signup_url": "https://developers.deepgram.com/guides/fundamentals/authenticating",
        "rate_limits_url": "https://developers.deepgram.com/docs/working-with-concurrency-rate-limits",
        "verified_at": "2026-11-01",
    },
    "tavily.com": {
        "auth": [{"method": "api_key", "source_url": "https://docs.tavily.com/documentation/best-practices/api-key-management"},
                 {"method": "none", "source_url": "https://docs.tavily.com/documentation/keyless"}],
        "key_signup_url": "https://docs.tavily.com/documentation/api-credits",
        "free_tier": {"source_url": "https://docs.tavily.com/documentation/api-credits",
                      "quote": "You get 1,000 free API Credits every month."},
        "rate_limits_url": "https://docs.tavily.com/documentation/rate-limits",
        "official_mcp": [{"endpoint": "https://mcp.tavily.com/mcp/", "doc_url": "https://docs.tavily.com/documentation/mcp"}],
        "terms": {"url": "https://www.tavily.com/terms",
                  "quote": "Tavily may make available to Customer an Agent Key that enables automated interaction between Customer's agents and the Services without requiring separate registration or the creation of an Account"},
        "verified_at": "2026-11-01",
    },
    "firecrawl.dev": {
        "auth": [{"method": "api_key", "source_url": "https://docs.firecrawl.dev/introduction"},
                 {"method": "none", "source_url": "https://docs.firecrawl.dev/introduction"}],
        "rate_limits_url": "https://docs.firecrawl.dev/rate-limits",
        "official_mcp": [{"endpoint": "https://mcp.firecrawl.dev/v2/mcp-oauth", "doc_url": "https://docs.firecrawl.dev/mcp-server"},
                         {"endpoint": "https://mcp.firecrawl.dev/v2/mcp", "doc_url": "https://docs.firecrawl.dev/mcp-server"}],
        "verified_at": "2026-11-01",
    },
    "resend.com": {
        "auth": [{"method": "api_key", "source_url": "https://resend.com/docs/api-reference/introduction"}],
        "key_signup_url": "https://resend.com/docs/api-reference/introduction",
        "rate_limits_url": "https://resend.com/docs/api-reference/rate-limit",
        "official_mcp": [{"endpoint": "https://mcp.resend.com/mcp", "doc_url": "https://resend.com/docs/mcp-server"}],
        "verified_at": "2026-11-01",
    },
    "brevo.com": {
        "auth": [{"method": "api_key", "source_url": "https://developers.brevo.com/reference/quickstart-reference"}],
        "key_signup_url": "https://developers.brevo.com/reference/quickstart-reference",
        "rate_limits_url": "https://developers.brevo.com/docs/api-limits",
        "official_mcp": [{"endpoint": "https://mcp.brevo.com/v1/brevo/mcp", "doc_url": "https://developers.brevo.com/docs/mcp-protocol"}],
        "verified_at": "2026-11-01",
    },
    "mollie.com": {
        "auth": [{"method": "api_key", "source_url": "https://docs.mollie.com/reference/authentication"},
                 {"method": "oauth2", "source_url": "https://docs.mollie.com/reference/authentication"}],
        "key_signup_url": "https://docs.mollie.com/reference/authentication",
        "rate_limits_url": "https://docs.mollie.com/reference/rate-limiting",
        "sandbox_url": "https://docs.mollie.com/reference/testing",
        "official_mcp": [{"endpoint": "https://mcp.mollie.com/mcp", "doc_url": "https://docs.mollie.com/docs/mollie-mcp-server"}],
        "verified_at": "2026-11-01",
    },
    "wise.com": {
        "auth": [{"method": "oauth2", "source_url": "https://docs.wise.com/guides/developer/auth-and-security"},
                 {"method": "pat", "source_url": "https://docs.wise.com/guides/developer/auth-and-security/personal-api-token"}],
        "key_signup_url": "https://docs.wise.com/guides/developer/auth-and-security/personal-api-token",
        "verified_at": "2026-11-01",
    },
    "zenodo.org": {
        "auth": [{"method": "oauth2", "source_url": "https://developers.zenodo.org/"},
                 {"method": "pat", "source_url": "https://developers.zenodo.org/"}],
        "key_signup_url": "https://developers.zenodo.org/",
        "rate_limits_url": "https://developers.zenodo.org/",
        "sandbox_url": "https://developers.zenodo.org/",
        "verified_at": "2026-11-01",
    },
    "abuseipdb.com": {
        "auth": [{"method": "api_key", "source_url": "https://docs.abuseipdb.com/"}],
        "key_signup_url": "https://docs.abuseipdb.com/",
        "rate_limits_url": "https://docs.abuseipdb.com/",
        "verified_at": "2026-11-01",
    },
    "openalex.org": {
        "auth": [{"method": "none", "source_url": "https://help.openalex.org/api/authentication/"},
                 {"method": "api_key", "source_url": "https://help.openalex.org/api/authentication/"}],
        "key_signup_url": "https://help.openalex.org/api/authentication/",
        "rate_limits_url": "https://help.openalex.org/api/authentication/",
        "verified_at": "2026-11-01",
    },
    "virustotal.com": {
        "auth": [{"method": "api_key", "source_url": "https://docs.virustotal.com/docs/api-overview"}],
        "key_signup_url": "https://docs.virustotal.com/docs/please-give-me-an-api-key",
        "free_tier": {"source_url": "https://docs.virustotal.com/docs/api-overview",
                      "quote": "The public API is a free service, available for any website or application that is free to consumers."},
        "rate_limits_url": "https://docs.virustotal.com/docs/api-overview",
        "verified_at": "2026-11-01",
    },
    "alphavantage.co": {
        "auth": [{"method": "api_key", "source_url": "https://www.alphavantage.co/support/"}],
        "key_signup_url": "https://www.alphavantage.co/support/",
        "free_tier": {"source_url": "https://www.alphavantage.co/support/",
                      "quote": "We are pleased to provide free stock API service covering the majority of our datasets for 25 API requests per day and unlimited API requests for verified open-source or educational projects."},
        "rate_limits_url": "https://www.alphavantage.co/support/",
        "verified_at": "2026-11-01",
    },
    "ipinfo.io": {
        "auth": [{"method": "api_key", "source_url": "https://ipinfo.io/developers"}],
        "key_signup_url": "https://ipinfo.io/developers",
        "free_tier": {"source_url": "https://ipinfo.io/developers",
                      "quote": "IPinfo Lite is our free tier API service available to all users. The API returns country and ASN information without any API request quota restrictions."},
        "rate_limits_url": "https://ipinfo.io/developers",
        "terms": {"url": "https://ipinfo.io/terms-of-service",
                  "quote": "You are solely responsible for any activity using your Credentials, whether or not you authorized that activity."},
        "verified_at": "2026-11-01",
    },
    "openweathermap.org": {
        "auth": [{"method": "api_key", "source_url": "https://openweathermap.org/appid"}],
        "key_signup_url": "https://openweathermap.org/appid",
        "verified_at": "2026-11-01",
    },
    "intercom.com": {
        "auth": [{"method": "api_key", "source_url": "https://developers.intercom.com/docs/build-an-integration/learn-more/authentication"},
                 {"method": "oauth2", "source_url": "https://developers.intercom.com/docs/build-an-integration/learn-more/authentication"}],
        "key_signup_url": "https://developers.intercom.com/docs/build-an-integration/learn-more/authentication",
        "official_mcp": [{"endpoint": "https://mcp.intercom.com/mcp", "doc_url": "https://developers.intercom.com/docs/guides/mcp"},
                         {"endpoint": "https://mcp.eu.intercom.com/mcp", "doc_url": "https://developers.intercom.com/docs/guides/mcp"}],
        "terms": {"url": "https://developers.intercom.com/docs/publish-to-the-app-store/intercom-developer-terms",
                  "quote": "Developer must keep all Access Credentials confidential."},
        "verified_at": "2026-11-01",
    },
    "posthog.com": {
        "auth": [{"method": "pat", "source_url": "https://posthog.com/docs/api/personal-api-keys"},
                 {"method": "oauth2", "source_url": "https://posthog.com/docs/api"}],
        "key_signup_url": "https://posthog.com/docs/api/personal-api-keys",
        "rate_limits_url": "https://posthog.com/docs/api",
        "verified_at": "2026-11-01",
    },
    "ngrok.com": {
        "auth": [{"method": "api_key", "source_url": "https://ngrok.com/docs/api/"}],
        "key_signup_url": "https://ngrok.com/docs/api/",
        "rate_limits_url": "https://ngrok.com/docs/api/",
        "openapi_url": "https://raw.githubusercontent.com/ngrok/ngrok-openapi/refs/heads/main/ngrok.yaml",
        "verified_at": "2026-11-01",
    },
}
# Separate hosts of the same publisher and API share one bundle.
for _alias, _primary in {"chatgpt.com": "openai.com", "notion.com": "notion.so", "twitter.com": "x.com",
                         "wikimedia.org": "wikipedia.org", "stackoverflow.com": "stackexchange.com"}.items():
    OFFICIAL_ACCESS[_alias] = OFFICIAL_ACCESS[_primary]


# The site's own customer path (D61), answered as official_paths.consumer for some table keys. A developer API is the path
# for building on a site's data; it may not reach a person's own account there (refill a prescription, pay a bill, change
# a booking), though some do through OAuth. For hosts with a clear consumer service this table lists where the site itself sends people:
#   customer_help  the site's own help centre or contact page (a phone number is never copied: the page is linked instead);
#   official_app   an App Store or Google Play listing that the site's own page links to;
#   entry_link     a public entry point the site itself links to for a common customer task.
# Every entry was read on verified_at with the curation user agent, after that host's robots.txt allowed it. url is the page
# itself; source_url is the site's own page where the link was found (the same page for a help centre reached directly).
# url and source_url are on the key's registrable domain, except an official_app url, which is on a store host and must be
# linked from source_url. Store URLs keep only the listing identifier (no campaign parameters). Nothing here is guessed:
# a page that refused the read, or a link not found on the site's own page, is left out. Procedure: scripts/README.md.
CONSUMER_KINDS = ("customer_help", "official_app", "entry_link")
CONSUMER_STORE_HOSTS = frozenset({"apps.apple.com", "play.google.com"})
CONSUMER_MAX_PATHS = 6
# A consumer entry older than this is left out of answers until it is verified again (same age as access bundles).
CONSUMER_MAX_AGE_DAYS = 90


def _help(url: str, source_url: str | None = None) -> dict:
    return {"kind": "customer_help", "url": url, "source_url": source_url or url}


def _app(url: str, source_url: str) -> dict:
    return {"kind": "official_app", "url": url, "source_url": source_url}


def _entry_link(url: str, source_url: str) -> dict:
    return {"kind": "entry_link", "url": url, "source_url": source_url}


CONSUMER_PATHS: dict[str, dict] = {
    "walgreens.com": {"paths": [
        _help("https://www.walgreens.com/topic/help/default.jsp", "https://www.walgreens.com/"),
        _help("https://www.walgreens.com/mktg/contactus/contact-us-landing.jsp", "https://www.walgreens.com/"),
        _entry_link("https://www.walgreens.com/rx-checkout/guest-express-refill", "https://www.walgreens.com/"),
    ], "verified_at": "2026-10-31"},
    "walmart.com": {"paths": [
        _help("https://www.walmart.com/help", "https://www.walmart.com/"),
    ], "verified_at": "2026-10-31"},
    "target.com": {"paths": [
        # corporate.target.com "Contact & Help" links contactus.target.com/ContactUs, which redirects here.
        _help("https://www.target.com/help/contact-us", "https://corporate.target.com/about/contact-help"),
    ], "verified_at": "2026-10-31"},
    "chase.com": {"paths": [
        _help("https://www.chase.com/digital/customer-service", "https://www.chase.com/"),
        _help("https://www.chase.com/personal/personal-banking-contact", "https://www.chase.com/digital/customer-service"),
    ], "verified_at": "2026-10-31"},
    "wellsfargo.com": {"paths": [
        _help("https://www.wellsfargo.com/help/", "https://www.wellsfargo.com/"),
        _help("https://www.wellsfargo.com/help/contact-us/", "https://www.wellsfargo.com/help/"),
        _app("https://apps.apple.com/us/app/wells-fargo-mobile/id311548709", "https://www.wellsfargo.com/"),
        _app("https://play.google.com/store/apps/details?id=com.wf.wellsfargomobile", "https://www.wellsfargo.com/"),
    ], "verified_at": "2026-10-31"},
    "capitalone.com": {"paths": [
        _help("https://www.capitalone.com/help-center/", "https://www.capitalone.com/"),
        _help("https://www.capitalone.com/help-center/contact-us/", "https://www.capitalone.com/"),
        _app("https://apps.apple.com/us/app/capital-one-mobile/id407558537", "https://www.capitalone.com/digital/tools/mobile/"),
    ], "verified_at": "2026-10-31"},
    "americanexpress.com": {"paths": [
        _help("https://www.americanexpress.com/us/customer-service/", "https://www.americanexpress.com/"),
        _help("https://www.americanexpress.com/en-us/support/contacts/", "https://www.americanexpress.com/"),
    ], "verified_at": "2026-10-31"},
    "verizon.com": {"paths": [
        _help("https://www.verizon.com/support/", "https://www.verizon.com/"),
        _app("https://apps.apple.com/us/app/my-verizon/id416023011", "https://www.verizon.com/solutions-and-services/my-verizon-mobile/"),
        _app("https://play.google.com/store/apps/details?id=com.vzw.hss.myverizon",
             "https://www.verizon.com/solutions-and-services/my-verizon-mobile/"),
    ], "verified_at": "2026-10-31"},
    "usps.com": {"paths": [
        _help("https://faq.usps.com/s/", "https://www.usps.com/"),
        _help("https://www.usps.com/help/contact-us.htm", "https://www.usps.com/"),
    ], "verified_at": "2026-10-31"},
    "uber.com": {"paths": [
        _help("https://help.uber.com/", "https://www.uber.com/"),
        _app("https://apps.apple.com/us/app/uber-rides-eats-hotels/id368677368", "https://help.uber.com/"),
        _app("https://play.google.com/store/apps/details?id=com.ubercab", "https://help.uber.com/"),
    ], "verified_at": "2026-10-31"},
    "paypal.com": {"paths": [
        _help("https://www.paypal.com/us/cshelp/personal", "https://www.paypal.com/us/home"),
        _app("https://apps.apple.com/us/app/paypal-pay-send-save/id283646709", "https://www.paypal.com/us/digital-wallet"),
        _app("https://play.google.com/store/apps/details?id=com.paypal.android.p2pmobile", "https://www.paypal.com/us/digital-wallet"),
    ], "verified_at": "2026-10-31"},
    "spotify.com": {"paths": [
        _help("https://support.spotify.com/us/"),
        _help("https://support.spotify.com/us/article/contact-us/", "https://support.spotify.com/us/"),
    ], "verified_at": "2026-10-31"},
    "dell.com": {"paths": [
        _help("https://www.dell.com/support/home/en-us", "https://www.dell.com/en-us"),
        _help("https://www.dell.com/en-us/shop/lp/contact-us", "https://www.dell.com/en-us"),
    ], "verified_at": "2026-10-31"},
    "samsung.com": {"paths": [
        _help("https://www.samsung.com/us/support/contact/", "https://www.samsung.com/us/"),
    ], "verified_at": "2026-10-31"},
    "lg.com": {"paths": [
        _help("https://www.lg.com/us/support", "https://www.lg.com/us/"),
        _help("https://www.lg.com/us/support/contact", "https://www.lg.com/us/"),
    ], "verified_at": "2026-10-31"},
    "apple.com": {"paths": [
        _help("https://www.apple.com/contact/", "https://www.apple.com/"),
        _app("https://apps.apple.com/us/app/apple-store/id375380948", "https://www.apple.com/"),
    ], "verified_at": "2026-10-31"},
    "microsoft.com": {"paths": [
        _help("https://support.microsoft.com/en-us/", "https://www.microsoft.com/en-us"),
        _help("https://support.microsoft.com/contactus", "https://www.microsoft.com/en-us"),
    ], "verified_at": "2026-10-31"},
    "ticketmaster.com": {"paths": [
        # the home page links help.ticketmaster.com, which redirects here.
        _help("https://help.ticketmaster.com/hc/en-us"),
        _entry_link("https://www.ticketmaster.com/giftcards", "https://www.ticketmaster.com/"),
        _app("https://apps.apple.com/us/app/ticketmaster-buy-sell-tickets/id500003565", "https://www.ticketmaster.com/"),
        _app("https://play.google.com/store/apps/details?id=com.ticketmaster.mobile.android.na", "https://www.ticketmaster.com/"),
    ], "verified_at": "2026-10-31"},
    "eventbrite.com": {"paths": [
        _help("https://www.eventbrite.com/help/en-us/", "https://www.eventbrite.com/"),
        _help("https://www.eventbrite.com/help/en-us/contact-us/", "https://www.eventbrite.com/"),
    ], "verified_at": "2026-10-31"},
    "thumbtack.com": {"paths": [
        _help("https://help.thumbtack.com", "https://www.thumbtack.com/"),
    ], "verified_at": "2026-10-31"},
    "steampowered.com": {"paths": [
        _help("https://help.steampowered.com/en/", "https://store.steampowered.com/"),
    ], "verified_at": "2026-10-31"},
    "strava.com": {"paths": [
        # the home page links support.strava.com, which redirects here.
        _help("https://support.strava.com/en-us/"),
        _app("https://apps.apple.com/us/app/strava-run-bike-walk/id426826309", "https://www.strava.com/"),
        _app("https://play.google.com/store/apps/details?id=com.strava", "https://www.strava.com/"),
    ], "verified_at": "2026-10-31"},
    "deezer.com": {"paths": [
        _help("https://support.deezer.com/hc/en-gb", "https://www.deezer.com/en/"),
        _app("https://apps.apple.com/us/app/deezer-music-player-podcast/id292738169", "https://www.deezer.com/en/"),
        _app("https://play.google.com/store/apps/details?id=deezer.android.app", "https://www.deezer.com/en/"),
    ], "verified_at": "2026-10-31"},
    "youtube.com": {"paths": [
        _help("https://www.youtube.com/t/contact_us/", "https://www.youtube.com/"),
    ], "verified_at": "2026-10-31"},
    "vimeo.com": {"paths": [
        # the home page links help.vimeo.com, which redirects here.
        _help("https://help.vimeo.com/hc/en-us"),
    ], "verified_at": "2026-10-31"},
    "dropbox.com": {"paths": [
        _help("https://help.dropbox.com/", "https://www.dropbox.com/"),
        _help("https://www.dropbox.com/contact", "https://www.dropbox.com/"),
    ], "verified_at": "2026-10-31"},
    "1password.com": {"paths": [
        _help("https://1password.com/contact-support", "https://1password.com/"),
    ], "verified_at": "2026-10-31"},
    "bitwarden.com": {"paths": [
        _help("https://bitwarden.com/help/", "https://bitwarden.com/"),
    ], "verified_at": "2026-10-31"},
    "todoist.com": {"paths": [
        _help("https://www.todoist.com/help", "https://www.todoist.com/"),
    ], "verified_at": "2026-10-31"},
    "whatsapp.com": {"paths": [
        _help("https://faq.whatsapp.com/", "https://www.whatsapp.com/"),
        _help("https://www.whatsapp.com/contact", "https://www.whatsapp.com/"),
    ], "verified_at": "2026-10-31"},
    "telegram.org": {"paths": [
        _help("https://www.telegram.org/faq", "https://www.telegram.org/"),
    ], "verified_at": "2026-10-31"},
    "slack.com": {"paths": [
        _help("https://slack.com/help", "https://slack.com/"),
    ], "verified_at": "2026-10-31"},
    "wix.com": {"paths": [
        _help("https://support.wix.com/en", "https://www.wix.com/"),
        _help("https://www.wix.com/about/contact-us", "https://www.wix.com/"),
    ], "verified_at": "2026-10-31"},
    "wordpress.com": {"paths": [
        _help("https://wordpress.com/support/", "https://wordpress.com/"),
        _help("https://wordpress.com/support/contact/", "https://wordpress.com/"),
    ], "verified_at": "2026-10-31"},
    "venmo.com": {"paths": [
        _help("https://help.venmo.com/cs/home", "https://venmo.com/"),
        _help("https://help.venmo.com/cs/contact-us", "https://venmo.com/"),
    ], "verified_at": "2026-10-31"},
    "wise.com": {"paths": [
        _help("https://wise.com/help/", "https://wise.com/"),
    ], "verified_at": "2026-10-31"},
    "klarna.com": {"paths": [
        _help("https://www.klarna.com/us/help/", "https://www.klarna.com/us/"),
    ], "verified_at": "2026-10-31"},
    "affirm.com": {"paths": [
        _help("https://helpcenter.affirm.com/s/", "https://www.affirm.com/"),
    ], "verified_at": "2026-10-31"},
    "flightaware.com": {"paths": [
        _help("https://www.flightaware.com/about/faq/", "https://www.flightaware.com/"),
    ], "verified_at": "2026-10-31"},
    "mta.info": {"paths": [
        _help("https://www.mta.info/contact-us", "https://www.mta.info/"),
    ], "verified_at": "2026-10-31"},
    "mbta.com": {"paths": [
        _help("https://www.mbta.com/customer-support", "https://www.mbta.com/"),
    ], "verified_at": "2026-10-31"},
    "irs.gov": {"paths": [
        _help("https://www.irs.gov/help/let-us-help-you", "https://www.irs.gov/"),
    ], "verified_at": "2026-10-31"},
    "healthcare.gov": {"paths": [
        _help("https://www.healthcare.gov/contact-us/", "https://www.healthcare.gov/"),
    ], "verified_at": "2026-10-31"},
    "zoom.com": {"paths": [
        _help("https://support.zoom.com/hc/en", "https://www.zoom.com/"),
    ], "verified_at": "2026-10-31"},
    "notion.com": {"paths": [
        _help("https://www.notion.com/help", "https://www.notion.com/"),
    ], "verified_at": "2026-10-31"},
    "figma.com": {"paths": [
        _help("https://help.figma.com/hc/en-us", "https://www.figma.com/"),
    ], "verified_at": "2026-10-31"},
    "supercell.com": {"paths": [
        _help("https://supercell.com/en/support/", "https://supercell.com/"),
    ], "verified_at": "2026-10-31"},
    "calendly.com": {"paths": [
        _help("https://calendly.com/help", "https://calendly.com/"),
        _help("https://calendly.com/contact-us", "https://calendly.com/"),
        _app("https://apps.apple.com/us/app/calendly-meeting-scheduling/id1451094657", "https://calendly.com/"),
    ], "verified_at": "2026-10-31"},
    "meta.com": {"paths": [
        _help("https://www.meta.com/help/", "https://www.meta.com/"),
    ], "verified_at": "2026-10-31"},
    "surveymonkey.com": {"paths": [
        _help("https://help.surveymonkey.com/en/", "https://www.surveymonkey.com/"),
    ], "verified_at": "2026-10-31"},
    "synology.com": {"paths": [
        # the home page links /company/contact_us, which redirects here.
        _help("https://www.synology.com/en-global/company/contact_us"),
    ], "verified_at": "2026-10-31"},
    "tomtom.com": {"paths": [
        # the home page links help.tomtom.com/hc/, which redirects here.
        _help("https://help.tomtom.com/hc/en-150"),
    ], "verified_at": "2026-10-31"},
    "nvidia.com": {"paths": [
        _help("https://www.nvidia.com/en-us/support/", "https://www.nvidia.com/en-us/"),
        _help("https://www.nvidia.com/en-us/contact/", "https://www.nvidia.com/en-us/"),
    ], "verified_at": "2026-10-31"},
    "intel.com": {"paths": [
        _help("https://www.intel.com/content/www/us/en/support.html", "https://www.intel.com/content/www/us/en/homepage.html"),
    ], "verified_at": "2026-10-31"},
    "last.fm": {"paths": [
        _help("https://www.last.fm/about/contact", "https://www.last.fm/"),
    ], "verified_at": "2026-10-31"},
    # Consumer-only hosts (D64): no OFFICIAL_PATHS entry, so official_paths answers with consumer only.
    "cvs.com": {"paths": [
        _help("https://www.cvs.com/retail/help/help_index", "https://www.cvs.com/"),
        _help("https://www.cvs.com/retail/help/help_contact_us", "https://www.cvs.com/"),
        _entry_link("https://www.cvs.com/store-locator/landing", "https://www.cvs.com/"),
        _app("https://apps.apple.com/us/app/cvs-health/id395545555", "https://www.cvs.com/"),
        _app("https://play.google.com/store/apps/details?id=com.cvs.launchers.cvs", "https://www.cvs.com/"),
    ], "verified_at": "2026-10-31"},
    "jetblue.com": {"paths": [
        _help("https://www.jetblue.com/contact-us", "https://www.jetblue.com/"),
    ], "verified_at": "2026-10-31"},
    "nike.com": {"paths": [
        _help("https://www.nike.com/help", "https://www.nike.com/"),
    ], "verified_at": "2026-10-31"},
    "ikea.com": {"paths": [
        _help("https://www.ikea.com/global/en/support/faq/", "https://www.ikea.com/"),
    ], "verified_at": "2026-10-31"},
    "progressive.com": {"paths": [
        _help("https://www.progressive.com/contact-us/", "https://www.progressive.com/"),
    ], "verified_at": "2026-10-31"},
    "statefarm.com": {"paths": [
        _help("https://www.statefarm.com/help-center/account-management", "https://www.statefarm.com/"),
        _entry_link("https://www.statefarm.com/claims", "https://www.statefarm.com/"),
        _app("https://apps.apple.com/us/app/state-farm/id318142137", "https://www.statefarm.com/"),
        _app("https://play.google.com/store/apps/details?id=com.statefarm.pocketagent", "https://www.statefarm.com/"),
    ], "verified_at": "2026-10-31"},
    "peacocktv.com": {"paths": [
        _help("https://www.peacocktv.com/help", "https://www.peacocktv.com/"),
        _entry_link("https://www.peacocktv.com/gift-cards", "https://www.peacocktv.com/"),
        _app("https://apps.apple.com/us/app/peacock-tv-stream-tv-movies/id1508186374", "https://www.peacocktv.com/"),
        _app("https://play.google.com/store/apps/details?id=com.peacocktv.peacockandroid", "https://www.peacocktv.com/"),
    ], "verified_at": "2026-10-31"},
    "sling.com": {"paths": [
        _help("https://www.sling.com/help/en"),
        _entry_link("https://www.sling.com/deals/gift-card", "https://www.sling.com/"),
    ], "verified_at": "2026-10-31"},
    "siriusxm.com": {"paths": [
        _help("https://www.siriusxm.com/help", "https://www.siriusxm.com/"),
    ], "verified_at": "2026-10-31"},
    "lyft.com": {"paths": [
        _help("https://help.lyft.com/hc/en-us/rider/categories/2872191865", "https://www.lyft.com/"),
    ], "verified_at": "2026-10-31"},
    "visible.com": {"paths": [
        _help("https://www.visible.com/help", "https://www.visible.com/"),
        _app("https://apps.apple.com/us/app/visible-mobile/id1367950045", "https://www.visible.com/"),
        _app("https://play.google.com/store/apps/details?id=com.visiblemobile.flagship", "https://www.visible.com/"),
    ], "verified_at": "2026-10-31"},
    "cricketwireless.com": {"paths": [
        _help("https://www.cricketwireless.com/support", "https://www.cricketwireless.com/"),
        _help("https://www.cricketwireless.com/contactus", "https://www.cricketwireless.com/"),
        _app("https://play.google.com/store/apps/details?id=com.cricketwireless.trycricket", "https://www.cricketwireless.com/"),
    ], "verified_at": "2026-10-31"},
    "boostmobile.com": {"paths": [
        _help("https://help.boostmobile.com/", "https://www.boostmobile.com/"),
        _entry_link("https://www.boostmobile.com/locations/locator/", "https://www.boostmobile.com/"),
    ], "verified_at": "2026-10-31"},
    "dish.com": {"paths": [
        _help("https://support.dish.com", "https://www.dish.com/"),
    ], "verified_at": "2026-10-31"},
    "cash.app": {"paths": [
        _help("https://cash.app/help", "https://cash.app/"),
    ], "verified_at": "2026-10-31"},
    "publix.com": {"paths": [
        _help("https://www.publix.com/contact", "https://www.publix.com/"),
        _entry_link("https://www.publix.com/gift-cards", "https://www.publix.com/"),
    ], "verified_at": "2026-10-31"},
    "enterprise.com": {"paths": [
        _help("https://www.enterprise.com/en/help/contact.html", "https://www.enterprise.com/en/home.html"),
        _app("https://play.google.com/store/apps/details?id=com.ehi.enterprise.android", "https://www.enterprise.com/en/home.html"),
    ], "verified_at": "2026-10-31"},
    "avis.com": {"paths": [
        _help("https://www.avis.com/en/customer-service", "https://www.avis.com/en/home"),
    ], "verified_at": "2026-10-31"},
    "petsmart.com": {"paths": [
        _help("https://www.petsmart.com/help", "https://www.petsmart.com/"),
        _help("https://www.petsmart.com/account/contact", "https://www.petsmart.com/"),
        _entry_link("https://www.petsmart.com/gift-cards/", "https://www.petsmart.com/"),
    ], "verified_at": "2026-10-31"},
    "academy.com": {"paths": [
        _help("https://www.academy.com/help", "https://www.academy.com/"),
        _entry_link("https://www.academy.com/c/shops/gift-card-shop", "https://www.academy.com/"),
        _app("https://apps.apple.com/us/app/academy-sports-outdoors/id1572699554", "https://www.academy.com/"),
        _app("https://play.google.com/store/apps/details?id=com.academy.android", "https://www.academy.com/"),
    ], "verified_at": "2026-10-31"},
    "travelers.com": {"paths": [
        _help("https://www.travelers.com/contact-us", "https://www.travelers.com/"),
    ], "verified_at": "2026-10-31"},
    "coned.com": {"paths": [
        _help("https://www.coned.com/en/faqs/frequently-asked-questions", "https://www.coned.com/en/"),
        _help("https://www.coned.com/en/contact-us", "https://www.coned.com/en/"),
    ], "verified_at": "2026-10-31"},
    "pge.com": {"paths": [
        _help("https://www.pge.com/en/contact-us.html", "https://www.pge.com/"),
    ], "verified_at": "2026-10-31"},
    "coursera.org": {"paths": [
        _help("https://www.coursera.org/about/contact", "https://www.coursera.org/"),
    ], "verified_at": "2026-10-31"},
    "vividseats.com": {"paths": [
        _help("https://www.vividseats.com/contact-us", "https://www.vividseats.com/"),
        _entry_link("https://www.vividseats.com/gift-center", "https://www.vividseats.com/"),
    ], "verified_at": "2026-10-31"},
    "shutterfly.com": {"paths": [
        _help("https://support.shutterfly.com/s/", "https://support.shutterfly.com"),
        _entry_link("https://www.shutterfly.com/gift-certificates/", "https://www.shutterfly.com/"),
    ], "verified_at": "2026-10-31"},
    "sonos.com": {"paths": [
        _help("https://support.sonos.com/en-us/home", "https://www.sonos.com/en-us/home"),
        _help("https://www.sonos.com/en-us/contact", "https://www.sonos.com/en-us/home"),
    ], "verified_at": "2026-10-31"},
    "roku.com": {"paths": [
        _help("https://www.roku.com/about/contact", "https://www.roku.com/"),
    ], "verified_at": "2026-10-31"},
    "nintendo.com": {"paths": [
        _help("https://en-americas-support.nintendo.com/app/region/d/home", "https://support.nintendo.com/"),
    ], "verified_at": "2026-10-31"},
    "flyfrontier.com": {"paths": [
        _help("https://www.flyfrontier.com/customer-service/", "https://www.flyfrontier.com/"),
        _app("https://apps.apple.com/us/app/frontier-airlines/id1041410548", "https://www.flyfrontier.com/"),
        _app("https://play.google.com/store/apps/details?id=com.flyfrontier.android", "https://www.flyfrontier.com/"),
    ], "verified_at": "2026-10-31"},
    "xbox.com": {"paths": [
        _help("https://support.xbox.com/en-US/", "https://www.xbox.com/en-US/"),
    ], "verified_at": "2026-10-31"},
    "playstation.com": {"paths": [
        _help("https://www.playstation.com/en-us/support/", "https://www.playstation.com/en-us/"),
    ], "verified_at": "2026-10-31"},
    "paramountplus.com": {"paths": [
        _help("https://help.paramountplus.com/s/", "https://help.paramountplus.com/s/"),
    ], "verified_at": "2026-10-31"},
    "vistaprint.com": {"paths": [
        _help("https://www.vistaprint.com/customer-care/help-center/", "https://www.vistaprint.com/"),
    ], "verified_at": "2026-10-31"},
    "kayak.com": {"paths": [
        _help("https://www.kayak.com/c/help/", "https://www.kayak.com/"),
        _app("https://apps.apple.com/us/app/kayak-flights-hotels-cars/id305204535", "https://www.kayak.com/"),
        _app("https://play.google.com/store/apps/details?id=com.kayak.android", "https://www.kayak.com/"),
    ], "verified_at": "2026-10-31"},
}


def consumer_is_current(consumer: dict, today: date | None = None) -> bool:
    """True while verified_at is at most CONSUMER_MAX_AGE_DAYS old (and not in the future)."""
    today = today or datetime.now(timezone.utc).date()
    age = (today - date.fromisoformat(consumer["verified_at"])).days
    return 0 <= age <= CONSUMER_MAX_AGE_DAYS


def customer_url(official: dict | None) -> str | None:
    """The one consumer URL a next step points to: the first help page, else the first entry link, else the first app."""
    paths = ((official or {}).get("consumer") or {}).get("paths") or []
    for kind in ("customer_help", "entry_link", "official_app"):
        for item in paths:
            if item.get("kind") == kind and item.get("url"):
                return item["url"]
    return None


def access_is_current(access: dict, today: date | None = None) -> bool:
    """True while verified_at is at most ACCESS_MAX_AGE_DAYS old (and not in the future)."""
    today = today or datetime.now(timezone.utc).date()
    age = (today - date.fromisoformat(access["verified_at"])).days
    return 0 <= age <= ACCESS_MAX_AGE_DAYS


# D66: hosts whose listed developer path cannot be used by an ordinary caller. Kept out of official_paths() on purpose, so no
# response field carries it. Under AHN_PROCEED_WITHOUT_FAILURE=d65 a lookup that reports no failure is not assumed blocked for
# these hosts (it would send the caller to an API it cannot use). A host that is not listed here is "unknown": no gate.
# Evidence: Amazon's developer documentation for the Selling Partner API says individual accounts are not eligible; Reddit's help
# page on its developer platform says access is for approved developers after an App Review.
USABLE_NOW_BLOCKED = {
    "amazon.com": {"access": "partner_only", "verified_at": "2026-10-31",
                   "source_url": "https://developer-docs.amazon/sp-api/docs/registering-as-a-developer"},
    "reddit.com": {"access": "approval_required", "verified_at": "2026-10-31",
                   "source_url": "https://support.reddithelp.com/hc/en-us/articles/14945211791892-Developer-Platform-Accessing-Reddit-Data"},
}


def official_path_unusable(site_host: str) -> bool:
    labels = site_host.strip().rstrip(".").lower().split(".")
    return any(".".join(labels[index:]) in USABLE_NOW_BLOCKED for index in range(len(labels) - 1))


def official_paths(site_host: str, today: date | None = None) -> dict | None:
    """Exact apex match first, then the apex of any subdomain; returns a copy or None.

    The copy carries `access` only when the key has a bundle that is still current, and `consumer` (D61) only when the
    key has a consumer entry that is still current; otherwise each key is absent. A host that has a current consumer
    entry but no OFFICIAL_PATHS entry (D64) answers with `consumer` only: name is the table key, and api_docs,
    developer_portal and note are null, so the verdict (which counts only api_docs and developer_portal) is unchanged.
    """
    host = site_host.strip().rstrip(".").lower()
    labels = host.split(".")
    for index in range(len(labels) - 1):
        key = ".".join(labels[index:])
        entry = OFFICIAL_PATHS.get(key)
        if entry is None:
            consumer = CONSUMER_PATHS.get(key)
            if consumer is not None and consumer_is_current(consumer, today):
                return {"name": key, "api_docs": None, "developer_portal": None, "note": None,
                        "consumer": copy.deepcopy(consumer)}
            continue
        if entry is not None:
            result = dict(entry)
            access = OFFICIAL_ACCESS.get(key)
            if access is not None and access_is_current(access, today):
                result["access"] = copy.deepcopy(access)
            consumer = CONSUMER_PATHS.get(key)
            if consumer is not None and consumer_is_current(consumer, today):
                result["consumer"] = copy.deepcopy(consumer)
            return result
    return None
