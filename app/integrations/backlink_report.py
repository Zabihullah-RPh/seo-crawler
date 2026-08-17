from __future__ import annotations

from pathlib import Path


def finalize_backlink_html(html_path: Path, crawl_data: dict) -> Path:
    """Keep Layer 1 as referring domains and hide empty Layer 2 output."""
    html = html_path.read_text(encoding="utf-8")
    backlinks = crawl_data.get("backlinks", {}) or {}
    layer1 = backlinks.get("layer1", {}) or {}
    layer2 = backlinks.get("layer2", {}) or {}

    # Layer 1 is a domain-level graph: never label its referring-domain count as backlinks.
    html = html.replace(
        f'<p><b>Total Backlinks:</b> {int(layer1.get("total_backlinks", 0) or 0)}</p>',
        "",
    )

    # Layer 2 should be silent when it found nothing. Keep the Backlinks block and
    # Layer 1 referring domains, but remove the empty Layer 2 heading/status/table.
    if not (layer2.get("backlinks") or []):
        marker = '<h3>Layer 2 — Confirmed Page-Level Links</h3>'
        start = html.find(marker)
        if start >= 0:
            table_end = html.find('</table>', start)
            if table_end >= 0:
                html = html[:start] + html[table_end + len('</table>'):]

    html_path.write_text(html, encoding="utf-8")
    return html_path
