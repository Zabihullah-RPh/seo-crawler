from pathlib import Path
import aiosqlite
from app.config import DATABASE_PATH


async def initialize():
    DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)

    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.executescript("""
        PRAGMA journal_mode=WAL;

        CREATE TABLE IF NOT EXISTS crawls (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            start_url TEXT NOT NULL,
            status TEXT NOT NULL,
            max_pages INTEGER DEFAULT 100000,
            max_depth INTEGER DEFAULT 50,
            concurrency INTEGER DEFAULT 20,
            pages_discovered INTEGER DEFAULT 0,
            pages_crawled INTEGER DEFAULT 0,
            pages_indexable INTEGER DEFAULT 0,
            started_at TEXT,
            finished_at TEXT
        );

        CREATE TABLE IF NOT EXISTS pages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            crawl_id INTEGER NOT NULL,
            url TEXT NOT NULL,
            final_url TEXT,
            status_code INTEGER,
            depth INTEGER,
            content_type TEXT,
            response_time REAL,
            content_length INTEGER,
            title TEXT,
            title_length INTEGER,
            meta_description TEXT,
            meta_description_length INTEGER,
            canonical TEXT,
            robots TEXT,
            language TEXT,
            viewport TEXT,
            word_count INTEGER,
            content_hash TEXT,
            is_indexable INTEGER DEFAULT 0,
            redirect INTEGER DEFAULT 0,
            error TEXT,
            pagerank REAL
        );

        CREATE TABLE IF NOT EXISTS links (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            crawl_id INTEGER,
            source_url TEXT,
            target_url TEXT,
            anchor_text TEXT,
            internal INTEGER DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS images (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            crawl_id INTEGER,
            page_url TEXT,
            image_url TEXT,
            alt TEXT
        );

        CREATE TABLE IF NOT EXISTS resources (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            crawl_id INTEGER,
            page_url TEXT,
            resource_url TEXT,
            resource_type TEXT
        );

        CREATE TABLE IF NOT EXISTS hreflang (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            crawl_id INTEGER,
            page_url TEXT,
            lang TEXT,
            url TEXT
        );

        CREATE TABLE IF NOT EXISTS schemas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            crawl_id INTEGER,
            page_url TEXT,
            schema_type TEXT,
            raw_json TEXT
        );

        CREATE TABLE IF NOT EXISTS issues (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            crawl_id INTEGER,
            url TEXT,
            issue_type TEXT,
            severity TEXT,
            message TEXT,
            details TEXT
        );

        CREATE TABLE IF NOT EXISTS discovered_urls (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            crawl_id INTEGER,
            url TEXT
        );

        CREATE TABLE IF NOT EXISTS sitemaps (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            crawl_id INTEGER,
            sitemap_url TEXT,
            url TEXT
        );
        """)
        await db.commit()


async def create_crawl(
    start_url,
    max_pages=100000,
    max_depth=50,
    concurrency=20
):
    async with aiosqlite.connect(DATABASE_PATH) as db:
        cursor = await db.execute("""
            INSERT INTO crawls
            (start_url, status, max_pages, max_depth, concurrency,
             pages_discovered, pages_crawled, pages_indexable, started_at)
            VALUES (?, 'queued', ?, ?, ?, 0, 0, 0, CURRENT_TIMESTAMP)
        """, (start_url, max_pages, max_depth, concurrency))

        await db.commit()
        return cursor.lastrowid


async def update_crawl(crawl_id, **fields):
    if not fields:
        return

    allowed = {
        "status",
        "pages_discovered",
        "pages_crawled",
        "pages_indexable",
        "started_at",
        "finished_at"
    }

    fields = {k: v for k, v in fields.items() if k in allowed}

    if not fields:
        return

    assignments = ", ".join(f"{k}=?" for k in fields)
    values = list(fields.values())
    values.append(crawl_id)

    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.execute(
            f"UPDATE crawls SET {assignments} WHERE id=?",
            values
        )
        await db.commit()


async def save_page(crawl_id, page):
    # Normalize URL-like objects before SQLite binding.
    def db_value(value):
        if value is None:
            return None

        if isinstance(value, (str, int, float, bytes)):
            return value

        return str(value)

    columns = [
        "crawl_id", "url", "final_url", "status_code", "depth",
        "content_type", "response_time", "content_length", "title",
        "title_length", "meta_description", "meta_description_length",
        "canonical", "robots", "language", "viewport", "word_count",
        "content_hash", "is_indexable", "redirect", "error", "pagerank"
    ]

    values = [
        crawl_id,
        db_value(page.get("url")),
        db_value(page.get("final_url")),
        page.get("status_code"),
        page.get("depth", 0),
        page.get("content_type"),
        page.get("response_time"),
        page.get("content_length"),
        page.get("title"),
        page.get("title_length"),
        page.get("meta_description"),
        page.get("meta_description_length"),
        db_value(page.get("canonical")),
        db_value(page.get("robots")),
        db_value(page.get("language")),
        db_value(page.get("viewport")),
        page.get("word_count"),
        page.get("content_hash"),
        int(bool(page.get("is_indexable", False))),
        int(bool(page.get("redirect", False))),
        page.get("error"),
        page.get("pagerank")
    ]

    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.execute(
            f"""
            INSERT INTO pages ({",".join(columns)})
            VALUES ({",".join(["?"] * len(columns))})
            """,
            values
        )
        await db.commit()


async def save_issue(crawl_id, issue):
    if isinstance(issue, dict):
        url = issue.get("url")
        issue_type = issue.get("type") or issue.get("issue_type")
        severity = issue.get("severity")
        message = issue.get("message")
        details = str(issue)
    else:
        url = None
        issue_type = str(issue)
        severity = None
        message = str(issue)
        details = None

    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.execute("""
            INSERT INTO issues
            (crawl_id, url, issue_type, severity, message, details)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            crawl_id,
            url,
            issue_type,
            severity,
            message,
            details
        ))
        await db.commit()


async def save_image(crawl_id, image):
    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.execute("""
            INSERT INTO images
            (crawl_id, page_url, image_url, alt)
            VALUES (?, ?, ?, ?)
        """, (
            crawl_id,
            image.get("page_url"),
            image.get("url"),
            image.get("alt")
        ))
        await db.commit()


async def save_resource(crawl_id, page_url, resource):
    if isinstance(resource, dict):
        resource_url = resource.get("url")
        resource_type = resource.get("type")
    else:
        resource_url = str(resource)
        resource_type = None

    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.execute("""
            INSERT INTO resources
            (crawl_id, page_url, resource_url, resource_type)
            VALUES (?, ?, ?, ?)
        """, (
            crawl_id,
            page_url,
            resource_url,
            resource_type
        ))
        await db.commit()


async def save_hreflang(crawl_id, item):
    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.execute("""
            INSERT INTO hreflang
            (crawl_id, page_url, lang, url)
            VALUES (?, ?, ?, ?)
        """, (
            crawl_id,
            item.get("page_url"),
            item.get("lang"),
            item.get("url")
        ))
        await db.commit()


async def save_schema(crawl_id, item):
    if isinstance(item, dict):
        page_url = item.get("page_url")
        schema_type = item.get("type")
        raw_json = item.get("raw") or item.get("json")
    else:
        page_url = None
        schema_type = None
        raw_json = str(item)

    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.execute("""
            INSERT INTO schemas
            (crawl_id, page_url, schema_type, raw_json)
            VALUES (?, ?, ?, ?)
        """, (
            crawl_id,
            page_url,
            schema_type,
            str(raw_json) if raw_json is not None else None
        ))
        await db.commit()


async def save_link(crawl_id, link):
    if isinstance(link, dict):
        source_url = link.get("source_url")
        target_url = link.get("url") or link.get("target_url")
        anchor_text = link.get("anchor_text")
        internal = int(bool(link.get("internal", False)))
    else:
        source_url = None
        target_url = str(link)
        anchor_text = None
        internal = 0

    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.execute("""
            INSERT INTO links
            (crawl_id, source_url, target_url, anchor_text, internal)
            VALUES (?, ?, ?, ?, ?)
        """, (
            crawl_id,
            source_url,
            target_url,
            anchor_text,
            internal
        ))
        await db.commit()


async def save_discovered_url(crawl_id, url):
    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.execute("""
            INSERT INTO discovered_urls (crawl_id, url)
            VALUES (?, ?)
        """, (crawl_id, url))
        await db.commit()


async def save_sitemap(crawl_id, sitemap_url, url=None):
    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.execute("""
            INSERT INTO sitemaps
            (crawl_id, sitemap_url, url)
            VALUES (?, ?, ?)
        """, (crawl_id, sitemap_url, url))
        await db.commit()
