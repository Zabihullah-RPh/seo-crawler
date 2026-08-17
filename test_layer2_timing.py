"""Benchmark Layer 2 with live throughput output."""
from __future__ import annotations

import argparse
import asyncio
import json
import time

from app.crawler.http import HTTPClient
from app.integrations.backlink_layer2 import investigate_referring_domain


async def main(source_domain: str, target_domain: str, timeout: int, progress_every: int) -> None:
    started = time.monotonic()
    last_print = 0
    print(f"Starting Layer 2 test: {source_domain} -> {target_domain}", flush=True)
    print(f"Time limit: {timeout}s | target: actual href only", flush=True)
    print("Concurrency: 32 | batch size: 32", flush=True)

    http = HTTPClient(concurrency=32)

    def progress(pages: int, elapsed: float, queue: int) -> None:
        nonlocal last_print
        if pages >= last_print + progress_every or pages == 1:
            last_print = pages
            rate = pages / elapsed if elapsed > 0 else 0.0
            print(f"PROGRESS | elapsed={elapsed:.1f}s | pages={pages} | rate={rate:.2f}/sec | queue={queue}", flush=True)

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

    elapsed = time.monotonic() - started
    pages = int(result.get("pages_checked") or 0)
    rate = pages / elapsed if elapsed > 0 else 0.0

    print("\n=== FINAL RESULT ===", flush=True)
    print(json.dumps(result, indent=2, ensure_ascii=False), flush=True)
    print(f"Domain: {source_domain}", flush=True)
    print(f"Target: {target_domain}", flush=True)
    print(f"Status: {result.get('status')}", flush=True)
    print(f"Elapsed seconds: {result.get('elapsed_seconds')}", flush=True)
    print(f"Pages checked: {pages}", flush=True)
    print(f"Average pages/sec: {rate:.2f}", flush=True)
    print(f"Links found: {result.get('links_found')}", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-domain", default="bluebook-directory.com")
    parser.add_argument("--target-domain", default="kingdrivingschool.com")
    parser.add_argument("--timeout", type=int, default=300)
    parser.add_argument("--progress-every", type=int, default=25)
    args = parser.parse_args()
    asyncio.run(main(args.source_domain, args.target_domain, args.timeout, args.progress_every))
