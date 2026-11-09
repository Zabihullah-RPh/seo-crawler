import unittest
from unittest.mock import AsyncMock, patch

import httpx

from app.crawler.http import HTTPClient
from app.crawler.robots import RobotsManager
from app.config import ROBOTS_TIMEOUT


class FakeResponse:
    def __init__(self, status_code, text=""):
        self.status_code = status_code
        self.headers = {"content-type": "text/plain"} if text else {}
        self.text = text
        self.content = text.encode("utf-8")
        self.encoding = "utf-8"
        self.closed = False

    async def aclose(self):
        self.closed = True


class FakeAsyncClient:
    def __init__(self, results):
        self.results = list(results)
        self.calls = []

    async def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        result = self.results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


class HTTPRetryPolicyTests(unittest.IsolatedAsyncioTestCase):
    def make_client(self, fake):
        client = HTTPClient.__new__(HTTPClient)
        import asyncio
        client.semaphore = asyncio.Semaphore(1)
        client.client = fake
        return client

    async def test_403_is_not_retried(self):
        fake = FakeAsyncClient([FakeResponse(403)])
        client = self.make_client(fake)

        result = await client.get("https://example.com/", retries=1)

        self.assertEqual(len(fake.calls), 1)
        self.assertEqual(result["response"].status_code, 403)
        self.assertEqual(result["attempts"], 1)

    async def test_404_is_not_retried(self):
        fake = FakeAsyncClient([FakeResponse(404)])
        client = self.make_client(fake)

        result = await client.get("https://example.com/missing", retries=1)

        self.assertEqual(len(fake.calls), 1)
        self.assertEqual(result["response"].status_code, 404)

    async def test_temporary_503_gets_only_one_retry(self):
        first = FakeResponse(503)
        second = FakeResponse(200)
        fake = FakeAsyncClient([first, second])
        client = self.make_client(fake)

        with patch("app.crawler.http.asyncio.sleep", new=AsyncMock()):
            result = await client.get("https://example.com/", retries=1)

        self.assertEqual(len(fake.calls), 2)
        self.assertTrue(first.closed)
        self.assertEqual(result["response"].status_code, 200)
        self.assertEqual(result["attempts"], 2)

    async def test_transport_error_gets_only_one_retry(self):
        fake = FakeAsyncClient([
            httpx.ConnectError("temporary connection failure"),
            FakeResponse(200),
        ])
        client = self.make_client(fake)

        with patch("app.crawler.http.asyncio.sleep", new=AsyncMock()):
            result = await client.get("https://example.com/", retries=1)

        self.assertEqual(len(fake.calls), 2)
        self.assertEqual(result["response"].status_code, 200)


class RobotsSingleAttemptTests(unittest.IsolatedAsyncioTestCase):
    class FakeHTTP:
        def __init__(self, result):
            self.result = result
            self.calls = []

        async def get(self, url, **kwargs):
            self.calls.append((url, kwargs))
            return self.result

    async def test_robots_uses_one_attempt_and_short_timeout(self):
        http = self.FakeHTTP({
            "response": None,
            "error": "ReadTimeout",
            "attempts": 1,
        })
        manager = RobotsManager("https://example.com/", "SEO-Crawler/1.0")

        result = await manager.load(http)

        self.assertEqual(len(http.calls), 1)
        self.assertEqual(http.calls[0][1]["retries"], 0)
        self.assertEqual(http.calls[0][1]["timeout"], ROBOTS_TIMEOUT)
        self.assertEqual(result["attempts"], 1)
        self.assertFalse(manager.allowed("https://example.com/"))
        self.assertIn("without retry", manager.blocked_reason)

    async def test_explicit_robots_disallow_is_not_retried(self):
        response = FakeResponse(200, "User-agent: *\nDisallow: /\n")
        http = self.FakeHTTP({"response": response, "attempts": 1})
        manager = RobotsManager("https://example.com/", "SEO-Crawler/1.0")

        await manager.load(http)

        self.assertEqual(len(http.calls), 1)
        self.assertFalse(manager.allowed("https://example.com/"))

    async def test_403_robots_blocks_without_retry(self):
        http = self.FakeHTTP({"response": FakeResponse(403), "attempts": 1})
        manager = RobotsManager("https://example.com/", "SEO-Crawler/1.0")

        await manager.load(http)

        self.assertEqual(len(http.calls), 1)
        self.assertFalse(manager.allowed("https://example.com/"))
        self.assertIn("HTTP 403", manager.blocked_reason)

    async def test_missing_robots_file_allows_crawl(self):
        http = self.FakeHTTP({"response": FakeResponse(404), "attempts": 1})
        manager = RobotsManager("https://example.com/", "SEO-Crawler/1.0")

        await manager.load(http)

        self.assertEqual(len(http.calls), 1)
        self.assertTrue(manager.allowed("https://example.com/"))


if __name__ == "__main__":
    unittest.main()
