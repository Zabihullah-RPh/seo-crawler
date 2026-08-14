import aiosqlite

from app.storage.db import DATABASE_PATH


async def analyze_redirects(
    crawl_id
):

    async with aiosqlite.connect(
        DATABASE_PATH
    ) as db:

        cursor = await db.execute(
            """
            SELECT
                url,
                final_url,
                status_code
            FROM pages
            WHERE crawl_id=?
            AND redirect=1
            """,
            (crawl_id,)
        )

        redirects = await cursor.fetchall()

        for url, final_url, status in redirects:

            if url == final_url:
                continue

            await db.execute(
                """
                INSERT INTO issues
                (
                    crawl_id,
                    url,
                    issue_type,
                    severity
                )
                VALUES (?, ?, ?, ?)
                """,
                (
                    crawl_id,
                    url,
                    "redirect_target",
                    "info"
                )
            )

        await db.commit()


async def analyze_indexability(
    crawl_id
):

    async with aiosqlite.connect(
        DATABASE_PATH
    ) as db:

        cursor = await db.execute(
            """
            SELECT
                url,
                status_code,
                robots,
                canonical
            FROM pages
            WHERE crawl_id=?
            """,
            (crawl_id,)
        )

        pages = await cursor.fetchall()

        for (
            url,
            status,
            robots,
            canonical
        ) in pages:

            robots = (
                robots or ""
            ).lower()

            if status != 200:

                continue

            if "noindex" in robots:

                await db.execute(
                    """
                    INSERT INTO issues
                    (
                        crawl_id,
                        url,
                        issue_type,
                        severity
                    )
                    VALUES (?, ?, ?, ?)
                    """,
                    (
                        crawl_id,
                        url,
                        "blocked_by_noindex",
                        "high"
                    )
                )

            if canonical and canonical != url:

                await db.execute(
                    """
                    INSERT INTO issues
                    (
                        crawl_id,
                        url,
                        issue_type,
                        severity
                    )
                    VALUES (?, ?, ?, ?)
                    """,
                    (
                        crawl_id,
                        url,
                        "non_self_canonical",
                        "medium"
                    )
                )

        await db.commit()
