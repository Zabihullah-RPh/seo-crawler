import asyncio

from app.crawler.http import HTTPClient
from app.crawler.sitemaps import discover_sitemaps
from app.crawler.budget import CrawlBudget


class CrawlManager:

    def __init__(
        self,
        concurrency=20,
        requests_per_second=10
    ):

        self.concurrency = concurrency

        self.http = HTTPClient(
            concurrency
        )

        self.budget = CrawlBudget(
            requests_per_second
        )

        self.active = True

    async def close(self):

        self.active = False

        await self.http.close()

    async def fetch(
        self,
        url
    ):

        if not self.active:
            return None

        await self.budget.wait()

        return await self.http.get(
            url
        )
