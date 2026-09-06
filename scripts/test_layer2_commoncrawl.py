"""Experimental Common Crawl archive-based Layer 2 backlink test.

This test does NOT modify the production Layer 2 implementation.
It uses the public Common Crawl CDXJ index to find captured HTML pages on
known referring domains, then range-fetches only those WARC records and checks
for exact links to the target domain.

The index is queried sequentially with retries because Common Crawl rate-limits
parallel/repeated CDX requests. When a domain has no usable captures in the
latest crawl, the test can fall back to a small number of older crawls.

Usage:
    python -m scripts.test_layer2_commoncrawl https://avw.au

Optional:
    --domain example.com --domain example.org
    --crawl CC-MAIN-2026-34
    --fallback-crawls 2
    --pages 25
    --concurrency 8
"""
from __future__ import annotations

import argparse
import asyncio
import gzip
import json
import random
import time
from urllib.parse import urldefrag, urljoin, urlparse

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
USER_AGENT = "SEO-Crawler-Layer2-CommonCrawl-Test/1.1 (+https://index.commoncrawl.org/)"


def host(url: str) -> str:
    value = (urlparse(url).hostname or "").lower().rstrip(".")
    return value[4:] if value.startswith("www.") else value


def canonical(url: str) -> str:
    try:
        parsed = urlparse(url)
        scheme = parsed.scheme.lower() or "https"
        hostname = (parsed.hostname or "").lower()
        if not hostname:
            return url
        netloc = hostname
        if parsed.port:
            netloc += f":{parsed.port}"
        path = parsed.path or "/"
        result = f"{scheme}://{netloc}{path}"
        if parsed.query:
            result += f"?{parsed.query}"
        return result
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
        marker = b"\n\n"
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


async def latest_crawls(client: httpx.AsyncClient, count: int) -> list[str]:
    response = await client.get(f"{CC_INDEX_BASE}/collinfo.json")
    response.raise_for_status()
    payload = response.json()
    crawls = []
    for item in payload:
        crawl_id = str(item.get("id") or "").strip()
        if crawl_id:
            crawls.append(crawl_id)
    return crawls[: max(1, count)]


async def query_domain(client: httpx.AsyncClient, crawl: str, domain: str, limit: int, retries: int = 3) -> tuple[list[dict], str | None]:
    query_items = [
        ("url", f"{domain}/*"),
        ("matchType", "prefix"),
        ("output", "json"),
        ("filter", "status:200"),
        ("filter", "mime:text/html"),
        ("collapse", "urlkey"),
        ("limit", str(limit)),
    ]
    last_error = None
    for attempt in range(retries + 1):
        try:
            response = await client.get(f"{CC_INDEX_BASE}/{crawl}-index", params=query_items)
            if response.status_code == 404:
                return [], None
            if response.status_code in (429, 502, 503, 504):
                last_error = f"HTTP {response.status_code}"
                if attempt < retries:
                    await asyncio.sleep(2.0 * (2 ** attempt) + random.uniform(0.25, 0.75))
                    continue
            response.raise_for_status()
            records = []
            for line in response.text.splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    item = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if item.get("url") and item.get("filename") and item.get("offset") is not None and item.get("length") is not None:
                    records.append(item)
            return records[:limit], None
        except Exception as exc:
            last_error = f"{type(exc).__name__}: {exc}"
            if attempt < retries:
                await asyncio.sleep(2.0 * (2 ** attempt) + random.uniform(0.25, 0.75))
    return [], last_error


async def fetch_record(client: httpx.AsyncClient, record: dict) -> bytes | None:
    offset = int(record["offset"])
    length = int(record["length"])
    end = offset + length - 1
    url = f"{CC_DATA_BASE}/{record['filename']}"
    response = await client.get(url, headers={"Range": f"bytes={offset}-{end}"})
    if response.status_code not in (200, 206):
        return None
    return response.content


async def verify_domain(
    client: httpx.AsyncClient,
    crawls: list[str],
    source_domain: str,
    target_domain: str,
    page_limit: int,
    sem: asyncio.Semaphore,
    index_delay: float,
) -> dict:
    started = time.monotonic()
    attempted_crawls = []
    total_records = 0
    total_pages = 0
    all_errors = []

    for crawl_index, crawl in enumerate(crawls):
        if crawl_index:
            await asyncio.sleep(index_delay)
        attempted_crawls.append(crawl)
        records, error = await query_domain(client, crawl, source_domain, page_limit)
        if error:
            all_errors.append(f"{crawl}: {error}")
            continue
        total_records += len(records)
        if not records:
            continue

        found = []

        async def one(record: dict):
            async with sem:
                blob = await fetch_record(client, record)
            if not blob:
                return []
            try:
                parsed = extract_http_payload(blob)
            except (OSError, EOFError, gzip.BadGzipFile):
                return []
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
            total_pages += 1
            found.extend(item)
            if found:
                return {
                    "domain": source_domain,
                    "status": "confirmed",
                    "crawl": crawl,
                    "crawls_tried": attempted_crawls,
                    "records_returned": total_records,
                    "pages_sampled": total_pages,
                    "links_found": len(found),
                    "elapsed": round(time.monotonic() - started, 2),
                    "backlinks": found,
                    "errors": all_errors,
                }

    if total_records == 0 and all_errors:
        status = "index_unavailable"
    elif total_records == 0:
        status = "no_captures_found"
    else:
        status = "not_found_in_sample"
    return {
        "domain": source_domain,
        "status": status,
        "crawl": attempted_crawls[-1] if attempted_crawls else None,
        "crawls_tried": attempted_crawls,
        "records_returned": total_records,
        "pages_sampled": total_pages,
        "links_found": 0,
        "elapsed": round(time.monotonic() - started, 2),
        "backlinks": [],
        "errors": all_errors,
    }


async def run(args) -> int:
    target_domain = host(args.url)
    domains = args.domain or (DEFAULT_DOMAINS if target_domain == "avw.au" else [])
    if not domains:
        print("No referring domains supplied. For a custom target, use --domain repeatedly.")
        return 2

    started = time.monotonic()
    timeout = httpx.Timeout(30.0, connect=12.0)
    limits = httpx.Limits(max_connections=max(8, args.concurrency * 2), max_keepalive_connections=args.concurrency)
    async with httpx.AsyncClient(headers={"User-Agent": USER_AGENT}, timeout=timeout, follow_redirects=True, limits=limits) as client:
        if args.crawl:
            crawls = [args.crawl]
        else:
            crawls = await latest_crawls(client, args.fallback_crawls + 1)
        crawls = crawls[: args.fallback_crawls + 1]

        print("\n========== COMMON CRAWL LAYER 2 TEST ==========")
        print(f"Target:            {args.url}")
        print(f"Common Crawl:      {', '.join(crawls)}")
        print(f"Known domains:     {len(domains)}")
        print(f"Pages/domain:      {args.pages}")
        print(f"Concurrency:       {args.concurrency}")
        print(f"Index delay:       {args.index_delay}s")
        print("\nLayer 1 is not re-run; the known referring domains are used directly.")
        print("The test uses sequential CDX requests and older-crawl fallback when needed.")

        sem = asyncio.Semaphore(max(1, args.concurrency))
        results = []
        # Common Crawl explicitly asks clients not to issue multiple CDX requests
        # concurrently, so domain index lookups are intentionally sequential.
        for index, domain in enumerate(domains):
            if index:
                await asyncio.sleep(args.index_delay)
            results.append(await verify_domain(client, crawls, domain.lower(), target_domain, args.pages, sem, args.index_delay))

    print("\nPer-domain archive verification:")
    confirmed = []
    for item in results:
        extra = f" | errors={len(item.get('errors', []))}" if item.get("errors") else ""
        print(
            f"  - {item['domain']}: {item['status']} | records={item.get('records_returned', 0)} | "
            f"pages={item.get('pages_sampled', 0)} | links={item.get('links_found', 0)} | "
            f"crawls={','.join(item.get('crawls_tried', []))} | time={item.get('elapsed', 0)}s{extra}"
        )
        confirmed.extend(item.get("backlinks", []))
        for error in item.get("errors", []) or []:
            print(f"      ! {error}")

    print("\nConfirmed page-level backlinks:")
    for item in confirmed:
        print(
            f"  - {item['source_url']} -> {item['target_url']} "
            f"| anchor={item.get('anchor_text', '')!r} | rel={item.get('rel', '')!r} "
            f"| crawl={item.get('crawl', '')} | capture={item.get('capture_timestamp', '')}"
        )

    print(f"\nConfirmed links:   {len(confirmed)}")
    print(f"Total test time:   {time.monotonic() - started:.2f}s")
    print("===============================================\n")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Test page-level Layer 2 backlink confirmation using Common Crawl WARC records")
    parser.add_argument("url", help="Target website URL")
    parser.add_argument("--domain", action="append", help="Known referring domain; repeat for multiple domains")
    parser.add_argument("--crawl", help="Specific Common Crawl collection; default uses latest plus fallback crawls")
    parser.add_argument("--fallback-crawls", type=int, default=2, help="Older crawls to try after the latest crawl")
    parser.add_argument("--pages", type=int, default=25, help="Maximum captured HTML pages sampled per referring domain per crawl")
    parser.add_argument("--concurrency", type=int, default=8, help="Concurrent WARC range fetches")
    parser.add_argument("--index-delay", type=float, default=2.0, help="Seconds between Common Crawl index requests")
    args = parser.parse_args()
    args.pages = max(1, min(args.pages, 100))
    args.concurrency = max(1, min(args.concurrency, 16))
    args.fallback_crawls = max(0, min(args.fallback_crawls, 4))
    args.index_delay = max(1.0, min(args.index_delay, 10.0))
    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())
