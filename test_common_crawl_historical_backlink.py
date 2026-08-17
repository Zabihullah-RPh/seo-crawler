from __future__ import annotations

import argparse
import gzip
import io
import json
import re
from urllib.parse import quote_plus

import httpx

CDX_BASE = "https://index.commoncrawl.org"
DATA_BASE = "https://data.commoncrawl.org/"
DEFAULT_CRAWLS = [
    "CC-MAIN-2026-30",
    "CC-MAIN-2026-25",
    "CC-MAIN-2026-21",
]


def target_host(value: str) -> str:
    value = value.strip().lower()
    value = re.sub(r"^https?://", "", value)
    value = value.split("/", 1)[0].split(":", 1)[0].rstrip(".")
    if value.startswith("www."):
        value = value[4:]
    return value


def extract_html_from_warc(raw: bytes) -> str:
    data = gzip.decompress(raw)
    marker = b"\r\n\r\n"
    first = data.find(marker)
    if first < 0:
        marker = b"\n\n"
        first = data.find(marker)
    if first < 0:
        return data.decode("utf-8", errors="replace")
    payload = data[first + len(marker):]
    return payload.decode("utf-8", errors="replace")


def find_target_hrefs(html: str, target: str) -> list[str]:
    hits = []
    pattern = re.compile(r"<a\b[^>]*?href\s*=\s*[\"']([^\"']+)[\"']", re.I)
    target = target_host(target)
    for href in pattern.findall(html):
        h = target_host(href)
        if h == target or h.endswith("." + target):
            hits.append(href)
    return hits


def query_index(client: httpx.Client, crawl: str, domain: str, limit: int) -> list[dict]:
    url = f"{CDX_BASE}/{crawl}-index"
    params = {
        "url": f"{domain}/*",
        "output": "json",
        "filter": "status:200",
        "collapse": "urlkey",
        "limit": str(limit),
    }
    r = client.get(url, params=params, timeout=30)
    r.raise_for_status()
    rows = []
    for line in r.text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def fetch_record(client: httpx.Client, row: dict) -> bytes:
    filename = row["filename"]
    offset = int(row["offset"])
    length = int(row["length"])
    start = offset
    end = offset + length - 1
    url = DATA_BASE + filename
    r = client.get(url, headers={"Range": f"bytes={start}-{end}"}, timeout=60)
    r.raise_for_status()
    return r.content


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source-domain", required=True)
    ap.add_argument("--target-domain", required=True)
    ap.add_argument("--limit", type=int, default=500)
    ap.add_argument("--crawls", nargs="*", default=DEFAULT_CRAWLS)
    args = ap.parse_args()

    source = target_host(args.source_domain)
    target = target_host(args.target_domain)
    print(f"Historical Common Crawl test: {source} -> {target}", flush=True)
    print(f"Crawls: {', '.join(args.crawls)} | index limit/crawl: {args.limit}", flush=True)

    seen_urls: set[str] = set()
    checked = 0
    with httpx.Client(follow_redirects=True, headers={"User-Agent": "seo-crawler-historical-backlink/1.0"}) as client:
        for crawl in args.crawls:
            print(f"\nINDEX | {crawl}", flush=True)
            try:
                rows = query_index(client, crawl, source, args.limit)
            except Exception as exc:
                print(f"INDEX ERROR | {crawl} | {type(exc).__name__}: {exc}", flush=True)
                continue
            print(f"INDEX ROWS | {len(rows)}", flush=True)

            for row in rows:
                page = str(row.get("url") or "")
                if not page or page in seen_urls:
                    continue
                seen_urls.add(page)
                checked += 1
                print(f"CHECK | {checked} | {crawl} | {page}", flush=True)
                try:
                    raw = fetch_record(client, row)
                    html = extract_html_from_warc(raw)
                    hits = find_target_hrefs(html, target)
                    if hits:
                        print("\n=== HISTORICAL BACKLINK FOUND ===", flush=True)
                        print(json.dumps({
                            "status": "confirmed_historical",
                            "crawl": crawl,
                            "source_url": page,
                            "target_domain": target,
                            "target_hrefs": hits,
                            "timestamp": row.get("timestamp"),
                            "filename": row.get("filename"),
                            "offset": row.get("offset"),
                            "length": row.get("length"),
                        }, indent=2, ensure_ascii=False), flush=True)
                        return
                except Exception as exc:
                    print(f"RECORD ERROR | {page} | {type(exc).__name__}: {exc}", flush=True)

    print("\n=== NO HISTORICAL MATCH FOUND ===", flush=True)
    print(json.dumps({
        "status": "not_found",
        "source_domain": source,
        "target_domain": target,
        "pages_checked": checked,
        "crawls_checked": args.crawls,
    }, indent=2), flush=True)


if __name__ == "__main__":
    main()
