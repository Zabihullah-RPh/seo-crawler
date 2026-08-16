"""Runtime Common Crawl domain-level backlink discovery."""

from __future__ import annotations

import gzip
import os
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests

GRAPHINFO_URL = "https://index.commoncrawl.org/graphinfo.json"
GRAPH_BASE_URL = "https://data.commoncrawl.org/projects/hyperlinkgraph"
FALLBACK_RELEASE = "cc-main-2026-may-jun-jul"


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
            "granularity": "domain",
        }


def target_domain(url: str) -> str:
    host = (urlparse(url).hostname or "").lower().strip().rstrip(".")
    if host.startswith("www."):
        host = host[4:]
    return host


def _reverse_domain(domain: str) -> str:
    return ".".join(reversed([x for x in domain.split(".") if x]))


def _latest_graph_release() -> str:
    last_error = None
    for attempt in range(1, 6):
        try:
            response = requests.get(GRAPHINFO_URL, timeout=30)
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, list) or not payload:
                raise RuntimeError("Common Crawl graphinfo.json returned no releases.")
            release = str(payload[0].get("id") or "").strip()
            if not release:
                raise RuntimeError("Common Crawl graphinfo.json did not contain a release id.")
            return release
        except Exception as exc:
            last_error = exc
            print(f"[COMMON CRAWL] graphinfo attempt {attempt}/5 failed: {exc}")
    print(f"[COMMON CRAWL] Using known latest release fallback: {FALLBACK_RELEASE}")
    return FALLBACK_RELEASE


def _download(url: str, destination: Path, max_seconds: int = 7200) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists() and destination.stat().st_size > 0:
        return
    part = destination.with_suffix(destination.suffix + ".part")
    cmd = [
        "curl", "-fL", "--retry", "5", "--retry-all-errors", "--retry-delay", "3",
        "--connect-timeout", "30", "--max-time", str(max_seconds),
        "-o", str(part), url,
    ]
    subprocess.run(cmd, check=True)
    part.replace(destination)


def _find_domain_id(vertices_gz: Path, target: str) -> int | None:
    wanted = _reverse_domain(target)
    with gzip.open(vertices_gz, "rt", encoding="utf-8", errors="ignore") as fh:
        for line in fh:
            if not line or line.startswith("#"):
                continue
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
            if not line or line.startswith("#"):
                continue
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


def _ensure_webgraph_package(temp: Path) -> Path:
    repo = temp / "cc-webgraph"
    jar = repo / "target" / "cc-webgraph-0.1-SNAPSHOT-jar-with-dependencies.jar"
    if jar.exists():
        return jar
    subprocess.run(
        ["git", "clone", "--depth", "1", "https://github.com/commoncrawl/cc-webgraph.git", str(repo)],
        check=True,
    )
    subprocess.run(["mvn", "-q", "-DskipTests", "package"], cwd=repo, check=True)
    if not jar.exists():
        raise RuntimeError("cc-webgraph build did not produce the assembly JAR")
    return jar


def _ensure_offsets(graph_base: Path, jar: Path) -> None:
    offsets = Path(str(graph_base) + ".offsets")
    if offsets.exists() and offsets.stat().st_size > 0:
        return
    subprocess.run(
        ["java", "-cp", str(jar), "it.unimi.dsi.webgraph.BVGraph", "-O", "-L", str(graph_base)],
        check=True,
    )


def _incoming_node_ids(graph_base: Path, node_id: int, jar: Path) -> list[int]:
    helper = graph_base.parent / "GraphLookup.java"
    helper.write_text(
        """
import it.unimi.dsi.webgraph.ImmutableGraph;
import it.unimi.dsi.webgraph.LazyIntIterator;

public class GraphLookup {
  public static void main(String[] args) throws Exception {
    ImmutableGraph g = ImmutableGraph.load(args[0]);
    int node = Integer.parseInt(args[1]);
    LazyIntIterator it = g.successors(node);
    int degree = g.outdegree(node);
    for (int i = 0; i < degree; i++) System.out.println(it.nextInt());
  }
}
""",
        encoding="utf-8",
    )
    subprocess.run(["javac", "-cp", str(jar), str(helper)], check=True)
    proc = subprocess.run(
        ["java", "-Xmx5g", "-cp", f"{jar}:{helper.parent}", "GraphLookup", str(graph_base), str(node_id)],
        check=True,
        capture_output=True,
        text=True,
    )
    result: list[int] = []
    for line in proc.stdout.splitlines():
        line = line.strip()
        if line.isdigit():
            result.append(int(line))
    return result


def query_commoncrawl_backlinks(url: str) -> dict[str, Any]:
    domain = target_domain(url)
    if not domain:
        return BacklinkResult("Common Crawl", "invalid_target", domain, message="Target domain could not be determined.").as_dict()

    release = _latest_graph_release()
    base = f"{GRAPH_BASE_URL}/{release}/domain"
    vertices_name = f"{release}-domain-vertices.txt.gz"
    transpose_name = f"{release}-domain-t.graph"
    properties_name = f"{release}-domain-t.properties"

    try:
        with tempfile.TemporaryDirectory(prefix="cc-backlinks-") as temp_dir:
            temp = Path(temp_dir)
            vertices = temp / vertices_name
            transpose = temp / transpose_name
            properties = temp / properties_name

            print(f"[COMMON CRAWL] Release: {release}")
            print(f"[COMMON CRAWL] Target domain: {domain}")
            print("[COMMON CRAWL] Fetching domain vertex map...")
            _download(f"{base}/{vertices_name}", vertices, max_seconds=3600)

            node_id = _find_domain_id(vertices, domain)
            if node_id is None:
                return BacklinkResult(
                    "Common Crawl", "not_in_graph", domain, graph_release=release,
                    message="The target domain is not present in the current Common Crawl domain graph.",
                ).as_dict()

            print(f"[COMMON CRAWL] Target node id: {node_id}")
            print("[COMMON CRAWL] Fetching transpose graph for incoming domain links...")
            _download(f"{base}/{transpose_name}", transpose)
            _download(f"{base}/{properties_name}", properties, max_seconds=300)

            jar = _ensure_webgraph_package(temp)
            graph_base = transpose.with_suffix("")
            _ensure_offsets(graph_base, jar)
            source_ids = set(_incoming_node_ids(graph_base, node_id, jar))
            source_domains = _map_source_ids(vertices, source_ids)

            backlinks = []
            for source_id in sorted(source_ids):
                source_domain = source_domains.get(source_id)
                if not source_domain:
                    continue
                backlinks.append({
                    "source_url": f"https://{source_domain}/",
                    "target_url": f"https://{domain}/",
                    "anchor_text": "",
                    "rel": "",
                    "referring_domain": source_domain,
                    "graph_level": "domain",
                    "graph_release": release,
                })

            return BacklinkResult(
                "Common Crawl",
                "success",
                domain,
                graph_release=release,
                total_backlinks=len(backlinks),
                referring_domains=len({x["referring_domain"] for x in backlinks}),
                backlinks=backlinks,
                message=(
                    "Common Crawl domain-level incoming links collected successfully. "
                    "Anchor text, rel attributes, and source page URLs require the separate page-level WARC enrichment phase."
                ),
            ).as_dict()

    except requests.RequestException as exc:
        return BacklinkResult("Common Crawl", "request_failed", domain, graph_release=release, message=f"Common Crawl request failed: {exc}").as_dict()
    except subprocess.CalledProcessError as exc:
        return BacklinkResult("Common Crawl", "processing_failed", domain, graph_release=release, message=f"Common Crawl graph processing failed: {exc}").as_dict()
    except Exception as exc:
        return BacklinkResult("Common Crawl", "collector_failed", domain, graph_release=release, message=f"{type(exc).__name__}: {exc}").as_dict()


def query_runtime_backlinks(url: str) -> dict[str, Any]:
    provider = os.getenv("BACKLINK_PROVIDER", "commoncrawl").strip().lower()
    enabled = os.getenv("COMMON_CRAWL_BACKLINKS_ENABLED", "true").strip().lower() not in {"0", "false", "no", "off"}
    if provider not in {"", "commoncrawl", "common_crawl"}:
        return BacklinkResult(provider, "unsupported_provider", target_domain(url), message="Only Common Crawl is supported.").as_dict()
    if not enabled:
        return BacklinkResult("Common Crawl", "disabled", target_domain(url), message="Common Crawl backlink lookup is disabled for this run.").as_dict()
    return query_commoncrawl_backlinks(url)
