"""ahn-client: ask Agent Help Network what a host declares and recommends when a web step is blocked.

Agent Help Network is an independent project, not an official service of OpenAI, Meta,
Muse, dots or any site it describes. This client only reads; it never reports.

    import ahn_client as ahn

    try:
        page.goto(url)
    except Exception as exc:
        error_class, stage = ahn.classify(exc)
        if error_class:  # None: a 404 or a bad request, not a block or an outage
            answer = ahn.check(url, error_class, stage, **ahn.signals(exc))
            if answer:
                print(answer.summary())
"""

from ._classify import classify, signals
from ._client import check, clear_cache
from ._diagnose import diagnose
from ._result import NextAction, Result

__version__ = "0.6.1"
__all__ = ["check", "classify", "clear_cache", "diagnose", "signals", "Result", "NextAction", "__version__"]
