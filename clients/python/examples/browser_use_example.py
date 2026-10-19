"""Browser Use: when a run fails, ask Agent Help Network what the target host recommends.

    pip install ahn-client browser-use
    python browser_use_example.py https://example.com/

Build ``llm`` as the Browser Use documentation shows for your provider; the Agent call
below follows its documented ``Agent(task=..., llm=...)`` / ``await agent.run()`` shape,
so check it against the version you have installed. The lookup sends only the target
hostname and a coarse error class, and the script stops or hands off when told to.
"""

import asyncio
import sys

import ahn_client as ahn
from browser_use import Agent


def handle_failure(target_url, exc):
    """Print what to do next. Returns True when another attempt is reasonable."""
    error_class, stage = ahn.classify(exc)
    answer = ahn.check(target_url, error_class, stage)  # blocking, 4 s at most; never raises
    if answer is None:
        print("No answer from Agent Help Network (or an excluded host). Not retrying automatically.")
        return False
    print(answer.summary())
    if answer.recommendation == "use_official_path":
        print("Use the official path instead of driving the page:", answer.official_url or "(see the answer)")
    elif answer.recommendation == "hand_off_to_human":
        print("The next step needs the user (sign-in or human verification). Ask them to do it.")
    elif answer.recommendation == "wait_provider_incident":
        print("The provider reports an incident. Wait; do not retry in a loop.")
    elif answer.recommendation == "stop_declared_block":
        print("The site declares agents blocked. Stopping; no workaround attempted.")
    else:
        return True  # proceed / unknown: one modest retry is not ruled out
    return False


async def main(target_url, llm):
    agent = Agent(task=f"Open {target_url} and report the page title.", llm=llm)
    try:
        await agent.run()
    except Exception as exc:
        if handle_failure(target_url, exc):
            print("Retrying once.")
            await Agent(task=f"Open {target_url} and report the page title.", llm=llm).run()


if __name__ == "__main__":
    from browser_use import ChatBrowserUse  # or the chat model class for your provider

    asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else "https://example.com/", ChatBrowserUse()))
