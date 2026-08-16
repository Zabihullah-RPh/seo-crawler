"""Universal Common Crawl-only backlink runtime integration.

This module prepares a backlink lookup for the domain supplied at crawl time.
Preparation is network-free by default: no Common Crawl graph or page data is
bundled in the repository and no Common Crawl payload is downloaded until the
runtime backlink collector is explicitly enabled.
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlparse


COMMON_CRAWL_GRAPHINFO_URL = "https://index.commoncrawl.org/graphinfo.json"
COMMON_CRAWL_DATA_BASE = "https://data.commoncrawl.org/projects/hyperlinkgraph"


@dataclass(frozen=True)
class BacklinkLookupPlan:
    target_domain: str
    reverse_domain: str
    graph_level: str = "domain"
    graphinfo_url: str = COMMON_CRAWL_GRAPHINFO_URL
    data_base: str = COMMON_CRAWL_DATA_BASE

    def as_dict(self) -> dict:
        return {
            "target_domain": self.target_domain,
            "reverse_domain": self.reverse_domain,
            "graph_level": self.graph_level,
            "graphinfo_url": self.graphinfo_url,
            "data_base": self.data_base,
        }


def target_domain(url: str) -> str:
    value = (url or "").strip()
    host = (urlparse(value).hostname or value.split("/", 1)[0]).lower().strip().rstrip(".")
    if host.startswith("www."):
        host = host[4:]
    return host


def reverse_domain(domain: str) -> str:
    labels = [part for part in (domain or "").split(".") if part]
    return ".".join(reversed(labels))


def build_lookup_plan(url: str) -> BacklinkLookupPlan:
    domain = target_domain(url)
    if not domain or "." not in domain:
        raise ValueError("A valid target domain is required")
    return BacklinkLookupPlan(
        target_domain=domain,
        reverse_domain=reverse_domain(domain),
    )


def prepare_runtime_lookup(url: str) -> dict:
    """Return the universal Common Crawl plan without network I/O."""
    plan = build_lookup_plan(url)
    return {
        "provider": "Common Crawl",
        "status": "ready",
        "mode": "runtime",
        "data_downloaded": False,
        "message": "Common Crawl backlink discovery is ready for this domain; no Common Crawl data was downloaded during preparation.",
        "lookup": plan.as_dict(),
    }
