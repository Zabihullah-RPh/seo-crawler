import asyncio
import hashlib
import json
import os
import socket
import ssl
import urllib.request
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin, urlparse

from app.crawler.browser import BrowserClient
from app.crawler.http import HTTPClient
from app.crawler.parser import parse_html
from app.storage.db import save_page, save_issue, save_image, save_resource, save_hreflang, save_schema, update_crawl
from app.storage.persistence import CrawlPersistence
from app.analyzers.technical import analyze
from app.utils.urls import normalize_url, same_host


TRACKING_SIGNATURES = {
    "Google Analytics": ["google-analytics.com", "googletagmanager.com/gtag", "gtag("],
    "Google Tag Manager": ["googletagmanager.com", "gtm.js"],
    "Google Ads": ["googleadservices.com", "googlesyndication.com", "google_conversion", "AW-"],
    "Meta Pixel": ["connect.facebook.net", "fbq(", "facebook pixel"],
    "TikTok Pixel": ["analytics.tiktok.com", "ttq."],
    "Snapchat Pixel": ["sc-static.net", "snaptr(", "snap pixel"],
    "Microsoft Clarity": ["clarity.ms", "clarity("],
    "Microsoft Advertising": ["bat.bing.com", "uetq"],
    "LinkedIn Insight": ["snap.licdn.com", "linkedin insight"],
    "Pinterest Tag": ["pintrk(", "pinimg.com/ct"],
    "Twitter/X Pixel": ["static.ads-twitter.com", "twq("],
    "Reddit Pixel": ["alb.reddit.com", "rdt("],
}

PLATFORM_SIGNATURES = {
    "WordPress": ["wp-content/", "wp-includes/", "wp-json", "wordpress"],
    "Shopify": ["cdn.shopify.com", "shopify.theme", "shopify.routes", "myshopify.com"],
    "Wix": ["wixstatic.com", "wix.com", "wixsite.com"],
    "Squarespace": ["squarespace.com", "static1.squarespace.com", "squarespace-cdn.com"],
    "Webflow": ["webflow.css", "webflow.js", "assets.website-files.com", "webflow.io"],
    "Drupal": ["drupalsettings", "drupal-settings-json", "sites/default/files", "drupal.js"],
    "Joomla": ["/media/system/js/", "/media/jui/", "joomla"],
    "Laravel": ["laravel_session", "laravel"],
    "Next.js": ["/_next/", "next/static", "__next_data__"],
    "Nuxt": ["/_nuxt/", "__nuxt__"],
    "React": ["react", "react-dom"],
    "Vue.js": ["vue", "__vue__"],
    "Angular": ["ng-version", "angular"],
}


def detect_signatures(html, scripts, signatures):
    text = ((html or "") + "\n" + "\n".join(scripts or [])).lower()
    found = []
    evidence = {}
    for name, patterns in signatures.items():
        hits = [p for p in patterns if p.lower() in text]
        if hits:
            found.append(name)
            evidence[name] = hits
    return sorted(found), evidence


def extract_schema_types(html):
    import re
    results = []
    raw_items = []
    matches = re.findall(r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', html or "", flags=re.I | re.S)
    def collect(obj):
        if isinstance(obj, dict):
            t = obj.get("@type")
            if isinstance(t, list): results.extend(str(x) for x in t)
            elif t: results.append(str(t))
            for v in obj.values(): collect(v)
        elif isinstance(obj, list):
            for v in obj: collect(v)
    for raw in matches:
        try:
            obj = json.loads(raw.strip())
            collect(obj)
            raw_items.append(obj)
        except Exception:
            pass
    return sorted(set(results)), raw_items


def site_metadata(url, headers):
    host = urlparse(url).hostname or ""
    data = {"hostname": host, "ip_addresses": [], "server": headers.get("server"), "ssl": {}, "domain_age": None, "hosting": {}}
    try:
        data["ip_addresses"] = sorted(set(socket.gethostbyname_ex(host)[2]))
    except Exception:
        pass
    try:
        ctx = ssl.create_default_context()
        with socket.create_connection((host, 443), timeout=5) as sock:
            with ctx.wrap_socket(sock, server_hostname=host) as ssock:
                cert = ssock.getpeercert()
                data["ssl"] = {
                    "enabled": True,
                    "subject": dict(x[0] for x in cert.get("subject", [])),
                    "issuer": dict(x[0] for x in cert.get("issuer", [])),
                    "valid_from": cert.get("notBefore"),
                    "valid_until": cert.get("notAfter"),
                }
    except Exception as e:
        data["ssl"] = {"enabled": False, "error": str(e)}
    # Best-effort RDAP. Failure is intentionally non-fatal.
    try:
        req = urllib.request.Request(f"https://rdap.org/domain/{host}", headers={"User-Agent": "SEO-Crawler/1.0"})
        with urllib.request.urlopen(req, timeout=8) as r:
            rdap = json.loads(r.read().decode("utf-8", errors="ignore"))
        events = {x.get("eventAction"): x.get("eventDate") for x in rdap.get("events", [])}
        created = events.get("registration") or events.get("creation")
        if created:
            data["domain_age"] = {"created": created}
    except Exception:
        pass
    return data


class ProductionCrawler:
    def __init__(self, crawl_id, start_url, max_pages=100000, max_depth=50, concurrency=20):
        self.crawl_id = crawl_id
        self.start_url = normalize_url(start_url)
        self.max_pages = int(max_pages)
        self.max_depth = int(max_depth)
        self.concurrency = int(concurrency)
        self.discovered, self.crawled, self.failed = set(), set(), set()
        self.queue, self.running = [], True
        self.http = HTTPClient(concurrency=self.concurrency)
        self.browser = BrowserClient(concurrency=min(5, self.concurrency))
        self.persistence = CrawlPersistence(self.crawl_id)
        self.site = {}

    def _add_url(self, url, depth):
        try: url = normalize_url(url)
        except Exception: return False
        if not url or not same_host(url, self.start_url) or depth > self.max_depth or url in self.discovered or len(self.discovered) >= self.max_pages:
            return False
        self.discovered.add(url); self.queue.append((url, depth)); return True

    def _persist_page(self, page): self.persistence.save_page(page)
    def _persist_link(self, link): self.persistence.save_link(link)
    def _persist_failed(self, url, error): self.persistence.save_failed({"url": url, "error": str(error) if error else None})

    async def process(self, url, depth):
        try:
            result = await self.http.get(url)
            response = result.get("response")
            if not response:
                self.failed.add(url); self._persist_failed(url, result.get("error")); return
            status = getattr(response, "status_code", 0)
            final_url = str(getattr(response, "url", url))
            headers = dict(getattr(response, "headers", {}) or {})
            content_type = headers.get("content-type", "")
            content = getattr(response, "content", b"") or b""
            text = getattr(response, "text", "") or ""
            if "text/html" not in content_type.lower():
                page = {"url": url, "final_url": final_url, "status_code": status, "depth": depth, "content_type": content_type,
                        "response_time": result.get("elapsed", 0), "content_length": len(content), "title": "", "meta_description": "",
                        "canonical": "", "robots": "", "language": "", "viewport": "", "word_count": 0,
                        "content_hash": hashlib.sha256(content).hexdigest(), "is_indexable": False, "redirect": final_url != url, "error": None}
                await save_page(self.crawl_id, page); self._persist_page(page); return

            parsed = parse_html(text, final_url)
            browser_result = await self.browser.get(url)
            b = browser_result.get("browser", {}) or {}
            browser_html = browser_result.get("html") or text
            if b:
                for key in ("title", "meta_description", "canonical", "robots", "language", "viewport"):
                    if b.get(key): parsed[key] = b[key]
                parsed["h1s"] = b.get("h1s", [])
                parsed["h2s"] = b.get("h2s", [])
                parsed["h3s"] = b.get("h3s", [])
                parsed["images"] = b.get("images", parsed.get("images", []))
                parsed["links"] = b.get("links", parsed.get("links", []))
                parsed["scripts"] = b.get("scripts", parsed.get("scripts", []))
                parsed["stylesheets"] = b.get("stylesheets", parsed.get("stylesheets", []))
                parsed["word_count"] = len((b.get("body_text") or "").split()) or parsed.get("word_count", 0)
            schema_types, schema_objects = extract_schema_types(browser_html)
            tracking, tracking_evidence = detect_signatures(browser_html, parsed.get("scripts", []), TRACKING_SIGNATURES)
            platforms, platform_evidence = detect_signatures(browser_html, parsed.get("scripts", []), PLATFORM_SIGNATURES)
            platform = platforms[0] if platforms else "Custom / Unknown"
            robots = parsed.get("robots", "") or ""
            is_indexable = status == 200 and "noindex" not in robots.lower()
            page_text = parsed.get("text", "")
            page = {
                "url": url, "final_url": final_url, "status_code": status, "depth": depth, "content_type": content_type,
                "response_time": result.get("elapsed", 0), "content_length": len(content), "title": parsed.get("title", ""),
                "title_length": len(parsed.get("title", "")), "meta_description": parsed.get("meta_description", ""),
                "meta_description_length": len(parsed.get("meta_description", "")), "canonical": parsed.get("canonical", ""),
                "robots": robots, "language": parsed.get("language", ""), "viewport": parsed.get("viewport", ""),
                "word_count": parsed.get("word_count", 0), "content_hash": hashlib.sha256(page_text.encode("utf-8", errors="ignore")).hexdigest(),
                "is_indexable": is_indexable, "redirect": final_url != url, "error": None,
                "h1s": parsed.get("h1s", []), "h2s": parsed.get("h2s", []), "h3s": parsed.get("h3s", []),
                "images": parsed.get("images", []), "links": parsed.get("links", []), "scripts": parsed.get("scripts", []),
                "stylesheets": parsed.get("stylesheets", []), "hreflang": parsed.get("hreflang", []), "schemas": schema_types,
                "schema_objects": schema_objects, "tracking": tracking, "tracking_evidence": tracking_evidence,
                "platform": platform, "platform_evidence": platform_evidence,
                "performance": {"dom_loaded_seconds": browser_result.get("dom_loaded_seconds", 0), "fully_loaded_seconds": browser_result.get("fully_loaded_seconds", 0)},
                "http_headers": headers, "raw_html_size": len(browser_html.encode("utf-8", errors="ignore")),
            }
            await save_page(self.crawl_id, page); self._persist_page(page)
            try:
                for issue in analyze(page): await save_issue(self.crawl_id, issue)
            except Exception as e: self._persist_failed(url, "analysis: " + str(e))
            for image in page["images"]:
                try: await save_image(self.crawl_id, image)
                except Exception: pass
            for script in page["scripts"]:
                try: await save_resource(self.crawl_id, "javascript")
                except Exception: pass
            for stylesheet in page["stylesheets"]:
                try: await save_resource(self.crawl_id, "stylesheet")
                except Exception: pass
            for item in page["hreflang"]:
                try: await save_hreflang(self.crawl_id, item)
                except Exception: pass
            for schema in schema_types:
                try: await save_schema(self.crawl_id, schema)
                except Exception: pass
            for link in page["links"]:
                target = link.get("url", "")
                if not target: continue
                internal = bool(urlparse(target).netloc in ("", urlparse(self.start_url).netloc))
                self._persist_link({"source_url": url, "target_url": target, "anchor": link.get("anchor", ""), "internal": internal})
                if internal: self._add_url(target, depth + 1)
        except Exception as e:
            self.failed.add(url); self._persist_failed(url, e); print(f"[FAILED] {url} | {type(e).__name__}: {e}")

    async def run(self):
        self._add_url(self.start_url, 0)
        self.persistence.update_meta(start_url=self.start_url, max_pages=self.max_pages, max_depth=self.max_depth, concurrency=self.concurrency, status="running")
        await update_crawl(self.crawl_id, status="running", started_at=datetime.utcnow().isoformat())
        await self.browser.start()
        try:
            while self.queue and len(self.crawled) < self.max_pages and self.running:
                batch = []
                while self.queue and len(batch) < self.concurrency and len(self.crawled) < self.max_pages:
                    url, depth = self.queue.pop(0)
                    if url in self.crawled: continue
                    self.crawled.add(url); batch.append(self.process(url, depth))
                if batch: await asyncio.gather(*batch, return_exceptions=True)
                self.persistence.update_meta(pages_saved=len(self.crawled), discovered=len(self.discovered), failed=len(self.failed))
            self.persistence.finish("completed")
            await update_crawl(self.crawl_id, status="completed", pages_discovered=len(self.discovered), pages_crawled=len(self.crawled), pages_indexable=sum(1 for p in self.crawled if p))
            await self._export_and_report()
            print("\n========== CRAWL COMPLETE ==========")
            print(f"Discovered: {len(self.discovered)}")
            print(f"Crawled: {len(self.crawled)}")
            print(f"Failed: {len(self.failed)}")
        except Exception:
            self.persistence.finish("failed")
            await update_crawl(self.crawl_id, status="failed", finished_at=datetime.utcnow().isoformat())
            raise
        finally:
            await self.browser.close()
            await self.http.close()

    async def _export_and_report(self):
        import sqlite3
        from app.audit_engine import generate_report
        os.makedirs("results", exist_ok=True)
        db = "data/crawler.db"
        out = Path(f"results/crawl_{self.crawl_id}.json")
        con = sqlite3.connect(db); con.row_factory = sqlite3.Row
        pages = [dict(r) for r in con.execute("SELECT * FROM pages WHERE crawl_id=? ORDER BY id", (self.crawl_id,))]
        links = [dict(r) for r in con.execute("SELECT * FROM links WHERE crawl_id=? ORDER BY id", (self.crawl_id,))]
        images = [dict(r) for r in con.execute("SELECT * FROM images WHERE crawl_id=? ORDER BY id", (self.crawl_id,))]
        resources = [dict(r) for r in con.execute("SELECT * FROM resources WHERE crawl_id=? ORDER BY id", (self.crawl_id,))]
        schemas = [dict(r) for r in con.execute("SELECT * FROM schemas WHERE crawl_id=? ORDER BY id", (self.crawl_id,))]
        failed = [dict(r) for r in con.execute("SELECT * FROM issues WHERE crawl_id=? AND severity IN ('high','critical')", (self.crawl_id,))]
        con.close()
        # Merge rich JSONL records over relational page records.
        jsonl = self.persistence.pages_file
        rich = []
        if jsonl.exists():
            for line in jsonl.read_text(encoding="utf-8", errors="ignore").splitlines():
                try: rich.append(json.loads(line))
                except Exception: pass
        by_url = {x.get("url"): x for x in rich}
        for p in pages:
            p.update({k:v for k,v in by_url.get(p.get("url"), {}).items() if k not in p or k in {"h1s","h2s","h3s","images","links","scripts","stylesheets","schemas","schema_objects","tracking","platform","performance","http_headers","raw_html_size"}})
        site_pages = [x for x in pages if x.get("status_code")]
        first_headers = (site_pages[0].get("http_headers") or {}) if site_pages else {}
        platform_counts = {}
        tracking = set()
        dom_times=[]; full_times=[]
        for p in site_pages:
            platform_counts[p.get("platform", "Custom / Unknown")] = platform_counts.get(p.get("platform", "Custom / Unknown"),0)+1
            tracking.update(p.get("tracking",[]) or [])
            perf=p.get("performance") or {}
            if perf.get("dom_loaded_seconds"): dom_times.append(perf["dom_loaded_seconds"])
            if perf.get("fully_loaded_seconds"): full_times.append(perf["fully_loaded_seconds"])
        platform=max(platform_counts,key=platform_counts.get) if platform_counts else "Custom / Unknown"
        site = {"platform":platform,"platform_evidence":platform_counts,"tracking":sorted(tracking),
                "average_dom_loaded_seconds":round(sum(dom_times)/len(dom_times),3) if dom_times else 0,
                "average_fully_loaded_seconds":round(sum(full_times)/len(full_times),3) if full_times else 0,
                "domain":site_metadata(self.start_url, first_headers), "pages_crawled":len(pages), "pages_discovered":len(self.discovered)}
        data={"crawl_id":self.crawl_id,"start_url":self.start_url,"site":site,"pages":pages,"links":links,"images":images,"resources":resources,"schemas":schemas,"failed_urls":[x.get("url") for x in failed]}
        out.write_text(json.dumps(data,indent=2,ensure_ascii=False,default=str),encoding="utf-8")
        print(f"[JSON] Saved: {out}")
        html_path = generate_report(data, out)
        print(f"[REPORT] Saved: {html_path}")
        try:
            import webbrowser; webbrowser.open(html_path.resolve().as_uri())
        except Exception: pass
