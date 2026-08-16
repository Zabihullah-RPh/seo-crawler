"""Runtime backlink discovery providers.

The crawler is intentionally provider-agnostic. A provider is invoked only
when a crawl is running for a concrete target URL; no backlink dataset is
preloaded into the repository or downloaded ahead of time.
"""

from __future__ import annotations

from dataclasses import dataclass
import os
from typing import Any
from urllib.parse import urlparse

import requests


@dataclass
class BacklinkResult:
    provider: str
    status: str
    target_domain: str
    total_backlinks: int = 0
    referring_domains: int = 0
    backlinks: list[dict[str, Any]] | None = None
    message: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "status": self.status,
            "target_domain": self.target_domain,
            "total_backlinks": self.total_backlinks,
            "referring_domains": self.referring_domains,
            "backlinks": self.backlinks or [],
            "message": self.message,
        }


def target_domain(url: str) -> str:
    host = (urlparse(url).hostname or "").lower().strip().rstrip(".")
    if host.startswith("www."):
        host = host[4:]
    return host


def _normalize_item(item: Any) -> dict[str, Any]:
    if not isinstance(item, dict):
        return {"source_url": str(item)}
    return {
        "source_url": item.get("source_url") or item.get("source") or item.get("url") or "",
        "target_url": item.get("target_url") or item.get("target") or "",
        "anchor_text": item.get("anchor_text") or item.get("anchor") or "",
        "rel": item.get("rel") or "",
        "status_code": item.get("status_code") or item.get("status"),
        "first_seen": item.get("first_seen") or item.get("timestamp") or item.get("crawl_date"),
        "referring_domain": item.get("referring_domain") or item.get("domain") or "",
    }


def query_runtime_backlinks(url: str) -> dict[str, Any]:
    """Query a configured Common-Crawl-backed backlink API for one target.

    The repository deliberately does not bundle or download a Common Crawl
    graph. Instead, the provider is selected at runtime through environment
    variables so each audit processes only its requested domain.

    Supported configuration:
      BACKLINK_PROVIDER=commoncrawl_api
      BACKLINK_API_URL=https://.../backlinks
      BACKLINK_API_KEY=...
      BACKLINK_LIMIT=1000
    """
    domain = target_domain(url)
    provider = os.getenv("BACKLINK_PROVIDER", "none").strip().lower()
    if not domain:
        return BacklinkResult("none", "invalid_target", domain, message="Target domain could not be determined.").as_dict()

    if provider in {"", "none", "off", "disabled"}:
        return BacklinkResult(
            "none",
            "not_configured",
            domain,
            message="No runtime backlink provider is configured.",
        ).as_dict()

    if provider != "commoncrawl_api":
        return BacklinkResult(provider, "unsupported_provider", domain, message=f"Unsupported backlink provider: {provider}").as_dict()

    endpoint = os.getenv("BACKLINK_API_URL", "").strip()
    api_key = os.getenv("BACKLINK_API_KEY", "").strip()
    try:
        limit = max(1, min(int(os.getenv("BACKLINK_LIMIT", "1000")), 10000))
    except ValueError:
        limit = 1000

    if not endpoint:
        return BacklinkResult(
            "commoncrawl_api",
            "not_configured",
            domain,
            message="BACKLINK_API_URL is not configured.",
        ).as_dict()

    headers = {"Accept": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
        headers["X-API-Key"] = api_key

    try:
        response = requests.post(
            endpoint,
            json={"domain": domain, "target": domain, "limit": limit},
            headers=headers,
            timeout=60,
        )
        response.raise_for_status()
        payload = response.json()
    except requests.RequestException as exc:
        return BacklinkResult("commoncrawl_api", "request_failed", domain, message=str(exc)).as_dict()
    except ValueError as exc:
        return BacklinkResult("commoncrawl_api", "invalid_response", domain, message=str(exc)).as_dict()

    raw = payload.get("backlinks") or payload.get("results") or []
    if isinstance(raw, dict):
        raw = raw.get("results") or raw.get("backlinks") or []
    items = [_normalize_item(x) for x in raw]

    total_backlinks = int(payload.get("total_backlinks") or payload.get("total") or len(items) or 0)
    referring_domains = int(payload.get("referring_domains") or payload.get("total_linking_domains") or len({x.get("referring_domain") for x in items if x.get("referring_domain")}))

    return BacklinkResult(
        "commoncrawl_api",
        "success",
        domain,
        total_backlinks=total_backlinks,
        referring_domains=referring_domains,
        backlinks=items,
        message="Runtime backlink discovery completed.",
    ).as_dict()
