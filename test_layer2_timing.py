"""Measure Layer 2 live-search coverage for one referring domain.

Example:
  python test_layer2_timing.py --source-domain bluebook-directory.com --target-domain kingdrivingschool.com
"""
from __future__ import annotations

import argparse
import asyncio
import json

from app.crawler.http import HTTPClient
from app.integrations.backlink_layer2 import investigate_referring_domain


async def main(source_domain: str, target_domain: str, timeout: int) -> None:
    http = HTTPClient(concurrency=8)
    try:
        result = await investigate_referring_domain(
            http,
            source_domain,
            target_domain,
            timeout_seconds=timeout,
        )
    finally:
        await http.close()

    print(json.dumps(result, indent=2, ensure_ascii=False))
    print()
    print(f"Domain: {source_domain}")
    print(f"Target: {target_domain}")
    print(f"Status: {result.get('status')}")
    print(f"Elapsed seconds: {result.get('elapsed_seconds')}")
    print(f"Pages checked: {result.get('pages_checked')}")
    print(f"Links found: {result.get('links_found')}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-domain", default="bluebook-directory.com")
    parser.add_argument("--target-domain", default="kingdrivingschool.com")
    parser.add_argument("--timeout", type=int, default=300)
    args = parser.parse_args()
    asyncio.run(main(args.source_domain, args.target_domain, args.timeout))
