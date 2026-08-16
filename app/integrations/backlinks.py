"""Runtime Common Crawl backlink discovery.

No Common Crawl graph data is stored in the repository. When a crawl runs,
this module can download the current Common Crawl domain graph into the
GitHub Actions runner's temporary workspace, identify incoming domain links,
and discard the temporary graph when the job finishes.

The current phase reports domain-level incoming links. Page-level source URLs,
anchor text, and rel attributes require a second Common Crawl URL-index/WARC
enrichment phase.
"""

from __future__ import annotations

import gzip
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from urllib.parse import urlparse

import requests


GRAPHINFO_URL = "https://index.commoncrawl.org/graphinfo.json"
GRAPH_BASE_URL = "https://data.commoncrawl.org/projects/hyperlinkgraph"


@dataclass
class BacklinkResult:
    provider: str
    status: str
    target_domain: str
    graph_release: str = ""
    total_backlinks: int = 0
    referring_domains: int = 0
    backlinks: list[dict[str, Any]] | None = None
    message: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "status": self.status,
            "target_domain": self.target_domain,
            "graph_release": self.graph_release,
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


def _reverse_domain(domain: str) -> str:
    return ".".join(reversed([x for x in domain.split(".") if x]))


def _latest_graph_release() -> str:
    response = requests.get(GRAPHINFO_URL, timeout=30)
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, list) or not payload:
        raise RuntimeError("Common Crawl graphinfo.json returned no releases.")
    release = str(payload[0].get("id") or "").strip()
    if not release:
        raise RuntimeError("Common Crawl graphinfo.json did not contain a release id.")
    return release


def _download(url: str, destination: Path, max_seconds: int = 3600) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists() and destination.stat().st_size > 0:
        return
    part = destination.with_suffix(destination.suffix + ".part")
    cmd = [
        "curl", "-fL", "--retry", "3", "--retry-delay", "2",
        "--connect-timeout", "30", "--max-time", str(max_seconds),
        "-o", str(part), url,
    ]
    subprocess.run(cmd, check=True)
    part.replace(destination)


def _find_domain_id(vertices_gz: Path, target: str) -> int | None:
    wanted = _reverse_domain(target)
    with gzip.open(vertices_gz, "rt", encoding="utf-8", errors="ignore") as fh:
        for line in fh:
            parts = line.rstrip("\n").split("\t")
            if len(parts) >= 2 and parts[1].strip().lower() == wanted:
                return int(parts[0])
    return None


def _map_source_ids(vertices_gz: Path, ids: set[int]) -> dict[int, str]:
    if not ids:
        return {}
    found: dict[int, str] = {}
    with gzip.open(vertices_gz, "rt", encoding="utf-8", errors="ignore") as fh:
        for line in fh:
            parts = line.rstrip("\n").split("\t")
            if len(parts) < 2:
                continue
            try:
                node_id = int(parts[0])
            except ValueError:
                continue
            if node_id in ids:
                rev = parts[1].strip()
                found[node_id] = ".".join(reversed([x for x in rev.split(".") if x]))
                if len(found) == len(ids):
                    break
    return found


def query_commoncrawl_backlinks(url: str) -> dict[str, Any]:
    domain = target_domain(url)
    if not domain:
        return BacklinkResult("Common Crawl", "invalid_target", domain, message="Target domain could not be determined.").as_dict()

    try:
        release = _latest_graph_release()
        base = f"{GRAPH_BASE_URL}/{release}/domain"
        vertices_name = f"{release}-domain-vertices.txt.gz"
        transpose_name = f"{release}-domain-t.graph"
        properties_name = f"{release}-domain-t.properties"

        with TemporaryDirectory(prefix="cc-backlinks-") as temp_dir:
            temp = Path(temp_dir)
            vertices = temp / vertices_name
            transpose = temp / transpose_name
            properties = temp / properties_name

            print(f"[COMMON CRAWL] Release: {release}")
            print(f"[COMMON CRAWL] Target domain: {domain}")
            print("[COMMON CRAWL] Downloading current domain vertex map...")
            _download(f"{base}/{vertices_name}", vertices)

            node_id = _find_domain_id(vertices, domain)
            if node_id is None:
                return BacklinkResult(
                    "Common Crawl", "not_in_graph", domain, graph_release=release,
                    message="The target domain is not present in the current Common Crawl domain graph.",
                ).as_dict()

            print(f"[COMMON CRAWL] Target node id: {node_id}")
            print("[COMMON CRAWL] Downloading current transpose graph for incoming links...")
            _download(f"{base}/{transpose_name}", transpose, max_seconds=7200)
            _download(f"{base}/{properties_name}", properties)

            try:
                import webgraph
            except ImportError as exc:
                raise RuntimeError("The webgraph package is required for Common Crawl graph queries.") from exc

            graph = webgraph.BvGraph(str(transpose.with_suffix("")))
            source_ids = {int(x) for x in graph.successors(node_id)}
            source_domains = _map_source_ids(vertices, source_ids)
            backlinks = [
                {
                    "source_domain": source_domains.get(source_id, f"node:{source_id}"),
                    "target_domain": domain,
                    "graph_level": "domain",
                    "graph_release": release,
                }
                for source_id in sorted(source_ids)
            ]

            return BacklinkResult(
                "Common Crawl",
                "success",
                domain,
                graph_release=release,
                total_backlinks=len(backlinks),
                referring_domains=len(backlinks),
                backlinks=backlinks,
                message="Domain-level incoming links discovered from the current Common Crawl Web Graph.",
            ).as_dict()

    except subprocess.CalledProcessError as exc:
        return BacklinkResult("Common Crawl", "download_failed", domain, message=f"Common Crawl graph download failed: {exc}").as_dict()
    except requests.RequestException as exc:
        return BacklinkResult("Common Crawl", "request_failed", domain, message=str(exc)).as_dict()
    except Exception as exc:
        return BacklinkResult("Common Crawl", "query_failed", domain, message=f"{type(exc).__name__}: {exc}").as_dict()


def query_runtime_backlinks(url: str) -> dict[str, Any]:
    provider = os.getenv("BACKLINK_PROVIDER", "commoncrawl").strip().lower()
    if provider not in {"commoncrawl", "common_crawl", ""}:
        return BacklinkResult(provider, "unsupported_provider", target_domain(url), message="Only Common Crawl is supported.").as_dict()
    if os.getenv("COMMON_CRAWL_BACKLINKS_ENABLED", "true").strip().lower() in {"0", "false", "no", "off"}:
        return BacklinkResult("Common Crawl", "disabled", target_domain(url), message="Common Crawl backlink lookup is disabled for this run.").as_dict()
    return query_commoncrawl_backlinks(url)
