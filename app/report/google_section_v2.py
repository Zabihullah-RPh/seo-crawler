from __future__ import annotations
import re
from html import escape
from pathlib import Path
from typing import Any

def _status(status: Any) -> str:
    return escape(str(status or "DATA_NOT_AVAILABLE"))

def _detail(data: dict[str, Any]) -> str:
    reason = data.get("reason") or data.get("error")
    return f'<div class="report-note"><b>Reason:</b> {escape(str(reason))}</div>' if reason else ""

def _score(value: Any) -> str:
    if value is None:
        return "Data not available"
    try:
        return str(round(float(value) * 100, 1))
    except (TypeError, ValueError):
        return escape(str(value))

def _api_card(title: str, item: dict[str, Any], key: str | None = None) -> str:
    value = item.get(key) if key else None
    if isinstance(value, list):
        value = f"{len(value)} rows"
    elif isinstance(value, dict):
        value = "Available"
    elif value in (None, ""):
        value = "Data not available"
    return f'<div class="site"><h2>{escape(title)}</h2><p><b>Status:</b> {_status(item.get("status"))}</p>{_detail(item)}<p><b>Data:</b> {escape(str(value))}</p></div>'

def _remove_public_block(html: str) -> str:
    return re.sub(r'<div class="site"><h2>Public / External Data Sources</h2>.*?</div>', '', html, flags=re.S, count=1)

def _remove_api_blocks(html: str) -> str:
    return re.sub(r'<div class="site"><h2>Google (?:Data Enrichment|&amp; Public API Results)</h2>.*?(?=<div class="site"><h2>Backlinks</h2>|</div></body></html>)', '', html, flags=re.S, count=1)

def append_google_section(html_path: Path, google: dict[str, Any]) -> None:
    if not html_path.exists():
        return
    pagespeed = google.get("pagespeed", {}) or {}
    specs = [
        ("Google Search Console", google.get("search_console", {}) or {}, "property"),
        ("Google Sitemaps API", google.get("sitemaps", {}) or {}, "count"),
        ("Google URL Inspection", google.get("url_inspection", {}) or {}, "coverage_state"),
        ("Google Search Analytics", google.get("search_analytics", {}) or {}, "rows"),
        ("Google Analytics 4", google.get("ga4", {}) or {}, "property_id"),
    ]
    ps = f'''<div class="site"><h2>PageSpeed Insights</h2><p><b>Status:</b> {_status(pagespeed.get("status"))}</p>{_detail(pagespeed)}<table><tr><th>Metric</th><th>Result</th></tr><tr><td>Performance</td><td>{_score(pagespeed.get("performance"))}</td></tr><tr><td>Accessibility</td><td>{_score(pagespeed.get("accessibility"))}</td></tr><tr><td>Best Practices</td><td>{_score(pagespeed.get("best_practices"))}</td></tr><tr><td>SEO</td><td>{_score(pagespeed.get("seo"))}</td></tr></table></div>'''
    private = "".join(_api_card(title, item, key) for title, item, key in specs)
    block = f'<div class="site"><h2>Google API Results</h2><p>PageSpeed is public-by-URL. Search Console, Sitemaps, URL Inspection, Search Analytics, and GA4 depend on account/property access.</p></div>{ps}{private}'
    html = html_path.read_text(encoding="utf-8")
    html = _remove_public_block(html)
    html = _remove_api_blocks(html)
    marker = "</div></body></html>"
    html = html.replace(marker, block + marker, 1) if marker in html else html + block
    html_path.write_text(html, encoding="utf-8")
