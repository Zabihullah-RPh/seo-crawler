import sqlite3, json, os

db = "data/crawler.db"
crawl_id = 20
out = "results/crawl_20.json"

con = sqlite3.connect(db)
con.row_factory = sqlite3.Row

pages = []
for r in con.execute("""
    SELECT url, final_url, status_code, depth, content_type,
           response_time, content_length, title,
           meta_description, canonical, robots, language,
           viewport, word_count, is_indexable, redirect, error
    FROM pages
    WHERE crawl_id = ?
    ORDER BY id
""", (crawl_id,)):
    p = dict(r)
    p["description"] = p.get("meta_description") or ""
    p["h1s"] = []
    p["images"] = []
    p["images_count"] = 0
    p["schemas"] = []
    p["schema_count"] = 0
    pages.append(p)

failed_urls = [
    r[0] for r in con.execute(
        "SELECT url FROM failed WHERE crawl_id = ?", (crawl_id,)
    )
] if con.execute(
    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='failed'"
).fetchone() else []

data = {
    "crawl_id": crawl_id,
    "pages": pages,
    "links": [],
    "failed_urls": failed_urls
}

os.makedirs("results", exist_ok=True)

with open(out, "w", encoding="utf-8") as f:
    json.dump(data, f, indent=2, ensure_ascii=False)

con.close()

print(f"CREATED: {out}")
print(f"PAGES: {len(pages)}")

