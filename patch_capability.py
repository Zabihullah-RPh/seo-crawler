from pathlib import Path

p = Path("test_capability.py")
s = p.read_text(encoding="utf-8")

s = s.replace(
'''        meta_description = clean(
            await page.locator(
                'meta[name="description"]'
            ).get_attribute("content")
        )''',
'''        meta_description = clean(
            await page.locator(
                'meta[name="description"]'
            ).first.get_attribute("content")
            if await page.locator(
                'meta[name="description"]'
            ).count()
            else ""
        )'''
)

s = s.replace(
'''        canonical = clean(
            await page.locator(
                'link[rel="canonical"]'
            ).get_attribute("href")
        )''',
'''        canonical = clean(
            await page.locator(
                'link[rel="canonical"]'
            ).first.get_attribute("href")
            if await page.locator(
                'link[rel="canonical"]'
            ).count()
            else ""
        )'''
)

s = s.replace(
'''        robots = clean(
            await page.locator(
                'meta[name="robots"]'
            ).get_attribute("content")
        )''',
'''        robots = clean(
            await page.locator(
                'meta[name="robots"]'
            ).first.get_attribute("content")
            if await page.locator(
                'meta[name="robots"]'
            ).count()
            else ""
        )'''
)

p.write_text(s, encoding="utf-8")

print("SUCCESS: missing-meta handling patched.")
