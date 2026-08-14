import asyncio
import heapq
import time
from urllib.parse import urljoin,urlparse
from collections import defaultdict

from app.crawler.browser import BrowserClient
from app.crawler.robots import RobotsManager
from app.crawler.parser import parse_html
from app.storage.db import *
from app.analyzers.technical import analyze
from app.utils.urls import normalize_url,same_host
from app.analyzers.intelligence import detect_orphans, detect_duplicates, calculate_pagerank


class SmartCrawler:

    def __init__(
        self,
        crawl_id,
        start_url,
        max_pages=100000,
        max_depth=50,
        concurrency=20
    ):
        self.crawl_id=crawl_id
        self.start_url=normalize_url(start_url)
        self.max_pages=max_pages
        self.max_depth=max_depth
        self.concurrency=concurrency

        self.queue=[]
        self.sequence=0

        self.discovered=set()
        self.crawled=set()
        self.failed=set()

        self.incoming=defaultdict(int)
        self.outgoing=defaultdict(int)

        self.http=None
        self.robots=None
        self.running=True

        self.discovered.add(self.start_url)
        self.sequence += 1
        heapq.heappush(self.queue, (0, self.sequence, self.start_url, 0))

    def priority(self,depth,url):

        score=depth*10

        path=urlparse(url).path.lower()

        if path in ("","/"):
            score-=100

        if any(x in path for x in [
            "product","service","category",
            "blog","article"
        ]):
            score-=5

        return score

    def add_url(self,url,depth,priority=None):

        url=normalize_url(url)

        if not url:
            return

        if url in self.discovered:
            return

        if depth>self.max_depth:
            return

        if len(self.discovered)>=self.max_pages:
            return

        if not same_host(
            self.start_url,
            url
        ):
            return

        if not self.robots.allowed(url):
            return

        self.discovered.add(url)

        self.sequence+=1

        if priority is None:
            priority=self.priority(
                depth,
                url
            )

        heapq.heappush(
            self.queue,
            (
                priority,
                self.sequence,
                url,
                depth
            )
        )

    async def process(self,url,depth):

        result=await self.http.get(url)

        response=result["response"]

        if not response:

            self.failed.add(url)

            page={
                "url":url,
                "final_url":url,
                "status_code":0,
                "depth":depth,
                "content_type":"",
                "response_time":0,
                "content_length":0,
                "title":"",
                "meta_description":"",
                "canonical":"",
                "robots":"",
                "language":"",
                "viewport":"",
                "word_count":0,
                "content_hash":"",
                "is_indexable":False,
                "redirect":False,
                "error":result["error"]
            }

            await save_page(
                self.crawl_id,
                page
            )

            await save_issue(
                self.crawl_id,
                {
                    "url":url,
                    "type":"request_error",
                    "severity":"critical"
                }
            )

            return

        status=response.status_code
        final_url=str(response.url)

        content_type=response.headers.get(
            "content-type",
            ""
        )

        if 300<=status<400:

            location=response.headers.get(
                "location"
            )

            if location:

                target=normalize_url(
                    urljoin(
                        url,
                        location
                    )
                )

                if target:
                    self.add_url(
                        target,
                        depth+1,
                        -50
                    )

            page={
                "url":url,
                "final_url":final_url,
                "status_code":status,
                "depth":depth,
                "content_type":content_type,
                "response_time":result["elapsed"],
                "content_length":len(
                    response.content
                ),
                "title":"",
                "meta_description":"",
                "canonical":"",
                "robots":"",
                "language":"",
                "viewport":"",
                "word_count":0,
                "content_hash":"",
                "is_indexable":False,
                "redirect":True,
                "error":None
            }

            await save_page(
                self.crawl_id,
                page
            )

            await save_issue(
                self.crawl_id,
                {
                    "url":url,
                    "type":"redirect",
                    "severity":"info"
                }
            )

            return

        if "text/html" not in content_type.lower():

            page={
                "url":url,
                "final_url":final_url,
                "status_code":status,
                "depth":depth,
                "content_type":content_type,
                "response_time":result["elapsed"],
                "content_length":len(
                    response.content
                ),
                "title":"",
                "meta_description":"",
                "canonical":"",
                "robots":"",
                "language":"",
                "viewport":"",
                "word_count":0,
                "content_hash":"",
                "is_indexable":False,
                "redirect":False,
                "error":None
            }

            await save_page(
                self.crawl_id,
                page
            )

            return

        html = result.get(
            "html",
            ""
        )

        if not html:
            html = response.text

        parsed=parse_html(
            html,
            final_url
        )

        import hashlib

        content_hash=hashlib.sha256(
            parsed["text"].encode(
                "utf-8",
                errors="ignore"
            )
        ).hexdigest()

        indexable=(
            status==200
            and
            "noindex"
            not in parsed["robots"].lower()
        )

        page={
            "url":url,
            "final_url":final_url,
            "status_code":status,
            "depth":depth,
            "content_type":content_type,
            "response_time":result["elapsed"],
            "content_length":len(
                response.content
            ),
            "title":parsed["title"],
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
                indexable,
            "redirect":False,
            "error":None
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

        for stylesheet in parsed["stylesheets"]:

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

        discovered_links = 0

        for link in parsed.get("links", []):

            raw_url = link.get("url")

            if not raw_url:
                continue

            destination = normalize_url(raw_url)

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

            if internal:

                self.incoming[destination] += 1
                self.outgoing[url] += 1

                before = len(self.discovered)

                self.add_url(
                    destination,
                    depth + 1
                )

                if len(self.discovered) > before:
                    discovered_links += 1

        print(
            f"[CRAWL] {url} | "
            f"links={len(parsed.get('links', []))} | "
            f"internal_added={discovered_links} | "
            f"queue={len(self.queue)} | "
            f"discovered={len(self.discovered)}"
        )

    async def run(self):

        await update_crawl(
            self.crawl_id,
            status="crawling"
        )

        self.http=BrowserClient(
            self.concurrency
        )

        await self.http.start()

        self.robots=RobotsManager(
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

                batch=[]

                while (
                    self.queue
                    and
                    len(batch)<self.concurrency
                    and
                    len(self.crawled)<self.max_pages
                ):

                    _,_,url,depth=heapq.heappop(
                        self.queue
                    )

                    if url in self.crawled:
                        continue

                    self.crawled.add(url)

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

            await self.finalize()

        finally:

            await self.http.close()

    async def finalize(self):

        await update_crawl(
            self.crawl_id,
            status="completed",
            pages_discovered=len(
                self.discovered
            ),
            pages_crawled=len(
                self.crawled
            )
        )

        await detect_orphans(
            self.crawl_id
        )

        await detect_duplicates(
            self.crawl_id
        )

        await calculate_pagerank(
            self.crawl_id
        )

