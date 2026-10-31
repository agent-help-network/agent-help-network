"""Browser Use: an ``on_step_end`` hook that asks Agent Help Network when the page a step ended on looks blocked.

    pip install ahn-client browser-use
    python browser_use_hook_example.py https://example.com/

The hook (https://docs.browser-use.com/customize/hooks; check it against the version you have installed) runs
after every step. It reads only the current page's URL and title; when the title looks like a block page it looks
the host up and prints the answer for you. Browser Use documents no supported way for a hook to add text to the
model's context, so this hook only reports to the developer, and Browser Use does not give hooks the step's error
text, so a title is all it can go on.

To put the answer in front of the model, register a custom action instead: see examples/browser-use/ in this
repository (``ahn_browser_use.py``), which also handles the ``retry_lookup_once`` wait.

The lookup sends the hostname and a coarse error class only: no path, query, title or page content. It does not
try to get past a site's controls.
"""

import asyncio
import re
import sys
from urllib.parse import urlsplit

import ahn_client as ahn
from browser_use import Agent

ADAPTER = "browser-use"  # shows up in the User-Agent so the service can count client kinds
# Titles of the usual block pages. A title that matches none of these is left alone.
BLOCK_TITLE = re.compile(r"\b(?:just a moment|attention required|access denied|forbidden|verify (?:that )?you are (?:a )?human"
                         r"|are you a robot|captcha|too many requests|service unavailable|bad gateway"
                         r"|internal server error|gateway time-?out)\b|\b(?:error|http)\s*(?:403|429|50[0-4])\b", re.I)
# A bare number is not enough: "S&P 500" and "500 Startups" are page titles, not a 500.
VERIFICATION_TITLE = re.compile(r"\b(?:just a moment|attention required|verify (?:that )?you are (?:a )?human|are you a robot|captcha)\b", re.I)


def block_class(title):
    """error_class for a block page's title, or None when the title does not look like one."""
    if not title or not BLOCK_TITLE.search(title):
        return None
    if VERIFICATION_TITLE.search(title):
        return "human_verification"
    return ahn.classify(title)[0] or "site_access"


async def on_step_end(agent: Agent) -> None:
    """Report to the developer when the page the step ended on looks like a block page."""
    try:
        url = await agent.browser_session.get_current_page_url()
        title = await agent.browser_session.get_current_page_title()
        error_class = block_class(title)
        if not error_class or not urlsplit(url).hostname:
            return
        answer = await asyncio.to_thread(ahn.check, url, error_class, "response_observed", timeout=3.0, adapter=ADAPTER)
        if answer is not None:
            print("[ahn]", answer.summary())
    except Exception:
        pass  # a lookup must never break the run


async def main(target_url, llm):
    agent = Agent(task=f"Open {target_url} and report the page title.", llm=llm)
    await agent.run(on_step_end=on_step_end)


if __name__ == "__main__":
    from browser_use import ChatBrowserUse  # or the chat model class for your provider

    asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else "https://example.com/", ChatBrowserUse()))
