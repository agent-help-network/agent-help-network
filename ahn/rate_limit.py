"""Small single-instance request limiter for the initial public read-only preview."""

from __future__ import annotations

import asyncio
import ipaddress
import time

from starlette.requests import Request


class RequestLimiter:
    def __init__(self, *, per_client: int = 120, total: int = 600, seconds: int = 60):
        self.per_client = per_client
        self.total = total
        self.seconds = seconds
        self._lock = asyncio.Lock()
        self._window = -1
        self._total = 0
        self._clients: dict[str, int] = {}

    @staticmethod
    def client_key(request: Request) -> str:
        # The hosting edge overwrites CF-Connecting-IP, so it is the trusted key
        # (same trust the owner gate uses). X-Forwarded-For's first entry is
        # client-controlled and only serves local runs without an edge.
        for header in ("cf-connecting-ip", "x-forwarded-for"):
            candidate = request.headers.get(header, "").split(",", 1)[0].strip()
            try:
                return str(ipaddress.ip_address(candidate))
            except ValueError:
                continue
        return request.client.host if request.client else "unknown"

    async def allow(self, request: Request) -> bool:
        key = self.client_key(request)
        window = int(time.monotonic() // self.seconds)
        async with self._lock:
            if window != self._window:
                self._window = window
                self._total = 0
                self._clients.clear()
            if self._total >= self.total or self._clients.get(key, 0) >= self.per_client:
                return False
            self._total += 1
            self._clients[key] = self._clients.get(key, 0) + 1
            return True
