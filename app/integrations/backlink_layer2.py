"""Layer 2 backlink investigation.

Takes referring domains discovered by the local Common Crawl Layer 1 graph and
uses the existing HTTP crawler stack to search their pages for links pointing to
the target domain. This is intentionally best-effort and bounded so it can run
inside a normal audit without turning into a full-web crawl.
"""
from __future__ import annotations

import asyncio
from collections import deque
from urllib.parse import urldefrag, urljoin, urlparse

from bs4 import BeautifulSoup

from app.crawler.http import HTTPClient
from app.utils.urls import normalize_url


def _domain(url: str) -> str:
    host = (urlparse(url).hostname or "").lower().rstrip(".")
    return host[4:] if host.startswith("www.") else host


def _same_domain(a: str, b: str) -> bool:
    return _domain(a) == _domain(b)


def _target_match(href: str, target_domain: str) -> bool:
    try:
        return _domain(href) == target_domain
    except Exception:
        return False


def _extract_links(html: str, source_url: str, target_domain: str):
    soup = BeautifulSoup(html or "", "html.parser")
    results = []
    for tag in soup.find_all("a", href=True):
        raw = str(tag.get("href") or "").strip()
        if not raw or raw.startswith(("#", "javascript:", "mailto:", "tel:")):
            continue
        absolute = urldefrag(urljoin(source_url, raw))[0]
        try:
            absolute = normalize_url(absolute)
        except Exception:
            pass
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


async def investigate_referring_domain(
    http: HTTPClient,
    source_domain: str,
    target_domain: str,
    *,
    max_pages: int = 20,
    max_depth: int = 2,
) -> list[dict]:
    seed = normalize_url(f"https://{source_domain}/")
    queue = deque([(seed, 0)])
    seen: set[str] = set()
    found: list[dict] = []

    while queue and len(seen) < max_pages:
        url, depth = queue.popleft()
        if url in seen or depth > max_depth:
            continue
        seen.add(url)
        result = await http.get(url)
        response = result.get("response")
        if not response:
            continue
        status = getattr(response, "status_code", 0)
        headers = dict(getattr(response, "headers", {}) or {})
        content_type = str(headers.get("content-type", ""))
        if status < 200 or status >= 400 or "text/html" not in content_type.lower():
            continue
        final_url = str(getattr(response, "url", url))
        html = getattr(response, "text", "") or ""
        found.extend(_extract_links(html, final_url, target_domain))
        if found:
            # We only need confirmed source pages. Avoid unnecessary expansion once found.
            continue

        soup = BeautifulSoup(html, "html.parser")
        for tag in soup.find_all("a", href=True):
            href = str(tag.get("href") or "").strip()
            if not href or href.startswith(("#", "javascript:", "mailto:", "tel:")):
                continue
            absolute = urldefrag(urljoin(final_url, href))[0]
            try:
                absolute = normalize_url(absolute)
            except Exception:
                pass
            if _same_domain(absolute, source_domain) and absolute not in seen and depth < max_depth:
                queue.append((absolute, depth + 1))

    return found


async def investigate_layer2(url: str, layer1: dict, *, max_pages_per_domain: int = 20, max_depth: int = 2, concurrency: int = 4) -> dict:
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
                )
            except Exception as exc:
                return [{"source_url": "", "target_url": "", "referring_domain": domain, "error": f"{type(exc).__name__}: {exc}"}]

    try:
        groups = await asyncio.gather(*(one(domain) for domain in layer1_domains))
    finally:
        await http.close()

    links = []
    domain_status = []
    for domain, group in zip(layer1_domains, groups):
        valid = [item for item in group if item.get("target_url")]
        links.extend([{**item, "referring_domain": domain, "layer": 2} for item in valid])
        domain_status.append({
            "referring_domain": domain,
            "pages_checked": max_pages_per_domain,
            "links_found": len(valid),
        })

    return {
        "status": "success",
        "target_domain": target_domain,
        "layer1_referring_domains": len(layer1_domains),
        "domains_investigated": len(layer1_domains),
        "links_found": len(links),
        "domains": domain_status,
        "backlinks": links,
    }
