from __future__ import annotations

import json
import re
import sys
from collections import Counter, defaultdict
from html import escape
from pathlib import Path
from urllib.parse import urljoin, urlparse

from app.utils.urls import normalize_url, same_host


def load_crawl(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _issue(bucket, severity, message, detail=""):
    bucket.append((severity, message, detail))


def _build_link_graph(data):
    start_url = normalize_url(data.get("start_url", ""))
    incoming = Counter()
    outgoing = Counter()
    seen = set()
    page_urls = {normalize_url(p.get("url", "")) for p in data.get("pages", []) if p.get("url")}

    def add_link(source, target):
        source = normalize_url(source)
        if not source or not target:
            return
        absolute = urljoin(source, str(target).strip())
        target = normalize_url(absolute)
        if not target:
            return
        key = (source, target)
        if key in seen:
            return
        seen.add(key)
        if same_host(target, start_url):
            incoming[target] += 1
            outgoing[source] += 1

    for page in data.get("pages", []):
        source = page.get("url", "")
        for link in page.get("links", []) or []:
            if isinstance(link, dict):
                add_link(source, link.get("url") or link.get("target_url"))
            elif isinstance(link, str):
                add_link(source, link)
    if not seen:
        for link in data.get("links", []) or []:
            if isinstance(link, dict):
                add_link(link.get("source_url", ""), link.get("target_url", ""))
    return incoming, outgoing, page_urls


def analyze(data):
    pages = data.get("pages", [])
    site = data.get("site", {}) or {}
    incoming, outgoing, _ = _build_link_graph(data)
    findings = defaultdict(list)
    counts = Counter()
    total_checks = 0
    passed = 0

    def check(url, ok, severity, message, detail=""):
        nonlocal total_checks, passed
        total_checks += 1
        if ok:
            passed += 1
        else:
            findings[url].append((severity, message, detail))
            counts[severity] += 1

    for p in pages:
        url = p.get("url", "")
        status = int(p.get("status_code") or 0)
        title = (p.get("title") or "").strip()
        desc = (p.get("meta_description") or "").strip()
        h1 = p.get("h1s") or []
        words = int(p.get("word_count") or 0)
        imgs = p.get("images") or []
        canonical = (p.get("canonical") or "").strip()
        robots = (p.get("robots") or "").lower()
        schema = p.get("schemas") or []
        links_out = outgoing[normalize_url(url)]
        check(url, 200 <= status < 400, "critical" if status >= 500 else "high", f"HTTP {status}")
        check(url, bool(title), "critical", "Missing title")
        if title:
            check(url, 30 <= len(title) <= 60, "medium" if len(title) > 60 else "notice", "Long title" if len(title) > 60 else "Short title", f"{len(title)} characters")
        check(url, bool(desc), "medium", "Missing meta description")
        if desc:
            check(url, 70 <= len(desc) <= 160, "notice", "Long meta description" if len(desc) > 160 else "Short meta description", f"{len(desc)} characters")
        check(url, len(h1) == 1, "critical" if len(h1) == 0 else "medium", "No H1" if len(h1) == 0 else "Multiple H1s", f"{len(h1)} H1s")
        check(url, words >= 300, "medium" if words < 100 else "notice", "Very low word count" if words < 100 else "Low word count", f"{words} words")
        check(url, bool(canonical), "medium", "Missing canonical")
        if imgs:
            missing = sum(1 for i in imgs if not (i.get("alt") or "").strip())
            check(url, missing == 0, "medium" if missing > max(2, len(imgs) // 2) else "notice", f"{missing} images have no alt text", f"{missing}/{len(imgs)}")
        check(url, links_out > 0, "medium", "No internal links")
        check(url, "noindex" not in robots, "critical", "Noindex directive")
        check(url, bool(schema), "notice", "No structured data")
        check(url, bool(p.get("language")), "notice", "Missing HTML language")
        check(url, bool(p.get("viewport")), "notice", "Missing viewport")

    for field, label in (("title", "Duplicate title"), ("meta_description", "Duplicate meta description")):
        groups = defaultdict(list)
        for p in pages:
            v = (p.get(field) or "").strip().lower()
            if v:
                groups[v].append(p.get("url"))
        for urls in groups.values():
            if len(urls) > 1:
                for u in urls:
                    findings[u].append(("medium", label, f"{len(urls)} pages"))
                    counts["medium"] += 1

    weights = {"critical": 5.0, "high": 4.0, "medium": 2.5, "notice": 1.0}
    max_weight = sum(weights.values())
    page_scores = []
    for p in pages:
        url = p.get("url", "")
        fs = findings.get(url, [])
        penalty = sum(weights.get(s, 1) for s, _, _ in fs)
        raw = 100 - (penalty / max_weight * 100) if max_weight else 100
        page_scores.append(max(0, min(100, raw)))
    seo_score = round(sum(page_scores) / len(page_scores)) if page_scores else 0

    health_penalty = 0.0
    status_fail = sum(1 for p in pages if int(p.get("status_code") or 0) >= 400)
    noindex = sum(1 for p in pages if "noindex" in (p.get("robots") or "").lower())
    if status_fail:
        health_penalty += min(35, status_fail / max(len(pages), 1) * 35)
    if noindex:
        health_penalty += min(25, noindex / max(len(pages), 1) * 25)
    ssl = (site.get("domain") or {}).get("ssl") or {}
    if ssl.get("enabled") is False:
        health_penalty += 25
    if not site.get("domain", {}).get("hostname"):
        health_penalty += 10
    if not site.get("platform"):
        health_penalty += 5
    site_health = round(max(0, min(100, 100 - health_penalty)))

    page_rows = []
    for index, p in enumerate(pages):
        url = p.get("url", "")
        fs = findings.get(url, [])
        page_rows.append({
            "url": url,
            "issues": [{"severity": s, "message": m, "detail": d} for s, m, d in fs],
            "score": round(page_scores[index]) if page_scores else 0,
        })

    pages_crawled = len(pages)
    try:
        pages_discovered = int(data.get("site", {}).get("pages_discovered", pages_crawled) or pages_crawled)
    except (TypeError, ValueError):
        pages_discovered = pages_crawled
    pages_discovered = max(pages_discovered, pages_crawled)
    coverage = (pages_crawled / pages_discovered * 100) if pages_discovered else 0
    crawl_coverage_score = 10 if coverage >= 98 else 9 if coverage >= 95 else 7

    return {
        "summary": {
            "seo_score": seo_score, "site_health": site_health,
            "pages_crawled": pages_crawled, "pages_discovered": pages_discovered,
            "crawl_coverage_score": crawl_coverage_score,
            "critical": counts["critical"], "high": counts["high"],
            "medium": counts["medium"], "notice": counts["notice"],
            "total_issues": sum(counts.values()), "checks": total_checks, "passed": passed,
        },
        "site": site, "pages": page_rows,
    }


def _score_class(score):
    if score < 50: return "score-red"
    if score < 90: return "score-orange"
    return "score-green"


def _crawl_coverage_class(score):
    if score == 10: return "score-green"
    if score == 9: return "score-orange"
    return "score-red"


def _status_class(status):
    status = str(status or "").upper()
    if status == "PASS" or status in {"SUCCESS", "CONFIRMED"}: return "status-pass"
    if status in {"DATA_NOT_AVAILABLE", "NOT_CONFIGURED", "SKIPPED", "NOT_FOUND", "TIMEOUT"}: return "status-muted"
    return "status-error"


def _status_badge(status):
    status = str(status or "DATA_NOT_AVAILABLE")
    return f'<span class="badge {_status_class(status)}">{escape(status)}</span>'


def _kv_rows(items):
    if isinstance(items, dict):
        iterable = items.items()
    else:
        iterable = items or []
    rows = []
    for pair in iterable:
        if isinstance(pair, (list, tuple)) and len(pair) == 2:
            key, value = pair
        else:
            key, value = "data", pair
        if isinstance(value, (dict, list)):
            rendered = escape(json.dumps(value, indent=2, ensure_ascii=False, default=str))
            rendered = f'<pre>{rendered}</pre>'
        else:
            rendered = escape(str(value))
        rows.append(f"<tr><th>{escape(str(key))}</th><td>{rendered}</td></tr>")
    return "".join(rows)


def _google_section(data):
    google = data.get("google_enrichment", {}) or {}
    if not google:
        return '<div class="site"><h2>Google API Results</h2><p>Data not available.</p></div>'
    rows = []
    for name in ("oauth", "pagespeed", "search_console", "sitemaps", "url_inspection", "search_analytics", "ga4"):
        item = google.get(name) or {"status": "DATA_NOT_AVAILABLE", "reason": "No result returned."}
        status = item.get("status", "DATA_NOT_AVAILABLE")
        rows.append(f"<tr><td><b>{escape(name.replace('_', ' ').title())}</b></td><td>{_status_badge(status)}</td><td>{escape(str(item.get('reason') or item.get('error') or ''))}</td></tr>")

    detail_blocks = []
    for name in ("pagespeed", "search_console", "sitemaps", "url_inspection", "search_analytics", "ga4"):
        item = google.get(name)
        if not item: continue
        status = item.get("status", "DATA_NOT_AVAILABLE")
        detail = {k: v for k, v in item.items() if k not in {"status", "reason", "error"}}
        heading = name.replace('_', ' ').title()
        body = f"<p>Status: {_status_badge(status)}</p>"
        if item.get("reason"): body += f"<p><b>Reason:</b> {escape(str(item['reason']))}</p>"
        if item.get("error"): body += f"<p><b>Error:</b> {escape(str(item['error']))}</p>"
        if detail: body += f"<table>{_kv_rows(detail)}</table>"
        detail_blocks.append(f"<div class=\"api-card\"><h3>{escape(heading)}</h3>{body}</div>")
    return f'''<div class="site"><h2>Google API Results</h2><table><tr><th>Source</th><th>Status</th><th>Reason / Error</th></tr>{''.join(rows)}</table><div class="api-grid">{''.join(detail_blocks)}</div></div>'''


def html_report(audit_data, crawl_data):
    s = audit_data["summary"]; site = audit_data.get("site", {})
    rows = []
    for p in audit_data["pages"]:
        if not p["issues"]: continue
        parts = []
        for i in p["issues"]:
            text = i["message"] + (f" ({i['detail']})" if i.get("detail") else "")
            parts.append(escape(text))
        u = escape(p["url"], quote=True)
        rows.append(f'<tr><td><a href="{u}" target="_blank" rel="noopener noreferrer">{escape(p["url"])}</a></td><td>{", ".join(parts)}</td></tr>')
    if not rows: rows.append('<tr><td colspan="2">No issues detected.</td></tr>')
    tracking = ", ".join(site.get("tracking", [])) or "None detected"
    domain = site.get("domain", {}) or {}; ssl = domain.get("ssl", {}) or {}
    platform = site.get("platform") or "Custom / Unknown"
    site_lines = f"<p><b>Platform:</b> {escape(platform)}</p><p><b>Tracking / Pixels / Tags:</b> {escape(tracking)}</p><p><b>Average DOM Load:</b> {site.get('average_dom_loaded_seconds', 0)} seconds</p><p><b>Average Fully Loaded:</b> {site.get('average_fully_loaded_seconds', 0)} seconds</p><p><b>Domain:</b> {escape(domain.get('hostname', ''))}</p><p><b>Domain Created:</b> {escape(str((domain.get('domain_age') or {}).get('created', 'Not detected')))}</p><p><b>IP:</b> {escape(', '.join(domain.get('ip_addresses', [])) or 'Not detected')}</p><p><b>Server:</b> {escape(str(domain.get('server') or 'Not disclosed'))}</p><p><b>SSL:</b> {escape('Enabled' if ssl.get('enabled') else 'Not verified')}</p>"
    backlinks = crawl_data.get("backlinks", {}) or {}; layer1 = backlinks.get("layer1", {}) or {}; layer2 = backlinks.get("layer2", {}) or {}
    l1_rows = []
    for item in layer1.get("backlinks", []) or []:
        src = escape(item.get("source_url", ""), quote=True); tgt = escape(item.get("target_url", ""), quote=True)
        l1_rows.append(f'<tr><td><a href="{src}" target="_blank" rel="noopener noreferrer">{escape(item.get("referring_domain", ""))}</a></td><td>{tgt}</td></tr>')
    if not l1_rows: l1_rows.append('<tr><td colspan="2">No Layer 1 referring domains available.</td></tr>')
    l2_rows = []
    for item in layer2.get("backlinks", []) or []:
        src = escape(item.get("source_url", ""), quote=True); tgt = escape(item.get("target_url", ""), quote=True); anchor = escape(item.get("anchor_text", "")); rel = escape(item.get("rel", ""))
        l2_rows.append(f'<tr><td><a href="{src}" target="_blank" rel="noopener noreferrer">{escape(item.get("source_url", ""))}</a></td><td>{tgt}</td><td>{anchor}</td><td>{rel}</td></tr>')
    if not l2_rows: l2_rows.append('<tr><td colspan="4">No confirmed page-level backlinks found.</td></tr>')
    backlink_section = f'''<div class="site"><h2>Backlinks</h2><p><b>Provider:</b> Common Crawl</p><p><b>Layer 1 Status:</b> {escape(str(layer1.get("status", "unknown")))}</p><p><b>Total Backlinks:</b> {int(layer1.get("total_backlinks", 0) or 0)}</p><p><b>Referring Domains:</b> {int(layer1.get("referring_domains", 0) or 0)}</p><h3>Referring Domains</h3><table><tr><th>Source Domain</th><th>Target</th></tr>{''.join(l1_rows)}</table><h3>Layer 2 — Confirmed Page-Level Links</h3><p><b>Status:</b> {escape(str(layer2.get("status", "unknown")))} &nbsp; <b>Confirmed Links:</b> {int(layer2.get("links_found", 0) or 0)}</p><table><tr><th>Source URL</th><th>Target URL</th><th>Anchor Text</th><th>Rel</th></tr>{''.join(l2_rows)}</table></div>'''
    google_section = _google_section(crawl_data)
    return f'''<!doctype html><html><head><meta charset="utf-8"><title>SEO Audit Report - Crawl {crawl_data.get('crawl_id')}</title><style>body{{font-family:Arial,sans-serif;background:#f4f5f7;color:#222;margin:0;padding:35px}}.container{{max-width:1400px;margin:auto;background:#fff;padding:35px}}.metrics{{display:flex;gap:20px;margin:25px 0}}.metric{{flex:1;border:1px solid #ddd;padding:20px;border-radius:8px}}.metric strong{{display:block;font-size:30px;margin-top:8px}}.score-red{{color:#dc2626!important}}.score-orange{{color:#d97706!important}}.score-green{{color:#16a34a!important}}table{{width:100%;border-collapse:collapse;background:#fff}}th,td{{border:1px solid #ddd;padding:12px;text-align:left;vertical-align:top}}th{{background:#eee}}a{{color:#2563eb}}.badge{{display:inline-block;padding:4px 8px;border-radius:999px;font-size:12px;font-weight:700}}.status-pass{{background:#dcfce7;color:#166534}}.status-muted{{background:#f3f4f6;color:#374151}}.status-error{{background:#fee2e2;color:#991b1b}}.api-grid{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px;margin-top:18px}}.api-card{{border:1px solid #ddd;border-radius:8px;padding:16px;background:#fafafa}}pre{{white-space:pre-wrap;word-break:break-word;font-size:12px}}</style></head><body><div class="container"><h1>SEO Audit Report</h1><p>Crawl ID: {crawl_data.get('crawl_id')}</p><div class="metrics"><div class="metric">SEO Score<strong class="{_score_class(s['seo_score'])}">{s['seo_score']}/100</strong></div><div class="metric">Site Health<strong class="{_score_class(s['site_health'])}">{s['site_health']}/100</strong></div><div class="metric">Pages Crawled<strong>{s['pages_crawled']}/{s['pages_discovered']}</strong></div></div><div class="site"><h2>Site-Wide Information</h2>{site_lines}</div>{google_section}{backlink_section}<h2>Issues by Page</h2><table><tr><th>Page</th><th>Problems / Opportunities</th></tr>{''.join(rows)}</table></div></body></html>'''


def generate_report(data, source_path):
    result = analyze(data)
    source = Path(source_path)
    json_path = source.parent / f"audit_{data.get('crawl_id')}.json"
    html_path = source.parent / f"complete_seo_report_{data.get('crawl_id')}.html"
    json_path.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    html_path.write_text(html_report(result, data), encoding="utf-8")
    return html_path


def main():
    if len(sys.argv) < 2:
        print("Usage: python audit_engine.py results/crawl_X.json"); sys.exit(1)
    path = Path(sys.argv[1]); html = generate_report(load_crawl(path), path); print(f"Report: {html}")

if __name__ == "__main__": main()
