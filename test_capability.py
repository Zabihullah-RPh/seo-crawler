import asyncio
import json
import re
import sqlite3
import time
from pathlib import Path
from urllib.parse import urljoin, urlparse

from playwright.async_api import async_playwright


CRAWL_ID = 22
DB = "data/crawler.db"
OUT = "results/capability_test_22.json"


def get_pages():
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row

    rows = con.execute(
        """
        SELECT url
        FROM pages
        WHERE crawl_id = ?
        ORDER BY id
        """,
        (CRAWL_ID,),
    ).fetchall()

    con.close()

    return [r["url"] for r in rows]


def clean(value):
    return (value or "").strip()


def detect_platform(html, headers):
    text = (html or "").lower()
    headers_text = str(headers).lower()

    signatures = {
        "WordPress": [
            "wp-content/",
            "wp-includes/",
            "wp-json",
            "wordpress",
            "generator\" content=\"wordpress",
        ],
        "Shopify": [
            "cdn.shopify.com",
            "shopify.theme",
            "shopify.routes",
            "myshopify.com",
        ],
        "Wix": [
            "wixstatic.com",
            "wix.com",
            "wixsite.com",
        ],
        "Squarespace": [
            "squarespace.com",
            "static1.squarespace.com",
            "squarespace-cdn.com",
        ],
        "Webflow": [
            "webflow.css",
            "webflow.js",
            "assets.website-files.com",
            "webflow.io",
        ],
        "Drupal": [
            "drupalsettings",
            "drupal-settings-json",
            "sites/default/files",
            "drupal.js",
        ],
        "Joomla": [
            "joomla",
            "/media/system/js/",
            "/media/jui/",
        ],
        "Laravel": [
            "laravel_session",
            "laravel",
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
            "react",
            "react-dom",
        ],
        "Vue.js": [
            "vue",
            "__vue__",
        ],
        "Angular": [
            "ng-version",
            "angular",
        ],
    }

    scores = {}

    for platform, patterns in signatures.items():
        score = 0

        for pattern in patterns:
            if pattern in text or pattern in headers_text:
                score += 1

        if score:
            scores[platform] = score

    if not scores:
        return "Custom / Unknown"

    return max(
        scores,
        key=scores.get
    )


def detect_tracking(scripts, html):
    found = set()
    text = ((html or "") + "\n" + "\n".join(scripts)).lower()

    signatures = {
        "Google Analytics": [
            "google-analytics.com",
            "googletagmanager.com/gtag",
            "gtag(",
        ],
        "Google Tag Manager": [
            "googletagmanager.com/gtm",
            "gtm.js",
        ],
        "Google Ads": [
            "googleadservices.com",
            "googlesyndication.com",
            "google_conversion",
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
            "snap pixel",
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
            "linkedin insight",
        ],
        "Pinterest Tag": [
            "pintrk(",
            "pinimg.com/ct",
        ],
    }

    for name, signatures_list in signatures.items():
        if any(signature in text for signature in signatures_list):
            found.add(name)

    return sorted(found)


def extract_schema_types(html):
    results = []

    matches = re.findall(
        r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        html or "",
        flags=re.I | re.S,
    )

    def collect(obj):
        if isinstance(obj, dict):
            value = obj.get("@type")

            if isinstance(value, list):
                results.extend(str(x) for x in value)
            elif value:
                results.append(str(value))

            for item in obj.values():
                collect(item)

        elif isinstance(obj, list):
            for item in obj:
                collect(item)

    for raw in matches:
        try:
            data = json.loads(raw.strip())
            collect(data)
        except Exception:
            pass

    return sorted(set(results))


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

        title = clean(await page.title())

        meta_description = clean(
            await page.locator(
                'meta[name="description"]'
            ).first.get_attribute("content")
            if await page.locator(
                'meta[name="description"]'
            ).count()
            else ""
        )

        canonical = clean(
            await page.locator(
                'link[rel="canonical"]'
            ).first.get_attribute("href")
            if await page.locator(
                'link[rel="canonical"]'
            ).count()
            else ""
        )

        robots = clean(
            await page.locator(
                'meta[name="robots"]'
            ).first.get_attribute("content")
            if await page.locator(
                'meta[name="robots"]'
            ).count()
            else ""
        )

        language = clean(
            await page.locator("html").get_attribute("lang")
        )

        viewport = clean(
            await page.locator(
                'meta[name="viewport"]'
            ).get_attribute("content")
        )

        h1s = await page.locator("h1").all_inner_texts()
        h2s = await page.locator("h2").all_inner_texts()
        h3s = await page.locator("h3").all_inner_texts()

        images = await page.locator("img").evaluate_all(
            """
            imgs => imgs.map(img => ({
                src: img.src || "",
                alt: img.getAttribute("alt"),
                width: img.naturalWidth || 0,
                height: img.naturalHeight || 0
            }))
            """
        )

        links = await page.locator("a[href]").evaluate_all(
            """
            links => links.map(a => ({
                href: a.href || "",
                text: (a.innerText || "").trim()
            }))
            """
        )

        scripts = await page.locator("script[src]").evaluate_all(
            "scripts => scripts.map(s => s.src)"
        )

        stylesheets = await page.locator(
            'link[rel="stylesheet"]'
        ).evaluate_all(
            "links => links.map(l => l.href)"
        )

        body_text = clean(
            await page.locator("body").inner_text()
        )

        words = len(body_text.split())

        schema_types = extract_schema_types(html)

        tracking = detect_tracking(
            scripts,
            html,
        )

        platform = detect_platform(
            html,
            dict(response.headers) if response else {},
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

        return {
            "url": url,
            "final_url": page.url,
            "status": response.status if response else None,
            "title": title,
            "meta_description": meta_description,
            "canonical": canonical,
            "robots": robots,
            "language": language,
            "viewport": viewport,

            "headings": {
                "h1": h1s,
                "h2": h2s,
                "h3": h3s,
                "h1_count": len(h1s),
                "h2_count": len(h2s),
                "h3_count": len(h3s),
            },

            "content": {
                "word_count": words,
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
            },

            "schema": {
                "count": len(schema_types),
                "types": schema_types,
            },

            "technology": {
                "platform": platform,
                "tracking": tracking,
            },

            "performance": {
                "dom_loaded_seconds": round(dom_loaded, 3),
                "fully_loaded_seconds": round(fully_loaded, 3),
            },

            "http": {
                "content_type": response.headers.get("content-type")
                if response else None,
                "server": response.headers.get("server")
                if response else None,
                "content_length": len(html.encode("utf-8")),
            },

        }

    finally:
        await page.close()


async def main():
    urls = get_pages()

    print("========================================")
    print("5-PAGE CRAWLER CAPABILITY TEST")
    print("========================================")
    print(f"Crawl ID: {CRAWL_ID}")
    print(f"Pages: {len(urls)}")
    print("")

    results = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True
        )

        for number, url in enumerate(urls, 1):
            print(
                f"[{number}/{len(urls)}] {url}"
            )

            try:
                result = await collect_page(
                    browser,
                    url,
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
                    f"    Platform: "
                    f"{result['technology']['platform']}"
                )

                print(
                    f"    Tracking: "
                    f"{', '.join(result['technology']['tracking']) or 'None'}"
                )

                print(
                    f"    Load: "
                    f"{result['performance']['fully_loaded_seconds']}s"
                )

            except Exception as e:
                print(
                    f"    ERROR: "
                    f"{type(e).__name__}: {e}"
                )

        await browser.close()

    site = {
        "platforms": sorted(
            set(
                x["technology"]["platform"]
                for x in results
            )
        ),
        "tracking": sorted(
            set(
                item
                for x in results
                for item in x["technology"]["tracking"]
            )
        ),
        "average_dom_loaded_seconds": round(
            sum(
                x["performance"]["dom_loaded_seconds"]
                for x in results
            ) / len(results),
            3,
        ) if results else 0,
        "average_fully_loaded_seconds": round(
            sum(
                x["performance"]["fully_loaded_seconds"]
                for x in results
            ) / len(results),
            3,
        ) if results else 0,
    }

    output = {
        "test": "5-page crawler capability test",
        "crawl_id": CRAWL_ID,
        "pages_tested": len(results),
        "site": site,
        "pages": results,
    }

    Path("results").mkdir(
        exist_ok=True
    )

    Path(OUT).write_text(
        json.dumps(
            output,
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    print("")
    print("========================================")
    print("TEST COMPLETE")
    print("========================================")
    print(f"Pages tested: {len(results)}")
    print(
        f"Average DOM loaded: "
        f"{site['average_dom_loaded_seconds']}s"
    )
    print(
        f"Average fully loaded: "
        f"{site['average_fully_loaded_seconds']}s"
    )
    print(
        f"Platforms: "
        f"{', '.join(site['platforms']) or 'None detected'}"
    )
    print(
        f"Tracking: "
        f"{', '.join(site['tracking']) or 'None detected'}"
    )
    print(f"JSON: {OUT}")


if __name__ == "__main__":
    asyncio.run(main())
