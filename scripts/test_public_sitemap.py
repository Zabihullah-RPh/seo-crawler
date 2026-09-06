"""Standalone test for discovering and auditing public XML sitemaps.

Usage:
    python -m scripts.test_public_sitemap https://avw.au

This is intentionally isolated from the production audit pipeline. It tests the
public-sitemap approach before wiring it into the main report.
"""
from __future__ import annotations

import argparse
import sys
from collections import deque
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen
import xml.etree.ElementTree as ET


COMMON_PATHS = (
    "/sitemap.xml",
    "/sitemap_index.xml",
    "/wp-sitemap.xml",
    "/sitemapindex.xml",
)
USER_AGENT = "SEO-Crawler-Public-Sitemap-Test/1.0"
MAX_SITEMAPS = 50
MAX_URLS = 10000
TIMEOUT = 12


def fetch(url: str) -> tuple[int, str, str]:
    req = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/xml,text/xml,text/plain,*/*"})
    with urlopen(req, timeout=TIMEOUT) as response:
        body = response.read().decode("utf-8", errors="replace")
        return int(getattr(response, "status", 200) or 200), str(response.headers.get("Content-Type", "")), body


def clean_tag(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def parse_sitemap(text: str, base_url: str) -> tuple[str, list[str]]:
    root = ET.fromstring(text)
    root_name = clean_tag(root.tag)
    locs = []
    for elem in root.iter():
        if clean_tag(elem.tag) == "loc" and (elem.text or "").strip():
            locs.append(urljoin(base_url, (elem.text or "").strip()))
    return root_name, list(dict.fromkeys(locs))


def robot_sitemaps(base: str) -> list[str]:
    try:
        status, _, body = fetch(urljoin(base, "/robots.txt"))
    except Exception as exc:
        print(f"robots.txt: ERROR ({exc})")
        return []
    if status >= 400:
        print(f"robots.txt: HTTP {status}")
        return []
    maps = []
    for line in body.splitlines():
        if line.strip().lower().startswith("sitemap:"):
            value = line.split(":", 1)[1].strip()
            if value:
                maps.append(urljoin(base, value))
    print(f"robots.txt: HTTP {status} | declared_sitemaps={len(maps)}")
    return list(dict.fromkeys(maps))


def discover(base: str) -> list[str]:
    found = []
    found.extend(robot_sitemaps(base))
    for path in COMMON_PATHS:
        url = urljoin(base, path)
        if url in found:
            continue
        try:
            status, content_type, body = fetch(url)
        except Exception as exc:
            print(f"probe {path}: ERROR ({exc})")
            continue
        print(f"probe {path}: HTTP {status} | content_type={content_type.split(';', 1)[0] if content_type else 'unknown'}")
        if 200 <= status < 300 and body.lstrip().startswith("<"):
            try:
                parse_sitemap(body, url)
                found.append(url)
            except Exception:
                pass
    return list(dict.fromkeys(found))


def analyze(base: str) -> dict:
    discovered = discover(base)
    queue = deque(discovered)
    seen_maps = set()
    urls = []
    sitemap_stats = []
    malformed = []

    while queue and len(seen_maps) < MAX_SITEMAPS and len(urls) < MAX_URLS:
        sitemap_url = queue.popleft()
        if sitemap_url in seen_maps:
            continue
        seen_maps.add(sitemap_url)
        try:
            status, content_type, body = fetch(sitemap_url)
            root_name, locs = parse_sitemap(body, sitemap_url)
        except Exception as exc:
            malformed.append({"url": sitemap_url, "error": str(exc)})
            continue

        sitemap_stats.append({
            "url": sitemap_url,
            "status": status,
            "content_type": content_type,
            "type": root_name,
            "entries": len(locs),
        })

        if root_name in {"sitemapindex", "index"}:
            queue.extend(locs)
        else:
            urls.extend(locs)

    base_host = (urlparse(base).hostname or "").lower().lstrip("www.")
    same_host = [u for u in urls if (urlparse(u).hostname or "").lower().lstrip("www.") == base_host]
    external = [u for u in urls if u not in same_host]
    duplicates = len(urls) - len(set(urls))

    return {
        "discovered_sitemaps": discovered,
        "sitemaps_checked": len(sitemap_stats),
        "sitemaps": sitemap_stats,
        "urls_collected": len(urls),
        "unique_urls": len(set(urls)),
        "duplicate_urls": duplicates,
        "same_host_urls": len(same_host),
        "external_urls": len(external),
        "malformed_sitemaps": malformed,
        "limits": {"max_sitemaps": MAX_SITEMAPS, "max_urls": MAX_URLS},
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Test public sitemap discovery and parsing")
    parser.add_argument("url", help="Website URL, e.g. https://avw.au")
    args = parser.parse_args()
    base = args.url.rstrip("/") + "/"

    print("\n========== PUBLIC SITEMAP TEST ==========")
    print(f"Site: {base}")
    try:
        result = analyze(base)
    except Exception as exc:
        print(f"FAILED: {type(exc).__name__}: {exc}")
        return 1

    print("\nDiscovered sitemaps:")
    for item in result["discovered_sitemaps"]:
        print(f"  - {item}")

    print("\nSitemaps checked:")
    for item in result["sitemaps"]:
        print(f"  - {item['url']} | {item['type']} | HTTP {item['status']} | entries={item['entries']}")

    print("\nResults:")
    print(f"  URLs collected:  {result['urls_collected']}")
    print(f"  Unique URLs:     {result['unique_urls']}")
    print(f"  Duplicate URLs:  {result['duplicate_urls']}")
    print(f"  Same-host URLs:  {result['same_host_urls']}")
    print(f"  External URLs:   {result['external_urls']}")
    print(f"  Malformed maps:  {len(result['malformed_sitemaps'])}")
    print("========================================\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
