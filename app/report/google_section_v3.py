from __future__ import annotations

from html import escape
from pathlib import Path
from typing import Any

from bs4 import BeautifulSoup


def _status(value: Any) -> str:
    return escape(str(value or "DATA_NOT_AVAILABLE"))


def _score(value: Any) -> str:
    if value is None:
        return "Data not available"
    try:
        return str(round(float(value) * 100, 1))
    except (TypeError, ValueError):
        return escape(str(value))


def _remove_site_heading(soup: BeautifulSoup, names: set[str]) -> None:
    for heading in list(soup.find_all("h2")):
        if heading.get_text(" ", strip=True).lower() in names:
            parent = heading.find_parent("div", class_="site")
            if parent:
                parent.decompose()


def _append_simple_card(soup: BeautifulSoup, title: str, item: dict[str, Any], key: str) -> Any:
    box = soup.new_tag("div", attrs={"class": "site"})
    h2 = soup.new_tag("h2")
    h2.string = title
    box.append(h2)
    p = soup.new_tag("p")
    b = soup.new_tag("b")
    b.string = "Status:"
    p.append(b)
    p.append(" " + _status(item.get("status")))
    box.append(p)
    reason = item.get("reason") or item.get("error")
    if reason:
        p = soup.new_tag("p")
        b = soup.new_tag("b")
        b.string = "Reason:"
        p.append(b)
        p.append(" " + str(reason))
        box.append(p)
    value = item.get(key)
    if isinstance(value, list):
        value = f"{len(value)} rows"
    elif isinstance(value, dict):
        value = "Available"
    elif value in (None, ""):
        value = "Data not available"
    p = soup.new_tag("p")
    b = soup.new_tag("b")
    b.string = "Data:"
    p.append(b)
    p.append(" " + str(value))
    box.append(p)
    return box


def append_google_section(html_path: Path, google: dict[str, Any]) -> None:
    if not html_path.exists():
        return

    soup = BeautifulSoup(html_path.read_text(encoding="utf-8"), "html.parser")

    # Remove obsolete/duplicate API blocks. DNS/RDAP and Common Crawl are intentionally
    # not rendered here because they already have dedicated site/backlink fields.
    _remove_site_heading(soup, {
        "public / external data sources",
        "google & public api enrichment",
        "google api results",
        "google data enrichment",
        "pagespeed insights",
        "google search console",
        "google sitemaps api",
        "google url inspection",
        "google search analytics",
        "google analytics 4",
    })

    container = soup.find("div", class_="container") or soup.body
    if container is None:
        return

    root = soup.new_tag("div", attrs={"id": "google-api-results"})
    intro = soup.new_tag("div", attrs={"class": "site"})
    h2 = soup.new_tag("h2")
    h2.string = "Google API Results"
    intro.append(h2)
    p = soup.new_tag("p")
    p.string = "PageSpeed Insights is public-by-URL. Search Console, Sitemaps, URL Inspection, Search Analytics, and GA4 depend on account/property access."
    intro.append(p)
    root.append(intro)

    ps = google.get("pagespeed", {}) or {}
    box = soup.new_tag("div", attrs={"class": "site"})
    h2 = soup.new_tag("h2")
    h2.string = "PageSpeed Insights"
    box.append(h2)
    p = soup.new_tag("p")
    b = soup.new_tag("b")
    b.string = "Status:"
    p.append(b)
    p.append(" " + _status(ps.get("status")))
    box.append(p)
    reason = ps.get("reason") or ps.get("error")
    if reason:
        p = soup.new_tag("p")
        b = soup.new_tag("b")
        b.string = "Reason:"
        p.append(b)
        p.append(" " + str(reason))
        box.append(p)
    table = soup.new_tag("table")
    tr = soup.new_tag("tr")
    for label in ("Metric", "Result"):
        th = soup.new_tag("th")
        th.string = label
        tr.append(th)
    table.append(tr)
    for label, key in (("Performance", "performance"), ("Accessibility", "accessibility"), ("Best Practices", "best_practices"), ("SEO", "seo")):
        tr = soup.new_tag("tr")
        td = soup.new_tag("td")
        td.string = label
        tr.append(td)
        td = soup.new_tag("td")
        td.string = _score(ps.get(key))
        tr.append(td)
        table.append(tr)
    box.append(table)
    root.append(box)

    for title, item, key in (
        ("Google Search Console", google.get("search_console", {}) or {}, "property"),
        ("Google Sitemaps API", google.get("sitemaps", {}) or {}, "count"),
        ("Google URL Inspection", google.get("url_inspection", {}) or {}, "coverage_state"),
        ("Google Search Analytics", google.get("search_analytics", {}) or {}, "rows"),
        ("Google Analytics 4", google.get("ga4", {}) or {}, "property_id"),
    ):
        root.append(_append_simple_card(soup, title, item, key))

    container.append(root)
    html_path.write_text(str(soup), encoding="utf-8")
