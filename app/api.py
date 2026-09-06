import asyncio

# Playwright launches a subprocess for its browser driver. On Windows, Python 3.14
# can be started with a selector-based event loop by the surrounding server/runtime,
# but selector loops do not support subprocesses. Force the Proactor loop for this
# application so the existing Python 3.14 environment remains supported.
if hasattr(asyncio, "WindowsProactorEventLoopPolicy"):
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

from fastapi import FastAPI
from pydantic import BaseModel, Field
import aiosqlite

from app.storage.db import initialize, create_crawl, DATABASE_PATH
from app.crawler.production import ProductionCrawler
from app.integrations.google_routes import router as google_router

app = FastAPI(
    title="SEO Crawler",
    version="4.0"
)

app.include_router(google_router)

class CrawlRequest(BaseModel):
    url: str
    max_pages: int = Field(default=100000, ge=1, le=5000000)
    max_depth: int = Field(default=50, ge=1, le=500)
    concurrency: int = Field(default=20, ge=1, le=200)
    requests_per_second: int = Field(default=10, ge=1, le=1000)

async def run_job(crawl_id, request):

    try:
        crawler = ProductionCrawler(
            crawl_id,
            request.url,
            request.max_pages,
            request.max_depth,
            request.concurrency
        )

        await crawler.run()

    except Exception as e:

        print(f"CRAWL {crawl_id} FAILED: {type(e).__name__}: {e}")

        async with aiosqlite.connect(DATABASE_PATH) as db:
            await db.execute(
                """
                UPDATE crawls
                SET status='failed'
                WHERE id=?
                """,
                (crawl_id,)
            )
            await db.commit()

@app.on_event("startup")
async def startup():
    await initialize()

@app.get("/")
async def root():
    return {
        "service": "SEO Crawler",
        "version": "4.0",
        "status": "online"
    }

@app.post("/crawl")
async def crawl(request: CrawlRequest):

    crawl_id = await create_crawl(
        request.url,
        request.max_pages,
        request.max_depth,
        request.concurrency
    )

    asyncio.create_task(
        run_job(crawl_id, request)
    )

    return {
        "crawl_id": crawl_id,
        "status": "started"
    }

@app.get("/crawl/{crawl_id}")
async def status(crawl_id: int):

    async with aiosqlite.connect(DATABASE_PATH) as db:

        db.row_factory = aiosqlite.Row

        cursor = await db.execute(
            """
            SELECT *
            FROM crawls
            WHERE id=?
            """,
            (crawl_id,)
        )

        row = await cursor.fetchone()

    return dict(row) if row else {
        "error": "crawl_not_found"
    }
