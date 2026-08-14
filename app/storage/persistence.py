from pathlib import Path
import json
import threading
from datetime import datetime

class CrawlPersistence:

    def __init__(self, crawl_id):
        self.crawl_id = int(crawl_id)

        self.base_dir = Path("data") / "crawl_results"
        self.base_dir.mkdir(parents=True, exist_ok=True)

        self.pages_file = self.base_dir / f"crawl_{self.crawl_id}_pages.jsonl"
        self.links_file = self.base_dir / f"crawl_{self.crawl_id}_links.jsonl"
        self.failed_file = self.base_dir / f"crawl_{self.crawl_id}_failed.jsonl"
        self.meta_file = self.base_dir / f"crawl_{self.crawl_id}_meta.json"

        self._lock = threading.Lock()

        if not self.meta_file.exists():
            self._write_meta({
                "crawl_id": self.crawl_id,
                "started_at": datetime.utcnow().isoformat(),
                "pages_saved": 0,
                "links_saved": 0,
                "failed_saved": 0,
                "status": "running"
            })

    def _append(self, path, data):
        with self._lock:
            with path.open("a", encoding="utf-8", newline="\n") as f:
                f.write(
                    json.dumps(
                        data,
                        ensure_ascii=False,
                        default=str
                    )
                    + "\n"
                )
                f.flush()

    def _write_meta(self, data):
        tmp = self.meta_file.with_suffix(".tmp")

        with self._lock:
            tmp.write_text(
                json.dumps(
                    data,
                    ensure_ascii=False,
                    indent=2,
                    default=str
                ),
                encoding="utf-8"
            )
            tmp.replace(self.meta_file)

    def save_page(self, page):
        self._append(self.pages_file, page)

    def save_link(self, link):
        self._append(self.links_file, link)

    def save_failed(self, failed):
        self._append(self.failed_file, failed)

    def update_meta(self, **kwargs):
        current = {}

        if self.meta_file.exists():
            try:
                current = json.loads(
                    self.meta_file.read_text(encoding="utf-8")
                )
            except Exception:
                current = {}

        current.update(kwargs)
        current["updated_at"] = datetime.utcnow().isoformat()

        self._write_meta(current)

    def finish(self, status="completed"):
        self.update_meta(
            status=status,
            finished_at=datetime.utcnow().isoformat()
        )

    def summary(self):
        def count(path):
            if not path.exists():
                return 0

            with path.open(
                "r",
                encoding="utf-8",
                errors="ignore"
            ) as f:
                return sum(1 for _ in f)

        return {
            "crawl_id": self.crawl_id,
            "pages": count(self.pages_file),
            "links": count(self.links_file),
            "failed": count(self.failed_file),
            "meta": str(self.meta_file)
        }
