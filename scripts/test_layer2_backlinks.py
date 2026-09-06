"""Standalone Layer 2 backlink verification test.

Runs Layer 1 discovery and the bounded Layer 2 verifier. Layer 2 results are
persisted under results/layer2_cache.json, so known referring domains are not
re-crawled on every run. Use --refresh to force a fresh verification.

Usage:
    python -m scripts.test_layer2_backlinks https://avw.au

Optional:
    --timeout 20
    --concurrency 16
    --refresh
"""
from __future__ import annotations

import argparse
import asyncio
import time

from app.integrations.backlink_layer2 import investigate_layer2
from app.integrations.common_crawl_runtime import collect


def main() -> int:
    parser = argparse.ArgumentParser(description="Test Common Crawl Layer 1 + cached Layer 2 backlink verification")
    parser.add_argument("url", help="Website URL, e.g. https://avw.au")
    parser.add_argument("--timeout", type=float, default=20.0, help="Maximum fresh Layer 2 seconds per referring domain")
    parser.add_argument("--concurrency", type=int, default=16, help="Concurrent fresh referring-domain checks")
    parser.add_argument("--refresh", action="store_true", help="Ignore cached Layer 2 results and verify every domain again")
    args = parser.parse_args()

    print("\n========== BACKLINK LAYER 2 TEST ==========")
    print(f"Target:      {args.url}")
    print(f"Timeout:     {args.timeout}s/domain")
    print(f"Concurrency: {args.concurrency}")
    print(f"Refresh:     {args.refresh}")

    started = time.monotonic()
    print("\n[1] Common Crawl Layer 1 — domain discovery")
    layer1_started = time.monotonic()
    try:
        layer1 = collect(args.url)
    except Exception as exc:
        print(f"Layer 1 FAILED: {type(exc).__name__}: {exc}")
        return 1
    print(f"Status:            {layer1.get('status')}")
    print(f"Graph release:     {layer1.get('graph_release', 'n/a')}")
    print(f"Referring domains: {layer1.get('referring_domains', 0)}")
    print(f"Layer 1 time:      {time.monotonic() - layer1_started:.2f}s")

    if layer1.get("status") != "success":
        print("\nLayer 2 SKIPPED because Layer 1 did not provide referring domains.")
        print(f"Message: {layer1.get('message', '')}")
        print("==========================================\n")
        return 0

    domains = sorted({str(x.get("referring_domain") or "") for x in layer1.get("backlinks", []) if x.get("referring_domain")})
    print("\nLayer 1 domains:")
    for domain in domains:
        print(f"  - {domain}")

    print("\n[2] Layer 2 — cached/bounded page verification")
    layer2_started = time.monotonic()
    try:
        layer2 = asyncio.run(
            investigate_layer2(
                args.url,
                layer1,
                timeout_seconds=args.timeout,
                concurrency=args.concurrency,
                refresh=args.refresh,
            )
        )
    except Exception as exc:
        print(f"Layer 2 FAILED: {type(exc).__name__}: {exc}")
        return 1

    layer2_time = time.monotonic() - layer2_started
    print(f"Status:            {layer2.get('status')}")
    print(f"Fresh checks:      {layer2.get('domains_investigated', 0)}")
    print(f"Cached reused:     {layer2.get('domains_reused', 0)}")
    print(f"Confirmed links:   {layer2.get('links_found', 0)}")
    print(f"Layer 2 time:      {layer2_time:.2f}s")
    print(f"Cache:             {layer2.get('cache', {}).get('path', 'n/a')}")

    print("\nPer-domain results:")
    for item in layer2.get("domains", []) or []:
        print(
            f"  - {item.get('referring_domain')}: "
            f"{item.get('status')} | cache={'hit' if item.get('cache_hit') else 'miss'} | "
            f"pages={item.get('pages_checked', 0)} | links={item.get('links_found', 0)} | "
            f"time={item.get('elapsed_seconds', 0)}s"
        )

    print("\nConfirmed backlinks:")
    for item in layer2.get("backlinks", []) or []:
        print(
            f"  - {item.get('source_url')} -> {item.get('target_url')} "
            f"| anchor={item.get('anchor_text', '')!r} "
            f"| rel={item.get('rel', '')!r}"
        )

    print(f"\nTotal test time: {time.monotonic() - started:.2f}s")
    print("==========================================\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
