"""Attach runtime backlink data to an existing crawl audit/report."""

from __future__ import annotations

import json
import sys
from html import escape
from pathlib import Path

from app.integrations.backlinks import query_runtime_backlinks


def enrich(crawl_json: str) -> tuple[Path, Path]:
    source = Path(crawl_json)
    data = json.loads(source.read_text(encoding="utf-8"))
    result = query_runtime_backlinks(data.get("start_url") or data.get("site", {}).get("domain", {}).get("hostname", ""))
    data["backlinks"] = result
    source.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    audit_json = source.parent / f"audit_{source.stem.replace('crawl_', '')}.json"
    if audit_json.exists():
        audit = json.loads(audit_json.read_text(encoding="utf-8"))
        audit["backlinks"] = result
        audit_json.write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8")

    report = source.parent / f"complete_seo_report_{data.get('crawl_id')}.html"
    if not report.exists():
        return source, audit_json

    html = report.read_text(encoding="utf-8")
    rows = []
    for item in (result.get("backlinks") or [])[:1000]:
        src = escape(str(item.get("source_url") or ""), quote=True)
        target = escape(str(item.get("target_url") or ""), quote=True)
        anchor = escape(str(item.get("anchor_text") or ""))
        rel = escape(str(item.get("rel") or ""))
        rows.append(
            f'<tr><td><a href="{src}" target="_blank" rel="noopener noreferrer">{src or "—"}</a></td>'
            f'<td>{target or "—"}</td><td>{anchor or "—"}</td><td>{rel or "follow"}</td></tr>'
        )
    if not rows:
        rows.append('<tr><td colspan="4">No runtime backlink data available.</td></tr>')

    section = f'''<section class="backlinks-section" style="margin-top:30px">
<h2>Backlinks</h2>
<p><b>Provider:</b> {escape(str(result.get("provider") or "—"))}</p>
<p><b>Status:</b> {escape(str(result.get("status") or "—"))}</p>
<p><b>Total Backlinks:</b> {int(result.get("total_backlinks") or 0)}</p>
<p><b>Referring Domains:</b> {int(result.get("referring_domains") or 0)}</p>
<p>{escape(str(result.get("message") or ""))}</p>
<table><tr><th>Source URL</th><th>Target URL</th><th>Anchor Text</th><th>Rel</th></tr>{''.join(rows)}</table>
</section>'''

    marker = "</div></body></html>"
    if marker in html:
        html = html.replace(marker, section + marker, 1)
    else:
        html += section
    report.write_text(html, encoding="utf-8")
    return source, report


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("Usage: python -m app.integrations.backlink_report results/crawl_X.json")
    source, report = enrich(sys.argv[1])
    print(f"Backlink enrichment source: {source}")
    print(f"Backlink/report output: {report}")


if __name__ == "__main__":
    main()
