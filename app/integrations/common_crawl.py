"""Common Crawl integration layer.

Phase 1 intentionally performs no crawl-data download and no WARC retrieval.
It only defines the connection/configuration surface needed by the backlink
module so later phases can query Common Crawl without changing the rest of
the application.
"""

from dataclasses import dataclass
from typing import Optional
from urllib.parse import quote


COMMON_CRAWL_INDEX_API = "https://index.commoncrawl.org"
COMMON_CRAWL_DATA_BASE = "https://data.commoncrawl.org"
COMMON_CRAWL_COLLECTIONS_API = f"{COMMON_CRAWL_INDEX_API}/collinfo.json"


@dataclass(frozen=True)
class CommonCrawlConfig:
    """Connection settings only; no network I/O is performed here."""

    index_api: str = COMMON_CRAWL_INDEX_API
    data_base: str = COMMON_CRAWL_DATA_BASE
    collections_api: str = COMMON_CRAWL_COLLECTIONS_API
    enabled: bool = True
    crawl: Optional[str] = None


class CommonCrawlClient:
    """Configuration-aware Common Crawl client.

    Phase 1 deliberately exposes URL builders only. Callers must explicitly
    invoke a future query/retrieval method before any Common Crawl data is
    downloaded.
    """

    def __init__(self, config: Optional[CommonCrawlConfig] = None):
        self.config = config or CommonCrawlConfig()

    def connection_info(self) -> dict:
        return {
            "provider": "Common Crawl",
            "enabled": self.config.enabled,
            "index_api": self.config.index_api,
            "collections_api": self.config.collections_api,
            "data_base": self.config.data_base,
            "crawl": self.config.crawl,
            "phase": "connection-only",
            "data_download_enabled": False,
        }

    def index_query_url(self, crawl: str, url: str) -> str:
        """Build a CDXJ index query URL without performing the request."""
        if not crawl:
            raise ValueError("crawl is required")
        if not url:
            raise ValueError("url is required")
        return (
            f"{self.config.index_api}/{quote(crawl, safe='')}-index"
            f"?url={quote(url, safe=':/?*&=')}&output=json"
        )

    def capture_url(self, filename: str, offset: int, length: int) -> str:
        """Build the future WARC data URL without downloading anything."""
        if not filename:
            raise ValueError("filename is required")
        if offset < 0 or length <= 0:
            raise ValueError("offset must be >= 0 and length must be > 0")
        return f"{self.config.data_base}/{filename}"
