from __future__ import annotations

import json
from html import escape
from pathlib import Path
from typing import Any
from bs4 import BeautifulSoup


def _status(v: Any) -> str:
    return str(v or "DATA_NOT_AVAILABLE")


def _reason(item: dict[str, Any]) -> str:
    r = item.get("reason") or item.get("error")
    return str(r) if r else ""


def _score(v: Any) -> str:
    if v is None:
        return "Data not available"
    try:
        return str(round(float(v) * 100, 1))
    except (TypeError, ValueError):
        return str(v)


def _card(title: str, item: dict[str, Any], fields: list[tuple[str, str]]) -> str:
    out = [f'<div class="site"><h2>{escape(title)}</h2>', f'<p><b>Status:</b> {escape(_status(item.get("status")))}</p>']
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


def build_google_html(google: dict[str, Any]) -> str:
    ps = google.get("pagespeed", {}) or {}
    ps_html = f'''<div class="site"><h2>PageSpeed Insights</h2><p><b>Status:</b> {escape(_status(ps.get("status")))}</p>{f'<p><b>Reason:</b> {escape(_reason(ps))}</p>' if _reason(ps) else ''}<table><tr><th>Metric</th><th>Result</th></tr><tr><td>Performance</td><td>{escape(_score(ps.get("performance")))}</td></tr><tr><td>Accessibility</td><td>{escape(_score(ps.get("accessibility")))}</td></tr><tr><td>Best Practices</td><td>{escape(_score(ps.get("best_practices")))}</td></tr><tr><td>SEO</td><td>{escape(_score(ps.get("seo")))}</td></tr></table></div>'''
    cards = [
        _card("Google Search Console", google.get("search_console", {}) or {}, [("Property", "property")]),
        _card("Google Sitemaps API", google.get("sitemaps", {}) or {}, [("Sitemaps", "count"), ("Items", "items")]),
        _card("Google URL Inspection", google.get("url_inspection", {}) or {}, [("Coverage state", "coverage_state"), ("Indexing state", "indexing_state"), ("Verdict", "verdict")]),
        _card("Google Search Analytics", google.get("search_analytics", {}) or {}, [("Rows", "rows")]),
        _card("Google Analytics 4", google.get("ga4", {}) or {}, [("Property ID", "property_id"), ("Report", "report")]),
    ]
    return '<div id="google-api-results"><h2>Google API Results</h2>' + ps_html + ''.join(cards) + '</div>'


def write_pipeline_report(source_html: Path, output_html: Path, google: dict[str, Any]) -> None:
    soup = BeautifulSoup(source_html.read_text(encoding="utf-8"), "html.parser")
    for h2 in list(soup.find_all("h2")):
        title = h2.get_text(" ", strip=True).lower()
        if title in {"public / external data sources", "google & public api enrichment", "google data enrichment", "google api results", "pagespeed insights", "google search console", "google sitemaps api", "google url inspection", "google search analytics", "google analytics 4"}:
            parent = h2.find_parent("div", class_="site")
            if parent:
                parent.decompose()
    container = soup.find("div", class_="container") or soup.body
    if container is None:
        raise RuntimeError("Report container not found")
    old = soup.find(id="google-api-results")
    if old:
        old.decompose()
    frag = BeautifulSoup(build_google_html(google), "html.parser")
    container.append(frag)
    output_html.write_text(str(soup), encoding="utf-8")
