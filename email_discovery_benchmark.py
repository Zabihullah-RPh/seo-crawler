#!/usr/bin/env python3
"""Benchmark public-site email discovery with the existing crawler fetch/parser modules.

This is an experiment, not an existing production email-extraction feature.
Only counts and timings are written; addresses are not logged or saved.
"""
from __future__ import annotations

import asyncio
import csv
import re
import time
from pathlib import Path
from urllib.parse import urljoin, urlparse, urldefrag

from bs4 import BeautifulSoup

from app.crawler.http import HTTPClient
from app.crawler.parser import parse_html
from app.crawler.robots import RobotsManager

TARGETS = [
    ("Utopia.pk", "https://www.utopia.pk/"),
    ("Bosch Pharmaceuticals", "https://bosch-pharma.com/"),
    ("Gohar Textile Mills", "https://www.gohartextile.com/"),
    ("International Textile Limited", "https://internationaltextile.com/"),
    ("Service Sales", "https://sscbrands.com/"),
    ("Powersoft19", "https://www.powersoft19.com/"),
    ("Shahkam Industries", "https://www.shahkam.com/"),
    ("Innovative Pvt", "https://innovative-pk.com/"),
]

EMAIL_RE = re.compile(
    r"(?i)(?<![A-Z0-9._%+-])[A-Z0-9.!#$%&'*+/=?^_{}|~-]+@"
    r"[A-Z0-9](?:[A-Z0-9-]{0,61}[A-Z0-9])?"
    r"(?:\.[A-Z0-9](?:[A-Z0-9-]{0,61}[A-Z0-9])?)+"
)
CONTACT_TERMS = (
    "contact", "about", "team", "staff", "leadership", "support",
    "enquir", "sales", "marketing", "company", "get-in-touch",
    "our-people", "management",
)
MAX_EXTRA_PAGES = 3
OUTPUT = Path("email-crawler-benchmark.csv")
SUMMARY = Path("email-crawler-benchmark-summary.txt")


def _same_host(base_url: str, target_url: str) -> bool:
    return (urlparse(base_url).hostname or "").lower().removeprefix("www.") == (
        urlparse(target_url).hostname or ""
    ).lower().removeprefix("www.")


def _emails_from_page(raw_html: str, visible_text: str) -> set[str]:
    found = {m.group(0).strip(".,;:()[]{}<>").lower() for m in EMAIL_RE.finditer(visible_text or "")}
    soup = BeautifulSoup(raw_html or "", "html.parser")
    for node in soup.select('a[href^="mailto:" i]'):
        href = (node.get("href") or "")[7:].split("?", 1)[0]
        found.update(
            m.group(0).strip(".,;:()[]{}<>").lower()
            for m in EMAIL_RE.finditer(href)
        )
    return {
        email for email in found
        if not email.endswith((".png", ".jpg", ".jpeg", ".webp", ".svg"))
        and not email.endswith("@example.com")
        and "yourname@" not in email
    }


def _contact_candidates(raw_html: str, page_url: str, base_url: str, robots: RobotsManager) -> list[str]:
    soup = BeautifulSoup(raw_html or "", "html.parser")
    candidates: list[tuple[int, str]] = []
    seen: set[str] = set()
    for anchor in soup.find_all("a", href=True):
        href = (anchor.get("href") or "").strip()
        if not href or href.startswith(("mailto:", "tel:", "javascript:", "#")):
            continue
        target = urldefrag(urljoin(page_url, href))[0]
        parsed = urlparse(target)
        if parsed.scheme not in ("http", "https") or not _same_host(base_url, target):
            continue
        if target in seen:
            continue
        seen.add(target)
        hint = (parsed.path + " " + (anchor.get_text(" ", strip=True) or "")).lower()
        score = sum(1 for term in CONTACT_TERMS if term in hint)
        if score:
            candidates.append((score, target))
    candidates.sort(key=lambda pair: pair[0], reverse=True)
    return [url for _, url in candidates[:MAX_EXTRA_PAGES] if robots.allowed(url)]


async def _fetch_html(http: HTTPClient, url: str):
    result = await http.get(url)
    response = result.get("response")
    if not response or response.status_code >= 400:
        return None, None, 0.0, f"HTTP/error {getattr(response, 'status_code', 'request failed')}"
    content_type = response.headers.get("content-type", "").lower()
    if "html" not in content_type and "xhtml" not in content_type:
        return None, None, result.get("elapsed", 0.0), "not HTML"
    raw_html = response.text
    parsed = parse_html(raw_html, str(response.url))
    return raw_html, parsed, float(result.get("elapsed", 0.0)), ""


async def _scan_company(http: HTTPClient, name: str, url: str) -> dict:
    started = time.perf_counter()
    robots = RobotsManager(url, "SEO-Crawler/1.0")
    try:
        await robots.load(http)
    except Exception:
        pass

    if not robots.allowed(url):
        return {
            "company": name, "domain": urlparse(url).hostname or url,
            "pages_checked": 0, "public_email_count": 0,
            "elapsed_seconds": round(time.perf_counter() - started, 3),
            "status": "blocked by robots.txt",
        }

    email_set: set[str] = set()
    raw_html, parsed, _, error = await _fetch_html(http, url)
    pages_checked = 0
    if raw_html is not None and parsed is not None:
        pages_checked += 1
        email_set.update(_emails_from_page(raw_html, parsed.get("text", "")))
        candidates = _contact_candidates(raw_html, str(url), url, robots)
        delay = robots.crawl_delay() or 0
        for target in candidates:
            if delay:
                await asyncio.sleep(min(float(delay), 10.0))
            page_html, page_parsed, _, _ = await _fetch_html(http, target)
            if page_html is None or page_parsed is None:
                continue
            pages_checked += 1
            email_set.update(_emails_from_page(page_html, page_parsed.get("text", "")))
    elapsed = round(time.perf_counter() - started, 3)
    return {
        "company": name,
        "domain": urlparse(url).hostname or url,
        "pages_checked": pages_checked,
        "public_email_count": len(email_set),
        "elapsed_seconds": elapsed,
        "status": "ok" if pages_checked else (error or "no HTML page fetched"),
    }


async def main() -> None:
    total_started = time.perf_counter()
    http = HTTPClient(concurrency=16)
    try:
        results = await asyncio.gather(
            *(_scan_company(http, name, url) for name, url in TARGETS),
            return_exceptions=True,
        )
    finally:
        await http.close()

    rows = []
    for (name, url), result in zip(TARGETS, results):
        if isinstance(result, Exception):
            rows.append({
                "company": name,
                "domain": urlparse(url).hostname or url,
                "pages_checked": 0,
                "public_email_count": 0,
                "elapsed_seconds": "",
                "status": f"error: {type(result).__name__}",
            })
        else:
            rows.append(result)

    total_elapsed = round(time.perf_counter() - total_started, 3)
    fields = [
        "company", "domain", "pages_checked",
        "public_email_count", "elapsed_seconds", "status",
    ]
    with OUTPUT.open("w", newline="", encoding="utf-8-sig") as file:
        writer = csv.DictWriter(file, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)

    summary = [
        f"Companies attempted: {len(TARGETS)}",
        f"Companies with at least one public email candidate: {sum(1 for row in rows if row['public_email_count'] > 0)}",
        f"Distinct public email candidates (count only): {sum(int(row['public_email_count']) for row in rows)}",
        f"HTML pages checked: {sum(int(row['pages_checked']) for row in rows)}",
        f"Total elapsed seconds (8 companies scanned concurrently): {total_elapsed}",
        "Email addresses logged or stored: 0",
        "Method: SEO-Crawler HTTPClient + RobotsManager + parse_html; test-only regex/mailto extraction.",
        "Scope: homepage plus up to three internal contact/about/team pages per domain.",
    ]
    SUMMARY.write_text("\n".join(summary) + "\n", encoding="utf-8")
    print("\n".join(summary))
    for row in rows:
        print(
            f"{row['domain']}: pages={row['pages_checked']}, "
            f"candidate_count={row['public_email_count']}, "
            f"elapsed={row['elapsed_seconds']}s, status={row['status']}"
        )


if __name__ == "__main__":
    asyncio.run(main())
