from __future__ import annotations

from html import escape
from pathlib import Path
from typing import Any


def _status(status: str) -> str:
    return escape(str(status or "DATA_NOT_AVAILABLE"))


def _detail(data: dict[str, Any]) -> str:
    reason = data.get("reason") or data.get("error")
    return f"<div class=\"report-note\"><b>Reason:</b> {escape(str(reason))}</div>" if reason else ""


def _score(value: Any) -> str:
    if value is None:
        return "Data not available"
    try:
        return str(round(float(value) * 100, 1))
    except (TypeError, ValueError):
        return escape(str(value))


def append_google_section(html_path: Path, google: dict[str, Any]) -> None:
    if not html_path.exists():
        return

    pagespeed = google.get("pagespeed", {}) or {}
    gsc = google.get("search_console", {}) or {}
    sitemaps = google.get("sitemaps", {}) or {}
    inspection = google.get("url_inspection", {}) or {}
    analytics = google.get("search_analytics", {}) or {}
    ga4 = google.get("ga4", {}) or {}

    ps = """
    <div class="site">
      <h2>Google PageSpeed Insights</h2>
      <p><b>Status:</b> %s</p>
      %s
      <table>
        <tr><th>Metric</th><th>Result</th></tr>
        <tr><td>Performance</td><td>%s</td></tr>
        <tr><td>Accessibility</td><td>%s</td></tr>
        <tr><td>Best Practices</td><td>%s</td></tr>
        <tr><td>SEO</td><td>%s</td></tr>
      </table>
    </div>
    """ % (
        _status(pagespeed.get("status")), _detail(pagespeed),
        _score(pagespeed.get("performance")), _score(pagespeed.get("accessibility")),
        _score(pagespeed.get("best_practices")), _score(pagespeed.get("seo")),
    )

    sections = [ps]
    private_specs = [
        ("Google Search Console", gsc, "property"),
        ("Google Sitemaps API", sitemaps, "count"),
        ("Google URL Inspection", inspection, "coverage_state"),
        ("Google Search Analytics", analytics, "rows"),
        ("Google Analytics 4", ga4, "property_id"),
    ]
    for title, item, key in private_specs:
        value = item.get(key)
        if isinstance(value, list):
            value = f"{len(value)} rows"
        elif isinstance(value, dict):
            value = "Available"
        elif value in (None, ""):
            value = "Data not available"
        sections.append(
            f'<div class="site"><h2>{escape(title)}</h2>'
            f'<p><b>Status:</b> {_status(item.get("status"))}</p>'
            f'{_detail(item)}'
            f'<p><b>Data:</b> {escape(str(value))}</p></div>'
        )

    block = "<div class=\"site\"><h2>Google Data Enrichment</h2><p>Google API results are shown here when available. Private sources are account/property dependent.</p></div>" + "".join(sections)
    html = html_path.read_text(encoding="utf-8")
    marker = "</div></body></html>"
    if marker in html:
        html = html.replace(marker, block + marker, 1)
    else:
        html += block
    html_path.write_text(html, encoding="utf-8")
