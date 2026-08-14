import asyncio
from collections import deque
from urllib.parse import urljoin

from app.config import (
    DEFAULT_CONCURRENCY,
    DEFAULT_MAX_DEPTH,
    DEFAULT_MAX_PAGES
)

from app.crawler.http import HTTPClient
from app.crawler.robots import RobotsManager
from app.crawler.parser import parse_html
from app.analyzers.technical import analyze
from app.storage.db import (
    save_page,
    save_issue,
    save_link,
    save_image,
    save_resource,
    save_hreflang,
    save_schema,
    update_crawl
)

from app.utils.urls import (
    normalize_url,
    same_host
)


class CrawlJob:

    def __init__(
        self,
        crawl_id,
        start_url,
        max_pages=DEFAULT_MAX_PAGES,
        max_depth=DEFAULT_MAX_DEPTH,
        concurrency=DEFAULT_CONCURRENCY
    ):

        self.crawl_id = crawl_id
        self.start_url = normalize_url(
            start_url
        )

        self.max_pages = max_pages
        self.max_depth = max_depth
        self.concurrency = concurrency

        self.queue = deque()

        self.discovered = set()
        self.crawled = set()

        self.running = True

        self.http = None
        self.robots = None

        self.queue.append(
            (
                self.start_url,
                0
            )
        )

        self.discovered.add(
            self.start_url
        )

    async def process(
        self,
        url,
        depth
    ):

        result = await self.http.get(
            url
        )

        response = result["response"]

        if not response:

            page = {
                "url": url,
                "final_url": url,
                "status_code": 0,
                "depth": depth,
                "content_type": "",
                "response_time": 0,
                "content_length": 0,
                "title": "",
                "meta_description": "",
                "canonical": "",
                "robots": "",
                "language": "",
                "viewport": "",
                "word_count": 0,
                "content_hash": "",
                "is_indexable": False,
                "redirect": False,
                "error": result["error"]
            }

            await save_page(
                self.crawl_id,
                page
            )

            await save_issue(
                self.crawl_id,
                {
                    "url": url,
                    "type": "request_error",
                    "severity": "critical"
                }
            )

            return

        status = response.status_code

        final_url = str(
            response.url
        )

        content_type = (
            response.headers.get(
                "content-type",
                ""
            )
        )

        is_redirect = (
            300 <= status < 400
        )

        if is_redirect:

            location = (
                response.headers.get(
                    "location"
                )
            )

            if location:

                target = normalize_url(
                    urljoin(
                        url,
                        location
                    )
                )

                if (
                    target
                    and
                    same_host(
                        self.start_url,
                        target
                    )
                    and
                    target not in self.discovered
                    and
                    depth < self.max_depth
                ):

                    self.discovered.add(
                        target
                    )

                    self.queue.append(
                        (
                            target,
                            depth + 1
                        )
                    )

            page = {
                "url": url,
                "final_url": final_url,
                "status_code": status,
                "depth": depth,
                "content_type": content_type,
                "response_time": result["elapsed"],
                "content_length": len(
                    response.content
                ),
                "title": "",
                "meta_description": "",
                "canonical": "",
                "robots": "",
                "language": "",
                "viewport": "",
                "word_count": 0,
                "content_hash": "",
                "is_indexable": False,
                "redirect": True,
                "error": None
            }

            await save_page(
                self.crawl_id,
                page
            )

            await save_issue(
                self.crawl_id,
                {
                    "url": url,
                    "type": "redirect",
                    "severity": "info"
                }
            )

            return

        if (
            "text/html"
            not in content_type.lower()
        ):

            page = {
                "url": url,
                "final_url": final_url,
                "status_code": status,
                "depth": depth,
                "content_type": content_type,
                "response_time": result["elapsed"],
                "content_length": len(
                    response.content
                ),
                "title": "",
                "meta_description": "",
                "canonical": "",
                "robots": "",
                "language": "",
                "viewport": "",
                "word_count": 0,
                "content_hash": "",
                "is_indexable": False,
                "redirect": False,
                "error": None
            }

            await save_page(
                self.crawl_id,
                page
            )

            return

        html = response.text

        parsed = parse_html(
            html,
            final_url
        )

        is_indexable = (
            status == 200
            and
            "noindex"
            not in parsed["robots"].lower()
            and
            self.robots.allowed(url)
        )

        import hashlib

        content_hash = hashlib.sha256(
            parsed["text"].encode(
                "utf-8",
                errors="ignore"
            )
        ).hexdigest()

        page = {
            "url": url,
            "final_url": final_url,
            "status_code": status,
            "depth": depth,
            "content_type": content_type,
            "response_time": result["elapsed"],
            "content_length": len(
                response.content
            ),
            "title": parsed["title"],
            "meta_description":
                parsed["meta_description"],
            "canonical":
                parsed["canonical"],
            "robots":
                parsed["robots"],
            "language":
                parsed["language"],
            "viewport":
                parsed["viewport"],
            "word_count":
                parsed["word_count"],
            "content_hash":
                content_hash,
            "is_indexable":
                is_indexable,
            "redirect":
                False,
            "error":
                None,
            "headings":
                parsed["headings"]
        }

        await save_page(
            self.crawl_id,
            page
        )

        for issue in analyze(page):

            await save_issue(
                self.crawl_id,
                issue
            )

        for image in parsed["images"]:

            await save_image(
                self.crawl_id,
                url,
                image
            )

        for script in parsed["scripts"]:

            await save_resource(
                self.crawl_id,
                url,
                script,
                "javascript"
            )

        for stylesheet in parsed[
            "stylesheets"
        ]:

            await save_resource(
                self.crawl_id,
                url,
                stylesheet,
                "stylesheet"
            )

        for item in parsed["hreflang"]:

            await save_hreflang(
                self.crawl_id,
                url,
                item
            )

        for schema in parsed["schemas"]:

            await save_schema(
                self.crawl_id,
                url,
                schema
            )

        for link in parsed["links"]:

            destination = normalize_url(
                link["url"]
            )

            if not destination:
                continue

            internal = same_host(
                self.start_url,
                destination
            )

            link["url"] = destination
            link["internal"] = internal

            await save_link(
                self.crawl_id,
                url,
                link
            )

            if not internal:
                continue

            if depth >= self.max_depth:
                continue

            if destination in self.discovered:
                continue

            if len(self.discovered) >= self.max_pages:
                continue

            if not self.robots.allowed(
                destination
            ):
                continue

            self.discovered.add(
                destination
            )

            self.queue.append(
                (
                    destination,
                    depth + 1
                )
            )

    async def run(self):

        await update_crawl(
            self.crawl_id,
            status="crawling"
        )

        self.http = HTTPClient(
            self.concurrency
        )

        self.robots = RobotsManager(
            self.start_url,
            "SEO-Crawler/1.0"
        )

        await self.robots.load(
            self.http
        )

        try:

            while (
                self.queue
                and
                len(self.crawled)
                < self.max_pages
                and
                self.running
            ):

                batch = []

                while (
                    self.queue
                    and
                    len(batch)
                    < self.concurrency
                    and
                    len(self.crawled)
                    < self.max_pages
                ):

                    url, depth = (
                        self.queue.popleft()
                    )

                    if url in self.crawled:
                        continue

                    self.crawled.add(
                        url
                    )

                    batch.append(
                        self.process(
                            url,
                            depth
                        )
                    )

                if batch:

                    await asyncio.gather(
                        *batch,
                        return_exceptions=True
                    )

                await update_crawl(
                    self.crawl_id,
                    pages_discovered=len(
                        self.discovered
                    ),
                    pages_crawled=len(
                        self.crawled
                    )
                )

            await update_crawl(
                self.crawl_id,
                status="completed",
                pages_discovered=len(
                    self.discovered
                ),
                pages_crawled=len(
                    self.crawled
                ),
                finished_at="CURRENT_TIMESTAMP"
            )

        except asyncio.CancelledError:

            await update_crawl(
                self.crawl_id,
                status="cancelled"
            )

            raise

        except Exception:

            await update_crawl(
                self.crawl_id,
                status="failed",
                finished_at="CURRENT_TIMESTAMP"
            )

            raise

        finally:

            await self.http.close()
