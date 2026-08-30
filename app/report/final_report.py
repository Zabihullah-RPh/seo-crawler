from __future__ import annotations

import json
from html import escape
from pathlib import Path
from typing import Any

from app.audit_engine import analyze


def _score(v: Any) -> str:
    if v is None:
        return "Data not available"
    try:
        return str(round(float(v) * 100, 1))
    except (TypeError, ValueError):
        return escape(str(v))


def _status(v: Any) -> str:
    return escape(str(v or "DATA_NOT_AVAILABLE"))


def _reason(item: dict[str, Any]) -> str:
    r = item.get("reason") or item.get("error")
    return f"<p><b>Reason:</b> {escape(str(r))}</p>" if r else ""


def _data_value(value: Any) -> str:
    if isinstance(value, list):
        return f"{len(value)} rows"
    if isinstance(value, dict):
        return "Available"
    if value in (None, ""):
        return "Data not available"
    return str(value)


def render(data: dict[str, Any], output_path: Path) -> Path:
    audit = analyze(data)
    site = audit.get("site", {}) or {}
    summary = audit.get("summary", {}) or {}
    tracking = ", ".join(site.get("tracking", [])) or "None detected"
    domain = site.get("domain", {}) or {}
    ssl = domain.get("ssl", {}) or {}

    issues = []
    for p in audit.get("pages", []):
        if not p.get("issues"):
            continue
        text = ", ".join(
            f"{i['message']}" + (f" ({i['detail']})" if i.get("detail") else "")
            for i in p["issues"]
        )
        issues.append(f'<tr><td>{escape(p.get("url", ""))}</td><td>{escape(text)}</td></tr>')
    if not issues:
        issues.append('<tr><td colspan="2">No issues detected.</td></tr>')

    backlinks = data.get("backlinks", {}) or {}
    layer1 = backlinks.get("layer1", {}) or {}
    layer2 = backlinks.get("layer2", {}) or {}
    l1 = []
    for item in layer1.get("backlinks", []) or []:
        l1.append(f'<tr><td>{escape(str(item.get("referring_domain", "")))}</td><td>{escape(str(item.get("target_url", "")))}</td></tr>')
    if not l1:
        l1.append('<tr><td colspan="2">No referring domains available.</td></tr>')
    l2 = []
    for item in layer2.get("backlinks", []) or []:
        l2.append(f'<tr><td>{escape(str(item.get("source_url", "")))}</td><td>{escape(str(item.get("target_url", "")))}</td><td>{escape(str(item.get("anchor_text", "")))}</td><td>{escape(str(item.get("rel", "")))}</td></tr>')
    if not l2:
        l2.append('<tr><td colspan="4">No confirmed page-level backlinks found.</td></tr>')

    google = data.get("google_enrichment", {}) or {}
    ps = google.get("pagespeed", {}) or {}
    api_specs = [
        ("Google Search Console", google.get("search_console", {}) or {}, "property"),
        ("Google Sitemaps API", google.get("sitemaps", {}) or {}, "count"),
        ("Google URL Inspection", google.get("url_inspection", {}) or {}, "coverage_state"),
        ("Google Search Analytics", google.get("search_analytics", {}) or {}, "rows"),
        ("Google Analytics 4", google.get("ga4", {}) or {}, "property_id"),
    ]
    api_cards = []
    for title, item, key in api_specs:
        api_cards.append(f'<div class="card"><h3>{escape(title)}</h3><p><b>Status:</b> {_status(item.get("status"))}</p>{_reason(item)}<p><b>Data:</b> {escape(_data_value(item.get(key)))}</p></div>')

    html = f'''<!doctype html><html><head><meta charset="utf-8"><title>SEO Audit Report - Crawl {escape(str(data.get('crawl_id', '')))}</title><style>
body{{font-family:Arial,sans-serif;background:#f4f5f7;color:#222;margin:0;padding:35px}}.container{{max-width:1400px;margin:auto;background:#fff;padding:35px}}.metrics{{display:flex;gap:20px;margin:25px 0}}.metric{{flex:1;border:1px solid #ddd;padding:20px;border-radius:8px}}.metric strong{{display:block;font-size:30px;margin-top:8px}}.site{{border:1px solid #ddd;padding:20px;margin-bottom:30px}}.card{{border:1px solid #ddd;padding:16px;border-radius:8px;background:#fafafa;margin-bottom:12px}}.api-grid{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:15px}}table{{width:100%;border-collapse:collapse;background:#fff}}th,td{{border:1px solid #ddd;padding:12px;text-align:left;vertical-align:top}}th{{background:#eee}}.status{{font-weight:700}}
</style></head><body><div class="container"><h1>SEO Audit Report</h1><p>Crawl ID: {escape(str(data.get('crawl_id','')))}</p><div class="metrics"><div class="metric">SEO Score<strong>{summary.get('seo_score',0)}/100</strong></div><div class="metric">Site Health<strong>{summary.get('site_health',0)}/100</strong></div><div class="metric">Pages Crawled<strong>{summary.get('pages_crawled',0)}/{summary.get('pages_discovered',0)}</strong></div></div>
<div class="site"><h2>Site-Wide Information</h2><p><b>Platform:</b> {escape(str(site.get('platform') or 'Custom / Unknown'))}</p><p><b>Tracking / Pixels / Tags:</b> {escape(tracking)}</p><p><b>Average DOM Load:</b> {site.get('average_dom_loaded_seconds',0)} seconds</p><p><b>Average Fully Loaded:</b> {site.get('average_fully_loaded_seconds',0)} seconds</p><p><b>Domain:</b> {escape(str(domain.get('hostname','')))}</p><p><b>Domain Created:</b> {escape(str((domain.get('domain_age') or {}).get('created','Not detected')))}</p><p><b>IP:</b> {escape(', '.join(domain.get('ip_addresses',[])) or 'Not detected')}</p><p><b>Server:</b> {escape(str(domain.get('server') or 'Not disclosed'))}</p><p><b>SSL:</b> {escape('Enabled' if ssl.get('enabled') else 'Not verified')}</p></div>
<div class="site"><h2>PageSpeed Insights</h2><p><b>Status:</b> {_status(ps.get('status'))}</p>{_reason(ps)}<table><tr><th>Metric</th><th>Result</th></tr><tr><td>Performance</td><td>{_score(ps.get('performance'))}</td></tr><tr><td>Accessibility</td><td>{_score(ps.get('accessibility'))}</td></tr><tr><td>Best Practices</td><td>{_score(ps.get('best_practices'))}</td></tr><tr><td>SEO</td><td>{_score(ps.get('seo'))}</td></tr></table></div>
<div class="site"><h2>Google Data</h2><p>PageSpeed Insights is public-by-URL. The other Google sources depend on account/property access.</p><div class="api-grid">{''.join(api_cards)}</div></div>
<div class="site"><h2>Backlinks</h2><p><b>Provider:</b> Common Crawl</p><p><b>Total Backlinks:</b> {int(layer1.get('total_backlinks',0) or 0)}</p><p><b>Referring Domains:</b> {int(layer1.get('referring_domains',0) or 0)}</p><h3>Referring Domains</h3><table><tr><th>Source Domain</th><th>Target</th></tr>{''.join(l1)}</table><h3>Layer 2 — Confirmed Page-Level Links</h3><p><b>Status:</b> {escape(str(layer2.get('status','unknown')))} <b>Confirmed Links:</b> {int(layer2.get('links_found',0) or 0)}</p><table><tr><th>Source URL</th><th>Target URL</th><th>Anchor Text</th><th>Rel</th></tr>{''.join(l2)}</table></div>
<h2>Issues by Page</h2><table><tr><th>Page</th><th>Problems / Opportunities</th></tr>{''.join(issues)}</table></div></body></html>'''
    output_path.write_text(html, encoding="utf-8")
    return output_path
