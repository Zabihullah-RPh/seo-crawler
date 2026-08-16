"""Local Common Crawl Layer 1 backlink discovery.

The SEO audit engine is intentionally untouched. This module uses a locally
stored Common Crawl domain transpose graph to discover referring domains for
any target URL. It never downloads Common Crawl graph data at audit time.
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

# WebGraph runtime dependencies. They are small and cached locally; Maven is not required.
MAVEN_BASE = "https://repo1.maven.org/maven2"
WEBGRAPH_DEPS = {
    "webgraph-3.6.12.jar": "it/unimi/dsi/webgraph/3.6.12/webgraph-3.6.12.jar",
    "fastutil-8.5.16.jar": "it/unimi/dsi/fastutil/8.5.16/fastutil-8.5.16.jar",
    "dsiutils-2.7.4.jar": "it/unimi/dsi/dsiutils/2.7.4/dsiutils-2.7.4.jar",
    "sux4j-5.4.1.jar": "it/unimi/dsi/sux4j/5.4.1/sux4j-5.4.1.jar",
    "jsap-20210129.jar": "it/unimi/di/jsap/20210129/jsap-20210129.jar",
    "slf4j-api-2.0.3.jar": "org/slf4j/slf4j-api/2.0.3/slf4j-api-2.0.3.jar",
}


def target_domain(url: str) -> str:
    host = (urlparse(url).hostname or "").lower().strip().rstrip(".")
    return host[4:] if host.startswith("www.") else host


def reverse_domain(domain: str) -> str:
    return ".".join(reversed([p for p in domain.split(".") if p]))


def latest_release() -> str:
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


def local_graph_dir() -> Path | None:
    configured = os.environ.get("COMMON_CRAWL_GRAPH_DIR", "").strip()
    candidates = []
    if configured:
        candidates.append(Path(configured).expanduser())
    if os.name == "nt":
        candidates.append(Path(r"C:\commoncrawl"))
    candidates.append(Path.cwd() / "commoncrawl")

    for path in candidates:
        if path.exists() and path.is_dir():
            return path
    return None


def graph_files(root: Path, release: str) -> tuple[Path, Path, Path] | None:
    stem = root / f"{release}-domain-t"
    graph = stem.with_suffix(".graph")
    properties = stem.with_suffix(".properties")
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
    """Create the BVGraph random-access offset file once, locally.

    Common Crawl publishes the .graph and .properties files, but the WebGraph
    random-access API also needs the .offsets file. We generate it locally from
    the already-downloaded graph and cache it next to the graph. This is a
    one-time preprocessing step for each graph release.
    """
    offsets = Path(f"{graph_file}.offsets")
    if offsets.exists() and offsets.stat().st_size > 0:
        return offsets

    jars = sorted(jar_dir.glob("*.jar"))
    if not jars:
        raise RuntimeError("WebGraph runtime libraries are unavailable.")

    classpath = os.pathsep.join(str(path) for path in jars)
    xmx = os.environ.get("COMMON_CRAWL_JAVA_XMX", "4g")
    print(f"[COMMON CRAWL] generating missing offset file: {offsets.name}")
    print(f"[COMMON CRAWL] this is a one-time local preprocessing step for this graph release (JVM heap: {xmx})")

    process = subprocess.run(
        [
            "java",
            f"-Xmx{xmx}",
            "-cp",
            classpath,
            "it.unimi.dsi.webgraph.BVGraph",
            "-O",
            "-L",
            str(graph_file.with_suffix("")),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    if not offsets.exists() or offsets.stat().st_size == 0:
        detail = (process.stderr or process.stdout or "WebGraph did not create the offsets file.").strip()
        raise RuntimeError(detail)
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
    for (int i = 0; i < count; i++) {
      System.out.println(it.nextInt());
    }
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
        process = subprocess.run(
            ["java", "-cp", run_cp, "BacklinkLookup", str(graph_file.with_suffix("")), str(node_id)],
            check=True,
            capture_output=True,
            text=True,
        )
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
    base = {
        "provider": "Common Crawl",
        "target_domain": domain,
        "graph_level": "domain",
    }
    if not domain:
        return {**base, "status": "invalid_target", "total_backlinks": 0, "referring_domains": 0, "backlinks": []}

    root = local_graph_dir()
    if root is None:
        return {
            **base,
            "status": "not_configured",
            "total_backlinks": 0,
            "referring_domains": 0,
            "backlinks": [],
            "message": "Local Common Crawl graph directory was not found. Set COMMON_CRAWL_GRAPH_DIR or use C:\\commoncrawl.",
        }

    release = latest_release()
    files = graph_files(root, release)
    if files is None:
        return {
            **base,
            "status": "graph_missing",
            "graph_release": release,
            "total_backlinks": 0,
            "referring_domains": 0,
            "backlinks": [],
            "message": f"Local graph files for {release} were not found in {root}.",
        }

    graph, _properties, vertices = files
    try:
        node_id = find_domain_id(vertices, domain)
        if node_id is None:
            return {
                **base,
                "status": "not_in_graph",
                "graph_release": release,
                "total_backlinks": 0,
                "referring_domains": 0,
                "backlinks": [],
                "message": "Target domain is not present in the current Common Crawl domain graph.",
            }

        jar_dir = ensure_runtime_jars()
        incoming = incoming_ids(graph, node_id, jar_dir)
        domains = map_domain_ids(vertices, incoming)
        links = [
            {
                "source_url": f"https://{domains[node]}/",
                "target_url": f"https://{domain}/",
                "anchor_text": "",
                "rel": "",
                "referring_domain": domains[node],
                "graph_level": "domain",
                "graph_release": release,
            }
            for node in sorted(incoming)
            if node in domains
        ]
        return {
            **base,
            "status": "success",
            "graph_release": release,
            "total_backlinks": len(links),
            "referring_domains": len(domains),
            "backlinks": links,
            "message": "Domain-level incoming links discovered from the local Common Crawl graph. Exact page/anchor/rel details belong to Layer 2.",
        }
    except FileNotFoundError as exc:
        return {
            **base,
            "status": "java_not_configured",
            "graph_release": release,
            "total_backlinks": 0,
            "referring_domains": 0,
            "backlinks": [],
            "message": f"Java is required for the local WebGraph lookup: {exc}",
        }
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or str(exc)).strip()
        return {
            **base,
            "status": "processing_failed",
            "graph_release": release,
            "total_backlinks": 0,
            "referring_domains": 0,
            "backlinks": [],
            "message": detail,
        }
    except requests.RequestException as exc:
        return {
            **base,
            "status": "dependency_download_failed",
            "graph_release": release,
            "total_backlinks": 0,
            "referring_domains": 0,
            "backlinks": [],
            "message": str(exc),
        }
    except Exception as exc:
        return {
            **base,
            "status": "failed",
            "graph_release": release,
            "total_backlinks": 0,
            "referring_domains": 0,
            "backlinks": [],
            "message": f"{type(exc).__name__}: {exc}",
        }
