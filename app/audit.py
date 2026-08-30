from __future__ import annotations

import argparse
import asyncio
import json
import re
from pathlib import Path
from urllib.parse import urlparse

from app.audit_engine import generate_report
from app.crawler.production import ProductionCrawler
from app.integrations.google_enrichment import enrich as enrich_google
from app.report.pipeline_report import write_pipeline_report
from app.storage.db import create_crawl, initialize

ROOT = Path(__file__).resolve().parents[1]
RESULTS_DIR = ROOT / "results"


def _safe_name(url: str) -> str:
    host = urlparse(url).hostname or "site"
    name = re.sub(r"[^A-Za-z0-9._-]+", "_", host)
    return name or "site"


def _status(value: object, default: str = "DATA_NOT_AVAILABLE") -> str:
    return value if isinstance(value, str) else default


async def run_pipeline(url: str, max_pages: int = 100000, max_depth: int = 50, concurrency: int = 20) -> Path:
    await initialize()
    crawl_id = await create_crawl(url, max_pages, max_depth, concurrency)
    crawler = ProductionCrawler(crawl_id=crawl_id, start_url=url, max_pages=max_pages, max_depth=max_depth, concurrency=concurrency)
    await crawler.run()

    crawl_report = RESULTS_DIR / f"crawl_{crawl_id}.json"
    if not crawl_report.exists():
        raise RuntimeError(f"Crawler completed but did not create {crawl_report}")

    data = json.loads(crawl_report.read_text(encoding="utf-8"))
    google = enrich_google(crawler.start_url)

    public_layer = {
        "status": "PASS",
        "crawler": "PASS",
        "common_crawl": _status(data.get("backlinks", {}).get("layer1", {}).get("status")),
        "dns_rdap": "PASS" if data.get("site", {}).get("domain") else "DATA_NOT_AVAILABLE",
        "pagespeed": _status(google.get("pagespeed", {}).get("status")),
    }
    private_google = {name: _status(google.get(name, {}).get("status"), "NOT_CONFIGURED" if name == "ga4" else "DATA_NOT_AVAILABLE") for name in ("search_console", "sitemaps", "url_inspection", "search_analytics", "ga4")}
    data["pipeline"] = {"status": "PASS", "layers": {"site_crawl": "PASS", "public_external": public_layer, "private_google_enrichment": private_google}}
    data["google_enrichment"] = google

    output = RESULTS_DIR / f"audit_{_safe_name(crawler.start_url)}.json"
    output.write_text(json.dumps(data, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    generated_html = generate_report(data, output)
    final_html = RESULTS_DIR / f"pipeline_report_{crawl_id}.html"
    write_pipeline_report(generated_html, final_html, google)
    print(f"Final HTML report: {final_html}")
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a complete general-purpose SEO audit.")
    parser.add_argument("url", help="Public website URL to audit")
    parser.add_argument("--max-pages", type=int, default=100000)
    parser.add_argument("--max-depth", type=int, default=50)
    parser.add_argument("--concurrency", type=int, default=20)
    args = parser.parse_args()
    output = asyncio.run(run_pipeline(args.url, max_pages=args.max_pages, max_depth=args.max_depth, concurrency=args.concurrency))
    data = json.loads(output.read_text(encoding="utf-8"))
    print("\n========== SEO AUDIT PIPELINE COMPLETE ==========")
    print(f"Site:   {args.url}")
    print(f"JSON:   {output}")
    print(f"Pages:  {len(data.get('pages', []))}")
    print(f"Links:  {len(data.get('links', []))}")
    print(f"Images: {len(data.get('images', []))}")
    public = data.get("pipeline", {}).get("layers", {}).get("public_external", {})
    print(f"Common Crawl: {public.get('common_crawl', 'DATA_NOT_AVAILABLE')}")
    print(f"PageSpeed:    {public.get('pagespeed', 'DATA_NOT_AVAILABLE')}")
    private = data.get("pipeline", {}).get("layers", {}).get("private_google_enrichment", {})
    for name in ("search_console", "sitemaps", "url_inspection", "search_analytics", "ga4"):
        print(f"Google {name}: {private.get(name, 'DATA_NOT_AVAILABLE')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
