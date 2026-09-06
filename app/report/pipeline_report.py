from __future__ import annotations

import json
from html import escape
from pathlib import Path
from typing import Any

from bs4 import BeautifulSoup


def _status(value: Any) -> str:
    return str(value or "DATA_NOT_AVAILABLE")


def _reason(item: dict[str, Any]) -> str:
    return str(item.get("reason") or item.get("error") or "")


def _score(value: Any) -> str:
    if value is None:
        return "Data not available"
    try:
        return f"{float(value) * 100:.1f}".rstrip("0").rstrip(".")
    except (TypeError, ValueError):
        return escape(str(value))


def _card(title: str, item: dict[str, Any], fields: list[tuple[str, str]]) -> str:
    out = [
        f'<div class="site"><h2>{escape(title)}</h2>',
        f'<p><b>Status:</b> {escape(_status(item.get("status")))}</p>',
    ]
    reason = _reason(item)
    if reason:
        out.append(f'<p><b>Reason:</b> {escape(reason)}</p>')
    rows = []
    for label, key in fields:
        value = item.get(key)
        if isinstance(value, (dict, list)):
            value = json.dumps(value, ensure_ascii=False, default=str)
        if value in (None, ""):
            value = "Data not available"
        rows.append(f'<tr><th>{escape(label)}</th><td>{escape(str(value))}</td></tr>')
    if rows:
        out.append('<table>' + ''.join(rows) + '</table>')
    out.append('</div>')
    return ''.join(out)


def _pagespeed_section(item: dict[str, Any]) -> str:
    return f'''<div class="site" id="pagespeed-insights">
<h2>PageSpeed Insights</h2>
<p><b>Status:</b> {escape(_status(item.get("status")))}</p>
{f'<p><b>Reason:</b> {escape(_reason(item))}</p>' if _reason(item) else ''}
<table>
<tr><th>Metric</th><th>Result</th></tr>
<tr><td>Performance</td><td>{escape(_score(item.get("performance")))}</td></tr>
<tr><td>Accessibility</td><td>{escape(_score(item.get("accessibility")))}</td></tr>
<tr><td>Best Practices</td><td>{escape(_score(item.get("best_practices")))}</td></tr>
<tr><td>SEO</td><td>{escape(_score(item.get("seo")))}</td></tr>
</table></div>'''


def _google_private_section(google: dict[str, Any]) -> str:
    parts = ['<div class="site" id="google-api-results"><h2>Google API Results</h2>']
    parts.extend([
        _card("Google Search Console", google.get("search_console", {}) or {}, [("Property", "property")]),
        _card("Google Sitemaps API", google.get("sitemaps", {}) or {}, [("Sitemaps", "count"), ("Items", "items")]),
        _card("Google URL Inspection", google.get("url_inspection", {}) or {}, [("Coverage state", "coverage_state"), ("Indexing state", "indexing_state"), ("Verdict", "verdict")]),
        _card("Google Search Analytics", google.get("search_analytics", {}) or {}, [("Rows", "rows")]),
        _card("Google Analytics 4", google.get("ga4", {}) or {}, [("Property ID", "property_id"), ("Report", "report")]),
    ])
    parts.append('</div>')
    return ''.join(parts)


def _remove_report_sections(soup: BeautifulSoup) -> None:
    headings = {
        "public / external data sources",
        "dns / rdap / public domain data",
        "google api results",
        "google & public api enrichment",
        "google data enrichment",
        "pagespeed insights",
    }
    for h2 in list(soup.find_all("h2")):
        title = h2.get_text(" ", strip=True).lower()
        if title in headings:
            parent = h2.find_parent("div", class_="site")
            if parent:
                parent.decompose()


def _remove_old_pipeline(soup: BeautifulSoup) -> None:
    for node in list(soup.find_all(id="google-api-results")):
        node.decompose()
    for node in list(soup.find_all(id="pagespeed-insights")):
        node.decompose()


def write_pipeline_report(source_html: Path, output_html: Path, google: dict[str, Any]) -> None:
    if not source_html.exists():
        raise FileNotFoundError(source_html)

    soup = BeautifulSoup(source_html.read_text(encoding="utf-8"), "html.parser")
    _remove_report_sections(soup)
    _remove_old_pipeline(soup)

    container = soup.find("div", class_="container") or soup.body
    if container is None:
        raise RuntimeError("Final report container not found")

    pagespeed = BeautifulSoup(_pagespeed_section(google.get("pagespeed", {}) or {}), "html.parser")
    private_google = BeautifulSoup(_google_private_section(google), "html.parser")

    # PageSpeed is deliberately inserted as its own top-level section so it
    # cannot be removed with the private Google API block.
    site_wide = None
    for h2 in container.find_all("h2"):
        if h2.get_text(" ", strip=True).lower() == "site-wide information":
            site_wide = h2.find_parent("div", class_="site")
            break

    if site_wide is not None:
        site_wide.insert_after(pagespeed)
    else:
        container.append(pagespeed)

    container.append(private_google)
    output_html.write_text(str(soup), encoding="utf-8")
