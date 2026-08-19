"""Public-web backlink discovery and Layer 2 verification.

This module is deliberately separate from the Common Crawl domain graph.
It uses public search-engine result pages to discover candidate external pages,
then fetches those pages and inspects their actual HTML anchors for links to the
target domain. Search results are discovery signals, not backlink evidence;
only an HTML anchor found on an external source page becomes a confirmed link.

No paid API is required. Engines are best-effort and may rate-limit automated
requests, so failures are returned as diagnostics rather than treated as zero
backlinks.
"""
from __future__ import annotations

import html
import re
from collections import Counter
from urllib.parse import parse_qs, quote_plus, unquote, urljoin, urlparse

import requests


DEFAULT_ENGINES = ("bing", "duckduckgo")
DEFAULT_MAX_RESULTS_PER_ENGINE = 50
DEFAULT_TIMEOUT = 20

A_RE = re.compile(r"<a\b([^>]*)>(.*?)</a>", re.I | re.S)
HREF_RE = re.compile(r"\bhref\s*=\s*([\"'])(.*?)\1", re.I | re.S)
REL_RE = re.compile(r"\brel\s*=\s*([\"'])(.*?)\1", re.I | re.S)
TAG_RE = re.compile(r"<[^>]+>")


def normalize_domain(value: str) -> str:
    host = (urlparse(value).hostname or value).lower().strip().rstrip(".")
    return host[4:] if host.startswith("www.") else host


def is_target(value: str, target_domain: str) -> bool:
    host = normalize_domain(value)
    target = normalize_domain(target_domain)
    return host == target or host.endswith("." + target)


def clean_anchor(value: str) -> str:
    value = TAG_RE.sub(" ", value)
    value = html.unescape(value)
    return re.sub(r"\s+", " ", value).strip()


def classify_rel(rel: str) -> str:
    rels = set(rel.lower().split())
    if "sponsored" in rels:
        return "sponsored"
    if "ugc" in rels:
        return "ugc"
    if "nofollow" in rels:
        return "nofollow"
    return "dofollow"


def _bing_search(client: requests.Session, query: str, limit: int) -> list[str]:
    response = client.get(
        "https://www.bing.com/search",
        params={"q": query, "count": min(limit, 50)},
        timeout=DEFAULT_TIMEOUT,
    )
    response.raise_for_status()
    urls: list[str] = []
    for href in re.findall(r'<li[^>]*class="[^"]*b_algo[^"]*"[^>]*>.*?<a[^>]+href="([^"]+)"', response.text, re.I | re.S):
        if href.startswith("http://") or href.startswith("https://"):
            urls.append(html.unescape(href))
    return urls[:limit]


def _duckduckgo_search(client: requests.Session, query: str, limit: int) -> list[str]:
    response = client.get(
        "https://html.duckduckgo.com/html/",
        params={"q": query},
        timeout=DEFAULT_TIMEOUT,
    )
    response.raise_for_status()
    urls: list[str] = []
    for raw in re.findall(r'<a[^>]+class="result__a"[^>]+href="([^"]+)"', response.text, re.I):
        value = html.unescape(raw)
        parsed = urlparse(value)
        if parsed.netloc.endswith("duckduckgo.com") and "uddg" in parse_qs(parsed.query):
            value = parse_qs(parsed.query)["uddg"][0]
        value = unquote(value)
        if value.startswith("http://") or value.startswith("https://"):
            urls.append(value)
    return urls[:limit]


def discover_candidates(
    target_domain: str,
    *,
    engines: tuple[str, ...] = DEFAULT_ENGINES,
    max_results_per_engine: int = DEFAULT_MAX_RESULTS_PER_ENGINE,
) -> tuple[list[str], dict[str, str]]:
    """Discover external candidate pages that may link to target_domain."""
    target = normalize_domain(target_domain)
    query = f'"{target}" -site:{target}'
    candidates: list[str] = []
    errors: dict[str, str] = {}

    with requests.Session() as client:
        client.headers.update(
            {
                "User-Agent": "Mozilla/5.0 (compatible; SEO-Crawler/4.0; backlink-discovery)",
                "Accept-Language": "en-US,en;q=0.9",
            }
        )
        for engine in engines:
            try:
                if engine == "bing":
                    found = _bing_search(client, query, max_results_per_engine)
                elif engine == "duckduckgo":
                    found = _duckduckgo_search(client, query, max_results_per_engine)
                else:
                    errors[engine] = "unsupported_engine"
                    continue
                candidates.extend(found)
            except Exception as exc:
                errors[engine] = f"{type(exc).__name__}: {exc}"

    unique: list[str] = []
    seen: set[str] = set()
    for url in candidates:
        if not url or url in seen or is_target(url, target):
            continue
        seen.add(url)
        unique.append(url)
    return unique, errors


def verify_page(client: requests.Session, source_url: str, target_domain: str) -> list[dict]:
    """Fetch one external page and return only actual HTML links to target."""
    if is_target(source_url, target_domain):
        return []

    response = client.get(source_url, timeout=DEFAULT_TIMEOUT, allow_redirects=True)
    response.raise_for_status()
    final_source = response.url

    if is_target(final_source, target_domain):
        return []

    content_type = response.headers.get("content-type", "").lower()
    if content_type and "html" not in content_type and "xhtml" not in content_type:
        return []

    results: list[dict] = []
    for attrs, body in A_RE.findall(response.text):
        href_match = HREF_RE.search(attrs)
        if not href_match:
            continue
        target_url = urljoin(final_source, html.unescape(href_match.group(2)))
        if not is_target(target_url, target_domain):
            continue
        rel_match = REL_RE.search(attrs)
        rel = rel_match.group(2).strip() if rel_match else ""
        results.append(
            {
                "source_url": final_source,
                "source_domain": normalize_domain(final_source),
                "target_url": target_url,
                "anchor_text": clean_anchor(body),
                "rel": rel,
                "backlink_type": classify_rel(rel),
            }
        )
    return results


def collect_public_backlinks(
    target_domain: str,
    *,
    engines: tuple[str, ...] = DEFAULT_ENGINES,
    max_results_per_engine: int = DEFAULT_MAX_RESULTS_PER_ENGINE,
) -> dict:
    """Discover and verify external backlinks using public search engines."""
    target = normalize_domain(target_domain)
    if not target:
        return {"provider": "Public Web", "status": "invalid_target", "backlinks": []}

    candidates, discovery_errors = discover_candidates(
        target,
        engines=engines,
        max_results_per_engine=max_results_per_engine,
    )

    backlinks: list[dict] = []
    seen: set[tuple[str, str, str, str]] = set()
    verification_errors: dict[str, str] = {}

    with requests.Session() as client:
        client.headers.update(
            {"User-Agent": "Mozilla/5.0 (compatible; SEO-Crawler/4.0; backlink-verification)"}
        )
        for source_url in candidates:
            try:
                for item in verify_page(client, source_url, target):
                    key = (
                        item["source_url"],
                        item["target_url"],
                        item["anchor_text"],
                        item["rel"],
                    )
                    if key in seen:
                        continue
                    seen.add(key)
                    backlinks.append(item)
            except Exception as exc:
                verification_errors[source_url] = f"{type(exc).__name__}: {exc}"

    type_counts = Counter(item["backlink_type"] for item in backlinks)
    domains = {item["source_domain"] for item in backlinks}

    return {
        "provider": "Public Web",
        "target_domain": target,
        "status": "success",
        "discovery_query": f'"{target}" -site:{target}',
        "candidate_pages": len(candidates),
        "total_backlinks": len(backlinks),
        "referring_domains": len(domains),
        "backlink_types": dict(type_counts),
        "backlinks": backlinks,
        "discovery_errors": discovery_errors,
        "verification_errors": verification_errors,
        "message": "Search engines discover candidate pages; backlinks are counted only after the external page HTML contains an actual link to the target domain.",
    }
