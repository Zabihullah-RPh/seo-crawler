from __future__ import annotations

import argparse
import asyncio
import json
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def ts():
    return time.perf_counter()


def section(n, name):
    print(f"\n[{n}] {name}")
    print("-" * 60)


async def main(url: str, max_pages: int = 5):
    from app.crawler.production import ProductionCrawler, site_metadata
    from app.integrations.common_crawl_runtime import collect as collect_common_crawl
    from app.integrations.google_auth import get_credentials
    from app.integrations.google_enrichment import _ga4_result, _run_pagespeed
    from app.integrations.google_enrichment import enrich as enrich_google
    from app.storage.db import create_crawl, initialize

    timings = {}
    details = {}
    total_start = ts()
    await initialize()

    section(1, "CRAWLER / DISCOVERY + HTTP + ANALYSIS + REPORT")
    t = ts()
    crawl_id = await create_crawl(url, max_pages, 50, 5)
    crawler = ProductionCrawler(crawl_id=crawl_id, start_url=url, max_pages=max_pages, max_depth=50, concurrency=5)
    await crawler.run()
    timings["crawler_total"] = ts() - t
    details["crawl_id"] = crawl_id
    crawl_file = ROOT / "results" / f"crawl_{crawl_id}.json"
    data = json.loads(crawl_file.read_text(encoding="utf-8")) if crawl_file.exists() else {}
    details["pages"] = len(data.get("pages", []))
    details["links"] = len(data.get("links", []))
    details["images"] = len(data.get("images", []))
    print(f"Completed: {timings['crawler_total']:.2f}s | Pages: {details['pages']} | Links: {details['links']} | Images: {details['images']}")

    section(2, "COMMON CRAWL / PUBLIC EXTERNAL")
    t = ts()
    try:
        result = collect_common_crawl(url)
        details["common_crawl"] = result
        status = result.get("status", "DATA_NOT_AVAILABLE")
    except Exception as exc:
        status = "ERROR"
        details["common_crawl_error"] = f"{type(exc).__name__}: {exc}"
    timings["common_crawl"] = ts() - t
    print(f"Status: {status} | Layer time: {timings['common_crawl']:.2f}s")

    section(3, "DNS / RDAP / PUBLIC DOMAIN DATA")
    t = ts()
    try:
        result = site_metadata(url, {})
        details["domain_intelligence"] = result
        status = "PASS" if result.get("hostname") else "DATA_NOT_AVAILABLE"
    except Exception as exc:
        status = "ERROR"
        details["domain_intelligence_error"] = f"{type(exc).__name__}: {exc}"
    timings["dns_rdap"] = ts() - t
    print(f"Status: {status} | Layer time: {timings['dns_rdap']:.2f}s")

    section(4, "PAGESPEED INSIGHTS (PUBLIC)")
    t = ts()
    try:
        credentials = get_credentials()
        token = getattr(credentials, "token", None)
        result = _run_pagespeed(url, token)
        details["pagespeed"] = result
        status = result.get("status", "DATA_NOT_AVAILABLE")
    except Exception as exc:
        status = "ERROR"
        details["pagespeed_error"] = f"{type(exc).__name__}: {exc}"
    timings["pagespeed"] = ts() - t
    print(f"Status: {status} | Layer time: {timings['pagespeed']:.2f}s")

    section(5, "GOOGLE PRIVATE ENRICHMENT")
    t = ts()
    try:
        google = enrich_google(url)
        details["google"] = google
        for key, value in google.items():
            if isinstance(value, dict):
                print(f"{key}: {value.get('status', 'UNKNOWN')}")
    except Exception as exc:
        details["google_error"] = f"{type(exc).__name__}: {exc}"
    timings["google"] = ts() - t
    print(f"Layer time: {timings['google']:.2f}s")

    timings["total"] = ts() - total_start
    section(6, "TIMING SUMMARY")
    for name, seconds in timings.items():
        print(f"{name:24} {seconds:8.2f}s")
    slowest = max((k for k in timings if k != "total"), key=timings.get)
    print(f"\nBOTTLENECK: {slowest} ({timings[slowest]:.2f}s)")

    output = ROOT / "results" / "pipeline_diagnostic_5.json"
    output.write_text(json.dumps({"site": url, "max_pages": max_pages, "timings": timings, "details": details}, indent=2, default=str), encoding="utf-8")
    print(f"Diagnostic report: {output}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("url")
    parser.add_argument("--max-pages", type=int, default=5)
    args = parser.parse_args()
    asyncio.run(main(args.url, args.max_pages))
