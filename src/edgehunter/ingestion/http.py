from __future__ import annotations

import asyncio
import ipaddress
import json
import socket
import ssl
import time
from collections.abc import Mapping
from typing import Any
from urllib.parse import urlsplit

import httpx


class SourceError(RuntimeError):
    pass


class PublicHTTP:
    """GET only; fixed HTTPS hosts, no redirects, bounded bytes/concurrency/rate.

    Hostnames originate in project configuration, never in ingested content.
    System CA store is retained (including Windows enterprise certificates).
    """

    def __init__(self, hosts: set[str], *, transport: httpx.AsyncBaseTransport | None = None,
                 max_bytes: int = 4_000_000, interval: float = 0.15) -> None:
        self.hosts = frozenset(hosts)
        self.max_bytes = max_bytes
        self.interval = interval
        self._test_transport = transport is not None
        self.client = httpx.AsyncClient(
            verify=ssl.create_default_context(), timeout=20, follow_redirects=False,
            transport=transport, headers={"User-Agent": "EdgeHunter/0.1 public-research"},
            limits=httpx.Limits(max_connections=4),
        )
        self._semaphore = asyncio.Semaphore(3)
        self._rate_lock = asyncio.Lock()
        self._last_request = 0.0

    async def close(self) -> None:
        await self.client.aclose()

    async def _validate(self, url: str) -> None:
        parsed = urlsplit(url)
        if (parsed.scheme != "https" or parsed.hostname not in self.hosts
                or parsed.username or parsed.password or parsed.port not in (None, 443)):
            raise SourceError("URL_NOT_ALLOWED")
        if self._test_transport:
            return
        answers = await asyncio.to_thread(socket.getaddrinfo, parsed.hostname, 443,
                                         type=socket.SOCK_STREAM)
        if not answers or any(not ipaddress.ip_address(a[4][0]).is_global for a in answers):
            raise SourceError("NONPUBLIC_DNS_ADDRESS")

    async def get(self, url: str, params: Mapping[str, str | int] | None = None) -> bytes:
        await self._validate(url)
        async with self._semaphore:
            for attempt in range(3):
                async with self._rate_lock:
                    delay = self.interval - (time.monotonic() - self._last_request)
                    if delay > 0:
                        await asyncio.sleep(delay)
                    self._last_request = time.monotonic()
                try:
                    async with self.client.stream("GET", url, params=params) as response:
                        if response.status_code in (429, 502, 503, 504) and attempt < 2:
                            await asyncio.sleep(min(4.0, 0.5 * 2 ** attempt))
                            continue
                        if response.status_code != 200:
                            raise SourceError(f"HTTP_{response.status_code}")
                        body = bytearray()
                        async for chunk in response.aiter_bytes():
                            body.extend(chunk)
                            if len(body) > self.max_bytes:
                                raise SourceError("RESPONSE_TOO_LARGE")
                        return bytes(body)
                except httpx.HTTPError as exc:
                    if attempt == 2:
                        raise SourceError(type(exc).__name__) from None
                    await asyncio.sleep(0.5 * 2 ** attempt)
        raise SourceError("RETRY_EXHAUSTED")

    async def json(self, url: str, params: Mapping[str, str | int] | None = None) -> Any:
        try:
            return json.loads(await self.get(url, params))
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise SourceError("INVALID_JSON") from None
