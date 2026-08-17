"""Layer 2 live backlink verification.

Layer 1 supplies referring domains. Layer 2 does one job only:
find live HTML hrefs whose destination belongs to the target domain.

There is intentionally no page-count limit. Each referring domain gets a
wall-clock budget. The search stops as soon as a target-domain href is found,
or when the budget expires / the reachable crawl space is exhausted.
"""
from __future__ import annotations

import asyncio
import time
from collections import deque
from urllib.parse import urldefrag, urljoin, urlparse

from bs4 import BeautifulSoup

from app.crawler.http import HTTPClient
from app.utils.urls import normalize_url


DEFAULT_TIMEOUT_SECONDS = 300  # 5 minutes per Layer 1 referring domain.
DEFAULT_CONCURRENCY = 8


def _domain(url: str) -> str:
    host = (urlparse(url).hostname or "").lower().rstrip(".")
    return host[4:] if host.startswith("www.") else host


def _same_domain(a: str, b: str) -> bool:
    return _domain(a) == _domain(b)


def _target_match(href: str, target_domain: str) -> bool:
    """Match only links whose actual destination host is the target domain.

    We deliberately do not inspect anchor text, titles, page copy, or URL
    keywords. The href destination alone determines whether this is a match.
    """
    return _domain(href) == target_domain


def _canonicalize(url: str) -> str:
    try:
        return normalize_url(url)
    except Exception:
        return url


def _extract_target_links(html: str, source_url: str, target_domain: str) -> list[dict]:
    soup = BeautifulSoup(html or "", "html.parser")
    results: list[dict] = []
    for tag in soup.find_all("a", href=True):
        raw = str(tag.get("href") or "").strip()
        if not raw or raw.startswith(("#", "javascript:", "mailto:", "tel:")):
            continue
        absolute = _canonicalize(urldefrag(urljoin(source_url, raw))[0])
        if not _target_match(absolute, target_domain):
            continue

        rel = tag.get("rel") or []
        if isinstance(rel, str):
            rel = rel.split()

        results.append({
            "source_url": source_url,
            "target_url": absolute,
            "anchor_text": " ".join(tag.stripped_strings),
            "rel": " ".join(str(x) for x in rel),
            "nofollow": any(str(x).lower() == "nofollow" for x in rel),
            "sponsored": any(str(x).lower() == "sponsored" for x in rel),
            "ugc": any(str(x).lower() == "ugc" for x in rel),
        })
    return results


def _origin_urls(source_domain: str) -> list[str]:
    return [f"https://{source_domain}/", f"http://{source_domain}/"]


async def _fetch_first_working(http: HTTPClient, urls: list[str], deadline: float):
    last = None
    for url in urls:
        if time.monotonic() >= deadline:
            return {"response": None, "error": "timeout"}
        try:
            result = await http.get(url)
            response = result.get("response")
            if response and 200 <= getattr(response, "status_code", 0) < 400:
                return result
            last = result
        except Exception as exc:
            last = {"response": None, "error": str(exc)}
    return last or {"response": None}


async def _discover_sitemap_urls(http: HTTPClient, source_domain: str, deadline: float) -> list[str]:
    seeds: list[str] = []
    for base in _origin_urls(source_domain):
        seeds.extend([
            urljoin(base, "robots.txt"),
            urljoin(base, "sitemap.xml"),
            urljoin(base, "sitemap_index.xml"),
        ])

    sitemap_candidates: list[str] = []
    for candidate in dict.fromkeys(seeds):
        if time.monotonic() >= deadline:
            break
        result = await _fetch_first_working(http, [candidate], deadline)
        response = result.get("response")
        if not response:
            continue
        text = getattr(response, "text", "") or ""
        final_url = str(getattr(response, "url", candidate))
        if candidate.lower().endswith("robots.txt"):
            for line in text.splitlines():
                if line.lower().startswith("sitemap:"):
                    sitemap_candidates.append(line.split(":", 1)[1].strip())
            continue

        soup = BeautifulSoup(text, "xml")
        for loc in soup.find_all("loc"):
            href = str(loc.get_text(" ", strip=True)).strip()
            if href:
                sitemap_candidates.append(urljoin(final_url, href))

    urls: list[str] = []
    seen_pages: set[str] = set()
    pending = deque(sitemap_candidates)
    visited_maps: set[str] = set()

    while pending and time.monotonic() < deadline:
        sm = _canonicalize(pending.popleft())
        if sm in visited_maps:
            continue
        visited_maps.add(sm)

        result = await _fetch_first_working(http, [sm], deadline)
        response = result.get("response")
        if not response:
            continue
        text = getattr(response, "text", "") or ""
        soup = BeautifulSoup(text, "xml")
        root_name = soup.find()
        is_index = bool(root_name and root_name.name and root_name.name.lower().endswith("index"))
        locs = [str(loc.get_text(" ", strip=True)).strip() for loc in soup.find_all("loc")]

        if is_index:
            pending.extend(x for x in locs if x)
            continue

        for href in locs:
            if time.monotonic() >= deadline:
                break
            href = _canonicalize(href)
            if href and _same_domain(href, source_domain) and href not in seen_pages:
                seen_pages.add(href)
                urls.append(href)

    return urls


async def investigate_referring_domain(
    http: HTTPClient,
    source_domain: str,
    target_domain: str,
    *,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
) -> dict:
    """Search one referring domain until a target href is found or timeout.

    There is no artificial page or depth cap. The only search limit is the
    wall-clock timeout, plus the site's own reachability and crawl graph.
    """
    started = time.monotonic()
    deadline = started + max(1.0, float(timeout_seconds))
    found: list[dict] = []
    checked: set[str] = set()
    queue: deque[str] = deque()
    blocked = 0
    attempted = 0

    # Sitemaps often expose deep profile/listing pages that homepage crawling
    # would take a long time to discover, so use them as additional seeds.
    for url in await _discover_sitemap_urls(http, source_domain, deadline):
        queue.append(url)

    for seed in reversed(_origin_urls(source_domain)):
        queue.appendleft(seed)

    while queue and time.monotonic() < deadline:
        url = _canonicalize(queue.popleft())
        if url in checked:
            continue
        checked.add(url)
        attempted += 1

        alternatives = [url]
        if url.startswith("https://"):
            alternatives.append(url.replace("https://", "http://", 1))
        elif url.startswith("http://"):
            alternatives.append(url.replace("http://", "https://", 1))

        result = await _fetch_first_working(http, alternatives, deadline)
        response = result.get("response")
        if not response:
            continue

        status = int(getattr(response, "status_code", 0) or 0)
        if status in (401, 403, 429):
            blocked += 1
            continue
        if status < 200 or status >= 400:
            continue

        headers = dict(getattr(response, "headers", {}) or {})
        content_type = str(headers.get("content-type", ""))
        if "text/html" not in content_type.lower():
            continue

        final_url = _canonicalize(str(getattr(response, "url", url)))
        html = getattr(response, "text", "") or ""

        # This is the only backlink test: inspect href destinations.
        current = _extract_target_links(html, final_url, target_domain)
        if current:
            for item in current:
                item["layer"] = 2
                item["found_via"] = "live_crawl"
            found.extend(current)
            elapsed = time.monotonic() - started
            return {
                "status": "confirmed",
                "referring_domain": source_domain,
                "target_domain": target_domain,
                "pages_checked": attempted,
                "elapsed_seconds": round(elapsed, 2),
                "links_found": len(found),
                "backlinks": found,
            }

        # No target href on this page. Continue discovering internal pages.
        soup = BeautifulSoup(html, "html.parser")
        discovered: list[str] = []
        for tag in soup.find_all("a", href=True):
            if time.monotonic() >= deadline:
                break
            raw = str(tag.get("href") or "").strip()
            if not raw or raw.startswith(("#", "javascript:", "mailto:", "tel:")):
                continue
            absolute = _canonicalize(urldefrag(urljoin(final_url, raw))[0])
            if _same_domain(absolute, source_domain) and absolute not in checked:
                discovered.append(absolute)
        queue.extend(discovered)

    elapsed = time.monotonic() - started
    if time.monotonic() >= deadline:
        status = "timeout"
    elif blocked and attempted == blocked:
        status = "blocked"
    else:
        status = "not_found"

    return {
        "status": status,
        "referring_domain": source_domain,
        "target_domain": target_domain,
        "pages_checked": attempted,
        "elapsed_seconds": round(elapsed, 2),
        "links_found": 0,
        "backlinks": [],
    }


async def investigate_layer2(
    url: str,
    layer1: dict,
    *,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    concurrency: int = DEFAULT_CONCURRENCY,
) -> dict:
    target_domain = _domain(url)
    layer1_domains: list[str] = []
    seen: set[str] = set()

    for item in layer1.get("backlinks", []) or []:
        domain = str(item.get("referring_domain") or "").strip().lower()
        if domain and domain not in seen and domain != target_domain:
            seen.add(domain)
            layer1_domains.append(domain)

    if not layer1_domains:
        return {
            "status": "no_referring_domains",
            "target_domain": target_domain,
            "layer1_referring_domains": 0,
            "domains_investigated": 0,
            "links_found": 0,
            "backlinks": [],
        }

    http = HTTPClient(concurrency=max(1, concurrency))
    sem = asyncio.Semaphore(max(1, concurrency))

    async def one(domain: str):
        async with sem:
            try:
                return await asyncio.wait_for(
                    investigate_referring_domain(
                        http,
                        domain,
                        target_domain,
                        timeout_seconds=timeout_seconds,
                    ),
                    timeout=max(1.0, float(timeout_seconds)) + 5,
                )
            except asyncio.TimeoutError:
                return {
                    "status": "timeout",
                    "referring_domain": domain,
                    "target_domain": target_domain,
                    "pages_checked": 0,
                    "elapsed_seconds": float(timeout_seconds),
                    "links_found": 0,
                    "backlinks": [],
                }
            except Exception as exc:
                return {
                    "status": "error",
                    "referring_domain": domain,
                    "target_domain": target_domain,
                    "pages_checked": 0,
                    "elapsed_seconds": 0,
                    "links_found": 0,
                    "backlinks": [],
                    "error": f"{type(exc).__name__}: {exc}",
                }

    try:
        groups = await asyncio.gather(*(one(domain) for domain in layer1_domains))
    finally:
        await http.close()

    links: list[dict] = []
    domain_status: list[dict] = []
    statuses: set[str] = set()

    for domain, group in zip(layer1_domains, groups):
        valid = [item for item in group.get("backlinks", []) if item.get("target_url")]
        links.extend([{**item, "referring_domain": domain} for item in valid])
        status = str(group.get("status") or "error")
        statuses.add(status)
        domain_status.append({
            "referring_domain": domain,
            "status": status,
            "pages_checked": group.get("pages_checked", 0),
            "elapsed_seconds": group.get("elapsed_seconds", 0),
            "links_found": len(valid),
            "error": group.get("error"),
        })

    if links:
        overall_status = "confirmed"
    elif "error" in statuses:
        overall_status = "error"
    elif "timeout" in statuses:
        overall_status = "timeout"
    elif "blocked" in statuses:
        overall_status = "blocked"
    else:
        overall_status = "not_found"

    return {
        "status": overall_status,
        "target_domain": target_domain,
        "layer1_referring_domains": len(layer1_domains),
        "domains_investigated": len(layer1_domains),
        "links_found": len(links),
        "limits": {
            "timeout_seconds_per_domain": timeout_seconds,
            "concurrency": concurrency,
            "page_limit": None,
            "depth_limit": None,
        },
        "domains": domain_status,
        "backlinks": links,
    }
