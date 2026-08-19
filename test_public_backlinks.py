from __future__ import annotations

import argparse
import json

from app.integrations.public_backlink_discovery import collect_public_backlinks


parser = argparse.ArgumentParser(description="Discover and verify external backlinks from public search results.")
parser.add_argument("target", help="Target domain or URL, e.g. https://avw.au/")
parser.add_argument("--limit", type=int, default=50, help="Maximum candidate result pages per search engine")
args = parser.parse_args()

result = collect_public_backlinks(
    args.target,
    max_results_per_engine=max(1, args.limit),
)

print(json.dumps(result, indent=2, ensure_ascii=False))
