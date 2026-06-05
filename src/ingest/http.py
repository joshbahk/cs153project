"""Hardened HTTP client for bulk discovery and full-text resolution.

This wraps ``httpx`` with browser-like headers, automatic redirect following,
bounded retries with exponential backoff + jitter, and a polite per-host rate
limiter. It is used by the multi-source discovery adapters and the full-text
resolver chain. The legacy ``urllib`` helpers in ``batch_importer`` are kept
separately because existing tests patch ``urllib.request.urlopen`` directly.
"""

from __future__ import annotations

import random
import threading
import time
from dataclasses import dataclass
from typing import Any

import httpx

_DEFAULT_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36 replication-triage/0.1"
)


class HttpError(RuntimeError):
    """Raised when a request fails after exhausting retries."""


@dataclass(slots=True)
class HttpResponse:
    status_code: int
    content: bytes
    text: str
    content_type: str
    url: str


class _RateLimiter:
    """Minimum spacing between requests to the same host."""

    def __init__(self, min_interval: float) -> None:
        self._min_interval = max(0.0, min_interval)
        self._lock = threading.Lock()
        self._last: dict[str, float] = {}

    def wait(self, host: str) -> None:
        if self._min_interval <= 0:
            return
        with self._lock:
            now = time.monotonic()
            earliest = self._last.get(host, 0.0) + self._min_interval
            sleep_for = earliest - now
            self._last[host] = max(now, earliest)
        if sleep_for > 0:
            time.sleep(sleep_for)


_RETRYABLE_STATUS = {408, 425, 429, 500, 502, 503, 504}


class HttpClient:
    """Resilient HTTP client shared across an import batch."""

    def __init__(
        self,
        *,
        timeout: float = 25.0,
        max_retries: int = 3,
        backoff_base: float = 0.6,
        backoff_cap: float = 8.0,
        min_host_interval: float = 0.15,
        user_agent: str = _DEFAULT_UA,
        mailto: str = "",
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._timeout = timeout
        self._max_retries = max(0, max_retries)
        self._backoff_base = backoff_base
        self._backoff_cap = backoff_cap
        self._limiter = _RateLimiter(min_host_interval)
        from_addr = f" (mailto:{mailto})" if mailto else ""
        self._headers = {
            "User-Agent": user_agent + from_addr,
            "Accept": "*/*",
            "Accept-Language": "en-US,en;q=0.9",
        }
        self._client = httpx.Client(
            timeout=timeout,
            follow_redirects=True,
            headers=self._headers,
            limits=httpx.Limits(max_connections=8, max_keepalive_connections=4),
            transport=transport,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "HttpClient":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _backoff(self, attempt: int, retry_after: float | None) -> float:
        if retry_after is not None:
            return min(retry_after, self._backoff_cap)
        delay = self._backoff_base * (2 ** attempt)
        return min(delay, self._backoff_cap) + random.uniform(0, 0.25)

    def request(
        self,
        method: str,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        accept: str | None = None,
        max_bytes: int | None = None,
    ) -> HttpResponse:
        host = httpx.URL(url).host or ""
        merged_headers = dict(headers or {})
        if accept:
            merged_headers["Accept"] = accept

        last_error: Exception | None = None
        for attempt in range(self._max_retries + 1):
            self._limiter.wait(host)
            try:
                with self._client.stream(
                    method, url, params=params, headers=merged_headers
                ) as response:
                    status = response.status_code
                    if status in _RETRYABLE_STATUS and attempt < self._max_retries:
                        retry_after = _parse_retry_after(response.headers.get("retry-after"))
                        response.close()
                        time.sleep(self._backoff(attempt, retry_after))
                        continue
                    if status >= 400:
                        raise HttpError(f"HTTP {status} for {url}")

                    content = _read_capped(response, max_bytes)
                    content_type = response.headers.get("content-type", "")
                    text = _decode(content, content_type)
                    return HttpResponse(
                        status_code=status,
                        content=content,
                        text=text,
                        content_type=content_type,
                        url=str(response.url),
                    )
            except (httpx.TransportError, httpx.TimeoutException) as exc:
                last_error = exc
                if attempt < self._max_retries:
                    time.sleep(self._backoff(attempt, None))
                    continue
                raise HttpError(f"Network error for {url}: {exc}") from exc
            except HttpError as exc:
                last_error = exc
                raise
        raise HttpError(f"Request to {url} failed: {last_error}")

    def get_json(
        self,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> Any:
        response = self.request(
            "GET", url, params=params, headers=headers, accept="application/json"
        )
        import json

        return json.loads(response.text or "null")

    def get_bytes(
        self,
        url: str,
        *,
        max_bytes: int,
        params: dict[str, Any] | None = None,
    ) -> HttpResponse:
        return self.request("GET", url, params=params, max_bytes=max_bytes)

    def get_text(
        self,
        url: str,
        *,
        max_bytes: int,
        params: dict[str, Any] | None = None,
    ) -> HttpResponse:
        return self.request("GET", url, params=params, max_bytes=max_bytes)


def _read_capped(response: httpx.Response, max_bytes: int | None) -> bytes:
    declared = response.headers.get("content-length")
    if max_bytes is not None and declared and declared.isdigit() and int(declared) > max_bytes:
        raise HttpError(
            f"Remote file is larger than the {max_bytes // (1024 * 1024)} MB limit."
        )
    chunks: list[bytes] = []
    total = 0
    for chunk in response.iter_bytes():
        total += len(chunk)
        if max_bytes is not None and total > max_bytes:
            raise HttpError(
                f"Remote file is larger than the {max_bytes // (1024 * 1024)} MB limit."
            )
        chunks.append(chunk)
    return b"".join(chunks)


def _decode(content: bytes, content_type: str) -> str:
    charset = "utf-8"
    if "charset=" in content_type.lower():
        charset = content_type.lower().split("charset=", 1)[1].split(";", 1)[0].strip() or "utf-8"
    try:
        return content.decode(charset, errors="ignore")
    except LookupError:
        return content.decode("utf-8", errors="ignore")


def _parse_retry_after(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return float(value)
    except ValueError:
        return None
