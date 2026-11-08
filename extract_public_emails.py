#!/usr/bin/env python3
"""Optional, targeted public-email extractor.

This is intentionally separate from the normal SEO crawler. It only runs when
this file is invoked directly; it does not change the existing crawl workflow.

Example:
  python extract_public_emails.py \
      --url https://example.com \
      --name "Jane Smith" \
      --title "Founder"
"""
from __future__ import annotations

import argparse
import csv
import html
import re
import sys
import time
import urllib.robotparser
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote, urljoin, urlparse, urlunparse

import requests
from bs4 import BeautifulSoup

USER_AGENT = "SEO-Crawler-TargetedEmailExtractor/1.0"
TIMEOUT_SECONDS = 15
MAX_RESPONSE_BYTES = 2_000_000
DEFAULT_MAX_PAGES = 12

EMAIL_RE = re.compile(
    r"(?<![A-Z0-9._%+-])[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}(?![A-Z0-9-])",
    re.IGNORECASE,
)
OBFUSCATED_REPLACEMENTS = (
    (re.compile(r"\s*(?:\[at\]|\(at\)|\{at\})\s*", re.I), "@"),
    (re.compile(r"\s*(?:\[dot\]|\(dot\)|\{dot\})\s*", re.I), "."),
)
GENERIC_LOCAL_PARTS = {
    "admin", "administrator", "accounts", "billing", "careers", "contact",
    "enquiry", "enquiries", "hello", "help", "hr", "info", "inquiry",
    "inquiries", "jobs", "legal", "marketing", "media", " office",
    "noreply", "no-reply", "office", " press", "privacy", "sales",
    "service", "support", "team", "webmaster",
}
PLACEHOLDER_DOMAINS = {
    "example.com", "example.org", "example.net", "test.com", "domain.com",
    "email.com", "yourdomain.com",
}

# Requested page priority: Contact, About, Team, then Leadership.
PAGE_TYPES = (
    ("contact", 0, re.compile(r"(?:contact|contact-us|contactus|get-in-touch|reach-us)", re.I)),
    ("about", 1, re.compile(r"(?:about|about-us|our-story|who-we-are)", re.I)),
    ("team", 2, re.compile(r"(?:team|our-team|team-member|staff|people|employee)", re.I)),
    ("leadership", 3, re.compile(r"(?:leadership|management|executive|founder|director)", re.I)),
)
COMMON_PATHS = (
    ("contact", "/contact/"),
    ("contact", "/contact-us/"),
    ("about", "/about/"),
    ("about", "/about-us/"),
    ("team", "/team/"),
    ("team", "/our-team/"),
    ("leadership", "/leadership/"),
    ("leadership", "/management/"),
)


@dataclass
class Candidate:
    email: str
    source_url: str
    page_type: str
    context: str
    score: int = 0
    reason: str = ""


def clean_site_url(value: str) -> str:
    value = (value or "").strip()
    if not value:
        raise ValueError("A company website URL is required.")
    if "://" not in value:
        value = "https://" + value
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("Website URL must be a valid HTTP or HTTPS URL.")
    host = parsed.hostname.lower()
    netloc = host
    if parsed.port:
        netloc = f"{host}:{parsed.port}"
    return urlunparse((parsed.scheme, netloc, "/", "", "", ""))


def root_host(host: str) -> str:
    host = (host or "").lower().split(":")[0].strip(".")
    return host[4:] if host.startswith("www.") else host


def same_site(url: str, base_host: str) -> bool:
    host = root_host(urlparse(url).hostname or "")
    return bool(host and (host == base_host or host.endswith("." + base_host)))


def classify_page(url: str, anchor_text: str = "") -> tuple[str, int] | None:
    value = f"{urlparse(url).path} {anchor_text}".lower()
    for page_type, priority, pattern in PAGE_TYPES:
        if pattern.search(value):
            return page_type, priority
    return None


def make_robots_parser(session: requests.Session, base_url: str) -> urllib.robotparser.RobotFileParser:
    robots_url = urljoin(base_url, "/robots.txt")
    parser = urllib.robotparser.RobotFileParser()
    parser.set_url(robots_url)
    try:
        response = session.get(robots_url, timeout=TIMEOUT_SECONDS)
        if response.ok and len(response.content) <= 256_000:
            parser.parse(response.text.splitlines())
        else:
            # A missing/unavailable robots file has no published disallow rules.
            parser.parse([])
    except requests.RequestException:
        parser.parse([])
    return parser


def fetch_html(session: requests.Session, url: str) -> str | None:
    try:
        response = session.get(url, timeout=TIMEOUT_SECONDS, allow_redirects=True)
        response.raise_for_status()
        if len(response.content) > MAX_RESPONSE_BYTES:
            print(f"[SKIP] Page exceeds size limit: {url}", file=sys.stderr)
            return None
        content_type = response.headers.get("Content-Type", "").lower()
        if "html" not in content_type and "<html" not in response.text[:500].lower():
            return None
        return response.text
    except requests.RequestException as exc:
        print(f"[SKIP] Could not fetch {url}: {exc}", file=sys.stderr)
        return None


def discover_target_pages(home_html: str, base_url: str, base_host: str, max_pages: int) -> list[tuple[str, str, int]]:
    found: dict[str, tuple[str, str, int]] = {}
    soup = BeautifulSoup(home_html, "html.parser")

    for anchor in soup.find_all("a", href=True):
        href = (anchor.get("href") or "").strip()
        if not href or href.lower().startswith(("mailto:", "tel:", "javascript:", "#")):
            continue
        absolute = urljoin(base_url, href)
        parsed = urlparse(absolute)
        if parsed.scheme not in {"http", "https"} or not same_site(absolute, base_host):
            continue
        classified = classify_page(absolute, anchor.get_text(" ", strip=True))
        if not classified:
            continue
        page_type, priority = classified
        normalized = urlunparse((parsed.scheme, parsed.netloc, parsed.path or "/", "", "", ""))
        found.setdefault(normalized, (normalized, page_type, priority))

    for page_type, path in COMMON_PATHS:
        candidate = urljoin(base_url, path)
        found.setdefault(candidate, (candidate, page_type, next(p for t, p, _ in PAGE_TYPES if t == page_type)))

    return sorted(found.values(), key=lambda x: (x[2], x[0]))[:max_pages]


def clean_obfuscated_text(value: str) -> str:
    value = html.unescape(value or "")
    for pattern, replacement in OBFUSCATED_REPLACEMENTS:
        value = pattern.sub(replacement, value)
    return value


def email_domain_matches(email: str, base_host: str) -> bool:
    domain = email.rsplit("@", 1)[-1].lower().strip(".")
    return domain == base_host or domain.endswith("." + base_host)


def is_generic_or_placeholder(email: str) -> bool:
    local, domain = email.lower().split("@", 1)
    local = re.sub(r"[^a-z0-9-]", "", local)
    return (
        local in {re.sub(r"[^a-z0-9-]", "", item.strip()) for item in GENERIC_LOCAL_PARTS}
        or domain in PLACEHOLDER_DOMAINS
        or local.startswith(("noreply", "no-reply", "donotreply"))
    )


def name_tokens(value: str) -> list[str]:
    return [token for token in re.findall(r"[a-z0-9]+", (value or "").lower()) if len(token) > 1]


def context_name_match(name: str, context: str) -> bool:
    wanted = name_tokens(name)
    available = re.findall(r"[a-z0-9]+", (context or "").lower())
    if not wanted or not available:
        return False
    for start, token in enumerate(available):
        if token != wanted[0]:
            continue
        matched = True
        cursor = start + 1
        for next_token in wanted[1:]:
            if len(next_token) == 1:
                while cursor < len(available) and not available[cursor].startswith(next_token):
                    cursor += 1
            else:
                while cursor < len(available) and available[cursor] != next_token:
                    cursor += 1
            if cursor >= len(available):
                matched = False
                break
            cursor += 1
        if matched:
            return True
    return False


def score_candidate(candidate: Candidate, target_name: str, target_title: str) -> Candidate:
    local = candidate.email.split("@", 1)[0].lower()
    local_compact = re.sub(r"[^a-z0-9]", "", local)
    wanted = name_tokens(target_name)
    context = candidate.context.lower()
    reasons: list[str] = []

    if wanted and all(token in local_compact for token in wanted):
        candidate.score += 75
        reasons.append("email username matches the person's name")
    elif wanted and wanted[0] in local_compact:
        candidate.score += 35
        reasons.append("email username matches the person's first name")

    if context_name_match(target_name, candidate.context):
        candidate.score += 55
        reasons.append("person's name appears near the email on the source page")

    title_tokens = name_tokens(target_title)
    if title_tokens and any(token in context for token in title_tokens):
        candidate.score += 15
        reasons.append("target job title appears on the source page")

    page_bonus = {"contact": 0, "about": 5, "team": 10, "leadership": 10}
    candidate.score += page_bonus.get(candidate.page_type, 0)
    if not reasons:
        reasons.append("public email found, but no reliable match to the named contact")
    candidate.reason = "; ".join(reasons)
    return candidate


def extract_page_candidates(page_html: str, page_url: str, page_type: str, base_host: str,
                            target_name: str, target_title: str) -> list[Candidate]:
    soup = BeautifulSoup(page_html, "html.parser")
    visible_text = soup.get_text(" ", strip=True)
    normalized_text = clean_obfuscated_text(visible_text)
    candidates: dict[str, Candidate] = {}

    raw_values = [normalized_text]
    for anchor in soup.find_all("a", href=True):
        href = (anchor.get("href") or "").strip()
        if href.lower().startswith("mailto:"):
            raw_values.append(unquote(href[7:].split("?", 1)[0]))

    for raw in raw_values:
        for match in EMAIL_RE.findall(raw):
            email = match.strip(".,;:()[]{}<>").lower()
            if not email_domain_matches(email, base_host) or is_generic_or_placeholder(email):
                continue
            if email not in candidates:
                idx = normalized_text.lower().find(email)
                context = normalized_text[max(0, idx - 220):idx + len(email) + 220] if idx >= 0 else normalized_text[:500]
                candidates[email] = Candidate(email, page_url, page_type, context)
    return [score_candidate(item, target_name, target_title) for item in candidates.values()]


def extract(url: str, name: str, title: str, max_pages: int, delay: float) -> list[Candidate]:
    base_url = clean_site_url(url)
    base_host = root_host(urlparse(base_url).hostname or "")
    session = requests.Session()
    session.headers.update({
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml",
    })
    robots = make_robots_parser(session, base_url)

    if not robots.can_fetch(USER_AGENT, base_url):
        raise RuntimeError("robots.txt disallows crawling the company homepage.")

    home_html = fetch_html(session, base_url)
    if not home_html:
        raise RuntimeError("Could not retrieve the company homepage as HTML.")

    pages = [(base_url, "homepage", -1)] + discover_target_pages(home_html, base_url, base_host, max_pages)
    collected: dict[str, Candidate] = {}

    for index, (page_url, page_type, _priority) in enumerate(pages):
        if index > 0:
            if not robots.can_fetch(USER_AGENT, page_url):
                print(f"[ROBOTS] Skipping disallowed page: {page_url}", file=sys.stderr)
                continue
            if delay > 0:
                time.sleep(delay)
            page_html = fetch_html(session, page_url)
        else:
            page_html = home_html

        if not page_html:
            continue

        for candidate in extract_page_candidates(
            page_html, page_url, page_type, base_host, name, title
        ):
            previous = collected.get(candidate.email)
            if previous is None or candidate.score > previous.score:
                collected[candidate.email] = candidate

    session.close()
    # Do not guess an address. Return only a public email with a name-based signal.
    wanted = name_tokens(name)
    matched = [
        item for item in collected.values()
        if item.score >= 50 and (
            context_name_match(name, item.context)
            or (wanted and all(token in re.sub(r"[^a-z0-9]", "", item.email.split("@", 1)[0].lower()) for token in wanted))
        )
    ]
    return sorted(matched, key=lambda item: (-item.score, item.source_url))[:1]


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Find a named decision-maker's publicly published company email."
    )
    parser.add_argument("--url", required=True, help="Company website URL")
    parser.add_argument("--name", required=True, help='Decision-maker name, e.g. "Jane Smith"')
    parser.add_argument("--title", default="", help='Optional job title, e.g. "Founder"')
    parser.add_argument("--max-pages", type=int, default=DEFAULT_MAX_PAGES,
                        help=f"Maximum targeted pages to inspect (default {DEFAULT_MAX_PAGES}; max 30)")
    parser.add_argument("--delay", type=float, default=1.0,
                        help="Delay in seconds between page requests (default 1)")
    parser.add_argument("--output", default="",
                        help="Optional CSV output path (default: public_emails_<domain>.csv)")
    args = parser.parse_args()

    max_pages = max(1, min(args.max_pages, 30))
    delay = max(0.0, args.delay)
    try:
        results = extract(args.url, args.name, args.title, max_pages, delay)
    except (ValueError, RuntimeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    domain = root_host(urlparse(clean_site_url(args.url)).hostname or "")
    output_path = Path(args.output or f"public_emails_{domain.replace('.', '_')}.csv")
    fields = ["name_searched", "title_searched", "email", "match_score",
              "match_reason", "page_type", "source_url", "mailbox_verified"]
    with output_path.open("w", newline="", encoding="utf-8-sig") as file:
        writer = csv.DictWriter(file, fieldnames=fields)
        writer.writeheader()
        for item in results:
            writer.writerow({
                "name_searched": args.name,
                "title_searched": args.title,
                "email": item.email,
                "match_score": item.score,
                "match_reason": item.reason,
                "page_type": item.page_type,
                "source_url": item.source_url,
                "mailbox_verified": "No - public listing only",
            })

    if results:
        best = results[0]
        print(f"PUBLIC EMAIL FOUND: {best.email}")
        print(f"Match: {best.score}/155 | {best.reason}")
        print(f"Source: {best.source_url}")
        print("Mailbox verification: not performed")
    else:
        print("No sufficiently matched, publicly published personal email was found.")
    print(f"CSV saved: {output_path.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
