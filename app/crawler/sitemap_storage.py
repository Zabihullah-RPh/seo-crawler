import aiosqlite

from app.storage.db import DATABASE_PATH


async def save_discovered_url(
    crawl_id,
    url,
    source_url,
    source_type
):

    async with aiosqlite.connect(
        DATABASE_PATH
    ) as db:

        await db.execute(
            """
            INSERT OR IGNORE INTO discovered_urls
            (
                crawl_id,
                url,
                source_url,
                source_type,
                depth
            )
            VALUES (?, ?, ?, ?, 0)
            """,
            (
                crawl_id,
                url,
                source_url,
                source_type
            )
        )

        await db.commit()


async def mark_sitemap(
    crawl_id,
    sitemap_url,
    status_code,
    url_count
):

    async with aiosqlite.connect(
        DATABASE_PATH
    ) as db:

        await db.execute(
            """
            INSERT INTO sitemaps
            (
                crawl_id,
                sitemap_url,
                status_code,
                url_count
            )
            VALUES (?, ?, ?, ?)
            """,
            (
                crawl_id,
                sitemap_url,
                status_code,
                url_count
            )
        )

        await db.commit()
