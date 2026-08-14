from pathlib import Path

p = Path("test_capability.py")
s = p.read_text(encoding="utf-8")

start = s.index("def detect_platform(")
end = s.index("\n\ndef detect_tracking(", start)

new_function = r'''def detect_platform(html, headers):
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
'''

s = s[:start] + new_function + s[end:]

# Add meta generator/platform evidence collection
needle = '''        viewport = clean(
            await page.locator(
                'meta[name="viewport"]'
            ).get_attribute("content")
            if await page.locator(
                'meta[name="viewport"]'
            ).count()
            else ""
        )
'''

replacement = needle + '''

        generator = clean(
            await page.locator(
                'meta[name="generator"]'
            ).first.get_attribute("content")
            if await page.locator(
                'meta[name="generator"]'
            ).count()
            else ""
        )
'''

s = s.replace(needle, replacement)

s = s.replace(
'''        platform = detect_platform(
            html,
            dict(response.headers) if response else {},
        )''',
'''        platform = detect_platform(
            html + "\\n" + generator,
            dict(response.headers) if response else {},
        )'''
)

s = s.replace(
'''            "technology": {
                "platform": platform,
                "tracking": tracking,
            },''',
'''            "technology": {
                "platform": platform,
                "generator": generator,
                "tracking": tracking,
            },'''
)

p.write_text(s, encoding="utf-8")

print("SUCCESS: technology detection improved.")
