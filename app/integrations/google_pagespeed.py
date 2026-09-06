from __future__ import annotations

import os
import time
from typing import Any

import requests


class PageSpeedClient:
    ENDPOINT = "https://pagespeedonline.googleapis.com/pagespeedonline/v5/runPagespeed"
    RETRY_STATUS_CODES = {500, 502, 503, 504}
    MAX_RETRIES = 2

    def __init__(self, api_key: str | None = None, timeout: float = 120):
        self.api_key = api_key or os.getenv("PAGESPEED_API_KEY")
        self.timeout = timeout

    def analyze(
        self,
        url: str,
        strategy: str = "mobile",
        categories: list[str] | None = None,
        oauth_token: str | None = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"url": url, "strategy": strategy}
        if self.api_key:
            params["key"] = self.api_key
        for category in categories or ["performance", "accessibility", "best-practices", "seo"]:
            params.setdefault("category", []).append(category)

        headers = {"Authorization": f"Bearer {oauth_token}"} if oauth_token else None
        last_response: requests.Response | None = None

        for attempt in range(self.MAX_RETRIES + 1):
            response = requests.get(
                self.ENDPOINT,
                params=params,
                headers=headers,
                timeout=self.timeout,
            )
            last_response = response

            if response.status_code not in self.RETRY_STATUS_CODES:
                response.raise_for_status()
                return response.json()

            if attempt < self.MAX_RETRIES:
                time.sleep(1.5 * (attempt + 1))

        assert last_response is not None
        detail = last_response.text.strip()
        if len(detail) > 1000:
            detail = detail[:1000] + "..."
        raise requests.HTTPError(
            f"HTTP {last_response.status_code} after {self.MAX_RETRIES + 1} attempts"
            + (f": {detail}" if detail else ""),
            response=last_response,
        )
