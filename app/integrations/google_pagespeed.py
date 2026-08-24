from __future__ import annotations

import os
from typing import Any

import requests


class PageSpeedClient:
    ENDPOINT = "https://pagespeedonline.googleapis.com/pagespeedonline/v5/runPagespeed"

    def __init__(self, api_key: str | None = None, timeout: float = 120):
        self.api_key = api_key or os.getenv("PAGESPEED_API_KEY")
        self.timeout = timeout

    def analyze(
        self,
        url: str,
        strategy: str = "mobile",
        categories: list[str] | None = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"url": url, "strategy": strategy}
        if self.api_key:
            params["key"] = self.api_key
        for category in categories or ["performance", "accessibility", "best-practices", "seo"]:
            params.setdefault("category", []).append(category)

        response = requests.get(self.ENDPOINT, params=params, timeout=self.timeout)
        response.raise_for_status()
        return response.json()
