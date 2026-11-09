from pathlib import Path
import os

BASE_DIR = Path(__file__).resolve().parent.parent

DATABASE_PATH = BASE_DIR / "data" / "crawler.db"
EXPORT_DIR = BASE_DIR / "exports"

DEFAULT_USER_AGENT = os.getenv(
    "CRAWLER_USER_AGENT",
    "SEO-Crawler/1.0"
)

# Keep normal page requests bounded; robots.txt gets a shorter, single attempt.
DEFAULT_TIMEOUT = float(os.getenv("CRAWLER_TIMEOUT", "15"))
ROBOTS_TIMEOUT = float(os.getenv("CRAWLER_ROBOTS_TIMEOUT", "8"))

DEFAULT_CONCURRENCY = int(
    os.getenv("CRAWLER_CONCURRENCY", "20")
)

DEFAULT_MAX_PAGES = int(
    os.getenv("CRAWLER_MAX_PAGES", "100000")
)

DEFAULT_MAX_DEPTH = int(
    os.getenv("CRAWLER_MAX_DEPTH", "50")
)

# Retries means additional attempts after the first. Default: 1 retry / 2 attempts total.
MAX_RETRIES = min(
    max(int(os.getenv("CRAWLER_MAX_RETRIES", "1")), 0),
    1,
)

MAX_REDIRECTS = int(
    os.getenv("CRAWLER_MAX_REDIRECTS", "10")
)
