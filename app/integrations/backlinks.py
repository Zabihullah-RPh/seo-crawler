"""Universal Common Crawl-only runtime backlink integration."""

from __future__ import annotations

import os
from typing import Any

from app.integrations.common_crawl_backlinks import prepare_runtime_lookup, target_domain


def query_runtime_backlinks(url: str) -> dict[str, Any]:
    """Prepare a Common Crawl lookup for the concrete target domain.

    No Common Crawl data is downloaded unless the runtime collector is enabled
    in a later phase. This keeps the repository data-free and universal.
    """
    domain = target_domain(url)
    if not domain:
        return {
            "provider": "Common Crawl",
            "status": "invalid_target",
            "target_domain": "",
            "total_backlinks": 0,
            "referring_domains": 0,
            "backlinks": [],
            "message": "Target domain could not be determined.",
        }

    enabled = os.getenv("COMMON_CRAWL_BACKLINKS_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"}
    plan = prepare_runtime_lookup(url)

    if not enabled:
        return {
            "provider": "Common Crawl",
            "status": "ready",
            "target_domain": domain,
            "total_backlinks": 0,
            "referring_domains": 0,
            "backlinks": [],
            "message": "Common Crawl backlink discovery is ready for this domain. Data collection is disabled during system preparation.",
            "lookup": plan["lookup"],
        }

    # The actual graph/WARC retrieval is deliberately isolated from the
    # preparation phase and will be enabled in the runtime collector phase.
    return {
        "provider": "Common Crawl",
        "status": "collector_not_enabled",
        "target_domain": domain,
        "total_backlinks": 0,
        "referring_domains": 0,
        "backlinks": [],
        "message": "Common Crawl runtime collection is the only backlink provider. The graph query collector is not enabled yet.",
        "lookup": plan["lookup"],
    }
