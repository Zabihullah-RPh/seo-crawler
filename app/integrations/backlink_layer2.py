"""Layer 2 live backlink verification."""
from __future__ import annotations

import asyncio
import time
from collections import deque
from urllib.parse import urldefrag, urljoin, urlparse

from bs4 import BeautifulSoup

from app.crawler.http import HTTPClient
from app.utils.urls import normalize_url

DEFAULT_TIMEOUT_SECONDS = 300
DEFAULT_CONCURRENCY = 32
BATCH_SIZE = 32


def _host(url: str) -> str:
    try:
        host = (urlparse(url).hostname or "").strip().lower().rstrip(".")
    except Exception:
        return ""
    return host[4:] if host.startswith("www.") else host


def _same_domain(a: str, b: str) -> bool:
    return bool(_host(a)) and _host(a) == _host(b)


def _relative(raw: str) -> bool:
    return not (raw or "").strip().lower().startswith(("http://", "https://", "//"))


def _canonical(url: str) -> str:
    try:
        return normalize_url(url)
    except Exception:
        return url


def _extract(html: str, source_url: str, source_domain: str, target_domain: str):
    soup = BeautifulSoup(html or "", "html.parser")
    hits, internal = [], []
    seen = set()
    for tag in soup.find_all("a", href=True):
        raw = str(tag.get("href") or "").strip()
        if not raw or raw.startswith(("#", "javascript:", "mailto:", "tel:")):
            continue
        absolute = _canonical(urldefrag(urljoin(source_url, raw))[0])
        if not absolute:
            continue
        if _host(absolute) == _host(target_domain):
            rel = tag.get("rel") or []
            if isinstance(rel, str):
                rel = rel.split()
            hits.append({
                "source_url": source_url,
                "target_url": absolute,
                "anchor_text": " ".join(tag.stripped_strings),
                "rel": " ".join(str(x) for x in rel),
                "nofollow": any(str(x).lower() == "nofollow" for x in rel),
                "sponsored": any(str(x).lower() == "sponsored" for x in rel),
                "ugc": any(str(x).lower() == "ugc" for x in rel),
            })
        elif (_relative(raw) or _same_domain(absolute, source_domain)) and absolute not in seen:
            seen.add(absolute)
            internal.append(absolute)
    return hits, internal


def _origins(domain: str):
    host = _host(domain) or str(domain).strip().lower()
    return [f"https://{host}/", f"http://{host}/"]


async def _fetch(http: HTTPClient, url: str, deadline: float):
    candidates = [url]
    if url.startswith("https://"):
        candidates.append(url.replace("https://", "http://", 1))
    elif url.startswith("http://"):
        candidates.append(url.replace("http://", "https://", 1))
    last = None
    for candidate in candidates:
        if time.monotonic() >= deadline:
            return None
        try:
            result = await http.get(candidate)
            last = result
            response = result.get("response")
            if response and 200 <= int(getattr(response, "status_code", 0) or 0) < 400:
                return result
        except Exception:
            pass
    return last


async def _sitemaps(http: HTTPClient, domain: str, deadline: float) -> list[str]:
    seeds = []
    for base in _origins(domain):
        seeds += [urljoin(base, "robots.txt"), urljoin(base, "sitemap.xml"), urljoin(base, "sitemap_index.xml")]
    maps = []
    for u in dict.fromkeys(seeds):
        result = await _fetch(http, u, deadline)
        response = result.get("response") if result else None
        if not response:
            continue
        text = getattr(response, "text", "") or ""
        final = str(getattr(response, "url", u))
        if u.lower().endswith("robots.txt"):
            maps.extend(line.split(":", 1)[1].strip() for line in text.splitlines() if line.lower().startswith("sitemap:"))
        else:
            soup = BeautifulSoup(text, "xml")
            maps.extend(urljoin(final, loc.get_text(" ", strip=True)) for loc in soup.find_all("loc") if loc.get_text(strip=True))
    pages, pending, seen_maps, seen_pages = [], deque(dict.fromkeys(maps)), set(), set()
    while pending and time.monotonic() < deadline:
        sm = _canonical(pending.popleft())
        if not sm or sm in seen_maps:
            continue
        seen_maps.add(sm)
        result = await _fetch(http, sm, deadline)
        response = result.get("response") if result else None
        if not response:
            continue
        soup = BeautifulSoup(getattr(response, "text", "") or "", "xml")
        root = soup.find()
        locs = [loc.get_text(" ", strip=True) for loc in soup.find_all("loc")]
        if root and root.name and root.name.lower().endswith("index"):
            pending.extend(locs)
        else:
            for href in locs:
                href = _canonical(href)
                if href and _same_domain(href, domain) and href not in seen_pages:
                    seen_pages.add(href)
                    pages.append(href)
    return pages


async def investigate_referring_domain(http: HTTPClient, source_domain: str, target_domain: str, *, timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS, progress_callback=None) -> dict:
    started = time.monotonic()
    deadline = started + max(1.0, float(timeout_seconds))
    checked, queue = set(), deque()
    for u in await _sitemaps(http, source_domain, deadline):
        queue.append(u)
    for u in reversed(_origins(source_domain)):
        queue.appendleft(u)

    attempted = 0
    while queue and time.monotonic() < deadline:
        batch = []
        while queue and len(batch) < BATCH_SIZE and time.monotonic() < deadline:
            u = _canonical(queue.popleft())
            if u and u not in checked:
                checked.add(u)
                batch.append(u)
        if not batch:
            continue

        results = await asyncio.gather(*(_fetch(http, u, deadline) for u in batch), return_exceptions=True)
        new_urls = []
        for u, result in zip(batch, results):
            attempted += 1
            if isinstance(result, Exception) or not result:
                continue
            response = result.get("response")
            if not response:
                continue
            status = int(getattr(response, "status_code", 0) or 0)
            if status < 200 or status >= 400:
                continue
            ctype = str((getattr(response, "headers", {}) or {}).get("content-type", "")).lower()
            if "text/html" not in ctype:
                continue
            final = _canonical(str(getattr(response, "url", u)))
            hits, internal = _extract(getattr(response, "text", "") or "", final, source_domain, target_domain)
            if hits:
                elapsed = time.monotonic() - started
                for hit in hits:
                    hit["layer"] = 2
                    hit["found_via"] = "live_crawl"
                return {"status":"confirmed","referring_domain":source_domain,"target_domain":target_domain,"pages_checked":attempted,"elapsed_seconds":round(elapsed,2),"links_found":len(hits),"backlinks":hits}
            new_urls.extend(internal)
            if progress_callback:
                progress_callback(attempted, time.monotonic() - started, len(queue))
        for nxt in new_urls:
            if nxt not in checked:
                queue.append(nxt)

    elapsed = time.monotonic() - started
    return {"status":"timeout" if time.monotonic() >= deadline else "not_found","referring_domain":source_domain,"target_domain":target_domain,"pages_checked":attempted,"elapsed_seconds":round(elapsed,2),"links_found":0,"backlinks":[]}


async def investigate_layer2(url: str, layer1: dict, *, timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS, concurrency: int = DEFAULT_CONCURRENCY) -> dict:
    target_domain = _host(url)
    domains = []
    seen = set()
    for item in layer1.get("backlinks", []) or []:
        d = str(item.get("referring_domain") or "").strip().lower()
        if d and d not in seen and d != target_domain:
            seen.add(d); domains.append(d)
    if not domains:
        return {"status":"no_referring_domains","target_domain":target_domain,"layer1_referring_domains":0,"domains_investigated":0,"links_found":0,"backlinks":[]}

    http = HTTPClient(concurrency=max(1, int(concurrency)))
    try:
        groups = await asyncio.gather(*(investigate_referring_domain(http,d,target_domain,timeout_seconds=timeout_seconds) for d in domains), return_exceptions=True)
    finally:
        await http.close()
    links=[]; domain_status=[]; statuses=set()
    for d,g in zip(domains,groups):
        if isinstance(g,Exception):
            g={"status":"error","pages_checked":0,"elapsed_seconds":0,"links_found":0,"backlinks":[]}
        valid=[x for x in g.get("backlinks",[]) if x.get("target_url")]
        links.extend([{**x,"referring_domain":d} for x in valid])
        st=str(g.get("status") or "error"); statuses.add(st)
        domain_status.append({"referring_domain":d,"status":st,"pages_checked":g.get("pages_checked",0),"elapsed_seconds":g.get("elapsed_seconds",0),"links_found":len(valid)})
    overall="confirmed" if links else "timeout" if "timeout" in statuses else "error" if "error" in statuses else "blocked" if "blocked" in statuses else "not_found"
    return {"status":overall,"target_domain":target_domain,"layer1_referring_domains":len(domains),"domains_investigated":len(domains),"links_found":len(links),"limits":{"timeout_seconds_per_domain":timeout_seconds,"concurrency":concurrency,"batch_size":BATCH_SIZE,"page_limit":None,"depth_limit":None},"domains":domain_status,"backlinks":links}
