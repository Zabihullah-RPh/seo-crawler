"""Runtime Common Crawl domain-level backlink collector.

This collector downloads the current Common Crawl domain transpose graph only
when an audit explicitly requests backlink collection. It is temporary runtime
work data; nothing is stored in the repository.

Common Crawl's published Web Graph contains domain-to-domain relationships.
That means this module can return referring domains and domain-level backlink
edges, but not page-level anchor text or rel attributes.
"""

from __future__ import annotations

import gzip
import json
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable
from urllib.parse import urlparse

import requests

GRAPHINFO_URL = "https://index.commoncrawl.org/graphinfo.json"
DATA_BASE = "https://data.commoncrawl.org/projects/hyperlinkgraph"
FALLBACK_RELEASE = "cc-main-2026-may-jun-jul"


@dataclass(frozen=True)
class CollectorResult:
    provider: str
    status: str
    target_domain: str
    release: str = ""
    total_backlinks: int = 0
    referring_domains: int = 0
    backlinks: list[dict] | None = None
    message: str = ""

    def as_dict(self) -> dict:
        return {
            "provider": self.provider,
            "status": self.status,
            "target_domain": self.target_domain,
            "release": self.release,
            "total_backlinks": self.total_backlinks,
            "referring_domains": self.referring_domains,
            "backlinks": self.backlinks or [],
            "message": self.message,
            "granularity": "domain",
        }


def _target_domain(url: str) -> str:
    host = (urlparse((url or "").strip()).hostname or "").lower().rstrip(".")
    if host.startswith("www."):
        host = host[4:]
    return host


def _reverse_domain(domain: str) -> str:
    return ".".join(reversed([x for x in domain.split(".") if x]))


def _get_release() -> str:
    try:
        response = requests.get(GRAPHINFO_URL, timeout=20)
        response.raise_for_status()
        payload = response.json()
        if isinstance(payload, list) and payload:
            release = payload[0].get("id")
            if release:
                return str(release)
    except Exception:
        pass
    return FALLBACK_RELEASE


def _download(url: str, destination: Path, label: str) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with requests.get(url, stream=True, timeout=120) as response:
        response.raise_for_status()
        total = int(response.headers.get("content-length") or 0)
        received = 0
        with destination.open("wb") as output:
            for chunk in response.iter_content(chunk_size=1024 * 1024):
                if not chunk:
                    continue
                output.write(chunk)
                received += len(chunk)
                if total and received % (100 * 1024 * 1024) < len(chunk):
                    print(f"[COMMON CRAWL] {label}: {received / 1024 / 1024:.0f} MiB / {total / 1024 / 1024:.0f} MiB")
    print(f"[COMMON CRAWL] downloaded {label}: {destination}")


def _find_target_node(vertices_gz: Path, reverse_domain: str) -> int | None:
    with gzip.open(vertices_gz, "rt", encoding="utf-8", errors="ignore") as stream:
        for line in stream:
            if not line or line.startswith("#"):
                continue
            parts = line.rstrip("\n").split("\t")
            if len(parts) >= 2 and parts[1] == reverse_domain:
                try:
                    return int(parts[0])
                except ValueError:
                    return None
    return None


def _build_offsets(graph_base: Path, webgraph_repo: Path) -> Path:
    jar = webgraph_repo / "target" / "cc-webgraph-0.1-SNAPSHOT-jar-with-dependencies.jar"
    if not jar.exists():
        subprocess.run(["mvn", "-q", "-DskipTests", "package"], cwd=webgraph_repo, check=True)
    subprocess.run(
        ["java", "-cp", str(jar), "it.unimi.dsi.webgraph.BVGraph", "-O", "-L", str(graph_base)],
        check=True,
    )
    return jar


def _read_incoming_nodes(graph_base: Path, node_id: int, jar: Path) -> list[int]:
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
        ["java", "-Xmx6g", "-cp", f"{jar}:{helper.parent}", "GraphLookup", str(graph_base), str(node_id)],
        check=True,
        text=True,
        capture_output=True,
    )
    nodes: list[int] = []
    for value in proc.stdout.splitlines():
        try:
            nodes.append(int(value.strip()))
        except ValueError:
            pass
    return nodes


def _node_names(vertices_gz: Path, ids: Iterable[int]) -> dict[int, str]:
    wanted = set(ids)
    found: dict[int, str] = {}
    if not wanted:
        return found
    with gzip.open(vertices_gz, "rt", encoding="utf-8", errors="ignore") as stream:
        for line in stream:
            parts = line.rstrip("\n").split("\t")
            if len(parts) < 2:
                continue
            try:
                node_id = int(parts[0])
            except ValueError:
                continue
            if node_id in wanted:
                found[node_id] = parts[1]
                if len(found) == len(wanted):
                    break
    return found


def collect_domain_backlinks(url: str, max_backlinks: int = 10000) -> dict:
    domain = _target_domain(url)
    if not domain:
        return CollectorResult("Common Crawl", "invalid_target", "", message="Target domain could not be determined.").as_dict()

    release = _get_release()
    reverse = _reverse_domain(domain)

    # Runtime work directory only. It is deleted when the collector exits.
    with tempfile.TemporaryDirectory(prefix="cc-backlinks-") as temp_dir:
        work = Path(temp_dir)
        try:
            print(f"[COMMON CRAWL] release={release} target={domain}")
            vertices = work / f"{release}-domain-vertices.txt.gz"
            graph = work / f"{release}-domain-t.graph"
            props = work / f"{release}-domain-t.properties"

            base_url = f"{DATA_BASE}/{release}/domain"
            _download(f"{base_url}/{vertices.name}", vertices, "vertices")
            _download(f"{base_url}/{graph.name}", graph, "transpose graph")
            _download(f"{base_url}/{props.name}", props, "graph properties")

            node_id = _find_target_node(vertices, reverse)
            if node_id is None:
                return CollectorResult("Common Crawl", "not_in_graph", domain, release=release, message="Target domain is not present in the current Common Crawl domain graph.").as_dict()

            repo = work / "cc-webgraph"
            subprocess.run(["git", "clone", "--depth", "1", "https://github.com/commoncrawl/cc-webgraph.git", str(repo)], check=True, stdout=subprocess.DEVNULL)
            jar = _build_offsets(graph.with_suffix(""), repo)
            incoming_ids = _read_incoming_nodes(graph.with_suffix(""), node_id, jar)
            incoming_ids = incoming_ids[: max(1, min(int(max_backlinks), 10000))]
            names = _node_names(vertices, incoming_ids)

            rows = []
            for incoming_id in incoming_ids:
                reverse_name = names.get(incoming_id)
                if not reverse_name:
                    continue
                source_domain = ".".join(reversed(reverse_name.split(".")))
                rows.append(
                    {
                        "source_url": f"https://{source_domain}/",
                        "target_url": f"https://{domain}/",
                        "anchor_text": "",
                        "rel": "",
                        "referring_domain": source_domain,
                        "source_node_id": incoming_id,
                    }
                )

            return CollectorResult(
                "Common Crawl",
                "success",
                domain,
                release=release,
                total_backlinks=len(rows),
                referring_domains=len({r["referring_domain"] for r in rows}),
                backlinks=rows,
                message="Common Crawl domain-level backlink discovery completed. Source page, anchor text, and rel attributes are not included in the domain graph.",
            ).as_dict()
        except subprocess.CalledProcessError as exc:
            return CollectorResult("Common Crawl", "collector_failed", domain, release=release, message=f"Graph processing failed: {exc}").as_dict()
        except requests.RequestException as exc:
            return CollectorResult("Common Crawl", "request_failed", domain, release=release, message=f"Common Crawl request failed: {exc}").as_dict()
        except Exception as exc:
            return CollectorResult("Common Crawl", "collector_failed", domain, release=release, message=str(exc)).as_dict()
