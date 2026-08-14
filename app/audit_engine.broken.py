import json
import sys
from pathlib import Path
from collections import Counter, defaultdict
from html import escape


def load_crawl(path):
    return json.loads(
        Path(path).read_text(encoding="utf-8")
    )


def audit(data):

    pages = data.get("pages", [])
    links = data.get("links", [])
    failed_urls = data.get("failed_urls", [])

    issues = []

    def add(category, severity, issue, url="", details=""):
        issues.append({
            "category": category,
            "severity": severity,
            "issue": issue,
            "url": url,
            "details": details
        })

    # ---------------------------------------------------------
    # BASIC PAGE ANALYSIS
    # ---------------------------------------------------------

    titles = Counter()
    descriptions = Counter()
    h1s = Counter()

    for page in pages:

        url = page.get("url", "")

        title = (page.get("title") or "").strip()
        description = (
            page.get("description")
            or page.get("meta_description")
            or ""
        ).strip()

        page_h1s = page.get("h1s", [])

        if title:
            titles[title.lower()] += 1

        if description:
            descriptions[
                description.lower()
            ] += 1

        for h1 in page_h1s:
            if h1:
                h1s[h1.lower()] += 1

        # TITLE
        if not title:
            add(
                "On-Page SEO",
                "ERROR",
                "Missing title tag",
                url
            )

        elif len(title) < 30:
            add(
                "On-Page SEO",
                "WARNING",
                "Title tag too short",
                url,
                f"{len(title)} characters"
            )

        elif len(title) > 60:
            add(
                "On-Page SEO",
                "WARNING",
                "Title tag too long",
                url,
                f"{len(title)} characters"
            )

        # META DESCRIPTION
        if not description:
            add(
                "On-Page SEO",
                "ERROR",
                "Missing meta description",
                url
            )

        elif len(description) < 70:
            add(
                "On-Page SEO",
                "NOTICE",
                "Meta description too short",
                url,
                f"{len(description)} characters"
            )

        elif len(description) > 160:
            add(
                "On-Page SEO",
                "WARNING",
                "Meta description too long",
                url,
                f"{len(description)} characters"
            )

        # H1
        if len(page_h1s) == 0:
            add(
                "On-Page SEO",
                "ERROR",
                "Missing H1",
                url
            )

        elif len(page_h1s) > 1:
            add(
                "On-Page SEO",
                "WARNING",
                "Multiple H1 headings",
                url,
                f"{len(page_h1s)} H1 headings"
            )

        # CONTENT
        words = int(
            page.get("word_count") or 0
        )

        if words < 100:
            add(
                "Content",
                "ERROR",
                "Very thin content",
                url,
                f"{words} words"
            )

        elif words < 300:
            add(
                "Content",
                "WARNING",
                "Low word count",
                url,
                f"{words} words"
            )

        # CANONICAL
        if not page.get("canonical"):
            add(
                "Indexability",
                "WARNING",
                "Missing canonical",
                url
            )

        # IMAGES
        image_count = int(
            page.get("images_count") or
            len(page.get("images", []))
        )

        for image in page.get("images", []):

            if not (
                image.get("alt")
                or ""
            ).strip():

                add(
                    "Images",
                    "WARNING",
                    "Image missing ALT text",
                    url,
                    image.get("url", "")
                )

        # SCHEMA
        schema_count = int(
            page.get("schema_count") or
            len(page.get("schemas", []))
        )

        if schema_count == 0:
            add(
                "Structured Data",
                "NOTICE",
                "No JSON-LD structured data detected",
                url
            )

    # ---------------------------------------------------------
    # DUPLICATES
    # ---------------------------------------------------------

    for title, count in titles.items():

        if count > 1:

            for page in pages:

                current = (
                    page.get("title") or ""
                ).strip().lower()

                if current == title:

                    add(
                        "Duplicates",
                        "WARNING",
                        "Duplicate title tag",
                        page.get("url", ""),
                        f"{count} pages"
                    )

    for description, count in descriptions.items():

        if count > 1:

            for page in pages:

                current = (
                    page.get("description")
                    or page.get("meta_description")
                    or ""
                ).strip().lower()

                if current == description:

                    add(
                        "Duplicates",
                        "WARNING",
                        "Duplicate meta description",
                        page.get("url", ""),
                        f"{count} pages"
                    )

    # ---------------------------------------------------------
    # LINKS
    # ---------------------------------------------------------

    incoming = Counter()
    outgoing = Counter()

    crawled_urls = {
        p.get("url")
        for p in pages
    }

    for link in links:

        source = link.get("source", "")
        destination = link.get(
            "destination",
            link.get("url", "")
        )

        if link.get("internal"):

            outgoing[source] += 1
            incoming[destination] += 1

    for page in pages:

        url = page.get("url", "")

        if outgoing[url] == 0:

            add(
                "Internal Linking",
                "WARNING",
                "Page has no internal outgoing links",
                url
            )

        if (
            url != data.get("start_url")
            and incoming[url] == 0
        ):

            add(
                "Internal Linking",
                "ERROR",
                "Potential orphan page",
                url
            )

    # ---------------------------------------------------------
    # BROKEN LINKS
    # ---------------------------------------------------------

    for url in failed_urls:

        add(
            "Crawlability",
            "ERROR",
            "Broken URL / failed crawl",
            url
        )

    # ---------------------------------------------------------
    # STATUS
    # ---------------------------------------------------------

    for page in pages:

        status = int(
            page.get("status_code") or 0
        )

        url = page.get("url", "")

        if 400 <= status < 500:

            add(
                "Crawlability",
                "ERROR",
                "4xx HTTP status",
                url,
                str(status)
            )

        elif status >= 500:

            add(
                "Crawlability",
                "ERROR",
                "5xx HTTP status",
                url,
                str(status)
            )

        elif 300 <= status < 400:

            add(
                "Crawlability",
                "WARNING",
                "Redirected URL",
                url,
                str(status)
            )

    # ---------------------------------------------------------
    # SUMMARY
    # ---------------------------------------------------------

    severity_counts = Counter(
        x["severity"]
        for x in issues
    )

    category_counts = Counter(
        x["category"]
        for x in issues
    )

    total_pages = len(pages)

    errors = severity_counts["ERROR"]
    warnings = severity_counts["WARNING"]
    notices = severity_counts["NOTICE"]

    # Simple technical health score.
    if total_pages:

        penalty = (
            errors * 3
            + warnings * 1.5
            + notices * 0.25
        )

        score = max(
            0,
            min(
                100,
                round(
                    100 -
                    (
                        penalty /
                        max(total_pages, 1)
                    ) * 10
                )
            )
        )

    else:

        score = 0

    return {
        "summary": {
            "health_score": score,
            "pages_crawled": total_pages,
            "pages_discovered":
                data.get("summary", {}).get(
                    "discovered",
                    total_pages
                ),
            "errors": errors,
            "warnings": warnings,
            "notices": notices,
            "total_issues": len(issues)
        },
        "categories": dict(
            category_counts
        ),
        "severity": dict(
            severity_counts
        ),
        "issues": issues
    }


def html_report(audit_data, crawl_data):

    summary = audit_data["summary"]
    issues = audit_data["issues"]

    rows = []

    for issue in issues:

        rows.append(
            "<tr>"
            f"<td class=""{escape(issue['severity'])}"">{escape(issue['severity'])}</td>"
            f"<td>{escape(issue['category'])}</td>"
            f"<td>{escape(issue['issue'])}</td>"
            f"<td>{escape(issue['url'])}</td>"
            f"<td>{escape(issue['details'])}</td>"
            "</tr>"
        )

    category_rows = []

    for category, count in sorted(
        audit_data["categories"].items(),
        key=lambda x: -x[1]
    ):

        category_rows.append(
            "<tr>"
            f"<td>{escape(category)}</td>"
            f"<td>{count}</td>"
            "</tr>"
        )

    return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>SEO Audit Report</title>
<style>
body {{
    font-family: Arial, sans-serif;
    margin: 40px;
    background: #f5f6f8;
    color: #222;
}}
h1 {{
    margin-bottom: 5px;
}}
.container {{
    max-width: 1400px;
    margin: auto;
}}
.cards {{
    display: grid;
    grid-template-columns: repeat(5, 1fr);
    gap: 15px;
    margin: 25px 0;
}}
.card {{
    background: white;
    padding: 20px;
    border-radius: 8px;
    box-shadow: 0 1px 5px #ccc;
}}
.number {{
    font-size: 30px;
    font-weight: bold;
}}
table {{
    width: 100%;
    border-collapse: collapse;
    background: white;
    margin: 20px 0 40px;
}}
th, td {{
    padding: 10px;
    border: 1px solid #ddd;
    text-align: left;
    vertical-align: top;
}}
th {{
    background: #eee;
}}
.ERROR {{
    font-weight: bold;
    color: #dc2626;
}}

.WARNING {{
    font-weight: bold;
    color: #f97316;
}}

.NOTICE {{
    font-weight: bold;
    color: #eab308;
}}
</style>
</head>

<body>
<div class="container">

<h1>SEO Audit Report</h1>
<p>{escape(crawl_data.get("start_url", ""))}</p>

<div class="cards">

<div class="card">
Health Score
<div class="number">{summary["health_score"]}/100</div>
</div>

<div class="card">
Pages Crawled
<div class="number">{summary["pages_crawled"]}</div>
</div>

<div class="card">
Errors
<div class="number">{summary["errors"]}</div>
</div>

<div class="card">
Warnings
<div class="number">{summary["warnings"]}</div>
</div>

<div class="card">
Notices
<div class="number">{summary["notices"]}</div>
</div>

</div>

<h2>Issue Distribution</h2>

<table>
<tr>
<th>Category</th>
<th>Issues</th>
</tr>
{"".join(category_rows)}
</table>

<h2>All SEO Issues</h2>

<table>
<tr>
<th>Severity</th>
<th>Category</th>
<th>Issue</th>
<th>URL</th>
<th>Details</th>
</tr>
{"".join(rows)}
</table>

</div>
</body>
</html>"""


def main():

    if len(sys.argv) < 2:

        print(
            "Usage: python audit_engine.py "
            "results/crawl_X.json"
        )

        sys.exit(1)

    source = Path(sys.argv[1])

    crawl = load_crawl(source)

    result = audit(crawl)

    source.parent.mkdir(
        exist_ok=True
    )

    json_path = source.parent / (
        source.stem.replace(
            "crawl_",
            "audit_"
        ) + ".json"
    )

    html_path = source.parent / (
        source.stem.replace(
            "crawl_",
            "audit_"
        ) + ".html"
    )

    json_path.write_text(
        json.dumps(
            result,
            indent=2,
            ensure_ascii=False
        ),
        encoding="utf-8"
    )

    html_path.write_text(
        html_report(
            result,
            crawl
        ),
        encoding="utf-8"
    )

    print("")
    print("========== SEO AUDIT COMPLETE ==========")
    print(
        f"Health Score: "
        f"{result['summary']['health_score']}/100"
    )
    print(
        f"Pages: "
        f"{result['summary']['pages_crawled']}"
    )
    print(
        f"Errors: "
        f"{result['summary']['errors']}"
    )
    print(
        f"Warnings: "
        f"{result['summary']['warnings']}"
    )
    print(
        f"Notices: "
        f"{result['summary']['notices']}"
    )
    print(
        f"Total Issues: "
        f"{result['summary']['total_issues']}"
    )
    print("")
    print(f"JSON: {json_path}")
    print(f"HTML: {html_path}")


if __name__ == "__main__":
    main()


