#!/usr/bin/env python3
"""Benchmark the production email extractor on eight public business websites.

The CSV includes publicly published email candidates and their source pages.
Addresses are not printed to the Actions log.
"""
from __future__ import annotations

import asyncio
import csv
import re
import time
from pathlib import Path
from urllib.parse import urljoin, urlparse, urldefrag

from bs4 import BeautifulSoup

from app.crawler.email_extractor import extract_emails
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
EMAILS_OUTPUT = Path("email-crawler-public-emails.csv")


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
        return None, None, 0.0, f"HTTP/error {getattr(response, 'status_code', 'request failed')}", url
    content_type = response.headers.get("content-type", "").lower()
    if "html" not in content_type and "xhtml" not in content_type:
        return None, None, result.get("elapsed", 0.0), "not HTML", url
    raw_html = response.text
    final_url = str(response.url)
    parsed = parse_html(raw_html, final_url)
    return raw_html, parsed, float(result.get("elapsed", 0.0)), "", final_url


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

    email_map = {}
    raw_html, parsed, _, error, final_url = await _fetch_html(http, url)
    pages_checked = 0

    def add_page_emails(markup: str, page_url: str):
        for candidate in extract_emails(markup, page_url):
            address = candidate["email"]
            if address not in email_map:
                email_map[address] = {
                    **candidate,
                    "company": name,
                    "company_domain": urlparse(url).hostname or url,
                    "source_pages": [page_url],
                }
            else:
                current = email_map[address]
                if page_url not in current["source_pages"]:
                    current["source_pages"].append(page_url)
                methods = set(current["extraction_methods"])
                methods.update(candidate["extraction_methods"])
                current["extraction_methods"] = sorted(methods)

    if raw_html is not None and parsed is not None:
        pages_checked += 1
        add_page_emails(raw_html, final_url)
        candidates = _contact_candidates(raw_html, final_url, url, robots)
        delay = robots.crawl_delay() or 0
        for target in candidates:
            if delay:
                await asyncio.sleep(min(float(delay), 10.0))
            page_html, page_parsed, _, _, page_final_url = await _fetch_html(http, target)
            if page_html is None or page_parsed is None:
                continue
            pages_checked += 1
            add_page_emails(page_html, page_final_url)

    emails = sorted(email_map.values(), key=lambda item: item["email"])
    for item in emails:
        item["source_pages"].sort()
    elapsed = round(time.perf_counter() - started, 3)
    return {
        "company": name,
        "domain": urlparse(url).hostname or url,
        "pages_checked": pages_checked,
        "public_email_count": len(emails),
        "elapsed_seconds": elapsed,
        "status": "ok" if pages_checked else (error or "no HTML page fetched"),
        "emails": emails,
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
    email_rows = []
    for row in rows:
        for item in row.get("emails", []):
            email_rows.append({
                "company": item["company"],
                "company_domain": item["company_domain"],
                "email": item["email"],
                "email_domain": item["email_domain"],
                "same_domain": item["same_domain"],
                "generic_mailbox": item["generic_mailbox"],
                "source_pages": " | ".join(item["source_pages"]),
                "extraction_methods": ", ".join(item["extraction_methods"]),
            })

    email_fields = [
        "company", "company_domain", "email", "email_domain",
        "same_domain", "generic_mailbox", "source_pages", "extraction_methods",
    ]
    with EMAILS_OUTPUT.open("w", newline="", encoding="utf-8-sig") as file:
        writer = csv.DictWriter(file, fieldnames=email_fields)
        writer.writeheader()
        writer.writerows(email_rows)

    fields = [
        "company", "domain", "pages_checked",
        "public_email_count", "elapsed_seconds", "status",
    ]
    with OUTPUT.open("w", newline="", encoding="utf-8-sig") as file:
        writer = csv.DictWriter(file, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)

    summary = [
        f"Companies attempted: {len(TARGETS)}",
        f"Companies with at least one public email candidate: {sum(1 for row in rows if row['public_email_count'] > 0)}",
        f"Unique public email candidates saved: {len({row['email'] for row in email_rows})}",
        f"Email source rows saved: {len(email_rows)}",
        f"HTML pages checked: {sum(int(row['pages_checked']) for row in rows)}",
        f"Total elapsed seconds (8 companies scanned concurrently): {total_elapsed}",
        f"Email CSV: {EMAILS_OUTPUT}",
        "Method: production extract_emails() used by the SEO crawler.",
        "Scope: homepage plus up to three internal contact/about/team pages per domain.",
        "Only addresses published in visible page text, mailto links or JSON-LD are included.",
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
