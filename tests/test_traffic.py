from datetime import datetime, timezone
from decimal import Decimal

from starlette.requests import Request

from ahn.platform_metrics import latest_sample
from ahn.traffic import client_type, daily_client_digest, request_source


def _request(path: str, headers: dict[str, str] | None = None) -> Request:
    raw_headers = [(key.lower().encode(), value.encode()) for key, value in (headers or {}).items()]
    return Request({"type": "http", "method": "GET", "path": path.split("?", 1)[0],
                    "query_string": path.partition("?")[2].encode(), "headers": raw_headers})


def test_attribution_bounds_untrusted_input():
    req = _request("/en/agents?utm_source=moltychan&utm_medium=community&utm_campaign=beta_launch",
                   {"Referer": "https://moltychan.org/tool/33?secret=do-not-store", "User-Agent": "Mozilla/5.0"})
    assert request_source(req) == ("moltychan", "community", "beta_launch", "moltychan")
    assert client_type(req) == "browser_hint"
    unknown = _request("/?utm_source=" + "x" * 1000, {"Referer": "https://private.example/path?token=secret"})
    assert request_source(unknown) == ("tagged_other", "unknown", "none_or_other", "other")
    assert request_source(_request("/en/?utm_source=campfire&utm_medium=community"))[0] == "campfire"
    assert request_source(_request("/en/", {"Referer": "https://www.agentboard.ai/t/example"}))[0] == "agentboard"


def test_daily_digest_uses_only_edge_ip():
    pepper = "test-pepper-must-be-at-least-thirty-two-chars"
    trusted = _request("/", {"CF-Connecting-IP": "198.51.100.23", "X-Forwarded-For": "1.2.3.4"})
    other = _request("/", {"CF-Connecting-IP": "198.51.100.24"})
    day, digest = daily_client_digest(trusted, pepper)
    assert day == datetime.now(timezone.utc).date()
    assert digest and digest != daily_client_digest(other, pepper)[1]
    assert daily_client_digest(_request("/", {"X-Forwarded-For": "1.2.3.4"}), pepper)[1] is None


def test_render_sample_sums_distinct_series_at_latest_time():
    sample = latest_sample([
        {"labels": [{"field": "service", "value": "srv-test"}], "unit": "requests",
         "values": [{"timestamp": "2026-10-04T13:00:00Z", "value": 3}]},
        {"labels": [{"field": "service", "value": "srv-test"}], "unit": "requests",
         "values": [{"timestamp": "2026-10-04T13:00:00Z", "value": 2}]},
    ], "srv-test")
    assert sample == (datetime(2026, 10, 4, 13, tzinfo=timezone.utc), Decimal(5), "requests")
