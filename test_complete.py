import asyncio
import json
import re
import sqlite3
import subprocess
import time
from pathlib import Path
from urllib.parse import urlparse

from playwright.async_api import async_playwright


CRAWL_ID = 22
DB = "data/crawler.db"

RESULTS = Path("results")
RESULTS.mkdir(exist_ok=True)

JSON_OUT = RESULTS / f"complete_test_{CRAWL_ID}.json"
REPORT_OUT = RESULTS / f"complete_seo_report_{CRAWL_ID}.html"


def get_pages():
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row

    rows = con.execute(
        "SELECT url FROM pages WHERE crawl_id = ? ORDER BY id",
        (CRAWL_ID,),
    ).fetchall()

    con.close()
    return [r["url"] for r in rows]


def clean(value):
    return (value or "").strip()


def get_schema_types(html):
    found = set()

    matches = re.findall(
        r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        html or "",
        flags=re.I | re.S,
    )

    def walk(obj):
        if isinstance(obj, dict):
            value = obj.get("@type")

            if isinstance(value, list):
                found.update(str(x) for x in value)
            elif value:
                found.add(str(value))

            for v in obj.values():
                walk(v)

        elif isinstance(obj, list):
            for v in obj:
                walk(v)

    for raw in matches:
        try:
            walk(json.loads(raw.strip()))
        except Exception:
            pass

    return sorted(found)


def detect_tracking(html, scripts):
    text = (
        (html or "")
        + "\n"
        + "\n".join(scripts)
    ).lower()

    signatures = {
        "Google Analytics": [
            "google-analytics.com",
            "gtag(",
            "google_analytics",
        ],
        "Google Tag Manager": [
            "googletagmanager.com",
            "gtm.js",
        ],
        "Google Ads": [
            "googleadservices.com",
            "google_conversion",
            "googlesyndication.com",
        ],
        "Meta Pixel": [
            "connect.facebook.net",
            "fbq(",
        ],
        "TikTok Pixel": [
            "analytics.tiktok.com",
            "ttq.",
        ],
        "Snapchat Pixel": [
            "sc-static.net",
            "snaptr(",
        ],
        "Microsoft Clarity": [
            "clarity.ms",
            "clarity(",
        ],
        "Microsoft Advertising": [
            "bat.bing.com",
            "uetq",
        ],
        "LinkedIn Insight": [
            "snap.licdn.com",
        ],
        "Pinterest Tag": [
            "pintrk(",
            "pinimg.com/ct",
        ],
    }

    return sorted(
        name
        for name, patterns in signatures.items()
        if any(pattern in text for pattern in patterns)
    )


def detect_platform(html, headers):
    text = (
        (html or "")
        + "\n"
        + str(headers)
    ).lower()

    signatures = {
        "WordPress": [
            "wp-content/",
            "wp-includes/",
            "wp-json",
            "wordpress",
        ],
        "Shopify": [
            "cdn.shopify.com",
            "shopify.theme",
            "myshopify.com",
        ],
        "Wix": [
            "wixstatic.com",
            "wix.com",
        ],
        "Squarespace": [
            "squarespace.com",
            "squarespace-cdn.com",
        ],
        "Webflow": [
            "webflow.css",
            "webflow.js",
            "website-files.com",
        ],
        "Drupal": [
            "drupalsettings",
            "drupal-settings-json",
        ],
        "Joomla": [
            "joomla",
            "/media/system/js/",
        ],
        "Laravel": [
            "laravel_session",
        ],
        "Next.js": [
            "__next_data__",
            "/_next/",
            "next/static",
        ],
        "Nuxt": [
            "/_nuxt/",
            "__nuxt__",
        ],
        "React": [
            "react-dom",
        ],
        "Vue.js": [
            "__vue__",
        ],
        "Angular": [
            "ng-version",
        ],
    }

    scores = {}

    for name, patterns in signatures.items():
        score = sum(
            1 for pattern in patterns
            if pattern in text
        )

        if score:
            scores[name] = score

    return max(scores, key=scores.get) if scores else "Custom / Unknown"


async def get_attr(page, selector, attribute):
    locator = page.locator(selector).first

    if await locator.count():
        return clean(
            await locator.get_attribute(attribute)
        )

    return ""


async def collect_page(browser, url):
    page = await browser.new_page()

    started = time.perf_counter()

    try:
        response = await page.goto(
            url,
            wait_until="domcontentloaded",
            timeout=60000,
        )

        dom_loaded = time.perf_counter() - started

        try:
            await page.wait_for_load_state(
                "networkidle",
                timeout=30000,
            )
        except Exception:
            pass

        fully_loaded = time.perf_counter() - started

        html = await page.content()

        headers = (
            dict(response.headers)
            if response
            else {}
        )

        title = clean(await page.title())

        meta_description = await get_attr(
            page,
            'meta[name="description"]',
            "content",
        )

        canonical = await get_attr(
            page,
            'link[rel="canonical"]',
            "href",
        )

        robots = await get_attr(
            page,
            'meta[name="robots"]',
            "content",
        )

        language = clean(
            await page.locator("html").get_attribute("lang")
        )

        viewport = await get_attr(
            page,
            'meta[name="viewport"]',
            "content",
        )

        headings = {}

        for level in range(1, 7):
            values = await page.locator(
                f"h{level}"
            ).all_inner_texts()

            headings[f"h{level}"] = [
                clean(x)
                for x in values
                if clean(x)
            ]

        images = await page.locator(
            "img"
        ).evaluate_all(
            """
            imgs => imgs.map(img => ({
                src: img.currentSrc || img.src || "",
                alt: img.getAttribute("alt"),
                width: img.naturalWidth || 0,
                height: img.naturalHeight || 0,
                loading: img.getAttribute("loading")
            }))
            """
        )

        links = await page.locator(
            "a[href]"
        ).evaluate_all(
            """
            links => links.map(a => ({
                href: a.href || "",
                text: (a.innerText || "").trim()
            }))
            """
        )

        scripts = await page.locator(
            "script[src]"
        ).evaluate_all(
            "x => x.map(s => s.src)"
        )

        stylesheets = await page.locator(
            'link[rel="stylesheet"]'
        ).evaluate_all(
            "x => x.map(s => s.href)"
        )

        iframes = await page.locator(
            "iframe"
        ).evaluate_all(
            "x => x.src || ''"
        )

        forms = await page.locator("form").count()

        body_text = clean(
            await page.locator("body").inner_text()
        )

        host = urlparse(url).netloc

        internal_links = [
            x for x in links
            if urlparse(x["href"]).netloc in ("", host)
        ]

        external_links = [
            x for x in links
            if urlparse(x["href"]).netloc
            and urlparse(x["href"]).netloc != host
        ]

        missing_alt = [
            x for x in images
            if not clean(x.get("alt"))
        ]

        og = await page.locator(
            'meta[property^="og:"]'
        ).evaluate_all(
            """
            xs => xs.map(x => ({
                property: x.getAttribute("property"),
                content: x.getAttribute("content")
            }))
            """
        )

        twitter = await page.locator(
            'meta[name^="twitter:"]'
        ).evaluate_all(
            """
            xs => xs.map(x => ({
                name: x.getAttribute("name"),
                content: x.getAttribute("content")
            }))
            """
        )

        return {
            "url": url,
            "final_url": page.url,
            "status": response.status if response else None,

            "title": title,
            "title_length": len(title),

            "meta_description": meta_description,
            "meta_description_length": len(meta_description),

            "canonical": canonical,
            "robots": robots,
            "language": language,
            "viewport": viewport,

            "headings": headings,

            "content": {
                "word_count": len(body_text.split()),
            },

            "images": {
                "count": len(images),
                "missing_alt": len(missing_alt),
                "items": images,
            },

            "links": {
                "total": len(links),
                "internal": len(internal_links),
                "external": len(external_links),
            },

            "resources": {
                "scripts": len(scripts),
                "stylesheets": len(stylesheets),
                "iframes": len(iframes),
                "forms": forms,
            },

            "schema": {
                "types": get_schema_types(html),
            },

            "social": {
                "open_graph": og,
                "twitter": twitter,
            },

            "technology": {
                "platform": detect_platform(
                    html,
                    headers,
                ),
                "tracking": detect_tracking(
                    html,
                    scripts,
                ),
            },

            "performance": {
                "dom_loaded": round(dom_loaded, 3),
                "fully_loaded": round(fully_loaded, 3),
            },

            "http": {
                "content_type": headers.get("content-type"),
                "server": headers.get("server"),
                "headers": headers,
                "html_bytes": len(
                    html.encode("utf-8")
                ),
            },
        }

    finally:
        await page.close()


def analyze(page):
    issues = []

    status = page["status"]

    if status and status >= 400:
        issues.append(f"HTTP {status}")

    title_length = page["title_length"]

    if not page["title"]:
        issues.append("Missing title")
    elif title_length < 30:
        issues.append("Short title")
    elif title_length > 60:
        issues.append("Long title")

    description_length = page[
        "meta_description_length"
    ]

    if not page["meta_description"]:
        issues.append("Missing meta description")
    elif description_length < 70:
        issues.append("Short meta description")
    elif description_length > 160:
        issues.append("Long meta description")

    h1_count = len(
        page["headings"]["h1"]
    )

    if h1_count == 0:
        issues.append("No H1")
    elif h1_count > 1:
        issues.append(
            f"{h1_count} H1s"
        )

    if page["content"]["word_count"] < 300:
        issues.append("Low word count")

    missing_alt = page[
        "images"
    ]["missing_alt"]

    if missing_alt:
        issues.append(
            f"{missing_alt} images without ALT"
        )

    internal = page[
        "links"
    ]["internal"]

    if internal == 0:
        issues.append("No internal links")
    elif internal < 3:
        issues.append("Low internal links")

    if not page["canonical"]:
        issues.append("Missing canonical")

    if not page["schema"]["types"]:
        issues.append("No structured data")

    return issues


def calculate_seo_score(pages):
    """
    Temporary bounded test scoring.

    This is intentionally NOT the final production scoring
    model. It only verifies that scoring works in the pipeline.
    """

    total = 0
    passed = 0
    issues = 0

    for page in pages:
        total += 100

        issue_count = len(
            page["issues"]
        )

        issues += issue_count

        passed += max(
            0,
            100 - issue_count * 10
        )

    if not pages:
        return 0

    score = passed / len(pages)

    return max(
        0,
        min(
            100,
            round(score)
        )
    )


def calculate_health(pages):
    """
    Temporary site-health test.

    Production health model will be finalized separately.
    """

    deductions = 0

    for page in pages:

        if page["status"] and page["status"] >= 500:
            deductions += 20

        if page["status"] and page["status"] >= 400:
            deductions += 5

        if not page["canonical"]:
            deductions += 2

    return max(
        0,
        min(
            100,
            100 - deductions
        )
    )


def build_report(data):

    site = data["site"]

    issue_rows = []

    for page in data["pages"]:

        if not page["issues"]:
            continue

        issue_rows.append(
            f"""
<tr>
<td>{page["url"]}</td>
<td>{", ".join(page["issues"])}</td>
</tr>
"""
        )

    rows = "".join(issue_rows)

    return f"""
<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">

<title>
SEO Audit Report - Crawl {CRAWL_ID}
</title>

<style>

body {{
    margin: 0;
    padding: 40px;
    background: #f4f5f7;
    font-family: Arial, sans-serif;
    color: #222;
}}

.container {{
    max-width: 1200px;
    margin: auto;
    background: white;
    padding: 35px;
}}

h1 {{
    margin-top: 0;
}}

.metrics {{
    display: flex;
    gap: 20px;
    margin: 30px 0;
}}

.metric {{
    flex: 1;
    border: 1px solid #ddd;
    padding: 20px;
}}

.metric strong {{
    display: block;
    font-size: 30px;
    margin-top: 8px;
}}

.site {{
    border: 1px solid #ddd;
    padding: 20px;
    margin-bottom: 30px;
}}

table {{
    width: 100%;
    border-collapse: collapse;
}}

th,
td {{
    border: 1px solid #ddd;
    padding: 12px;
    vertical-align: top;
}}

th {{
    background: #eee;
}}

</style>
</head>

<body>

<div class="container">

<h1>SEO Audit Report</h1>

<p>
Crawl ID: {CRAWL_ID}
</p>

<div class="metrics">

<div class="metric">
SEO Score
<strong>
{data["seo_score"]}/100
</strong>
</div>

<div class="metric">
Site Health
<strong>
{data["site_health"]}/100
</strong>
</div>

<div class="metric">
Pages
<strong>
{data["pages_tested"]}
</strong>
</div>

</div>

<div class="site">

<h2>Site-Wide Information</h2>

<p>
<strong>Platform:</strong>
{", ".join(site["platforms"])}
</p>

<p>
<strong>Tracking:</strong>
{", ".join(site["tracking"]) or "None detected"}
</p>

<p>
<strong>Average DOM Load:</strong>
{site["average_dom_loaded"]} seconds
</p>

<p>
<strong>Average Fully Loaded:</strong>
{site["average_fully_loaded"]} seconds
</p>

</div>

<h2>Issues by Page</h2>

<table>

<thead>
<tr>
<th>Page</th>
<th>Problems / Opportunities</th>
</tr>
</thead>

<tbody>

{rows}

</tbody>

</table>

</div>

</body>
</html>
"""


async def main():

    urls = get_pages()

    print("========================================")
    print("COMPLETE SEO PIPELINE TEST")
    print("========================================")
    print(f"Crawl ID: {CRAWL_ID}")
    print(f"Pages available: {len(urls)}")
    print("")

    results = []

    async with async_playwright() as p:

        browser = await p.chromium.launch(
            headless=True
        )

        for index, url in enumerate(
            urls,
            1
        ):

            print(
                f"[{index}/{len(urls)}] {url}"
            )

            try:

                result = await collect_page(
                    browser,
                    url,
                )

                result["issues"] = analyze(
                    result
                )

                results.append(result)

                print(
                    f"    Status: {result['status']}"
                )

                print(
                    f"    Words: "
                    f"{result['content']['word_count']}"
                )

                print(
                    f"    Schema: "
                    f"{', '.join(result['schema']['types']) or 'None'}"
                )

                print(
                    f"    Issues: "
                    f"{len(result['issues'])}"
                )

                print(
                    f"    Load: "
                    f"{result['performance']['fully_loaded']}s"
                )

            except Exception as e:

                print(
                    f"    ERROR: "
                    f"{type(e).__name__}: {e}"
                )

    platforms = sorted(
        set(
            x["technology"]["platform"]
            for x in results
        )
    )

    tracking = sorted(
        set(
            item
            for x in results
            for item in x["technology"]["tracking"]
        )
    )

    avg_dom = round(
        sum(
            x["performance"]["dom_loaded"]
            for x in results
        ) / len(results),
        3
    ) if results else 0

    avg_full = round(
        sum(
            x["performance"]["fully_loaded"]
            for x in results
        ) / len(results),
        3
    ) if results else 0

    site = {
        "platforms": platforms,
        "tracking": tracking,
        "average_dom_loaded": avg_dom,
        "average_fully_loaded": avg_full,
    }

    seo_score = calculate_seo_score(
        results
    )

    health = calculate_health(
        results
    )

    data = {
        "test": "Complete SEO pipeline",
        "crawl_id": CRAWL_ID,
        "pages_tested": len(results),
        "seo_score": seo_score,
        "site_health": health,
        "site": site,
        "pages": results,
    }

    JSON_OUT.write_text(
        json.dumps(
            data,
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    REPORT_OUT.write_text(
        build_report(data),
        encoding="utf-8",
    )

    print("")
    print("========================================")
    print("PIPELINE COMPLETE")
    print("========================================")
    print(
        f"Pages tested: {len(results)}"
    )
    print(
        f"SEO Score: {seo_score}/100"
    )
    print(
        f"Site Health: {health}/100"
    )
    print(
        f"JSON: {JSON_OUT}"
    )
    print(
        f"REPORT: {REPORT_OUT}"
    )

    try:
        subprocess.Popen(
            [
                "cmd",
                "/c",
                "start",
                "",
                str(REPORT_OUT.resolve()),
            ]
        )

        print(
            "Report opened automatically."
        )

    except Exception as e:

        print(
            f"Auto-open failed: {e}"
        )


if __name__ == "__main__":
    asyncio.run(main())
