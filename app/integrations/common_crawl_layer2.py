"""Common Crawl archive-first Layer 2 backlink verification.

Layer 1 supplies referring domains from the local Common Crawl web graph.
This module verifies exact page-level backlinks from Common Crawl CDXJ/WARC
captures without crawling the referring websites live.
"""
from __future__ import annotations

import asyncio
import gzip
import json
import random
import time
from urllib.parse import urldefrag, urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

CC_INDEX_BASE = "https://index.commoncrawl.org"
CC_DATA_BASE = "https://data.commoncrawl.org"
USER_AGENT = "SEO-Crawler-Layer2-CommonCrawl/1.4 (+https://index.commoncrawl.org/)"
DEFAULT_FALLBACK_CRAWLS = 2
DEFAULT_PAGES_PER_DOMAIN = 25
DEFAULT_WARC_CONCURRENCY = 8
DEFAULT_INDEX_DELAY = 2.0


def _host(url: str) -> str:
    value = (urlparse(url).hostname or "").lower().rstrip(".")
    return value[4:] if value.startswith("www.") else value


def _canonical(url: str) -> str:
    try:
        parsed = urlparse(url)
        scheme = parsed.scheme.lower() or "https"
        hostname = (parsed.hostname or "").lower()
        if not hostname:
            return url
        netloc = hostname
        if parsed.port:
            netloc += f":{parsed.port}"
        result = f"{scheme}://{netloc}{parsed.path or '/'}"
        if parsed.query:
            result += f"?{parsed.query}"
        return result
    except Exception:
        return url


def _extract_links(html: str, source_url: str, target_domain: str) -> list[dict]:
    soup = BeautifulSoup(html or "", "html.parser")
    found = []
    seen = set()
    for tag in soup.find_all("a", href=True):
        raw = str(tag.get("href") or "").strip()
        if not raw or raw.startswith(("#", "javascript:", "mailto:", "tel:")):
            continue
        target = _canonical(urldefrag(urljoin(source_url, raw))[0])
        if _host(target) != target_domain or target in seen:
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
            "found_via": "common_crawl_warc",
        })
    return found


def _extract_http_payload(blob: bytes) -> tuple[str, str] | None:
    raw = gzip.decompress(blob)
    for marker in (b"\r\n\r\n", b"\n\n"):
        first = raw.find(marker)
        if first < 0:
            continue
        remainder = raw[first + len(marker):]
        second = remainder.find(marker)
        if second < 0:
            continue
        http_headers = remainder[:second].decode("latin-1", errors="replace")
        body = remainder[second + len(marker):]
        content_type = ""
        for line in http_headers.splitlines():
            if line.lower().startswith("content-type:"):
                content_type = line.split(":", 1)[1].strip().lower()
                break
        return content_type, body.decode("utf-8", errors="replace")
    return None


async def _latest_crawls(client: httpx.AsyncClient, count: int) -> list[str]:
    response = await client.get(f"{CC_INDEX_BASE}/collinfo.json")
    response.raise_for_status()
    payload = response.json()
    crawls = []
    for item in payload:
        crawl_id = str(item.get("id") or "").strip()
        if crawl_id:
            crawls.append(crawl_id)
    return crawls[:max(1, count)]


async def _query_form(
    client: httpx.AsyncClient,
    crawl: str,
    domain: str,
    limit: int,
    form: int,
    retries: int = 3,
) -> tuple[list[dict], str | None]:
    query_forms = [
        [("url", domain), ("matchType", "domain")],
        [("url", f"*.{domain}")],
        [("url", f"{domain}/*"), ("matchType", "prefix")],
    ]
    query_items = query_forms[form] + [
        ("output", "json"),
        ("collapse", "urlkey"),
        ("limit", str(limit)),
    ]
    last_error = None

    for attempt in range(retries + 1):
        try:
            response = await client.get(
                f"{CC_INDEX_BASE}/{crawl}-index",
                params=query_items,
            )

            if response.status_code == 404:
                return [], None

            if response.status_code in (429, 502, 503, 504):
                last_error = f"HTTP {response.status_code}"
                if attempt < retries:
                    await asyncio.sleep(
                        2.0 * (2 ** attempt)
                        + random.uniform(0.25, 0.75)
                    )
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
                if (
                    item.get("url")
                    and item.get("filename")
                    and item.get("offset") is not None
                    and item.get("length") is not None
                ):
                    records.append(item)

            if records:
                return records[:limit], f"form={form + 1}"

            return [], None

        except Exception as exc:
            last_error = f"{type(exc).__name__}: {exc}"
            if attempt < retries:
                await asyncio.sleep(
                    2.0 * (2 ** attempt)
                    + random.uniform(0.25, 0.75)
                )

    return [], last_error


async def _query_domain(
    client: httpx.AsyncClient,
    crawls: list[str],
    domain: str,
    limit: int,
    delay: float,
):
    errors = []
    tried = []
    forms = (0, 1, 2)

    for crawl_index, crawl in enumerate(crawls):
        if crawl_index:
            await asyncio.sleep(delay)
        tried.append(crawl)

        for form in forms:
            records, query_form = await _query_form(
                client,
                crawl,
                domain,
                limit,
                form,
            )
            if query_form is None and not records:
                # _query_form only returns an error string as the second item
                # when the index request itself failed.
                continue
            if isinstance(query_form, str) and query_form.startswith("form="):
                return records, crawl, query_form, errors, tried

        # Continue to older crawls if this crawl returned no captures.

    return [], None, None, errors, tried


async def _fetch_record(
    client: httpx.AsyncClient,
    record: dict,
) -> bytes | None:
    offset = int(record["offset"])
    length = int(record["length"])
    response = await client.get(
        f"{CC_DATA_BASE}/{record['filename']}",
        headers={
            "Range": f"bytes={offset}-{offset + length - 1}"
        },
    )
    if response.status_code not in (200, 206):
        return None
    return response.content


async def _inspect_record(
    client: httpx.AsyncClient,
    record: dict,
    crawl: str,
    target_domain: str,
    sem: asyncio.Semaphore,
) -> list[dict]:
    async with sem:
        blob = await _fetch_record(client, record)
    if not blob:
        return []

    try:
        parsed = _extract_http_payload(blob)
    except (OSError, EOFError, gzip.BadGzipFile):
        return []

    if not parsed:
        return []

    content_type, html = parsed
    looks_html = (
        "html" in content_type
        or "<html" in html[:1000].lower()
        or "<a " in html[:2000].lower()
    )
    if not looks_html:
        return []

    source_url = _canonical(str(record.get("url") or ""))
    return [
        {
            **hit,
            "crawl": crawl,
            "capture_timestamp": record.get("timestamp"),
        }
        for hit in _extract_links(
            html,
            source_url,
            target_domain,
        )
    ]


async def _verify_domain(
    client: httpx.AsyncClient,
    crawls: list[str],
    source_domain: str,
    target_domain: str,
    pages_limit: int,
    sem: asyncio.Semaphore,
    delay: float,
) -> dict:
    started = time.monotonic()

    records, crawl, query_form, errors, tried = await _query_domain(
        client,
        crawls,
        source_domain,
        pages_limit,
        delay,
    )

    if not records:
        return {
            "referring_domain": source_domain,
            "status": "no_captures_found" if not errors else "index_unavailable",
            "crawls_tried": tried,
            "query_form": query_form,
            "records_returned": 0,
            "pages_sampled": 0,
            "links_found": 0,
            "elapsed_seconds": round(time.monotonic() - started, 2),
            "backlinks": [],
            "errors": errors,
        }

    tasks = [
        asyncio.create_task(
            _inspect_record(
                client,
                record,
                crawl,
                target_domain,
                sem,
            )
        )
        for record in records
    ]

    links = []
    completed = 0

    try:
        for task in asyncio.as_completed(tasks):
            try:
                hits = await task
            except Exception:
                hits = []

            completed += 1

            if hits:
                links.extend(hits)
                break
    finally:
        pending = [task for task in tasks if not task.done()]
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(
                *pending,
                return_exceptions=True,
            )

    return {
        "referring_domain": source_domain,
        "status": "confirmed" if links else "not_found_in_sample",
        "crawls_tried": tried,
        "query_form": query_form,
        "records_returned": len(records),
        "pages_sampled": completed,
        "links_found": len(links),
        "elapsed_seconds": round(time.monotonic() - started, 2),
        "backlinks": links,
        "errors": errors,
    }


async def investigate_layer2(
    url: str,
    layer1: dict,
    *,
    fallback_crawls: int = DEFAULT_FALLBACK_CRAWLS,
    pages_per_domain: int = DEFAULT_PAGES_PER_DOMAIN,
    concurrency: int = DEFAULT_WARC_CONCURRENCY,
    index_delay: float = DEFAULT_INDEX_DELAY,
) -> dict:
    target_domain = _host(url)

    domains = []
    seen = set()

    for item in layer1.get("backlinks", []) or []:
        domain = str(
            item.get("referring_domain") or ""
        ).strip().lower()

        if (
            domain
            and domain != target_domain
            and domain not in seen
        ):
            seen.add(domain)
            domains.append(domain)

    if not domains:
        return {
            "status": "no_referring_domains",
            "provider": "Common Crawl",
            "target_domain": target_domain,
            "layer1_referring_domains": 0,
            "domains_investigated": 0,
            "links_found": 0,
            "backlinks": [],
        }

    fallback_crawls = max(0, min(int(fallback_crawls), 4))
    pages_per_domain = max(1, min(int(pages_per_domain), 100))
    concurrency = max(1, min(int(concurrency), 16))
    index_delay = max(1.0, min(float(index_delay), 10.0))

    timeout = httpx.Timeout(
        30.0,
        connect=12.0,
    )

    limits = httpx.Limits(
        max_connections=max(8, concurrency * 2),
        max_keepalive_connections=concurrency,
    )

    started = time.monotonic()

    async with httpx.AsyncClient(
        headers={"User-Agent": USER_AGENT},
        timeout=timeout,
        follow_redirects=True,
        limits=limits,
    ) as client:
        crawls = await _latest_crawls(
            client,
            fallback_crawls + 1,
        )

        sem = asyncio.Semaphore(concurrency)
        results = []

        # Common Crawl index requests are deliberately sequential.
        for index, domain in enumerate(domains):
            if index:
                await asyncio.sleep(index_delay)

            results.append(
                await _verify_domain(
                    client,
                    crawls,
                    domain,
                    target_domain,
                    pages_per_domain,
                    sem,
                    index_delay,
                )
            )

    links = []

    for result in results:
        links.extend(
            [
                {
                    **link,
                    "referring_domain": result[
                        "referring_domain"
                    ],
                    "layer": 2,
                }
                for link in result.get(
                    "backlinks", []
                )
            ]
        )

    statuses = {
        str(result.get("status") or "error")
        for result in results
    }

    if links:
        status = "confirmed"
    elif statuses and statuses.issubset(
        {
            "no_captures_found",
            "not_found_in_sample",
        }
    ):
        status = "not_found_in_archive"
    elif "index_unavailable" in statuses:
        status = "index_unavailable"
    else:
        status = "error"

    return {
        "status": status,
        "provider": "Common Crawl",
        "target_domain": target_domain,
        "layer1_referring_domains": len(domains),
        "domains_investigated": len(domains),
        "links_found": len(links),
        "fallback_available": status == "index_unavailable",
        "configuration": {
            "fallback_crawls": fallback_crawls,
            "pages_per_domain": pages_per_domain,
            "warc_concurrency": concurrency,
            "index_delay_seconds": index_delay,
        },
        "elapsed_seconds": round(
            time.monotonic() - started,
            2,
        ),
        "domains": results,
        "backlinks": links,
    }
