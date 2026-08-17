"""Layer 2 backlink investigation.

Takes referring domains discovered by the local Common Crawl Layer 1 graph and
uses the existing HTTP crawler stack to verify current page-level links. The
investigator is bounded, sitemap-aware, HTTP/HTTPS tolerant, and prioritizes
likely directory/profile/listing pages without turning the backlink check into
a full crawl of the referring domain.
"""
from __future__ import annotations

import asyncio
from collections import deque
from urllib.parse import urldefrag, urljoin, urlparse

from bs4 import BeautifulSoup

from app.crawler.http import HTTPClient
from app.utils.urls import normalize_url


DEFAULT_MAX_PAGES = 100
DEFAULT_MAX_DEPTH = 3
DEFAULT_MAX_SITEMAP_URLS = 300
DEFAULT_CONCURRENCY = 8

_PRIORITY_TERMS = (
    "profile", "member", "listing", "directory", "business", "company",
    "vendor", "provider", "service", "detail", "website", "link", "website-list",
    "submission", "submit", "account", "user", "author", "portfolio", "dealer",
)


def _domain(url: str) -> str:
    host = (urlparse(url).hostname or "").lower().rstrip(".")
    return host[4:] if host.startswith("www.") else host


def _same_domain(a: str, b: str) -> bool:
    return _domain(a) == _domain(b)


def _target_match(href: str, target_domain: str) -> bool:
    return _domain(href) == target_domain


def _canonicalize(url: str) -> str:
    try:
        return normalize_url(url)
    except Exception:
        return url


def _extract_links(html: str, source_url: str, target_domain: str):
    soup = BeautifulSoup(html or "", "html.parser")
    results = []
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
        anchor = " ".join(tag.stripped_strings)
        results.append({
            "source_url": source_url,
            "target_url": absolute,
            "anchor_text": anchor,
            "rel": " ".join(str(x) for x in rel),
            "nofollow": any(str(x).lower() == "nofollow" for x in rel),
            "sponsored": any(str(x).lower() == "sponsored" for x in rel),
            "ugc": any(str(x).lower() == "ugc" for x in rel),
        })
    return results


def _origin_urls(source_domain: str) -> list[str]:
    return [f"https://{source_domain}/", f"http://{source_domain}/"]


async def _fetch_first_working(http: HTTPClient, urls: list[str]):
    last = None
    for url in urls:
        try:
            result = await http.get(url)
            response = result.get("response")
            if response and 200 <= getattr(response, "status_code", 0) < 400:
                return result
            last = result
        except Exception as exc:
            last = {"response": None, "error": exc}
    return last or {"response": None}


async def _discover_sitemap_urls(http: HTTPClient, source_domain: str, max_urls: int) -> list[str]:
    seeds = []
    for base in _origin_urls(source_domain):
        seeds.extend([
            urljoin(base, "robots.txt"),
            urljoin(base, "sitemap.xml"),
            urljoin(base, "sitemap_index.xml"),
        ])

    sitemap_candidates: list[str] = []
    for candidate in dict.fromkeys(seeds):
        result = await _fetch_first_working(http, [candidate])
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
    seen_pages = set()
    pending = deque(sitemap_candidates)
    visited_maps = set()
    while pending and len(visited_maps) < max_urls * 2 and len(urls) < max_urls:
        sm = _canonicalize(pending.popleft())
        if sm in visited_maps:
            continue
        visited_maps.add(sm)
        result = await _fetch_first_working(http, [sm])
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
            href = _canonicalize(href)
            if href and _same_domain(href, source_domain) and href not in seen_pages:
                seen_pages.add(href)
                urls.append(href)
                if len(urls) >= max_urls:
                    break
    return urls


def _candidate_score(url: str) -> int:
    parsed = urlparse(url)
    path = (parsed.path or "").lower()
    query = (parsed.query or "").lower()
    score = 0
    for term in _PRIORITY_TERMS:
        score += 3 if term in path else 0
        score += 2 if term in query else 0
    score += min(max(0, path.strip("/").count("/")), 3)
    return score


async def investigate_referring_domain(
    http: HTTPClient,
    source_domain: str,
    target_domain: str,
    *,
    max_pages: int = DEFAULT_MAX_PAGES,
    max_depth: int = DEFAULT_MAX_DEPTH,
    max_sitemap_urls: int = DEFAULT_MAX_SITEMAP_URLS,
) -> dict:
    found: list[dict] = []
    checked: set[str] = set()

    sitemap_urls = await _discover_sitemap_urls(http, source_domain, max_sitemap_urls)
    sitemap_set = set(sitemap_urls)
    sitemap_urls.sort(key=_candidate_score, reverse=True)

    queue = deque((url, 1) for url in sitemap_urls)
    for seed in _origin_urls(source_domain):
        queue.appendleft((seed, 0))

    while queue and len(checked) < max_pages:
        url, depth = queue.popleft()
        url = _canonicalize(url)
        if url in checked or depth > max_depth:
            continue
        checked.add(url)

        alternatives = [url]
        if url.startswith("https://"):
            alternatives.append(url.replace("https://", "http://", 1))
        elif url.startswith("http://"):
            alternatives.append(url.replace("http://", "https://", 1))
        result = await _fetch_first_working(http, alternatives)
        response = result.get("response")
        if not response:
            continue
        status = getattr(response, "status_code", 0)
        headers = dict(getattr(response, "headers", {}) or {})
        content_type = str(headers.get("content-type", ""))
        if status < 200 or status >= 400 or "text/html" not in content_type.lower():
            continue
        final_url = _canonicalize(str(getattr(response, "url", url)))
        html = getattr(response, "text", "") or ""

        current = _extract_links(html, final_url, target_domain)
        if current:
            for item in current:
                item["found_via"] = "sitemap" if final_url in sitemap_set else "crawl"
            found.extend(current)
            # Once confirmed, stop expanding this domain; all target links on this page are captured.
            continue

        soup = BeautifulSoup(html, "html.parser")
        discovered = []
        for tag in soup.find_all("a", href=True):
            href = str(tag.get("href") or "").strip()
            if not href or href.startswith(("#", "javascript:", "mailto:", "tel:")):
                continue
            absolute = _canonicalize(urldefrag(urljoin(final_url, href))[0])
            if _same_domain(absolute, source_domain) and absolute not in checked and depth < max_depth:
                discovered.append(absolute)
        discovered.sort(key=_candidate_score, reverse=True)
        queue.extend((candidate, depth + 1) for candidate in discovered)

    return {
        "referring_domain": source_domain,
        "pages_checked": len(checked),
        "sitemap_urls_discovered": len(sitemap_urls),
        "links_found": len(found),
        "backlinks": found,
    }


async def investigate_layer2(
    url: str,
    layer1: dict,
    *,
    max_pages_per_domain: int = DEFAULT_MAX_PAGES,
    max_depth: int = DEFAULT_MAX_DEPTH,
    max_sitemap_urls: int = DEFAULT_MAX_SITEMAP_URLS,
    concurrency: int = DEFAULT_CONCURRENCY,
) -> dict:
    target_domain = _domain(url)
    layer1_domains = []
    seen = set()
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
                return await investigate_referring_domain(
                    http,
                    domain,
                    target_domain,
                    max_pages=max_pages_per_domain,
                    max_depth=max_depth,
                    max_sitemap_urls=max_sitemap_urls,
                )
            except Exception as exc:
                return {
                    "referring_domain": domain,
                    "pages_checked": 0,
                    "sitemap_urls_discovered": 0,
                    "links_found": 0,
                    "backlinks": [],
                    "error": f"{type(exc).__name__}: {exc}",
                }

    try:
        groups = await asyncio.gather(*(one(domain) for domain in layer1_domains))
    finally:
        await http.close()

    links = []
    domain_status = []
    had_errors = False
    for domain, group in zip(layer1_domains, groups):
        valid = [item for item in group.get("backlinks", []) if item.get("target_url")]
        links.extend([{**item, "referring_domain": domain, "layer": 2} for item in valid])
        had_errors = had_errors or bool(group.get("error"))
        domain_status.append({
            "referring_domain": domain,
            "pages_checked": group.get("pages_checked", 0),
            "sitemap_urls_discovered": group.get("sitemap_urls_discovered", 0),
            "links_found": len(valid),
            "error": group.get("error"),
        })

    return {
        "status": "partial" if had_errors and not links else "success",
        "target_domain": target_domain,
        "layer1_referring_domains": len(layer1_domains),
        "domains_investigated": len(layer1_domains),
        "links_found": len(links),
        "limits": {
            "max_pages_per_domain": max_pages_per_domain,
            "max_depth": max_depth,
            "max_sitemap_urls": max_sitemap_urls,
            "concurrency": concurrency,
        },
        "domains": domain_status,
        "backlinks": links,
    }
