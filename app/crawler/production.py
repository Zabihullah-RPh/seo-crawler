import asyncio
import csv
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
from app.crawler.email_extractor import extract_emails
from app.crawler.robots import RobotsManager
from app.config import DEFAULT_USER_AGENT
from app.storage.db import save_page, save_issue, save_image, save_resource, save_hreflang, save_schema, save_email, update_crawl
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
    found, evidence = [], {}
    for name, patterns in signatures.items():
        hits = [p for p in patterns if p.lower() in text]
        if hits:
            found.append(name); evidence[name] = hits
    return sorted(found), evidence

def extract_schema_types(html):
    import re
    results, raw_items = [], []
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
            obj = json.loads(raw.strip()); collect(obj); raw_items.append(obj)
        except Exception: pass
    return sorted(set(results)), raw_items

def site_metadata(url, headers):
    host = urlparse(url).hostname or ""
    data = {"hostname": host, "ip_addresses": [], "server": headers.get("server"), "ssl": {}, "domain_age": None, "hosting": {}}
    try: data["ip_addresses"] = sorted(set(socket.gethostbyname_ex(host)[2]))
    except Exception: pass
    try:
        ctx = ssl.create_default_context()
        with socket.create_connection((host, 443), timeout=5) as sock:
            with ctx.wrap_socket(sock, server_hostname=host) as ssock:
                cert = ssock.getpeercert()
                data["ssl"] = {"enabled": True, "subject": dict(x[0] for x in cert.get("subject", [])), "issuer": dict(x[0] for x in cert.get("issuer", [])), "valid_from": cert.get("notBefore"), "valid_until": cert.get("notAfter")}
    except Exception as e: data["ssl"] = {"enabled": False, "error": str(e)}
    try:
        req = urllib.request.Request(f"https://rdap.org/domain/{host}", headers={"User-Agent": "SEO-Crawler/1.0"})
        with urllib.request.urlopen(req, timeout=8) as r: rdap = json.loads(r.read().decode("utf-8", errors="ignore"))
        events = {x.get("eventAction"): x.get("eventDate") for x in rdap.get("events", [])}
        created = events.get("registration") or events.get("creation")
        if created: data["domain_age"] = {"created": created}
    except Exception: pass
    return data

class ProductionCrawler:
    def __init__(self, crawl_id, start_url, max_pages=100000, max_depth=50, concurrency=20):
        self.crawl_id = crawl_id; self.start_url = normalize_url(start_url); self.max_pages = int(max_pages); self.max_depth = int(max_depth); self.concurrency = int(concurrency)
        self.discovered, self.crawled, self.failed = set(), set(), set(); self.queue, self.running = [], True
        self.http = HTTPClient(concurrency=self.concurrency); self.browser = BrowserClient(concurrency=min(5, self.concurrency)); self.robots = RobotsManager(self.start_url, DEFAULT_USER_AGENT); self.persistence = CrawlPersistence(self.crawl_id); self.site = {}
    def _add_url(self, url, depth):
        try: url = normalize_url(url)
        except Exception: return False
        if not url or not same_host(url, self.start_url) or depth > self.max_depth or url in self.discovered or len(self.discovered) >= self.max_pages: return False
        self.discovered.add(url); self.queue.append((url, depth)); return True
    def _persist_page(self, page): self.persistence.save_page(page)
    def _persist_link(self, link): self.persistence.save_link(link)
    def _persist_failed(self, url, error): self.persistence.save_failed({"url": url, "error": str(error) if error else None})
    async def process(self, url, depth):
        try:
            if not self.robots.allowed(url):
                reason = self.robots.blocked_reason or "Disallowed by robots.txt; no request attempted."
                self.failed.add(url)
                self._persist_failed(url, reason)
                print(f"[ROBOTS BLOCKED] {url} | {reason}")
                return

            result = await self.http.get(url)
            response = result.get("response")
            if not response:
                self.failed.add(url)
                self._persist_failed(url, result.get("error"))
                return

            status = getattr(response, "status_code", 0)
            final_url = str(getattr(response, "url", url))
            headers = dict(getattr(response, "headers", {}) or {})
            content_type = headers.get("content-type", "")
            content = getattr(response, "content", b"") or b""
            text = getattr(response, "text", "") or ""

            if status >= 400:
                reason = f"HTTP {status}; request stopped without another attempt."
                self.failed.add(url)
                self._persist_failed(url, reason)
                print(f"[HTTP BLOCKED] {url} | {reason}")
                return

            if "text/html" not in content_type.lower():
                page = {"url": url, "final_url": final_url, "status_code": status, "depth": depth, "content_type": content_type, "response_time": result.get("elapsed", 0), "content_length": len(content), "title": "", "meta_description": "", "canonical": "", "robots": "", "language": "", "viewport": "", "word_count": 0, "content_hash": hashlib.sha256(content).hexdigest(), "is_indexable": False, "redirect": final_url != url, "error": None}
                await save_page(self.crawl_id, page); self._persist_page(page); return
            parsed = parse_html(text, final_url)
            browser_result = await self.browser.get(url)
            b = browser_result.get("browser", {}) or {}
            browser_html = browser_result.get("html") or text

            # Extract public contact details from both the original response and
            # rendered DOM. Merge duplicate discoveries but retain evidence.
            email_map = {}
            for markup in dict.fromkeys((text, browser_html)):
                for candidate in extract_emails(markup, final_url):
                    key = candidate["email"]
                    if key not in email_map:
                        email_map[key] = candidate
                    else:
                        existing = set(email_map[key].get("extraction_methods", []))
                        existing.update(candidate.get("extraction_methods", []))
                        email_map[key]["extraction_methods"] = sorted(existing)
            page_emails = [email_map[key] for key in sorted(email_map)]

            if b:
                for key in ("title", "meta_description", "canonical", "robots", "language", "viewport"):
                    if b.get(key): parsed[key] = b[key]
                parsed["h1s"] = b.get("h1s", []); parsed["h2s"] = b.get("h2s", []); parsed["h3s"] = b.get("h3s", []); parsed["images"] = b.get("images", parsed.get("images", [])); parsed["links"] = b.get("links", parsed.get("links", [])); parsed["scripts"] = b.get("scripts", parsed.get("scripts", [])); parsed["stylesheets"] = b.get("stylesheets", parsed.get("stylesheets", [])); parsed["word_count"] = len((b.get("body_text") or "").split()) or parsed.get("word_count", 0)
            schema_types, schema_objects = extract_schema_types(browser_html); tracking, tracking_evidence = detect_signatures(browser_html, parsed.get("scripts", []), TRACKING_SIGNATURES); platforms, platform_evidence = detect_signatures(browser_html, parsed.get("scripts", []), PLATFORM_SIGNATURES); platform = platforms[0] if platforms else "Custom / Unknown"; robots = parsed.get("robots", "") or ""; is_indexable = status == 200 and "noindex" not in robots.lower(); page_text = parsed.get("text", "")
            page = {"url": url, "final_url": final_url, "status_code": status, "depth": depth, "content_type": content_type, "response_time": result.get("elapsed", 0), "content_length": len(content), "title": parsed.get("title", ""), "title_length": len(parsed.get("title", "")), "meta_description": parsed.get("meta_description", ""), "meta_description_length": len(parsed.get("meta_description", "")), "canonical": parsed.get("canonical", ""), "robots": robots, "language": parsed.get("language", ""), "viewport": parsed.get("viewport", ""), "word_count": parsed.get("word_count", 0), "content_hash": hashlib.sha256(page_text.encode("utf-8", errors="ignore")).hexdigest(), "is_indexable": is_indexable, "redirect": final_url != url, "error": None, "h1s": parsed.get("h1s", []), "h2s": parsed.get("h2s", []), "h3s": parsed.get("h3s", []), "images": parsed.get("images", []), "links": parsed.get("links", []), "scripts": parsed.get("scripts", []), "stylesheets": parsed.get("stylesheets", []), "hreflang": parsed.get("hreflang", []), "schemas": schema_types, "schema_objects": schema_objects, "tracking": tracking, "tracking_evidence": tracking_evidence, "platform": platform, "platform_evidence": platform_evidence, "performance": {"dom_loaded_seconds": browser_result.get("dom_loaded_seconds", 0), "fully_loaded_seconds": browser_result.get("fully_loaded_seconds", 0)}, "http_headers": headers, "raw_html_size": len(browser_html.encode("utf-8", errors="ignore")), "emails": page_emails}
            await save_page(self.crawl_id, page)
            self._persist_page(page)
            for email_candidate in page_emails:
                await save_email(self.crawl_id, email_candidate)
                self.persistence.save_email(email_candidate)
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
                internal = bool(urlparse(target).netloc in ("", urlparse(self.start_url).netloc)); self._persist_link({"source_url": url, "target_url": target, "anchor": link.get("anchor", ""), "internal": internal})
                if internal: self._add_url(target, depth + 1)
        except Exception as e:
            self.failed.add(url); self._persist_failed(url, e); print(f"[FAILED] {url} | {type(e).__name__}: {e}")
    async def run(self):
        self._add_url(self.start_url, 0)
        self.persistence.update_meta(
            start_url=self.start_url,
            max_pages=self.max_pages,
            max_depth=self.max_depth,
            concurrency=self.concurrency,
        )
        await update_crawl(
            self.crawl_id,
            status="running",
            started_at=datetime.utcnow().isoformat(),
        )

        try:
            robots_result = await self.robots.load(self.http)
            if not self.robots.allowed(self.start_url):
                reason = self.robots.blocked_reason or "Start URL disallowed by robots.txt."
                self.failed.add(self.start_url)
                self._persist_failed(self.start_url, reason)
                self.persistence.update_meta(
                    pages_saved=0,
                    discovered=len(self.discovered),
                    failed=len(self.failed),
                    robots_status=robots_result.get("status"),
                    blocked_reason=reason,
                )
                self.persistence.finish("completed")
                await update_crawl(
                    self.crawl_id,
                    status="completed",
                    pages_discovered=len(self.discovered),
                    pages_crawled=0,
                    pages_indexable=0,
                    finished_at=datetime.utcnow().isoformat(),
                )
                print(f"[ROBOTS BLOCKED] {self.start_url} | {reason}")
                return

            await self.browser.start()

            while self.queue and len(self.crawled) < self.max_pages and self.running:
                batch = []
                while self.queue and len(batch) < self.concurrency and len(self.crawled) < self.max_pages:
                    url, depth = self.queue.pop(0)
                    if url in self.crawled:
                        continue
                    self.crawled.add(url)
                    batch.append(self.process(url, depth))

                if batch:
                    await asyncio.gather(*batch, return_exceptions=True)

                self.persistence.update_meta(
                    pages_saved=len(self.crawled),
                    discovered=len(self.discovered),
                    failed=len(self.failed),
                )

            self.persistence.finish("completed")
            await update_crawl(
                self.crawl_id,
                status="completed",
                pages_discovered=len(self.discovered),
                pages_crawled=len(self.crawled),
                pages_indexable=sum(1 for p in self.crawled if p),
                finished_at=datetime.utcnow().isoformat(),
            )
            await self._export_and_report()
            print("\\n========== CRAWL COMPLETE ==========")
            print(f"Discovered: {len(self.discovered)}")
            print(f"Crawled: {len(self.crawled)}")
            print(f"Failed: {len(self.failed)}")

        except Exception:
            self.persistence.finish("failed")
            await update_crawl(
                self.crawl_id,
                status="failed",
                finished_at=datetime.utcnow().isoformat(),
            )
            raise

        finally:
            await self.browser.close()
            await self.http.close()

    async def _export_and_report(self):
        import sqlite3
        from app.audit_engine import generate_report
        from app.integrations.common_crawl_runtime import collect as collect_layer1
        from app.integrations.backlink_layer2 import investigate_layer2 as investigate_live_layer2
        try:
            from app.integrations.common_crawl_layer2 import investigate_layer2 as investigate_archive_layer2
        except ModuleNotFoundError:
            # The archive-specific Layer 2 module is optional in this repository version.
            # Fall back to live verification rather than failing the completed crawl.
            investigate_archive_layer2 = None
        os.makedirs("results", exist_ok=True); db="data/crawler.db"; out=Path(f"results/crawl_{self.crawl_id}.json")
        con=sqlite3.connect(db); con.row_factory=sqlite3.Row
        pages=[dict(r) for r in con.execute("SELECT * FROM pages WHERE crawl_id=? ORDER BY id",(self.crawl_id,))]
        links=[dict(r) for r in con.execute("SELECT * FROM links WHERE crawl_id=? ORDER BY id",(self.crawl_id,))]
        images=[dict(r) for r in con.execute("SELECT * FROM images WHERE crawl_id=? ORDER BY id",(self.crawl_id,))]
        resources=[dict(r) for r in con.execute("SELECT * FROM resources WHERE crawl_id=? ORDER BY id",(self.crawl_id,))]
        schemas=[dict(r) for r in con.execute("SELECT * FROM schemas WHERE crawl_id=? ORDER BY id",(self.crawl_id,))]
        failed=[dict(r) for r in con.execute("SELECT * FROM issues WHERE crawl_id=? AND severity IN ('high','critical')",(self.crawl_id,))]
        email_rows=[dict(r) for r in con.execute("SELECT email, email_domain, page_url, same_domain, generic_mailbox, extraction_method FROM emails WHERE crawl_id=? ORDER BY email, page_url",(self.crawl_id,))]
        con.close()

        # Group repeated addresses and retain every source page/evidence method.
        email_lookup = {}
        for row in email_rows:
            address = row["email"]
            if address not in email_lookup:
                email_lookup[address] = {
                    "email": address,
                    "email_domain": row.get("email_domain") or "",
                    "same_domain": bool(row.get("same_domain")),
                    "generic_mailbox": bool(row.get("generic_mailbox")),
                    "source_pages": [],
                    "extraction_methods": [],
                }
            item = email_lookup[address]
            if row.get("page_url") and row["page_url"] not in item["source_pages"]:
                item["source_pages"].append(row["page_url"])
            for method in str(row.get("extraction_method") or "").split(","):
                if method and method not in item["extraction_methods"]:
                    item["extraction_methods"].append(method)
            item["same_domain"] = item["same_domain"] or bool(row.get("same_domain"))
        emails = sorted(email_lookup.values(), key=lambda item: item["email"])
        for item in emails:
            item["source_pages"].sort()
            item["extraction_methods"].sort()

        email_csv = Path(f"results/crawl_{self.crawl_id}_emails.csv")
        with email_csv.open("w", newline="", encoding="utf-8-sig") as email_file:
            fields = ["email", "email_domain", "same_domain", "generic_mailbox", "source_pages", "extraction_methods"]
            writer = csv.DictWriter(email_file, fieldnames=fields)
            writer.writeheader()
            for item in emails:
                writer.writerow({
                    **{key: item.get(key) for key in fields if key not in ("source_pages", "extraction_methods")},
                    "source_pages": " | ".join(item["source_pages"]),
                    "extraction_methods": ", ".join(item["extraction_methods"]),
                })
        rich=[]
        if self.persistence.pages_file.exists():
            for line in self.persistence.pages_file.read_text(encoding="utf-8",errors="ignore").splitlines():
                try: rich.append(json.loads(line))
                except Exception: pass
        by_url={x.get("url"):x for x in rich}
        for p in pages: p.update({k:v for k,v in by_url.get(p.get("url"),{}).items() if k not in p or k in {"h1s","h2s","h3s","images","links","scripts","stylesheets","schemas","schema_objects","tracking","platform","performance","http_headers","raw_html_size"}})
        site_pages=[x for x in pages if x.get("status_code")]; first_headers=(site_pages[0].get("http_headers") or {}) if site_pages else {}; platform_counts={}; tracking=set(); dom_times=[]; full_times=[]
        for p in site_pages:
            platform_counts[p.get("platform","Custom / Unknown")]=platform_counts.get(p.get("platform","Custom / Unknown"),0)+1; tracking.update(p.get("tracking",[]) or []); perf=p.get("performance") or {}; dom_times.append(perf["dom_loaded_seconds"]) if perf.get("dom_loaded_seconds") else None; full_times.append(perf["fully_loaded_seconds"]) if perf.get("fully_loaded_seconds") else None
        platform=max(platform_counts,key=platform_counts.get) if platform_counts else "Custom / Unknown"; site={"platform":platform,"platform_evidence":platform_counts,"tracking":sorted(tracking),"average_dom_loaded_seconds":round(sum(dom_times)/len(dom_times),3) if dom_times else 0,"average_fully_loaded_seconds":round(sum(full_times)/len(full_times),3) if full_times else 0,"domain":site_metadata(self.start_url,first_headers),"pages_crawled":len(pages),"pages_discovered":len(self.discovered)}
        data={"crawl_id":self.crawl_id,"start_url":self.start_url,"site":site,"pages":pages,"links":links,"images":images,"resources":resources,"schemas":schemas,"emails":emails,"email_summary":{"unique_addresses":len(emails),"source_occurrences":len(email_rows),"same_domain_addresses":sum(1 for x in emails if x["same_domain"]),"generic_mailboxes":sum(1 for x in emails if x["generic_mailbox"])},"failed_urls":[x.get("url") for x in failed]}
        backlinks={"provider":"Common Crawl","layer1":{},"layer2":{}}
        try:
            layer1=collect_layer1(self.start_url); backlinks["layer1"]=layer1
            if layer1.get("status")=="success" and layer1.get("backlinks"):
                print(f"[BACKLINKS] Layer 1 found {layer1.get('referring_domains',0)} referring domains; starting archive-first Layer 2 investigation")
                if investigate_archive_layer2 is None:
                    print("[BACKLINKS] Archive Layer 2 module is unavailable; falling back to live Layer 2")
                    layer2=await investigate_live_layer2(self.start_url,layer1)
                    layer2["found_via"]="live_crawl_fallback_missing_archive_module"
                else:
                    layer2=await investigate_archive_layer2(self.start_url,layer1)
                    if layer2.get("status")=="index_unavailable":
                        print("[BACKLINKS] Common Crawl archive index unavailable; falling back to live Layer 2")
                        layer2=await investigate_live_layer2(self.start_url,layer1)
                        layer2["found_via"]="live_crawl_fallback"
                    else:
                        layer2["found_via"]="common_crawl_warc"
                backlinks["layer2"]=layer2
            else: backlinks["layer2"]={"status":"skipped","reason":layer1.get("status","unknown"),"links_found":0,"backlinks":[]}
        except Exception as exc: backlinks["layer2"]={"status":"failed","links_found":0,"backlinks":[],"message":f"{type(exc).__name__}: {exc}"}
        data["backlinks"]=backlinks
        out.write_text(json.dumps(data,indent=2,ensure_ascii=False,default=str),encoding="utf-8")
        print(f"[JSON] Saved: {out}")
        print(f"[EMAILS] Saved {len(emails)} unique public email candidates to {email_csv}")
        html_path=generate_report(data,out)
        print(f"[REPORT] Saved: {html_path}")
        try:
            import webbrowser; webbrowser.open(html_path.resolve().as_uri())
        except Exception: pass
