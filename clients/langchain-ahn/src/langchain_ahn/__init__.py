"""langchain-ahn: Agent Help Network for LangChain.

Agent Help Network is an independent project, not an official service of LangChain or any provider.
This package only reads: a hostname and fixed codes leave the machine, never a URL path, a query or a body.

Two ways to use it:

    # 1. Automatic: add a line to a web tool call that failed with a block or an outage (LangChain v1 agents).
    from langchain.agents import create_agent
    from langchain_ahn import ahn_tool_failure_middleware

    agent = create_agent(model, tools=[fetch_page], middleware=[ahn_tool_failure_middleware()])

    # 2. A tool the model calls itself when a web step is blocked.
    from langchain_ahn import AHNLookupTool

    agent = create_agent(model, tools=[fetch_page, AHNLookupTool()])
"""

from ahn_client.langchain import (
    ahandle_tool_call,
    ahn_tool_failure_middleware,
    handle_tool_call,
)

from .tools import AHNLookupInput, AHNLookupTool, format_result

__version__ = "0.2.1"
__all__ = [
    "AHNLookupInput",
    "AHNLookupTool",
    "ahandle_tool_call",
    "ahn_tool_failure_middleware",
    "format_result",
    "handle_tool_call",
    "__version__",
]
