"""Common Crawl backlink discovery integration.

This module adds the discovery layer without downloading Common Crawl graph
or WARC data. It prepares the target-domain representation and a deterministic
plan for a future Web Graph lookup.

Common Crawl's current Web Graph releases provide domain/host nodes and edges.
The graph is not an HTTP API, so discovery is intentionally separated from the
later retrieval phase.
"""

from dataclasses import dataclass
from urllib.parse import urlparse


DEFAULT_GRAPH_RELEASE = "cc-main-2026-may-jun-jul"
DEFAULT_GRAPH_LEVEL = "domain"
DEFAULT_GRAPH_BASE = "https://data.commoncrawl.org/projects/hyperlinkgraph"


@dataclass(frozen=True)
class BacklinkDiscoveryConfig:
    graph_release: str = DEFAULT_GRAPH_RELEASE
    graph_level: str = DEFAULT_GRAPH_LEVEL
    graph_base: str = DEFAULT_GRAPH_BASE
    enabled: bool = True


@dataclass(frozen=True)
class BacklinkDiscoveryPlan:
    target_url: str
    target_domain: str
    reverse_domain: str
    graph_release: str
    graph_level: str
    graph_base: str
    mode: str = "planned"
    data_download_enabled: bool = False


def normalize_target_domain(value: str) -> str:
    if not value or not str(value).strip():
        raise ValueError("target URL/domain is required")

    raw = str(value).strip()
    parsed = urlparse(raw if "://" in raw else f"https://{raw}")
    hostname = (parsed.hostname or "").strip().lower().rstrip(".")
    if not hostname:
        raise ValueError("target URL/domain has no hostname")
    return hostname


def to_reverse_domain(hostname: str) -> str:
    hostname = normalize_target_domain(hostname)
    return ".".join(reversed(hostname.split(".")))


def build_discovery_plan(
    target: str,
    config: BacklinkDiscoveryConfig | None = None,
) -> BacklinkDiscoveryPlan:
    cfg = config or BacklinkDiscoveryConfig()
    if not cfg.enabled:
        raise RuntimeError("Common Crawl backlink discovery is disabled")
    if cfg.graph_level not in {"domain", "host"}:
        raise ValueError("graph_level must be 'domain' or 'host'")

    target_domain = normalize_target_domain(target)
    return BacklinkDiscoveryPlan(
        target_url=target,
        target_domain=target_domain,
        reverse_domain=to_reverse_domain(target_domain),
        graph_release=cfg.graph_release,
        graph_level=cfg.graph_level,
        graph_base=cfg.graph_base.rstrip("/"),
    )


def discovery_info() -> dict:
    """Return integration metadata without making any network request."""
    return {
        "provider": "Common Crawl Web Graph",
        "graph_release": DEFAULT_GRAPH_RELEASE,
        "graph_level": DEFAULT_GRAPH_LEVEL,
        "phase": "discovery-planning",
        "data_download_enabled": False,
        "network_requests_enabled": False,
    }
