"""Local Common Crawl Layer 1 backlink discovery.

Uses a locally stored Common Crawl domain transpose graph. No Common Crawl
graph data is downloaded at audit time; only small runtime libraries are
cached locally when missing.
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
FALLBACK_RELEASE = "cc-main-2026-may-jun-jul"
MAVEN_BASE = "https://repo1.maven.org/maven2"
WEBGRAPH_DEPS = {
    "webgraph-3.6.12.jar": "it/unimi/dsi/webgraph/3.6.12/webgraph-3.6.12.jar",
    "fastutil-8.5.18.jar": "it/unimi/dsi/fastutil/8.5.18/fastutil-8.5.18.jar",
    "dsiutils-2.7.4.jar": "it/unimi/dsi/dsiutils/2.7.4/dsiutils-2.7.4.jar",
    "sux4j-5.4.1.jar": "it/unimi/dsi/sux4j/5.4.1/sux4j-5.4.1.jar",
    "jsap-20210129.jar": "it/unimi/di/jsap/20210129/jsap-20210129.jar",
    "slf4j-api-2.0.18.jar": "org/slf4j/slf4j-api/2.0.18/slf4j-api-2.0.18.jar",
    "slf4j-simple-2.0.18.jar": "org/slf4j/slf4j-simple/2.0.18/slf4j-simple-2.0.18.jar",
    "commons-math3-3.6.1.jar": "org/apache/commons/commons-math3/3.6.1/commons-math3-3.6.1.jar",
}


def target_domain(url: str) -> str:
    host = (urlparse(url).hostname or "").lower().strip().rstrip(".")
    return host[4:] if host.startswith("www.") else host


def reverse_domain(domain: str) -> str:
    return ".".join(reversed([p for p in domain.split(".") if p]))


def local_graph_dir() -> Path | None:
    configured = os.environ.get("COMMON_CRAWL_GRAPH_DIR", "").strip()
    candidates = [Path(configured).expanduser()] if configured else []
    if os.name == "nt":
        candidates.append(Path(r"C:\commoncrawl"))
    candidates.append(Path.cwd() / "commoncrawl")
    return next((p for p in candidates if p.exists() and p.is_dir()), None)


def available_local_releases(root: Path) -> list[str]:
    return sorted(
        [
            graph.name.removesuffix("-domain-t.graph")
            for graph in root.glob("*-domain-t.graph")
            if (root / f"{graph.name.removesuffix('-domain-t.graph')}-domain-t.properties").exists()
            and (root / f"{graph.name.removesuffix('-domain-t.graph')}-domain-vertices.txt.gz").exists()
        ],
        reverse=True,
    )


def latest_release(root: Path | None = None) -> str:
    if root is not None:
        local = available_local_releases(root)
        if local:
            return local[0]
    try:
        response = requests.get(GRAPHINFO_URL, timeout=20)
        response.raise_for_status()
        payload = response.json()
        if isinstance(payload, list) and payload:
            release = str(payload[0].get("id") or "").strip()
            if release:
                return release
    except Exception as exc:
        print(f"[COMMON CRAWL] graphinfo unavailable: {exc}")
    return FALLBACK_RELEASE


def graph_files(root: Path, release: str) -> tuple[Path, Path, Path] | None:
    graph = root / f"{release}-domain-t.graph"
    properties = root / f"{release}-domain-t.properties"
    vertices = root / f"{release}-domain-vertices.txt.gz"
    if graph.exists() and properties.exists() and vertices.exists():
        return graph, properties, vertices
    return None


def find_domain_id(vertices: Path, domain: str) -> int | None:
    wanted = reverse_domain(domain)
    with gzip.open(vertices, "rt", encoding="utf-8", errors="ignore") as fh:
        for line in fh:
            if not line or line.startswith("#"):
                continue
            parts = line.rstrip("\n").split("\t")
            if len(parts) >= 2 and parts[1].strip().lower() == wanted:
                try:
                    return int(parts[0])
                except ValueError:
                    return None
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


def ensure_runtime_jars() -> Path:
    cache = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "seo-crawler" / "commoncrawl-jars"
    cache.mkdir(parents=True, exist_ok=True)
    for filename, relative_url in WEBGRAPH_DEPS.items():
        destination = cache / filename
        if destination.exists() and destination.stat().st_size > 0:
            continue
        url = f"{MAVEN_BASE}/{relative_url}"
        print(f"[COMMON CRAWL] downloading runtime dependency: {filename}")
        response = requests.get(url, timeout=120)
        response.raise_for_status()
        part = destination.with_suffix(destination.suffix + ".part")
        part.write_bytes(response.content)
        part.replace(destination)
    return cache


def ensure_offsets(graph_file: Path, jar_dir: Path) -> Path:
    """Generate/check the BVGraph random-access offsets file once locally."""
    offsets = graph_file.with_suffix(".offsets")
    if offsets.exists() and offsets.stat().st_size > 0:
        return offsets
    jars = sorted(jar_dir.glob("*.jar"))
    if not jars:
        raise RuntimeError("WebGraph runtime libraries are unavailable.")
    classpath = os.pathsep.join(str(path) for path in jars)
    xmx = os.environ.get("COMMON_CRAWL_JAVA_XMX", "4g")
    print(f"[COMMON CRAWL] generating missing offset file: {offsets.name}")
    print(f"[COMMON CRAWL] this is a one-time local preprocessing step for this graph release (JVM heap: {xmx})")
    subprocess.run(
        ["java", f"-Xmx{xmx}", "-cp", classpath, "it.unimi.dsi.webgraph.BVGraph", "-O", "-L", str(graph_file.with_suffix(""))],
        check=True,
        capture_output=True,
        text=True,
    )
    if not offsets.exists() or offsets.stat().st_size == 0:
        raise RuntimeError("WebGraph completed but the expected offsets file was not created.")
    return offsets


def incoming_ids(graph_file: Path, node_id: int, jar_dir: Path) -> set[int]:
    helper_dir = Path(tempfile.mkdtemp(prefix="cc-java-"))
    helper = helper_dir / "BacklinkLookup.java"
    helper.write_text(
        """
import it.unimi.dsi.webgraph.ImmutableGraph;
import it.unimi.dsi.webgraph.LazyIntIterator;

public class BacklinkLookup {
  public static void main(String[] args) throws Exception {
    ImmutableGraph graph = ImmutableGraph.loadMapped(args[0]);
    int node = Integer.parseInt(args[1]);
    LazyIntIterator it = graph.successors(node);
    int count = graph.outdegree(node);
    for (int i = 0; i < count; i++) System.out.println(it.nextInt());
  }
}
""",
        encoding="utf-8",
    )
    jars = sorted(jar_dir.glob("*.jar"))
    if not jars:
        raise RuntimeError("WebGraph runtime libraries are unavailable.")
    classpath = os.pathsep.join(str(path) for path in jars)
    try:
        ensure_offsets(graph_file, jar_dir)
        subprocess.run(["javac", "-cp", classpath, str(helper)], check=True, capture_output=True, text=True)
        run_cp = os.pathsep.join([classpath, str(helper_dir)])
        process = subprocess.run(["java", "-cp", run_cp, "BacklinkLookup", str(graph_file.with_suffix("")), str(node_id)], check=True, capture_output=True, text=True)
        return {int(x.strip()) for x in process.stdout.splitlines() if x.strip().isdigit()}
    finally:
        for path in helper_dir.glob("*"):
            try:
                path.unlink()
            except OSError:
                pass
        try:
            helper_dir.rmdir()
        except OSError:
            pass


def collect(url: str) -> dict:
    domain = target_domain(url)
    base = {"provider": "Common Crawl", "target_domain": domain, "graph_level": "domain"}
    if not domain:
        return {**base, "status": "invalid_target", "total_backlinks": 0, "referring_domains": 0, "backlinks": []}

    root = local_graph_dir()
    if root is None:
        return {**base, "status": "not_configured", "total_backlinks": 0, "referring_domains": 0, "backlinks": [], "message": "Local Common Crawl graph directory was not found. Set COMMON_CRAWL_GRAPH_DIR or use C:\\commoncrawl."}

    release = latest_release(root)
    files = graph_files(root, release)
    if files is None:
        return {**base, "status": "graph_missing", "graph_release": release, "total_backlinks": 0, "referring_domains": 0, "backlinks": [], "message": f"Local graph files for {release} were not found in {root}."}

    graph, _properties, vertices = files
    try:
        node_id = find_domain_id(vertices, domain)
        if node_id is None:
            return {**base, "status": "not_in_graph", "graph_release": release, "total_backlinks": 0, "referring_domains": 0, "backlinks": [], "message": "Target domain is not present in the current Common Crawl domain graph."}
        jar_dir = ensure_runtime_jars()
        incoming = incoming_ids(graph, node_id, jar_dir)
        domains = map_domain_ids(vertices, incoming)
        links = [{"source_url": f"https://{domains[node]}/", "target_url": f"https://{domain}/", "anchor_text": "", "rel": "", "referring_domain": domains[node], "graph_level": "domain", "graph_release": release} for node in sorted(incoming) if node in domains]
        return {**base, "status": "success", "graph_release": release, "total_backlinks": len(links), "referring_domains": len(domains), "backlinks": links, "message": "Domain-level incoming links discovered from the local Common Crawl graph. Exact page/anchor/rel details belong to Layer 2."}
    except FileNotFoundError as exc:
        return {**base, "status": "java_not_configured", "graph_release": release, "total_backlinks": 0, "referring_domains": 0, "backlinks": [], "message": f"Java is required for the local WebGraph lookup: {exc}"}
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or str(exc)).strip()
        return {**base, "status": "processing_failed", "graph_release": release, "total_backlinks": 0, "referring_domains": 0, "backlinks": [], "message": detail}
    except requests.RequestException as exc:
        return {**base, "status": "dependency_download_failed", "graph_release": release, "total_backlinks": 0, "referring_domains": 0, "backlinks": [], "message": str(exc)}
    except Exception as exc:
        return {**base, "status": "failed", "graph_release": release, "total_backlinks": 0, "referring_domains": 0, "backlinks": [], "message": f"{type(exc).__name__}: {exc}"}
