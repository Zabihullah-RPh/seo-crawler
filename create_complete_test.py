from pathlib import Path

script = r"""
# COMPLETE 5-PAGE SEO PIPELINE TEST
# Crawl -> Collect -> Analyze -> Score -> Report

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
        """
        SELECT *
        FROM pages
        WHERE crawl_id = ?
        ORDER BY id
        """,
        (CRAWL_ID,),
    ).fetchall()

    con.close()
    return [dict(r) for r in rows]


def clean(value):
    return (value or "").strip()


def word_count(text):
    return len((text or "").split())


def schema_types(html):
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

            for value in obj.values():
                walk(value)

        elif isinstance(obj, list):
            for value in obj:
                walk(value)

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
            "ga(",
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
            "facebook pixel",
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
            1
            for pattern in patterns
            if pattern in text
        )

        if score:
            scores[name] = score

    if not scores:
        return "Custom / Unknown"

    return max(
        scores,
        key=scores.get
    )


async def collect_page(browser, url):
    page = await browser.new_page()

    started = time.perf_counter()

    try:
        response = await page.goto(
            url,
            wait_until="domcontentloaded",
            timeout=60000,
        )

        dom_time = time.perf_counter() - started

        try:
            await page.wait_for_load_state(
                "networkidle",
                timeout=30000,
            )
        except Exception:
            pass

        full_time = time.perf_counter() - started

        html = await page.content()

        headers = dict(response.headers) if response else {}

        title = clean(await page.title())

        async def attr(selector, name):
            loc = page.locator(selector).first
            if await loc.count():
                return clean(await loc.get_attribute(name))
            return ""

        meta = await attr(
            'meta[name="description"]',
            "content",
        )

        canonical = await attr(
            'link[rel="canonical"]',
            "href",
        )

        robots = await attr(
            'meta[name="robots"]',
            "content",
        )

        language = await page.locator(
            "html"
        ).get_attribute("lang") or ""

        viewport = await attr(
            'meta[name="viewport"]',
            "content",
        )

        h = {}

        for level in range(1, 7):
            values = await page.locator(
                f"h{level}"
            ).all_inner_texts()

            h[f"h{level}"] = [
                clean(x) for x in values if clean(x)
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
            "x => x.map(i => i.src || '')"
        )

        forms = await page.locator(
            "form"
        ).count()

        body = clean(
            await page.locator("body").inner_text()
        )

        host = urlparse(url).netloc

        internal = [
            x for x in links
            if urlparse(x["href"]).netloc in ("", host)
        ]

        external = [
            x for x in links
            if urlparse(x["href"]).netloc
            and urlparse(x["href"]).netloc != host
        ]

        missing_alt = [
            x for x in images
            if not clean(x.get("alt"))
        ]

        og = {}
        for item in await page.locator(
            'meta[property^="og:"]'
        ).evaluate_all(
            """
            xs => xs.map(x => ({
                property: x.getAttribute("property"),
                content: x.getAttribute("content")
            }))
            """
        ):
            og[item["property"]] = item["content"]

        twitter = {}
        for item in await page.locator(
            'meta[name^="twitter:"]'
        ).evaluate_all(
            """
            xs => xs.map(x => ({
                name: x.getAttribute("name"),
                content: x.getAttribute("content")
            }))
            """
        ):
            twitter[item["name"]] = item["content"]

        return {
            "url": url,
            "final_url": page.url,
            "status": response.status if response else None,

            "title": title,
            "title_length": len(title),

            "meta_description": meta,
            "meta_description_length": len(meta),

            "canonical": canonical,
            "robots": robots,
            "language": clean(language),
            "viewport": viewport,

            "headings": h,

            "content": {
                "word_count": word_count(body),
            },

            "images": {
                "count": len(images),
                "missing_alt": len(missing_alt),
                "items": images,
            },

            "links": {
                "total": len(links),
                "internal": len(internal),
                "external": len(external),
            },

            "resources": {
                "scripts": len(scripts),
                "stylesheets": len(stylesheets),
                "iframes": len(iframes),
                "forms": forms,
            },

            "schema": {
                "types": schema_types(html),
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
                "dom_loaded": round(dom_time, 3),
                "fully_loaded": round(full_time, 3),
            },

            "http": {
                "content_type": headers.get(
                    "content-type"
                ),
                "server": headers.get(
                    "server"
                ),
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

    if not page["title"]:
        issues.append("Missing title")
    elif len(page["title"]) < 30:
        issues.append("Short title")
    elif len(page["title"]) > 60:
        issues.append("Long title")

    if not page["meta_description"]:
        issues.append("Missing meta description")
    elif len(page["meta_description"]) < 70:
        issues.append("Short meta description")
    elif len(page["meta_description"]) > 160:
        issues.append("Long meta description")

    h1 = len(page["headings"]["h1"])

    if h1 == 0:
        issues.append("No H1")
    elif h1 > 1:
        issues.append(f"{h1} H1s")

    words = page["content"]["word_count"]

    if words < 300:
        issues.append("Low word count")

    if page["images"]["missing_alt"]:
        issues.append(
            f'{page["images"]["missing_alt"]} images without ALT'
        )

    internal = page["links"]["internal"]

    if internal == 0:
        issues.append("No internal links")
    elif internal < 3:
        issues.append("Low internal links")

    if not page["canonical"]:
        issues.append("Missing canonical")

    if not page["schema"]["types"]:
        issues.append("No structured data")

    if page["status"] >= 400:
        issues.append(
            f'HTTP {page["status"]}'
        )

    return issues


def score_site(pages):
    total_elements = 0
    good = 0
    bad = 0

    for page in pages:
        total_elements += 10

        if page["issues"]:
            bad += len(page["issues"])
        else:
            good += 10

    if not total_elements:
        return 0

    # Bounded score: never below 0 or above 100.
    raw = (
        (good / total_elements) * 100
        - (bad / total_elements) * 25
    )

    return max(
        0,
        min(
            100,
            round(raw)
        )
    )


def site_health(pages, site):
    critical = 0

    for page in pages:
        if page["status"] >= 500:
            critical += 2

        if not page["canonical"]:
            critical += 1

    if "HTTPS" not in site["url"].upper():
        critical += 3

    health = 100 - critical * 5

    return max(
        0,
        min(
            100,
            health
        )
    )


def build_report(data):
    score = data["seo_score"]
    health = data["site_health"]

    rows = []

    for page in data["pages"]:
        issues = page["issues"]

        if not issues:
            continue

        rows.append(
            f"""
            <tr>
                <td>{page["url"]}</td>
                <td>{", ".join(issues)}</td>
            </tr>
            """
        )

    issue_rows = "".join(rows)

    site = data["site"]

    return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>SEO Audit Report - Crawl {CRAWL_ID}</title>
<style>
body {{
    font-family: Arial, sans-serif;
    margin: 40px;
    background: #f5f5f5;
    color: #222;
}}
.container {{
    max-width: 1200px;
    margin: auto;
    background: white;
    padding: 35px;
}}
h1 {{
    margin-bottom: 5px;
}}
.metrics {{
    display: flex;
    gap: 15px;
    margin: 25px 0;
}}
.metric {{
    padding: 20px;
    border: 1px solid #ddd;
    flex: 1;
}}
.score {{
    font-size: 30px;
    font-weight: bold;
}}
table {{
    width: 100%;
    border-collapse: collapse;
    margin-top: 20px;
}}
th, td {{
    border: 1px solid #ddd;
    padding: 12px;
    text-align: left;
}}
th {{
    background: #eee;
}}
.site {{
    background: #fafafa;
    padding: 20px;
    margin-top: 25px;
}}
</style>
</head>

<body>
<div class="container">

<h1>SEO Audit Report</h1>
<p>Crawl ID: {CRAWL_ID}</p>

<div class="metrics">
<div class="metric">
<div>SEO Score</div>
<div class="score">{score}/100</div>
</div>

<div class="metric">
<div>Site Health</div>
<div class="score">{health}/100</div>
</div>

<div class="metric">
<div>Pages Tested</div>
<div class="score">{len(data["pages"])}</div>
</div>
</div>

<div class="site">
<h2>Site-Wide Information</h2>
<p><b>Platform:</b> {", ".join(site["platforms"])}</p>
<p><b>Tracking:</b> {", ".join(site["tracking"]) or "None detected"}</p>
<p><b>Average DOM Load:</b> {site["average_dom_loaded"]}s</p>
<p><b>Average Fully Loaded:</b> {site["average_fully_loaded"]}s</p>
</div>

<h2>Issues by Page</h2>

<table>
<thead>
<tr>
<th>Page</th>
<th>Issues</th>
</tr>
</thead>
<tbody>
{issue_rows}
</tbody>
</table>

<h2>Collection Summary</h2>

<p>
The crawler collected rendered HTML, SEO metadata, headings,
content, images, links, resources, structured data, social metadata,
technology signals, tracking signals, HTTP information and performance
information.
</p>

</div>
</body>
</html>
"""


async def main():

    db_pages = get_pages()

    print("========================================")
    print("COMPLETE 5-PAGE SEO PIPELINE TEST")
    print("========================================")
    print(f"Crawl ID: {CRAWL_ID}")
    print(f"Pages available: {len(db_pages)}")
    print("")

    results = []

    async with async_playwright() as p:

        browser = await p.chromium.launch(
            headless=True
        )

        for i, row in enumerate(db_pages, 1):

            url = row["url"]

            print(
                f"[{i}/{len(db_pages)}] Collecting: {url}"
            )

            try:
                result = await collect_page(
                    browser,
                    url,
                )

                result["issues"] = analyze(result)

                results.append(result)

                print(
                    f"    Status: {result['status']}"
                )

                print(
                    f"    Words: "
                    f"{result['content']['word_count']}"
                )

                print(
                    f"    Issues: "
                    f"{len(result['issues'])}"
                )

            except Exception as e:

                print(
                    f"    ERROR: "
                    f"{type(e).__name__}: {e}"
                )

        await browser.close()

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
        "url": (
            results[0]["url"]
            if results
            else ""
        ),
    }

    seo_score = score_site(results)

    health = site_health(
        results,
        site,
    )

    data = {
        "test": "Complete 5-page SEO pipeline",
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
    print(f"Pages: {len(results)}")
    print(f"SEO Score: {seo_score}/100")
    print(f"Site Health: {health}/100")
    print(f"JSON: {JSON_OUT}")
    print(f"REPORT: {REPORT_OUT}")

    try:
        subprocess.Popen(
            ["cmd", "/c", "start", "", str(REPORT_OUT.resolve())]
        )
        print("Report opened automatically.")
    except Exception as e:
        print(
            f"Could not auto-open report: {e}"
        )


if __name__ == "__main__":
    asyncio.run(main())
"""

Path("test_complete.py").write_text(
    script,
    encoding="utf-8"
)

print("CREATED: test_complete.py")
