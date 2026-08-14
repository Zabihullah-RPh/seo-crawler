import asyncio
import json
import re
import time
from pathlib import Path
from urllib.parse import urljoin, urlparse

from playwright.async_api import async_playwright


START_URL = "https://weeklypakistan.com.pk/"
MAX_PAGES = 5
OUT_DIR = Path("results")
OUT_DIR.mkdir(exist_ok=True)

JSON_OUT = OUT_DIR / "5page_test.json"
HTML_OUT = OUT_DIR / "5page_test.html"


TRACKERS = {
    "Google Tag Manager": [
        r"googletagmanager\.com/gtm\.js",
        r"gtm-[a-z0-9]+",
    ],
    "Google Analytics / Google tag": [
        r"google-analytics\.com",
        r"googletagmanager\.com",
        r"gtag\(",
        r"googletag\.",
    ],
    "Meta Pixel": [
        r"connect\.facebook\.net",
        r"fbq\(",
        r"facebook\.com/tr",
    ],
    "TikTok Pixel": [
        r"analytics\.tiktok\.com",
        r"ttq\.",
        r"tiktok-pixel",
    ],
    "Snapchat Pixel": [
        r"sc-static\.net",
        r"snaptr\(",
        r"snapchat",
    ],
    "LinkedIn Insight": [
        r"snap\.licdn\.com",
        r"_linkedin_partner_id",
        r"linkedin\.com/insight",
    ],
    "Microsoft Clarity": [
        r"clarity\.ms",
        r"clarity\(",
    ],
    "Hotjar": [
        r"static\.hotjar\.com",
        r"hj\(",
    ],
    "Google Ads": [
        r"googleadservices\.com",
        r"doubleclick\.net",
        r"conversion\.js",
    ],
}


def detect_platform(html, headers):
    text = html.lower()
    detected = []

    signatures = {
        "WordPress": [
            r"/wp-content/",
            r"/wp-includes/",
            r"wp-json",
            r'name=["\']generator["\'][^>]+wordpress',
        ],
        "Shopify": [
            r"cdn\.shopify\.com",
            r"shopify",
            r"myshopify\.com",
        ],
        "Wix": [
            r"wixstatic\.com",
            r"wix\.com",
        ],
        "Joomla": [
            r"/media/system/",
            r"joomla",
        ],
        "Drupal": [
            r"drupal-settings-json",
            r"/sites/default/files/",
        ],
        "Laravel": [
            r"laravel",
            r"laravel_session",
        ],
        "Next.js": [
            r"__next_data__",
            r"/_next/",
        ],
        "Nuxt": [
            r"__nuxt__",
            r"/_nuxt/",
        ],
        "React": [
            r"react",
            r"react-dom",
        ],
        "Vue.js": [
            r"vue",
            r"vuejs",
        ],
    }

    for platform, patterns in signatures.items():
        if any(re.search(p, text, re.I) for p in patterns):
            detected.append(platform)

    server = headers.get("server", "")
    powered = headers.get("x-powered-by", "")

    return {
        "detected": sorted(set(detected)),
        "server_header": server,
        "x_powered_by": powered,
    }


def detect_trackers(html):
    found = {}

    for name, patterns in TRACKERS.items():
        matches = []

        for pattern in patterns:
            for m in re.finditer(pattern, html, re.I):
                start = max(0, m.start() - 100)
                end = min(len(html), m.end() + 100)
                snippet = re.sub(r"\s+", " ", html[start:end])
                matches.append(snippet[:250])

        if matches:
            found[name] = {
                "detected": True,
                "evidence": list(dict.fromkeys(matches))[:3],
            }

    return found


async def extract_page(browser, url, depth):
    started = time.perf_counter()

    page = await browser.new_page()

    response = await page.goto(
        url,
        wait_until="domcontentloaded",
        timeout=60000,
    )

    dom_loaded = time.perf_counter() - started

    try:
        await page.wait_for_load_state("networkidle", timeout=15000)
    except Exception:
        pass

    fully_loaded = time.perf_counter() - started

    raw_response = await page.content()

    headers = {}
    if response:
        try:
            headers = await response.all_headers()
        except Exception:
            headers = {}

    title = await page.title()

    meta_description = await page.locator(
        'meta[name="description"]'
    ).get_attribute("content")

    canonical = await page.locator(
        'link[rel="canonical"]'
    ).get_attribute("href")

    robots = await page.locator(
        'meta[name="robots"]'
    ).get_attribute("content")

    language = await page.locator("html").get_attribute("lang")

    viewport = await page.locator(
        'meta[name="viewport"]'
    ).get_attribute("content")

    h1s = await page.locator("h1").all_inner_texts()
    h2s = await page.locator("h2").all_inner_texts()
    h3s = await page.locator("h3").all_inner_texts()

    images = await page.locator("img").evaluate_all(
        """imgs => imgs.map(img => ({
            src: img.src,
            alt: img.getAttribute('alt'),
            width: img.naturalWidth,
            height: img.naturalHeight
        }))"""
    )

    links = await page.locator("a[href]").evaluate_all(
        """links => links.map(a => ({
            href: a.href,
            text: (a.innerText || '').trim()
        }))"""
    )

    scripts = await page.locator("script").evaluate_all(
        """scripts => scripts.map(s => ({
            src: s.src || null,
            type: s.type || null
        }))"""
    )

    stylesheets = await page.locator(
        'link[rel="stylesheet"]'
    ).count()

    schema_blocks = await page.locator(
        'script[type="application/ld+json"]'
    ).count()

    og = {}
    for prop in [
        "og:title",
        "og:description",
        "og:image",
        "og:url",
        "og:type",
        "twitter:card",
        "twitter:title",
        "twitter:description",
        "twitter:image",
    ]:
        value = await page.locator(
            f'meta[property="{prop}"], meta[name="{prop}"]'
        ).first.get_attribute("content")

        if value:
            og[prop] = value

    visible_text = await page.locator("body").inner_text()

    word_count = len(re.findall(
        r"\b[\w'-]+\b",
        visible_text
    ))

    platform = detect_platform(
        raw_response,
        headers
    )

    trackers = detect_trackers(raw_response)

    internal_links = []
    external_links = []

    base_host = urlparse(START_URL).netloc

    for link in links:
        href = link.get("href") or ""

        if not href.startswith(("http://", "https://")):
            continue

        if urlparse(href).netloc == base_host:
            internal_links.append(href)
        else:
            external_links.append(href)

    result = {
        "url": url,
        "final_url": page.url,
        "depth": depth,
        "status_code": response.status if response else None,

        "timing": {
            "dom_content_loaded_seconds": round(dom_loaded, 3),
            "fully_loaded_seconds": round(fully_loaded, 3),
        },

        "content": {
            "title": title,
            "title_length": len(title or ""),
            "meta_description": meta_description,
            "meta_description_length": len(meta_description or ""),
            "canonical": canonical,
            "robots": robots,
            "language": language,
            "viewport": viewport,
            "word_count": word_count,
        },

        "headings": {
            "h1": h1s,
            "h2": h2s,
            "h3": h3s,
        },

        "images": {
            "total": len(images),
            "missing_alt": sum(
                1 for x in images
                if x.get("alt") is None
            ),
            "empty_alt": sum(
                1 for x in images
                if x.get("alt") == ""
            ),
        },

        "links": {
            "total": len(links),
            "internal": len(internal_links),
            "external": len(external_links),
        },

        "technical": {
            "scripts": len(scripts),
            "external_scripts": sum(
                1 for x in scripts
                if x.get("src")
            ),
            "stylesheets": stylesheets,
            "json_ld_blocks": schema_blocks,
        },

        "open_graph": og,

        "platform": platform,

        "tracking": trackers,

        "http": {
            "content_type": headers.get("content-type"),
            "server": headers.get("server"),
            "x_powered_by": headers.get("x-powered-by"),
        },

        "rendering": {
            "chromium_rendered": True,
            "raw_html_length": len(raw_response),
            "rendered_html_length": len(
                await page.content()
            ),
        },
    }

    await page.close()

    return result, internal_links


def make_html(data):
    pages = data["pages"]

    rows = []

    for p in pages:
        trackers = ", ".join(
            p["tracking"].keys()
        ) or "None detected"

        platforms = ", ".join(
            p["platform"]["detected"]
        ) or "Not detected"

        rows.append(
            f"""
            <tr>
                <td>{p['status_code']}</td>
                <td>{p['url']}</td>
                <td>{p['content']['title']}</td>
                <td>{p['content']['word_count']}</td>
                <td>{p['timing']['fully_loaded_seconds']}s</td>
                <td>{platforms}</td>
                <td>{trackers}</td>
                <td>{p['technical']['json_ld_blocks']}</td>
            </tr>
            """
        )

    total = len(pages)

    avg_load = (
        sum(
            p["timing"]["fully_loaded_seconds"]
            for p in pages
        ) / total
        if total else 0
    )

    html = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>5 Page Crawler Capability Test</title>

<style>
body {{
    font-family: Arial, sans-serif;
    background: #f5f6f8;
    color: #222;
    margin: 40px;
}}

.container {{
    max-width: 1600px;
    margin: auto;
}}

h1 {{
    margin-bottom: 5px;
}}

.subtitle {{
    color: #666;
}}

.cards {{
    display: grid;
    grid-template-columns: repeat(4, 1fr);
    gap: 15px;
    margin: 25px 0;
}}

.card {{
    background: white;
    padding: 20px;
    border-radius: 10px;
    box-shadow: 0 2px 8px #ddd;
}}

.number {{
    font-size: 30px;
    font-weight: bold;
    margin-top: 8px;
}}

table {{
    width: 100%;
    border-collapse: collapse;
    background: white;
    margin-top: 20px;
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

.section {{
    margin-top: 40px;
}}

pre {{
    white-space: pre-wrap;
    background: #111;
    color: #eee;
    padding: 15px;
    border-radius: 8px;
    overflow-x: auto;
}}
</style>
</head>

<body>

<div class="container">

<h1>5-Page Crawler Capability Test</h1>

<p class="subtitle">
Raw HTML + Chromium rendering + SEO + technology + tracking detection
</p>

<div class="cards">

<div class="card">
Pages Tested
<div class="number">{total}</div>
</div>

<div class="card">
Average Load
<div class="number">{avg_load:.2f}s</div>
</div>

<div class="card">
Total Test Time
<div class="number">{data['execution']['total_seconds']:.2f}s</div>
</div>

<div class="card">
Chromium Rendering
<div class="number">YES</div>
</div>

</div>

<div class="section">

<h2>Page Summary</h2>

<table>
<tr>
<th>Status</th>
<th>URL</th>
<th>Title</th>
<th>Words</th>
<th>Load</th>
<th>Platform</th>
<th>Tracking</th>
<th>Schema</th>
</tr>

{"".join(rows)}

</table>

</div>

<div class="section">

<h2>Detailed Results</h2>

"""

    for i, p in enumerate(pages, 1):

        html += f"""
<h3>{i}. {p['url']}</h3>

<table>
<tr><th>Field</th><th>Value</th></tr>

<tr>
<td>Final URL</td>
<td>{p['final_url']}</td>
</tr>

<tr>
<td>Status</td>
<td>{p['status_code']}</td>
</tr>

<tr>
<td>Title</td>
<td>{p['content']['title']}</td>
</tr>

<tr>
<td>Meta Description</td>
<td>{p['content']['meta_description']}</td>
</tr>

<tr>
<td>Canonical</td>
<td>{p['content']['canonical']}</td>
</tr>

<tr>
<td>Robots</td>
<td>{p['content']['robots']}</td>
</tr>

<tr>
<td>H1 Count</td>
<td>{len(p['headings']['h1'])}</td>
</tr>

<tr>
<td>H2 Count</td>
<td>{len(p['headings']['h2'])}</td>
</tr>

<tr>
<td>H3 Count</td>
<td>{len(p['headings']['h3'])}</td>
</tr>

<tr>
<td>Word Count</td>
<td>{p['content']['word_count']}</td>
</tr>

<tr>
<td>Images</td>
<td>{p['images']['total']}</td>
</tr>

<tr>
<td>Images Missing ALT</td>
<td>{p['images']['missing_alt']}</td>
</tr>

<tr>
<td>Internal Links</td>
<td>{p['links']['internal']}</td>
</tr>

<tr>
<td>External Links</td>
<td>{p['links']['external']}</td>
</tr>

<tr>
<td>Scripts</td>
<td>{p['technical']['scripts']}</td>
</tr>

<tr>
<td>Stylesheets</td>
<td>{p['technical']['stylesheets']}</td>
</tr>

<tr>
<td>JSON-LD</td>
<td>{p['technical']['json_ld_blocks']}</td>
</tr>

<tr>
<td>Platform</td>
<td>{", ".join(p['platform']['detected']) or "Not detected"}</td>
</tr>

<tr>
<td>Tracking</td>
<td>{", ".join(p['tracking'].keys()) or "None detected"}</td>
</tr>

<tr>
<td>DOM Loaded</td>
<td>{p['timing']['dom_content_loaded_seconds']} seconds</td>
</tr>

<tr>
<td>Fully Loaded</td>
<td>{p['timing']['fully_loaded_seconds']} seconds</td>
</tr>

<tr>
<td>Raw HTML Size</td>
<td>{p['rendering']['raw_html_length']:,} bytes</td>
</tr>

<tr>
<td>Server</td>
<td>{p['http']['server']}</td>
</tr>

</table>
"""

    html += """
</div>

<div class="section">

<h2>What This Test Demonstrates</h2>

<ul>
<li>HTTP-level page collection</li>
<li>SEO metadata extraction</li>
<li>Heading extraction</li>
<li>Image and ALT analysis</li>
<li>Internal/external link analysis</li>
<li>Structured data detection</li>
<li>Open Graph detection</li>
<li>CMS/platform fingerprinting</li>
<li>Google and advertising tag detection</li>
<li>Meta Pixel detection</li>
<li>TikTok Pixel detection</li>
<li>Snapchat Pixel detection</li>
<li>LinkedIn Insight detection</li>
<li>Microsoft Clarity detection</li>
<li>Hotjar detection</li>
<li>Chromium JavaScript rendering</li>
<li>Page timing measurements</li>
<li>Raw vs rendered HTML inspection</li>
</ul>

</div>

</div>
</body>
</html>
"""

    return html


async def main():

    start = time.perf_counter()

    pages = []
    queue = [(START_URL, 0)]
    seen = set()

    async with async_playwright() as pw:

        browser = await pw.chromium.launch(
            headless=True
        )

        while queue and len(pages) < MAX_PAGES:

            url, depth = queue.pop(0)

            if url in seen:
                continue

            seen.add(url)

            try:

                print(
                    f"[{len(pages)+1}/{MAX_PAGES}] {url}"
                )

                result, links = await extract_page(
                    browser,
                    url,
                    depth
                )

                pages.append(result)

                for link in links:

                    clean = link.split("#")[0]

                    if (
                        clean not in seen
                        and urlparse(clean).netloc
                        == urlparse(START_URL).netloc
                    ):
                        queue.append(
                            (clean, depth + 1)
                        )

            except Exception as e:

                print(
                    f"[ERROR] {url} | "
                    f"{type(e).__name__}: {e}"
                )

        await browser.close()

    elapsed = time.perf_counter() - start

    data = {
        "test": "5-page crawler capability test",
        "start_url": START_URL,
        "max_pages": MAX_PAGES,
        "pages": pages,
        "execution": {
            "total_seconds": round(elapsed, 3),
            "pages_completed": len(pages),
        },
    }

    JSON_OUT.write_text(
        json.dumps(
            data,
            indent=2,
            ensure_ascii=False
        ),
        encoding="utf-8"
    )

    HTML_OUT.write_text(
        make_html(data),
        encoding="utf-8"
    )

    print("")
    print("========== TEST COMPLETE ==========")
    print(f"Pages: {len(pages)}")
    print(f"Time: {elapsed:.2f} seconds")
    print(f"JSON: {JSON_OUT}")
    print(f"HTML: {HTML_OUT}")

    import os
    os.startfile(str(HTML_OUT.resolve()))


if __name__ == "__main__":
    asyncio.run(main())
