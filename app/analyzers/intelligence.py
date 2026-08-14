import aiosqlite
from collections import defaultdict

from app.storage.db import DATABASE_PATH


async def detect_orphans(crawl_id):

    async with aiosqlite.connect(
        DATABASE_PATH
    ) as db:

        cursor=await db.execute("""
            SELECT url
            FROM pages
            WHERE crawl_id=?
        """,(crawl_id,))

        pages=await cursor.fetchall()

        cursor=await db.execute("""
            SELECT DISTINCT destination_url
            FROM links
            WHERE crawl_id=?
            AND internal=1
        """,(crawl_id,))

        linked={
            row[0]
            for row in await cursor.fetchall()
        }

        for row in pages:

            url=row[0]

            if url not in linked:

                await db.execute("""
                    INSERT INTO issues
                    (crawl_id,url,issue_type,severity)
                    VALUES (?,?,?,?)
                """,(
                    crawl_id,
                    url,
                    "orphan_page",
                    "high"
                ))

        await db.commit()


async def detect_duplicates(crawl_id):

    async with aiosqlite.connect(
        DATABASE_PATH
    ) as db:

        cursor=await db.execute("""
            SELECT content_hash,
                   COUNT(*)
            FROM pages
            WHERE crawl_id=?
            AND content_hash!=''
            GROUP BY content_hash
            HAVING COUNT(*)>1
        """,(crawl_id,))

        groups=await cursor.fetchall()

        for content_hash,count in groups:

            cursor=await db.execute("""
                SELECT url
                FROM pages
                WHERE crawl_id=?
                AND content_hash=?
            """,(
                crawl_id,
                content_hash
            ))

            urls=await cursor.fetchall()

            for row in urls:

                await db.execute("""
                    INSERT INTO issues
                    (crawl_id,url,issue_type,severity)
                    VALUES (?,?,?,?)
                """,(
                    crawl_id,
                    row[0],
                    "duplicate_content",
                    "high"
                ))

        cursor=await db.execute("""
            SELECT title,COUNT(*)
            FROM pages
            WHERE crawl_id=?
            AND title!=''
            GROUP BY title
            HAVING COUNT(*)>1
        """,(crawl_id,))

        groups=await cursor.fetchall()

        for title,count in groups:

            cursor=await db.execute("""
                SELECT url
                FROM pages
                WHERE crawl_id=?
                AND title=?
            """,(crawl_id,title))

            for row in await cursor.fetchall():

                await db.execute("""
                    INSERT INTO issues
                    (crawl_id,url,issue_type,severity)
                    VALUES (?,?,?,?)
                """,(
                    crawl_id,
                    row[0],
                    "duplicate_title",
                    "medium"
                ))

        await db.commit()


async def calculate_pagerank(crawl_id):

    async with aiosqlite.connect(
        DATABASE_PATH
    ) as db:

        cursor=await db.execute("""
            SELECT url
            FROM pages
            WHERE crawl_id=?
        """,(crawl_id,))

        urls=[
            row[0]
            for row in await cursor.fetchall()
        ]

        if not urls:
            return

        nodes=set(urls)

        incoming=defaultdict(set)

        cursor=await db.execute("""
            SELECT source_url,destination_url
            FROM links
            WHERE crawl_id=?
            AND internal=1
        """,(crawl_id,))

        for source,target in await cursor.fetchall():

            if target in nodes:
                incoming[target].add(
                    source
                )

        scores={
            url:1/len(nodes)
            for url in nodes
        }

        for _ in range(20):

            new={}

            for url in nodes:

                value=0.15/len(nodes)

                for source in incoming[url]:

                    outgoing_count=0

                    cursor=await db.execute("""
                        SELECT COUNT(*)
                        FROM links
                        WHERE crawl_id=?
                        AND source_url=?
                        AND internal=1
                    """,(crawl_id,source))

                    row=await cursor.fetchone()

                    if row:
                        outgoing_count=row[0]

                    if outgoing_count:

                        value+=(
                            0.85
                            * scores[source]
                            / outgoing_count
                        )

                new[url]=value

            scores=new

        try:

            await db.execute("""
                ALTER TABLE pages
                ADD COLUMN pagerank REAL
            """)

        except Exception:
            pass

        for url,score in scores.items():

            await db.execute("""
                UPDATE pages
                SET pagerank=?
                WHERE crawl_id=?
                AND url=?
            """,(
                score,
                crawl_id,
                url
            ))

        await db.commit()
