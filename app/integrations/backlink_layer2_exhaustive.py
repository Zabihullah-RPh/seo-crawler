"""Timed exhaustive live backlink verification.

Layer 1 supplies referring domains. This layer searches only for actual HTML hrefs
pointing at the target domain. It has no page/depth limit; the safety boundary is
an elapsed-time limit per referring domain.
"""
from __future__ import annotations

import asyncio
import time
from collections import deque
from urllib.parse import urldefrag, urljoin, urlparse

from bs4 import BeautifulSoup

from app.crawler.http import HTTPClient
from app.utils.urls import normalize_url

DEFAULT_TIMEOUT_SECONDS = 300
DEFAULT_CONCURRENCY = 8


def _domain(url: str) -> str:
    host = (urlparse(url).hostname or "").lower().rstrip(".")
    return host[4:] if host.startswith("www.") else host


def _canonical(url: str) -> str:
    try:
        return normalize_url(url)
    except Exception:
        return url


def _target_match(href: str, target_domain: str) -> bool:
    return _domain(href) == target_domain


def _extract_target_links(html: str, source_url: str, target_domain: str) -> list[dict]:
    soup = BeautifulSoup(html or "", "html.parser")
    found = []
    for tag in soup.find_all("a", href=True):
        raw = str(tag.get("href") or "").strip()
        if not raw or raw.startswith(("#", "javascript:", "mailto:", "tel:")):
            continue
        target = _canonical(urldefrag(urljoin(source_url, raw))[0])
        if not _target_match(target, target_domain):
            continue
        rel = tag.get("rel") or []
        if isinstance(rel, str):
            rel = rel.split()
        found.append({
            "source_url": source_url,
            "target_url": target,
            "anchor_text": " ".join(tag.stripped_strings),
            "rel": " ".join(str(x) for x in rel),
            "nofollow": any(str(x).lower() == "nofollow" for x in rel),
            "sponsored": any(str(x).lower() == "sponsored" for x in rel),
            "ugc": any(str(x).lower() == "ugc" for x in rel),
        })
    return found


async def _get(http: HTTPClient, url: str):
    alternatives = [url]
    if url.startswith("https://"):
        alternatives.append(url.replace("https://", "http://", 1))
    elif url.startswith("http://"):
        alternatives.append(url.replace("http://", "https://", 1))
    last = None
    for candidate in alternatives:
        try:
            result = await http.get(candidate)
            response = result.get("response")
            if response and 200 <= getattr(response, "status_code", 0) < 400:
                return result
            last = result
        except Exception as exc:
            last = {"response": None, "error": exc}
    return last or {"response": None}


async def _sitemap_seeds(http: HTTPClient, domain: str) -> list[str]:
    seeds = []
    for base in (f"https://{domain}/", f"http://{domain}/"):
        seeds.extend([urljoin(base, "robots.txt"), urljoin(base, "sitemap.xml"), urljoin(base, "sitemap_index.xml")])
    maps = []
    for seed in dict.fromkeys(seeds):
        result = await _get(http, seed)
        response = result.get("response")
        if not response:
            continue
        text = getattr(response, "text", "") or ""
        final_url = str(getattr(response, "url", seed))
        if seed.lower().endswith("robots.txt"):
            for line in text.splitlines():
                if line.lower().startswith("sitemap:"):
                    maps.append(line.split(":", 1)[1].strip())
        else:
            soup = BeautifulSoup(text, "xml")
            maps.extend(str(x.get_text(" ", strip=True)).strip() for x in soup.find_all("loc") if x.get_text(strip=True))
    return list(dict.fromkeys(_canonical(urljoin("https://" + domain + "/", x)) for x in maps if x))


async def _discover_from_sitemaps(http: HTTPClient, domain: str, deadline: float) -> list[str]:
    pending = deque(await _sitemap_seeds(http, domain))
    visited = set()
    pages = []
    while pending and time.monotonic() < deadline:
        sm = _canonical(pending.popleft())
        if sm in visited:
            continue
        visited.add(sm)
        result = await _get(http, sm)
        response = result.get("response")
        if not response:
            continue
        text = getattr(response, "text", "") or ""
        soup = BeautifulSoup(text, "xml")
        root = soup.find()
        is_index = bool(root and root.name and root.name.lower().endswith("index"))
        locs = [str(x.get_text(" ", strip=True)).strip() for x in soup.find_all("loc")]
        if is_index:
            pending.extend(x for x in locs if x)
            continue
        for href in locs:
            url = _canonical(href)
            if url and _domain(url) == domain and url not in pages:
                pages.append(url)
    return pages


async def investigate_referring_domain(http: HTTPClient, source_domain: str, target_domain: str, *, timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS) -> dict:
    started = time.monotonic()
    deadline = started + timeout_seconds
    checked: set[str] = set()
    queue: deque[tuple[str, int]] = deque()
    found: list[dict] = []
    successful_html = 0
    failed_fetches = 0

    sitemap_pages = await _discover_from_sitemaps(http, source_domain, deadline)
    for url in sitemap_pages:
        queue.append((url, 1))
    queue.appendleft((f"https://{source_domain}/", 0))
    queue.appendleft((f"http://{source_domain}/", 0))

    while queue and time.monotonic() < deadline:
        url, depth = queue.popleft()
        url = _canonical(url)
        if url in checked:
            continue
        checked.add(url)
        result = await _get(http, url)
        response = result.get("response")
        if not response:
            failed_fetches += 1
            continue
        status = getattr(response, "status_code", 0)
        headers = dict(getattr(response, "headers", {}) or {})
        if status < 200 or status >= 400 or "text/html" not in str(headers.get("content-type", "")).lower():
            continue
        successful_html += 1
        final_url = _canonical(str(getattr(response, "url", url)))
        html = getattr(response, "text", "") or ""
        current = _extract_target_links(html, final_url, target_domain)
        if current:
            found.extend(current)
            break
        soup = BeautifulSoup(html, "html.parser")
        for tag in soup.find_all("a", href=True):
            raw = str(tag.get("href") or "").strip()
            if not raw or raw.startswith(("#", "javascript:", "mailto:", "tel:")):
                continue
            child = _canonical(urldefrag(urljoin(final_url, raw))[0])
            if _domain(child) == source_domain and child not in checked:
                queue.append((child, depth + 1))

    elapsed = time.monotonic() - started
    if found:
        status = "confirmed"
    elif elapsed >= timeout_seconds:
        status = "timeout"
    elif not checked:
        status = "blocked"
    else:
        status = "not_found"
    return {
        "referring_domain": source_domain,
        "status": status,
        "elapsed_seconds": round(elapsed, 2),
        "pages_checked": len(checked),
        "html_pages_checked": successful_html,
        "failed_fetches": failed_fetches,
        "links_found": len(found),
        "backlinks": found,
    }


async def investigate_layer2(url: str, layer1: dict, *, timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS, concurrency: int = DEFAULT_CONCURRENCY) -> dict:
    target_domain = _domain(url)
    domains = []
    seen = set()
    for item in layer1.get("backlinks", []) or []:
        domain = str(item.get("referring_domain") or "").strip().lower()
        if domain and domain not in seen and domain != target_domain:
            seen.add(domain)
            domains.append(domain)
    if not domains:
        return {"status": "no_referring_domains", "target_domain": target_domain, "domains_investigated": 0, "links_found": 0, "backlinks": []}

    http = HTTPClient(concurrency=max(1, concurrency))
    sem = asyncio.Semaphore(max(1, concurrency))

    async def one(domain: str):
        async with sem:
            return await investigate_referring_domain(http, domain, target_domain, timeout_seconds=timeout_seconds)

    try:
        groups = await asyncio.gather(*(one(d) for d in domains), return_exceptions=False)
    finally:
        await http.close()

    links = []
    for group in groups:
        links.extend([{**x, "referring_domain": group["referring_domain"], "layer": 2} for x in group.get("backlinks", [])])
    return {
        "status": "success" if links else "not_found",
        "target_domain": target_domain,
        "domains_investigated": len(domains),
        "links_found": len(links),
        "timeout_seconds": timeout_seconds,
        "domains": groups,
        "backlinks": links,
    }
