"""Measure Layer 2 live-search coverage with visible progress.

Example:
  python test_layer2_timing.py --source-domain bluebook-directory.com --target-domain kingdrivingschool.com --timeout 500
"""
from __future__ import annotations

import argparse
import asyncio
import json
import time

from app.crawler.http import HTTPClient
from app.integrations.backlink_layer2 import investigate_referring_domain


async def main(source_domain: str, target_domain: str, timeout: int, progress_every: int) -> None:
    started = time.monotonic()
    print(f"Starting Layer 2 test: {source_domain} -> {target_domain}", flush=True)
    print(f"Time limit: {timeout}s | target: actual href only", flush=True)
    print(f"Concurrency: 8 | progress interval: every {progress_every} pages", flush=True)

    http = HTTPClient(concurrency=8)
    last_reported = 0
    last_report_time = started

    def progress(pages: int, elapsed: float, queue_remaining: int) -> None:
        nonlocal last_reported, last_report_time
        now = time.monotonic()
        if pages == last_reported:
            return
        if pages - last_reported < progress_every and (now - last_report_time) < 5:
            return
        rate = pages / elapsed if elapsed > 0 else 0.0
        print(
            f"PROGRESS | elapsed={elapsed:.1f}s | pages={pages} | "
            f"rate={rate:.2f}/sec | queue={queue_remaining}",
            flush=True,
        )
        last_reported = pages
        last_report_time = now

    try:
        result = await investigate_referring_domain(
            http,
            source_domain,
            target_domain,
            timeout_seconds=timeout,
            progress_callback=progress,
        )
    finally:
        await http.close()

    elapsed = float(result.get("elapsed_seconds") or (time.monotonic() - started))
    pages = int(result.get("pages_checked") or 0)
    rate = pages / elapsed if elapsed > 0 else 0.0

    print("\n=== FINAL RESULT ===", flush=True)
    print(json.dumps(result, indent=2, ensure_ascii=False), flush=True)
    print(f"Domain: {source_domain}", flush=True)
    print(f"Target: {target_domain}", flush=True)
    print(f"Status: {result.get('status')}", flush=True)
    print(f"Elapsed seconds: {elapsed:.2f}", flush=True)
    print(f"Pages checked: {pages}", flush=True)
    print(f"Average pages/sec: {rate:.2f}", flush=True)
    print(f"Links found: {result.get('links_found')}", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-domain", default="bluebook-directory.com")
    parser.add_argument("--target-domain", default="kingdrivingschool.com")
    parser.add_argument("--timeout", type=int, default=300)
    parser.add_argument("--progress-every", type=int, default=100)
    args = parser.parse_args()
    asyncio.run(main(args.source_domain, args.target_domain, args.timeout, args.progress_every))
