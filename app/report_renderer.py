from __future__ import annotations

import json
from html import escape
from pathlib import Path

from app.audit_engine import analyze


def _score_class(score: int) -> str:
    if score < 50:
        return "score-red"
    if score < 90:
        return "score-orange"
    return "score-green"


def _status_class(status: str) -> str:
    status = str(status or "").upper()
    if status in {"PASS", "SUCCESS", "CONFIRMED"}:
        return "status-pass"
    if status in {"DATA_NOT_AVAILABLE", "NOT_CONFIGURED", "SKIPPED", "NOT_FOUND", "TIMEOUT"}:
        return "status-muted"
    return "status-error"


def _badge(status: str) -> str:
    return f'<span class="badge {_status_class(status)}">{escape(str(status))}</span>'


def _render_value(value):
    if isinstance(value, (dict, list)):
        return f"<pre>{escape(json.dumps(value, indent=2, ensure_ascii=False, default=str))}</pre>"
    return escape(str(value))


def _api_section(data: dict) -> str:
    google = data.get("google_enrichment") or {}
    if not google:
        return '<div class="site"><h2>Google &amp; Public API Enrichment</h2><p>DATA NOT AVAILABLE: no enrichment results were attached to this report.</p></div>'

    rows = []
    detail_cards = []
    names = ["pagespeed", "search_console", "sitemaps", "url_inspection", "search_analytics", "ga4"]
    labels = {
        "pagespeed": "PageSpeed Insights",
        "search_console": "Google Search Console",
        "sitemaps": "Google Search Console Sitemaps",
        "url_inspection": "Google URL Inspection",
        "search_analytics": "Google Search Analytics",
        "ga4": "Google Analytics 4",
    }
    for name in names:
        item = google.get(name) or {"status": "DATA_NOT_AVAILABLE", "reason": "No result returned."}
        status = str(item.get("status", "DATA_NOT_AVAILABLE"))
        reason = item.get("reason") or item.get("error") or ""
        rows.append(f"<tr><td><b>{escape(labels[name])}</b></td><td>{_badge(status)}</td><td>{escape(str(reason))}</td></tr>")
        details = {k: v for k, v in item.items() if k not in {"status", "reason", "error"}}
        body = f"<p>Status: {_badge(status)}</p>"
        if reason:
            body += f"<p><b>Reason:</b> {escape(str(reason))}</p>"
        if item.get("error"):
            body += f"<p><b>Error:</b> {escape(str(item['error']))}</p>"
        if details:
            body += "<table>" + "".join(f"<tr><th>{escape(str(k))}</th><td>{_render_value(v)}</td></tr>" for k, v in details.items()) + "</table>"
        detail_cards.append(f"<div class=\"api-card\"><h3>{escape(labels[name])}</h3>{body}</div>")

    return f'''<div class="site"><h2>Google &amp; Public API Enrichment</h2>
<table><tr><th>Source</th><th>Status</th><th>Reason / Error</th></tr>{''.join(rows)}</table>
<div class="api-grid">{''.join(detail_cards)}</div></div>'''


def _backlinks_section(crawl_data: dict) -> str:
    backlinks = crawl_data.get("backlinks", {}) or {}
    layer1 = backlinks.get("layer1", {}) or {}
    layer2 = backlinks.get("layer2", {}) or {}
    l1_rows = []
    for item in layer1.get("backlinks", []) or []:
        src = escape(str(item.get("source_url", "")), quote=True)
        target = escape(str(item.get("target_url", "")), quote=True)
        domain = escape(str(item.get("referring_domain", "")))
        l1_rows.append(f'<tr><td><a href="{src}" target="_blank" rel="noopener noreferrer">{domain}</a></td><td>{target}</td></tr>')
    if not l1_rows:
        l1_rows.append('<tr><td colspan="2">No referring domains available.</td></tr>')
    l2_rows = []
    for item in layer2.get("backlinks", []) or []:
        src = escape(str(item.get("source_url", "")), quote=True)
        target = escape(str(item.get("target_url", "")), quote=True)
        anchor = escape(str(item.get("anchor_text", "")))
        rel = escape(str(item.get("rel", "")))
        l2_rows.append(f'<tr><td><a href="{src}" target="_blank" rel="noopener noreferrer">{escape(str(item.get("source_url", "")))}</a></td><td>{target}</td><td>{anchor}</td><td>{rel}</td></tr>')
    if not l2_rows:
        l2_rows.append('<tr><td colspan="4">No confirmed page-level backlinks found.</td></tr>')

    return f'''<div class="site"><h2>Backlinks</h2>
<p><b>Provider:</b> Common Crawl</p>
<p><b>Layer 1 Status:</b> {_badge(layer1.get("status", "DATA_NOT_AVAILABLE"))}</p>
<p><b>Total Backlinks:</b> {int(layer1.get("total_backlinks", 0) or 0)}</p>
<p><b>Referring Domains:</b> {int(layer1.get("referring_domains", 0) or 0)}</p>
<h3>Referring Domains</h3>
<table><tr><th>Source Domain</th><th>Target</th></tr>{''.join(l1_rows)}</table>
<h3>Layer 2 — Confirmed Page-Level Links</h3>
<p><b>Status:</b> {_badge(layer2.get("status", "DATA_NOT_AVAILABLE"))} &nbsp; <b>Confirmed Links:</b> {int(layer2.get("links_found", 0) or 0)}</p>
<table><tr><th>Source URL</th><th>Target URL</th><th>Anchor Text</th><th>Rel</th></tr>{''.join(l2_rows)}</table>
</div>'''


def render_html(data: dict) -> str:
    audit = analyze(data)
    summary = audit["summary"]
    site = audit.get("site", {}) or {}
    domain = site.get("domain", {}) or {}
    ssl = domain.get("ssl", {}) or {}
    tracking = ", ".join(site.get("tracking", [])) or "None detected"

    issue_rows = []
    for p in audit["pages"]:
        if not p["issues"]:
            continue
        messages = []
        for issue in p["issues"]:
            text = issue["message"]
            if issue.get("detail"):
                text += f" ({issue['detail']})"
            messages.append(escape(text))
        issue_rows.append(f'<tr><td><a href="{escape(p["url"], quote=True)}" target="_blank" rel="noopener noreferrer">{escape(p["url"])}</a></td><td>{", ".join(messages)}</td></tr>')
    if not issue_rows:
        issue_rows.append('<tr><td colspan="2">No issues detected.</td></tr>')

    public_data = data.get("pipeline", {}).get("layers", {}).get("public_external", {}) or {}
    public_pagespeed = (data.get("google_enrichment", {}) or {}).get("pagespeed", {}) or {}

    pagespeed_card = ""
    if public_pagespeed:
        status = public_pagespeed.get("status", "DATA_NOT_AVAILABLE")
        details = {k: v for k, v in public_pagespeed.items() if k not in {"status", "reason", "error"}}
        pagespeed_card = f'''<div class="site"><h2>PageSpeed Insights</h2><p>Status: {_badge(status)}</p>'''
        if public_pagespeed.get("reason"):
            pagespeed_card += f'<p><b>Reason:</b> {escape(str(public_pagespeed["reason"]))}</p>'
        if public_pagespeed.get("error"):
            pagespeed_card += f'<p><b>Error:</b> {escape(str(public_pagespeed["error"]))}</p>'
        if details:
            pagespeed_card += "<table>" + "".join(f"<tr><th>{escape(str(k))}</th><td>{_render_value(v)}</td></tr>" for k, v in details.items()) + "</table>"
        pagespeed_card += "</div>"

    site_lines = (
        f"<p><b>Platform:</b> {escape(str(site.get('platform') or 'Custom / Unknown'))}</p>"
        f"<p><b>Tracking / Pixels / Tags:</b> {escape(tracking)}</p>"
        f"<p><b>Average DOM Load:</b> {site.get('average_dom_loaded_seconds', 0)} seconds</p>"
        f"<p><b>Average Fully Loaded:</b> {site.get('average_fully_loaded_seconds', 0)} seconds</p>"
        f"<p><b>Domain:</b> {escape(str(domain.get('hostname') or ''))}</p>"
        f"<p><b>Domain Created:</b> {escape(str((domain.get('domain_age') or {}).get('created', 'Not detected')))}</p>"
        f"<p><b>IP:</b> {escape(', '.join(domain.get('ip_addresses', [])) or 'Not detected')}</p>"
        f"<p><b>Server:</b> {escape(str(domain.get('server') or 'Not disclosed'))}</p>"
        f"<p><b>SSL:</b> {escape('Enabled' if ssl.get('enabled') else 'Not verified')}</p>"
    )

    return f'''<!doctype html><html><head><meta charset="utf-8"><title>SEO Audit Report - Crawl {escape(str(data.get('crawl_id', '')))}</title><style>
body{{font-family:Arial,sans-serif;background:#f4f5f7;color:#222;margin:0;padding:35px}}.container{{max-width:1400px;margin:auto;background:#fff;padding:35px}}.metrics{{display:flex;gap:20px;margin:25px 0}}.metric{{flex:1;border:1px solid #ddd;padding:20px;border-radius:8px}}.metric strong{{display:block;font-size:30px;margin-top:8px}}.score-red{{color:#dc2626!important}}.score-orange{{color:#d97706!important}}.score-green{{color:#16a34a!important}}.site{{border:1px solid #ddd;padding:20px;margin-bottom:30px}}.api-grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(420px,1fr));gap:20px;margin-top:20px}}.api-card{{border:1px solid #ddd;padding:18px;border-radius:8px;background:#fafafa}}table{{width:100%;border-collapse:collapse;background:#fff}}th,td{{border:1px solid #ddd;padding:12px;text-align:left;vertical-align:top}}th{{background:#eee}}a{{color:#2563eb;text-decoration:underline}}a:hover{{text-decoration:none}}pre{{white-space:pre-wrap;word-break:break-word;font-size:12px;margin:0}}.badge{{display:inline-block;padding:4px 8px;border-radius:12px;font-size:12px;font-weight:700}}.status-pass{{background:#dcfce7;color:#166534}}.status-muted{{background:#e5e7eb;color:#374151}}.status-error{{background:#fee2e2;color:#991b1b}}
</style></head><body><div class="container"><h1>SEO Audit Report</h1><p>Crawl ID: {escape(str(data.get('crawl_id', '')))}</p><div class="metrics"><div class="metric">SEO Score<strong class="{_score_class(summary['seo_score'])}">{summary['seo_score']}/100</strong></div><div class="metric">Site Health<strong class="{_score_class(summary['site_health'])}">{summary['site_health']}/100</strong></div><div class="metric">Pages Crawled<strong>{summary['pages_crawled']}</strong></div></div><div class="site"><h2>Site-Wide Information</h2>{site_lines}</div>{pagespeed_card}{_api_section(data)}{_backlinks_section(data)}<h2>Issues by Page</h2><table><tr><th>Page</th><th>Problems / Opportunities</th></tr>{''.join(issue_rows)}</table></div></body></html>'''


def generate_report(data: dict, source_path) -> Path:
    source = Path(source_path)
    html_path = source.parent / f"complete_seo_report_{data.get('crawl_id', source.stem)}.html"
    html_path.write_text(render_html(data), encoding="utf-8")
    return html_path
