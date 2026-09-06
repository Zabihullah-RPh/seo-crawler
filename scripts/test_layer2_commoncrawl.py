"""Experimental Common Crawl archive-based Layer 2 backlink test.

This test does NOT modify the production Layer 2 implementation.
It uses the public Common Crawl CDXJ index to find captured HTML pages on
known referring domains, then range-fetches only those WARC records and checks
for exact links to the target domain.

Usage:
    python -m scripts.test_layer2_commoncrawl https://avw.au

For AVW the known Layer 1 domains are used by default so Layer 1 does not need
another run. Supply --domain multiple times for another target.

Optional:
    --domain example.com --domain example.org
    --crawl CC-MAIN-2026-25
    --pages 25
    --concurrency 8
"""
from __future__ import annotations

import argparse
import asyncio
import gzip
import json
import re
import time
from urllib.parse import quote_plus, urldefrag, urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

DEFAULT_DOMAINS = [
    "australianveterinarywholesalers.com.au",
    "hissestopurrs.org.au",
    "nugard.au",
    "vetfind.com.au",
    "wagfriendly.app",
]
CC_INDEX_BASE = "https://index.commoncrawl.org"
CC_DATA_BASE = "https://data.commoncrawl.org"
USER_AGENT = "SEO-Crawler-Layer2-CommonCrawl-Test/1.0"


def host(url: str) -> str:
    value = (urlparse(url).hostname or "").lower().rstrip(".")
    return value[4:] if value.startswith("www.") else value


def canonical(url: str) -> str:
    try:
        scheme = urlparse(url).scheme.lower() or "https"
        parsed = urlparse(url)
        hostname = (parsed.hostname or "").lower()
        if not hostname:
            return url
        netloc = hostname
        if parsed.port:
            netloc += f":{parsed.port}"
        path = parsed.path or "/"
        return f"{scheme}://{netloc}{path}" + (f"?{parsed.query}" if parsed.query else "")
    except Exception:
        return url


def extract_links(html: str, source_url: str, target_domain: str) -> list[dict]:
    soup = BeautifulSoup(html or "", "html.parser")
    found = []
    seen = set()
    for tag in soup.find_all("a", href=True):
        raw = str(tag.get("href") or "").strip()
        if not raw or raw.startswith(("#", "javascript:", "mailto:", "tel:")):
            continue
        target = canonical(urldefrag(urljoin(source_url, raw))[0])
        if host(target) != target_domain or target in seen:
            continue
        seen.add(target)
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


def extract_http_payload(warc_gzip: bytes) -> tuple[str, str] | None:
    raw = gzip.decompress(warc_gzip)
    marker = b"\r\n\r\n"
    first = raw.find(marker)
    if first < 0:
        return None
    remainder = raw[first + len(marker):]
    second = remainder.find(marker)
    if second < 0:
        return None
    http_headers = remainder[:second].decode("latin-1", errors="replace")
    body = remainder[second + len(marker):]
    content_type = ""
    for line in http_headers.splitlines():
        if line.lower().startswith("content-type:"):
            content_type = line.split(":", 1)[1].strip().lower()
            break
    return content_type, body.decode("utf-8", errors="replace")


async def latest_crawl(client: httpx.AsyncClient) -> str:
    response = await client.get(f"{CC_INDEX_BASE}/collinfo.json")
    response.raise_for_status()
    payload = response.json()
    for item in payload:
        crawl_id = str(item.get("id") or "").strip()
        if crawl_id:
            return crawl_id
    raise RuntimeError("Common Crawl did not return a crawl collection.")


async def query_domain(client: httpx.AsyncClient, crawl: str, domain: str, limit: int) -> list[dict]:
    params = {
        "url": f"{domain}/*",
        "output": "json",
        "filter": ["status:200", "mime:text/html"],
        "collapse": "urlkey",
        "limit": str(limit),
    }
    # The index API expects repeated filter= parameters; httpx handles a list of tuples.
    query_items = [
        ("url", f"{domain}/*"),
        ("output", "json"),
        ("filter", "status:200"),
        ("filter", "mime:text/html"),
        ("collapse", "urlkey"),
        ("limit", str(limit)),
    ]
    response = await client.get(f"{CC_INDEX_BASE}/{crawl}-index", params=query_items)
    if response.status_code == 404:
        return []
    response.raise_for_status()
    records = []
    for line in response.text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            item = json.loads(line)
            if item.get("url") and item.get("filename") and item.get("offset") and item.get("length"):
                records.append(item)
        except json.JSONDecodeError:
            continue
    return records[:limit]


async def fetch_record(client: httpx.AsyncClient, record: dict) -> bytes | None:
    offset = int(record["offset"])
    length = int(record["length"])
    end = offset + length - 1
    url = f"{CC_DATA_BASE}/{record['filename']}"
    response = await client.get(url, headers={"Range": f"bytes={offset}-{end}"})
    if response.status_code not in (200, 206):
        return None
    return response.content


async def verify_domain(client: httpx.AsyncClient, crawl: str, source_domain: str, target_domain: str, page_limit: int, sem: asyncio.Semaphore) -> dict:
    started = time.monotonic()
    try:
        records = await query_domain(client, crawl, source_domain, page_limit)
    except Exception as exc:
        return {"domain": source_domain, "status": "index_error", "pages_sampled": 0, "links_found": 0, "elapsed": round(time.monotonic() - started, 2), "backlinks": [], "error": f"{type(exc).__name__}: {exc}"}

    found = []
    pages_sampled = 0
    async def one(record: dict):
        async with sem:
            blob = await fetch_record(client, record)
        if not blob:
            return []
        parsed = extract_http_payload(blob)
        if not parsed:
            return []
        content_type, html = parsed
        if "html" not in content_type:
            return []
        source_url = canonical(str(record.get("url") or ""))
        return [
            {**hit, "found_via": "common_crawl_warc", "crawl": crawl, "capture_timestamp": record.get("timestamp")}
            for hit in extract_links(html, source_url, target_domain)
        ]

    results = await asyncio.gather(*(one(record) for record in records), return_exceptions=True)
    for item in results:
        if isinstance(item, Exception):
            continue
        pages_sampled += 1
        found.extend(item)
        if found:
            break

    return {
        "domain": source_domain,
        "status": "confirmed" if found else "not_found_in_sample",
        "pages_sampled": pages_sampled,
        "records_returned": len(records),
        "links_found": len(found),
        "elapsed": round(time.monotonic() - started, 2),
        "backlinks": found,
    }


async def run(args) -> int:
    target_domain = host(args.url)
    domains = args.domain or (DEFAULT_DOMAINS if target_domain == "avw.au" else [])
    if not domains:
        print("No referring domains supplied. For a custom target, use --domain repeatedly.")
        return 2

    started = time.monotonic()
    timeout = httpx.Timeout(20.0, connect=10.0)
    limits = httpx.Limits(max_connections=max(8, args.concurrency * 2), max_keepalive_connections=args.concurrency)
    async with httpx.AsyncClient(headers={"User-Agent": USER_AGENT}, timeout=timeout, follow_redirects=True, limits=limits) as client:
        crawl = args.crawl or await latest_crawl(client)
        print("\n========== COMMON CRAWL LAYER 2 TEST ==========")
        print(f"Target:            {args.url}")
        print(f"Common Crawl:      {crawl}")
        print(f"Known domains:     {len(domains)}")
        print(f"Pages/domain:      {args.pages}")
        print(f"Concurrency:       {args.concurrency}")
        print("\nLayer 1 is not re-run; the known referring domains are used directly.")

        sem = asyncio.Semaphore(max(1, args.concurrency))
        results = await asyncio.gather(*(verify_domain(client, crawl, d.lower(), target_domain, args.pages, sem) for d in domains))

    print("\nPer-domain archive verification:")
    confirmed = []
    for item in results:
        print(
            f"  - {item['domain']}: {item['status']} | records={item.get('records_returned', 0)} | "
            f"pages={item.get('pages_sampled', 0)} | links={item.get('links_found', 0)} | time={item.get('elapsed', 0)}s"
        )
        confirmed.extend(item.get("backlinks", []))

    print("\nConfirmed page-level backlinks:")
    for item in confirmed:
        print(
            f"  - {item['source_url']} -> {item['target_url']} "
            f"| anchor={item.get('anchor_text', '')!r} | rel={item.get('rel', '')!r} "
            f"| capture={item.get('capture_timestamp', '')}"
        )

    print(f"\nConfirmed links:   {len(confirmed)}")
    print(f"Total test time:   {time.monotonic() - started:.2f}s")
    print("===============================================\n")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Test page-level Layer 2 backlink confirmation using Common Crawl WARC records")
    parser.add_argument("url", help="Target website URL")
    parser.add_argument("--domain", action="append", help="Known referring domain; repeat for multiple domains")
    parser.add_argument("--crawl", help="Common Crawl collection, e.g. CC-MAIN-2026-25; default is latest")
    parser.add_argument("--pages", type=int, default=25, help="Maximum captured HTML pages sampled per referring domain")
    parser.add_argument("--concurrency", type=int, default=8, help="Concurrent WARC range fetches")
    args = parser.parse_args()
    args.pages = max(1, min(args.pages, 100))
    args.concurrency = max(1, min(args.concurrency, 32))
    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())
