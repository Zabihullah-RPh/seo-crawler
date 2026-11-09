import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.storage import db
from app.storage.persistence import CrawlPersistence


class EmailDatabasePersistenceTests(unittest.IsolatedAsyncioTestCase):
    async def test_email_is_saved_once_with_source_and_extraction_method(self):
        with tempfile.TemporaryDirectory() as directory:
            original = db.DATABASE_PATH
            db.DATABASE_PATH = Path(directory) / "crawler.db"
            try:
                await db.initialize()
                item = {
                    "email": "sales@example.com",
                    "email_domain": "example.com",
                    "source_url": "https://example.com/contact/",
                    "same_domain": True,
                    "generic_mailbox": True,
                    "extraction_methods": ["mailto", "visible_text"],
                }
                await db.save_email(17, item)
                await db.save_email(17, item)

                with sqlite3.connect(db.DATABASE_PATH) as connection:
                    rows = connection.execute(
                        "SELECT crawl_id, page_url, email, email_domain, "
                        "same_domain, generic_mailbox, extraction_method FROM emails"
                    ).fetchall()

                self.assertEqual(len(rows), 1)
                self.assertEqual(rows[0][0], 17)
                self.assertEqual(rows[0][1], item["source_url"])
                self.assertEqual(rows[0][2], item["email"])
                self.assertEqual(rows[0][3], "example.com")
                self.assertEqual(rows[0][4], 1)
                self.assertEqual(rows[0][5], 1)
                self.assertEqual(rows[0][6], "mailto,visible_text")
            finally:
                db.DATABASE_PATH = original


class EmailJSONLPersistenceTests(unittest.TestCase):
    def test_email_source_record_is_written_to_crawl_jsonl(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch("pathlib.Path.cwd", return_value=Path(directory)):
                persistence = CrawlPersistence(123)
                persistence.save_email({
                    "email": "careers@example.com",
                    "source_url": "https://example.com/jobs/",
                    "extraction_methods": ["visible_text"],
                })
                saved = json.loads(persistence.emails_file.read_text(encoding="utf-8"))
                self.assertEqual(saved["email"], "careers@example.com")
                self.assertEqual(saved["source_url"], "https://example.com/jobs/")
                self.assertEqual(persistence.summary()["emails"], 1)


if __name__ == "__main__":
    unittest.main()
