import asyncio
import time


class CrawlBudget:

    def __init__(
        self,
        requests_per_second=10,
        delay=0
    ):

        self.requests_per_second = (
            requests_per_second
        )

        self.delay = delay

        self.last_request = 0

        self.lock = asyncio.Lock()

    async def wait(self):

        async with self.lock:

            now = time.monotonic()

            minimum_interval = (
                1 /
                self.requests_per_second
                if self.requests_per_second > 0
                else 0
            )

            elapsed = (
                now -
                self.last_request
            )

            if elapsed < minimum_interval:

                await asyncio.sleep(
                    minimum_interval -
                    elapsed
                )

            if self.delay > 0:

                await asyncio.sleep(
                    self.delay
                )

            self.last_request = (
                time.monotonic()
            )
