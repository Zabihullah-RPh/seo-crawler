import argparse
import asyncio
from collections import deque
from urllib.parse import urldefrag, urljoin, urlparse

from bs4 import BeautifulSoup

from app.crawler.http import HTTPClient
from app.utils.urls import normalize_url


def domain(url: str) -> str:
    host = (urlparse(url).hostname or "").lower().strip().rstrip(".")
    if host.startswith("www."):
        host = host[4:]
    return host


def same_domain_or_relative(raw_href: str, absolute_url: str, source_url: str) -> bool:
    raw = (raw_href or "").strip().lower()
    if not raw.startswith(("http://", "https://", "//")):
        return True
    return domain(absolute_url) == domain(source_url)


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-domain", required=True)
    parser.add_argument("--target-domain", required=True)
    parser.add_argument("--seconds", type=int, default=60)
    args = parser.parse_args()

    source = args.source_domain.lower().strip()
    target = args.target_domain.lower().strip().removeprefix("www.").rstrip(".")
    deadline = asyncio.get_running_loop().time() + args.seconds

    http = HTTPClient(concurrency=4)
    queue = deque([f"https://{source}/", f"http://{source}/"])
    seen = set()
    fetched = 0

    try:
        while queue and asyncio.get_running_loop().time() < deadline:
            raw = queue.popleft()
            url = normalize_url(raw)
            if not url or url in seen:
                continue
            seen.add(url)
            result = await http.get(url)
            response = result.get("response")
            if not response:
                print(f"FETCH FAIL | {url} | error={result.get('error')}")
                continue

            fetched += 1
            status = int(getattr(response, "status_code", 0) or 0)
            headers = dict(getattr(response, "headers", {}) or {})
            ctype = headers.get("content-type", "")
            final_url = str(getattr(response, "url", url))
            html = getattr(response, "text", "") or ""
            soup = BeautifulSoup(html, "html.parser")
            anchors = soup.find_all("a", href=True)
            same = []
            target_hits = []
            for tag in anchors:
                href = str(tag.get("href") or "").strip()
                if not href or href.startswith(("#", "javascript:", "mailto:", "tel:")):
                    continue
                absolute = normalize_url(urldefrag(urljoin(final_url, href))[0])
                if domain(absolute) == target:
                    target_hits.append(absolute)
                if same_domain_or_relative(href, absolute, final_url) and absolute not in seen:
                    same.append(absolute)

            print(f"FETCH | {url} | final={final_url} | status={status} | type={ctype} | links={len(anchors)} | internal_new={len(same)} | target_hits={len(target_hits)}")
            for hit in target_hits[:10]:
                print(f"TARGET | {final_url} -> {hit}")

            for nxt in same:
                if nxt not in queue:
                    queue.append(nxt)

        print("--- SUMMARY ---")
        print(f"pages_checked={fetched}")
        print(f"urls_seen={len(seen)}")
        print(f"queue_remaining={len(queue)}")
        print(f"elapsed_limit_seconds={args.seconds}")
    finally:
        await http.close()


if __name__ == "__main__":
    asyncio.run(main())
