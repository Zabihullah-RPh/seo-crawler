import asyncio
import time
from playwright.async_api import async_playwright


class BrowserClient:
    def __init__(self, concurrency=5):
        self.concurrency = concurrency
        self.playwright = None
        self.browser = None
        self.semaphore = asyncio.Semaphore(concurrency)

    async def start(self):
        self.playwright = await async_playwright().start()
        self.browser = await self.playwright.chromium.launch(headless=True)

    async def get(self, url):
        async with self.semaphore:
            page = await self.browser.new_page()
            started = time.perf_counter()
            try:
                response = await page.goto(url, wait_until="domcontentloaded", timeout=60000)
                dom_loaded = time.perf_counter() - started
                try:
                    await page.wait_for_load_state("networkidle", timeout=30000)
                except Exception:
                    pass
                fully_loaded = time.perf_counter() - started
                html = await page.content()
                data = await page.evaluate("""
                () => ({
                    title: document.title || '',
                    meta_description: document.querySelector('meta[name="description"]')?.content || '',
                    canonical: document.querySelector('link[rel="canonical"]')?.href || '',
                    robots: document.querySelector('meta[name="robots"]')?.content || '',
                    language: document.documentElement?.lang || '',
                    viewport: document.querySelector('meta[name="viewport"]')?.content || '',
                    h1s: Array.from(document.querySelectorAll('h1')).map(x => x.innerText.trim()),
                    h2s: Array.from(document.querySelectorAll('h2')).map(x => x.innerText.trim()),
                    h3s: Array.from(document.querySelectorAll('h3')).map(x => x.innerText.trim()),
                    images: Array.from(document.images).map(img => ({
                        url: img.currentSrc || img.src || '', alt: img.getAttribute('alt'),
                        width: img.naturalWidth || 0, height: img.naturalHeight || 0,
                        loading: img.getAttribute('loading') || ''
                    })),
                    links: Array.from(document.querySelectorAll('a[href]')).map(a => ({
                        url: a.href || '', anchor: (a.innerText || '').trim().slice(0,1000),
                        rel: a.getAttribute('rel') || ''
                    })),
                    scripts: Array.from(document.querySelectorAll('script[src]')).map(x => x.src),
                    stylesheets: Array.from(document.querySelectorAll('link[rel="stylesheet"]')).map(x => x.href),
                    body_text: document.body ? document.body.innerText : '',
                    raw_html_size: document.documentElement ? document.documentElement.outerHTML.length : 0
                })
                """)
                return {
                    "response": response,
                    "html": html,
                    "url": page.url,
                    "status": response.status if response else 0,
                    "dom_loaded_seconds": round(dom_loaded, 3),
                    "fully_loaded_seconds": round(fully_loaded, 3),
                    "browser": data,
                    "headers": dict(response.headers) if response else {},
                }
            except Exception as e:
                return {"response": None, "html": "", "url": url, "status": 0, "error": str(e),
                        "dom_loaded_seconds": 0, "fully_loaded_seconds": 0, "browser": {}}
            finally:
                await page.close()

    async def close(self):
        if self.browser:
            await self.browser.close()
        if self.playwright:
            await self.playwright.stop()
