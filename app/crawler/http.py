import asyncio
import time

import httpx

from app.config import (
    DEFAULT_TIMEOUT,
    DEFAULT_USER_AGENT,
    MAX_RETRIES,
)


# Retry only transient transport/server failures. Access-denied, not-found,
# rate-limited and other client responses are returned immediately.
RETRYABLE_STATUS_CODES = frozenset({408, 500, 502, 503, 504})


class HTTPClient:

    def __init__(self, concurrency: int):
        self.concurrency = max(1, int(concurrency))
        self.semaphore = asyncio.Semaphore(self.concurrency)
        self.client = httpx.AsyncClient(
            http2=False,
            follow_redirects=True,
            timeout=httpx.Timeout(DEFAULT_TIMEOUT),
            limits=httpx.Limits(
                max_connections=self.concurrency * 2,
                max_keepalive_connections=self.concurrency,
            ),
            headers={
                "User-Agent": DEFAULT_USER_AGENT,
                "Accept": (
                    "text/html,"
                    "application/xhtml+xml,"
                    "application/xml;q=0.9,"
                    "*/*;q=0.8"
                ),
                "Accept-Encoding": "gzip, deflate",
            },
        )

    async def close(self):
        await self.client.aclose()

    def _decode_content(self, response):
        raw = response.content
        encoding = response.encoding or "utf-8"
        try:
            return raw.decode(encoding, errors="replace")
        except Exception:
            return raw.decode("utf-8", errors="replace")

    async def get(
        self,
        url,
        *,
        retries=None,
        timeout=None,
        retry_statuses=None,
    ):
        """Fetch once for special checks, or retry transient failures.

        retries is the number of additional attempts after the first.
        Permanent/blocked HTTP statuses (403, 404, 429, etc.) are never retried.
        RobotsManager uses retries=0 and its own short timeout.
        """
        retry_limit = MAX_RETRIES if retries is None else max(0, int(retries))
        status_codes = (
            RETRYABLE_STATUS_CODES
            if retry_statuses is None
            else frozenset(int(code) for code in retry_statuses)
        )

        async with self.semaphore:
            last_error = None
            started_total = time.perf_counter()

            for attempt_index in range(retry_limit + 1):
                try:
                    request_kwargs = {}
                    if timeout is not None:
                        request_kwargs["timeout"] = timeout

                    response = await self.client.get(url, **request_kwargs)

                    # Retry only a short allowlist of transient server statuses.
                    if (
                        response.status_code in status_codes
                        and attempt_index < retry_limit
                    ):
                        await response.aclose()
                        await asyncio.sleep(min(2 ** attempt_index, 2))
                        continue

                    content_type = response.headers.get("content-type", "")
                    if (
                        "text/" in content_type.lower()
                        or "html" in content_type.lower()
                        or "xml" in content_type.lower()
                    ):
                        decoded = self._decode_content(response)
                        response._content = decoded.encode(
                            "utf-8",
                            errors="replace",
                        )
                        response.encoding = "utf-8"

                    return {
                        "response": response,
                        "elapsed": round(time.perf_counter() - started_total, 3),
                        "error": None,
                        "attempt": attempt_index,
                        "attempts": attempt_index + 1,
                    }

                except httpx.TransportError as exc:
                    last_error = f"{type(exc).__name__}: {exc}"
                    if attempt_index >= retry_limit:
                        break
                    await asyncio.sleep(min(2 ** attempt_index, 2))

                except Exception as exc:
                    # Unexpected/client-side errors are not treated as transient.
                    last_error = f"{type(exc).__name__}: {exc}"
                    break

            return {
                "response": None,
                "elapsed": round(time.perf_counter() - started_total, 3),
                "error": last_error or "Request failed without a response",
                "attempt": min(retry_limit, attempt_index),
                "attempts": min(retry_limit + 1, attempt_index + 1),
            }
