"""Run a Browser Use agent that can ask Agent Help Network when a page blocks it.

    pip install -r requirements.txt
    python run_example.py https://example.com/

Build the chat model as the Browser Use documentation shows for your provider. ChatBrowserUse needs a Browser Use API key;
swap in ChatAnthropic, ChatOpenAI or another class if you prefer.
"""

import asyncio
import sys

from browser_use import Agent, ChatBrowserUse, Tools

from ahn_browser_use import register_tool

TASK = ("Open {url} and report the page title. If the page is blocked (403, CAPTCHA, login wall, 429, 5xx), "
        "call check_blocked_site once and do what it says.")


async def main(url: str) -> None:
    tools = Tools()
    register_tool(tools)
    agent = Agent(task=TASK.format(url=url), llm=ChatBrowserUse(), tools=tools)
    history = await agent.run(max_steps=15)
    print(history.final_result())


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else "https://example.com/"))
