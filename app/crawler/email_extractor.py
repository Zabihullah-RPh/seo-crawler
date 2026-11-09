"""Extract publicly published business email addresses from HTML pages."""
from __future__ import annotations

import json
import re
from collections import defaultdict
from html import unescape
from urllib.parse import unquote, urlparse

from bs4 import BeautifulSoup

EMAIL_RE = re.compile(
    r"(?i)(?<![A-Z0-9._%+-])"
    r"[A-Z0-9.!#$%&'*+/=?^_{|}~-]+@"
    r"[A-Z0-9](?:[A-Z0-9-]{0,61}[A-Z0-9])?"
    r"(?:\.[A-Z0-9](?:[A-Z0-9-]{0,61}[A-Z0-9])?)+"
    r"(?![A-Z0-9_-])"
)

GENERIC_LOCAL_PARTS = {
    "admin", "billing", "careers", "contact", "enquiries", "enquiry",
    "hello", "help", "hr", "info", "jobs", "marketing", "media",
    "office", "orders", "press", "privacy", "sales", "support", "webmaster",
}


def _normalize_candidate(value: str) -> str | None:
    candidate = unquote(unescape(str(value or ""))).strip()
    candidate = candidate.removeprefix("mailto:")
    candidate = candidate.split("?", 1)[0]
    candidate = candidate.strip(" \t\r\n.,;:!?()[]{}<>\"'")
    candidate = candidate.lower()

    if not candidate or candidate.count("@") != 1:
        return None

    local, domain = candidate.rsplit("@", 1)
    if (
        not local
        or not domain
        or local.startswith(".")
        or local.endswith(".")
        or ".." in candidate
        or len(local) > 64
        or len(candidate) > 254
    ):
        return None

    if not re.fullmatch(r"[a-z0-9.!#$%&'*+/=?^_{|}~-]+", local):
        return None
    if not re.fullmatch(r"[a-z0-9.-]+\.[a-z]{2,63}", domain):
        return None
    if domain.startswith(".") or domain.endswith(".") or ".." in domain:
        return None

    return candidate


def _find_in_text(text: str) -> set[str]:
    found = set()
    for match in EMAIL_RE.finditer(text or ""):
        email = _normalize_candidate(match.group(0))
        if email:
            found.add(email)
    return found


def _same_domain(email_domain: str, page_host: str) -> bool:
    host = (page_host or "").lower().removeprefix("www.")
    email_host = (email_domain or "").lower().removeprefix("www.")
    return bool(host and email_host and (email_host == host or email_host.endswith("." + host)))


def extract_emails(html: str, source_url: str) -> list[dict]:
    """Return public email candidates with their source page and extraction evidence.

    Extraction sources:
      - mailto: Explicit mail links.
      - visible_text: Text rendered in the document body.
      - json_ld: Public contact information in JSON-LD structured data.
    """
    soup = BeautifulSoup(html or "", "html.parser")
    methods: dict[str, set[str]] = defaultdict(set)

    for anchor in soup.find_all("a", href=True):
        href = str(anchor.get("href") or "").strip()
        if href.lower().startswith("mailto:"):
            for match in EMAIL_RE.finditer(unquote(href[7:].split("?", 1)[0])):
                email = _normalize_candidate(match.group(0))
                if email:
                    methods[email].add("mailto")

    visible_soup = BeautifulSoup(html or "", "html.parser")
    for element in visible_soup(["script", "style", "noscript", "svg", "template"]):
        element.decompose()
    for email in _find_in_text(visible_soup.get_text(" ", strip=True)):
        methods[email].add("visible_text")

    for script in soup.find_all("script", attrs={"type": re.compile(r"application/ld\+json", re.I)}):
        raw = script.string or script.get_text() or ""
        try:
            structured = json.loads(raw)
        except (TypeError, ValueError):
            continue

        def walk(value):
            if isinstance(value, dict):
                for key, child in value.items():
                    if str(key).lower() == "email":
                        for email in _find_in_text(str(child or "")):
                            methods[email].add("json_ld")
                    walk(child)
            elif isinstance(value, list):
                for child in value:
                    walk(child)

        walk(structured)

    page_host = urlparse(source_url).hostname or ""
    records = []
    for email in sorted(methods):
        local, email_domain = email.rsplit("@", 1)
        records.append({
            "email": email,
            "email_domain": email_domain,
            "source_url": source_url,
            "extraction_methods": sorted(methods[email]),
            "same_domain": _same_domain(email_domain, page_host),
            "generic_mailbox": local in GENERIC_LOCAL_PARTS,
        })

    return records
