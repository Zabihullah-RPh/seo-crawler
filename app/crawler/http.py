import asyncio
import time
import gzip
import zlib

import httpx

from app.config import (
    DEFAULT_TIMEOUT,
    DEFAULT_USER_AGENT,
    MAX_RETRIES
)


class HTTPClient:

    def __init__(
        self,
        concurrency: int
    ):
        self.concurrency = int(concurrency)

        self.semaphore = asyncio.Semaphore(
            self.concurrency
        )

        self.client = httpx.AsyncClient(
            http2=False,
            follow_redirects=True,
            timeout=httpx.Timeout(
                DEFAULT_TIMEOUT
            ),
            limits=httpx.Limits(
                max_connections=self.concurrency * 2,
                max_keepalive_connections=self.concurrency
            ),
            headers={
                "User-Agent": DEFAULT_USER_AGENT,
                "Accept": (
                    "text/html,"
                    "application/xhtml+xml,"
                    "application/xml;q=0.9,"
                    "*/*;q=0.8"
                ),
                "Accept-Encoding": "gzip, deflate"
            }
        )

    async def close(self):
        await self.client.aclose()

    def _decode_content(self, response):
        raw = response.content

        encoding = (
            response.encoding
            or "utf-8"
        )

        try:
            return raw.decode(
                encoding,
                errors="replace"
            )
        except Exception:
            return raw.decode(
                "utf-8",
                errors="replace"
            )

    async def get(self, url):

        async with self.semaphore:

            last_error = None

            for attempt in range(
                MAX_RETRIES + 1
            ):

                start = time.perf_counter()

                try:

                    response = await self.client.get(
                        url
                    )

                    elapsed = (
                        time.perf_counter()
                        - start
                    )

                    # httpx normally decompresses gzip/deflate
                    # automatically. Force the response encoding
                    # before the parser receives the content.
                    content_type = (
                        response.headers.get(
                            "content-type",
                            ""
                        )
                    )

                    if (
                        "text/" in content_type.lower()
                        or "html" in content_type.lower()
                        or "xml" in content_type.lower()
                    ):
                        text = self._decode_content(
                            response
                        )

                        # Replace response.text with guaranteed
                        # decoded text for the crawler/parser.
                        response._content = text.encode(
                            "utf-8",
                            errors="replace"
                        )
                        response.encoding = "utf-8"

                    return {
                        "response": response,
                        "elapsed": elapsed,
                        "error": None,
                        "attempt": attempt
                    }

                except Exception as exc:

                    last_error = str(exc)

                    if attempt < MAX_RETRIES:

                        await asyncio.sleep(
                            2 ** attempt
                        )

            return {
                "response": None,
                "elapsed": 0,
                "error": last_error,
                "attempt": MAX_RETRIES
            }
