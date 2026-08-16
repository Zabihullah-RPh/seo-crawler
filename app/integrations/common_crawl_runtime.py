"""Isolated Common Crawl backlink collector.

The collector is deliberately independent from the SEO audit engine. It runs only
for the domain supplied at crawl time and writes a separate backlink JSON file.
It does not modify the existing crawl/audit data structures.
"""
from __future__ import annotations

import gzip
import os
import subprocess
import tempfile
from pathlib import Path
from urllib.parse import urlparse

import requests

GRAPHINFO_URL = "https://index.commoncrawl.org/graphinfo.json"
GRAPH_BASE = "https://data.commoncrawl.org/projects/hyperlinkgraph"
FALLBACK_RELEASE = "cc-main-2026-may-jun-jul"


def target_domain(url: str) -> str:
    host = (urlparse(url).hostname or "").lower().strip().rstrip(".")
    return host[4:] if host.startswith("www.") else host


def reverse_domain(domain: str) -> str:
    return ".".join(reversed([p for p in domain.split(".") if p]))


def latest_release() -> str:
    try:
        r = requests.get(GRAPHINFO_URL, timeout=30)
        r.raise_for_status()
        payload = r.json()
        if isinstance(payload, list) and payload:
            release = str(payload[0].get("id") or "").strip()
            if release:
                return release
    except Exception as exc:
        print(f"[COMMON CRAWL] graphinfo unavailable: {exc}")
    return FALLBACK_RELEASE


def download(url: str, destination: Path, timeout: int = 7200) -> None:
    part = destination.with_suffix(destination.suffix + ".part")
    if destination.exists() and destination.stat().st_size > 0:
        return
    cmd = [
        "curl", "-fL", "--retry", "8", "--retry-all-errors", "--retry-delay", "5",
        "--connect-timeout", "30", "--max-time", str(timeout), "-o", str(part), url,
    ]
    subprocess.run(cmd, check=True)
    part.replace(destination)


def find_domain_id(vertices: Path, domain: str) -> int | None:
    wanted = reverse_domain(domain)
    with gzip.open(vertices, "rt", encoding="utf-8", errors="ignore") as fh:
        for line in fh:
            if not line or line.startswith("#"):
                continue
            parts = line.rstrip("\n").split("\t")
            if len(parts) >= 2 and parts[1].strip().lower() == wanted:
                return int(parts[0])
    return None


def map_domain_ids(vertices: Path, ids: set[int]) -> dict[int, str]:
    found: dict[int, str] = {}
    if not ids:
        return found
    with gzip.open(vertices, "rt", encoding="utf-8", errors="ignore") as fh:
        for line in fh:
            if not line or line.startswith("#"):
                continue
            parts = line.rstrip("\n").split("\t")
            if len(parts) < 2:
                continue
            try:
                node = int(parts[0])
            except ValueError:
                continue
            if node in ids:
                rev = parts[1].strip()
                found[node] = ".".join(reversed([p for p in rev.split(".") if p]))
                if len(found) == len(ids):
                    break
    return found


def build_webgraph_jar(temp: Path) -> Path:
    repo = temp / "cc-webgraph"
    jar = repo / "target" / "cc-webgraph-0.1-SNAPSHOT-jar-with-dependencies.jar"
    if jar.exists():
        return jar
    subprocess.run(["git", "clone", "--depth", "1", "https://github.com/commoncrawl/cc-webgraph.git", str(repo)], check=True)
    subprocess.run(["mvn", "-q", "-DskipTests", "package"], cwd=repo, check=True)
    return jar


def incoming_ids(graph_base: Path, node_id: int, jar: Path, temp: Path) -> set[int]:
    # Common Crawl publishes a transpose graph; its successors(node) are the incoming nodes.
    helper = temp / "BacklinkLookup.java"
    helper.write_text(
        """
import it.unimi.dsi.webgraph.ImmutableGraph;
import it.unimi.dsi.webgraph.LazyIntIterator;
public class BacklinkLookup {
  public static void main(String[] args) throws Exception {
    ImmutableGraph graph = ImmutableGraph.loadMapped(args[0]);
    int node = Integer.parseInt(args[1]);
    LazyIntIterator it = graph.successors(node);
    int n = graph.outdegree(node);
    for (int i = 0; i < n; i++) System.out.println(it.nextInt());
  }
}
""",
        encoding="utf-8",
    )
    subprocess.run(["javac", "-cp", str(jar), str(helper)], check=True)
    cp = f"{jar}{os.pathsep}{temp}"
    result = subprocess.run(
        ["java", "-Xmx6g", "-cp", cp, "BacklinkLookup", str(graph_base), str(node_id)],
        capture_output=True, text=True, check=True,
    )
    return {int(x.strip()) for x in result.stdout.splitlines() if x.strip().isdigit()}


def collect(url: str) -> dict:
    domain = target_domain(url)
    if not domain:
        return {"provider": "Common Crawl", "status": "invalid_target", "target_domain": domain, "backlinks": []}

    release = latest_release()
    base = f"{GRAPH_BASE}/{release}/domain"
    vertices_name = f"{release}-domain-vertices.txt.gz"
    graph_name = f"{release}-domain-t.graph"
    properties_name = f"{release}-domain-t.properties"

    try:
        with tempfile.TemporaryDirectory(prefix="cc-runtime-") as work:
            temp = Path(work)
            vertices = temp / vertices_name
            graph = temp / graph_name
            properties = temp / properties_name
            download(f"{base}/{vertices_name}", vertices, timeout=3600)
            node_id = find_domain_id(vertices, domain)
            if node_id is None:
                return {"provider": "Common Crawl", "status": "not_in_graph", "target_domain": domain, "graph_release": release, "total_backlinks": 0, "referring_domains": 0, "backlinks": []}
            download(f"{base}/{graph_name}", graph, timeout=10800)
            download(f"{base}/{properties_name}", properties, timeout=300)
            jar = build_webgraph_jar(temp)
            ids = incoming_ids(graph.with_suffix(""), node_id, jar, temp)
            domains = map_domain_ids(vertices, ids)
            links = [
                {"source_url": f"https://{domains[i]}/", "target_url": f"https://{domain}/", "anchor_text": "", "rel": "", "referring_domain": domains[i], "graph_level": "domain", "graph_release": release}
                for i in sorted(ids) if i in domains
            ]
            return {"provider": "Common Crawl", "status": "success", "target_domain": domain, "graph_release": release, "total_backlinks": len(links), "referring_domains": len(domains), "backlinks": links, "message": "Domain-level incoming links discovered from Common Crawl. Page-level URL, anchor and rel enrichment is separate."}
    except requests.RequestException as exc:
        return {"provider": "Common Crawl", "status": "request_failed", "target_domain": domain, "graph_release": release, "total_backlinks": 0, "referring_domains": 0, "backlinks": [], "message": str(exc)}
    except subprocess.CalledProcessError as exc:
        return {"provider": "Common Crawl", "status": "processing_failed", "target_domain": domain, "graph_release": release, "total_backlinks": 0, "referring_domains": 0, "backlinks": [], "message": str(exc)}
    except Exception as exc:
        return {"provider": "Common Crawl", "status": "failed", "target_domain": domain, "graph_release": release, "total_backlinks": 0, "referring_domains": 0, "backlinks": [], "message": f"{type(exc).__name__}: {exc}"}
