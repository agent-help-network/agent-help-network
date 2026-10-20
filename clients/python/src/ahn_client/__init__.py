"""ahn-client: ask Agent Help Network what a host declares and recommends when a web step is blocked.

Agent Help Network is an independent project, not an official service of OpenAI, Meta,
Muse, dots or any site it describes. This client only reads; it never reports.

    import ahn_client as ahn

    try:
        page.goto(url)
    except Exception as exc:
        answer = ahn.check(url, *ahn.classify(exc))
        if answer:
            print(answer.summary())
"""

from ._classify import classify
from ._client import check, clear_cache
from ._result import Result

__version__ = "0.2.0"
__all__ = ["check", "classify", "clear_cache", "Result", "__version__"]
