"""Aggregate Common Crawl domain-graph backlinks across all locally installed releases.

The Common Crawl Web Graph is a domain-level reverse-edge source. This module
keeps the existing single-release collector intact and adds a multi-release
union over graph files that are already present locally. No large graph data
is downloaded at audit time.
"""
from __future__ import annotations

from collections import defaultdict

from app.integrations.common_crawl_runtime import (
    available_local_releases,
    ensure_runtime_jars,
    find_domain_id,
    graph_files,
    incoming_ids,
    local_graph_dir,
    map_domain_ids,
    target_domain,
)


def collect_all_local_releases(url: str) -> dict:
    """Collect referring domains from every complete local graph release."""
    domain = target_domain(url)
    base = {
        "provider": "Common Crawl",
        "target_domain": domain,
        "graph_level": "domain",
        "scope": "all_local_releases",
    }

    if not domain:
        return {
            **base,
            "status": "invalid_target",
            "releases_checked": [],
            "total_backlinks": 0,
            "referring_domains": 0,
            "backlinks": [],
        }

    root = local_graph_dir()
    if root is None:
        return {
            **base,
            "status": "not_configured",
            "releases_checked": [],
            "total_backlinks": 0,
            "referring_domains": 0,
            "backlinks": [],
            "message": "Local Common Crawl graph directory was not found.",
        }

    releases = available_local_releases(root)
    if not releases:
        return {
            **base,
            "status": "graph_missing",
            "releases_checked": [],
            "total_backlinks": 0,
            "referring_domains": 0,
            "backlinks": [],
            "message": f"No complete local Common Crawl domain graph releases were found in {root}.",
        }

    jar_dir = ensure_runtime_jars()
    by_domain: dict[str, dict] = {}
    release_stats: list[dict] = []
    errors: list[dict] = []

    for release in releases:
        files = graph_files(root, release)
        if files is None:
            continue

        graph, _properties, vertices = files
        stat = {
            "graph_release": release,
            "status": "ok",
            "target_node_id": None,
            "incoming_node_ids": 0,
            "resolved_referring_domains": 0,
        }

        try:
            node_id = find_domain_id(vertices, domain)
            stat["target_node_id"] = node_id

            if node_id is None:
                stat["status"] = "target_not_in_graph"
                release_stats.append(stat)
                continue

            incoming = incoming_ids(graph, node_id, jar_dir)
            stat["incoming_node_ids"] = len(incoming)
            domains = map_domain_ids(vertices, incoming)
            stat["resolved_referring_domains"] = len(domains)

            for node, source_domain in domains.items():
                current = by_domain.get(source_domain)
                if current is None:
                    by_domain[source_domain] = {
                        "source_url": f"https://{source_domain}/",
                        "target_url": f"https://{domain}/",
                        "anchor_text": "",
                        "rel": "",
                        "referring_domain": source_domain,
                        "graph_level": "domain",
                        "graph_releases": [release],
                    }
                elif release not in current["graph_releases"]:
                    current["graph_releases"].append(release)

        except Exception as exc:
            stat["status"] = "error"
            stat["error"] = f"{type(exc).__name__}: {exc}"
            errors.append(stat)

        release_stats.append(stat)

    backlinks = sorted(
        by_domain.values(),
        key=lambda item: item["referring_domain"],
    )

    for item in backlinks:
        item["graph_releases"] = sorted(item["graph_releases"], reverse=True)

    status = "success" if backlinks else "not_found"

    return {
        **base,
        "status": status,
        "releases_checked": releases,
        "releases_with_errors": len(errors),
        "release_stats": release_stats,
        "total_backlinks": len(backlinks),
        "referring_domains": len(backlinks),
        "backlinks": backlinks,
        "message": (
            "Domain-level referring domains aggregated across every complete "
            "Common Crawl Web Graph release installed locally. Exact page, "
            "anchor, and rel details still require Layer 2 verification."
        ),
    }
