from pathlib import Path

p = Path("app/crawler/production.py")
lines = p.read_text(encoding="utf-8").splitlines()

# Find the completed finish() call.
target = None

for i in range(len(lines) - 2):
    if lines[i].strip() == 'self.persistence.finish(':
        if lines[i + 1].strip() == '"completed"':
            target = i
            break

if target is None:
    print("COMPLETED FINISH BLOCK NOT FOUND - NO CHANGES MADE.")
    raise SystemExit(1)

# Find the closing ')' of this finish() call.
close_line = target + 1

while close_line < len(lines):
    if lines[close_line].strip() == ")":
        break
    close_line += 1

if close_line >= len(lines):
    print("FINISH BLOCK CLOSING PARENTHESIS NOT FOUND.")
    raise SystemExit(1)

# Prevent duplicate insertion.
if any("Automatically export crawl data to JSON" in x for x in lines):
    print("JSON EXPORT ALREADY EXISTS - NO CHANGES MADE.")
    raise SystemExit(0)

indent = lines[target][:len(lines[target]) - len(lines[target].lstrip())]

block = [
    "",
    indent + "# Automatically export crawl data to JSON",
    indent + "try:",
    indent + "    import sqlite3",
    indent + "    import json",
    indent + "    import os",
    "",
    indent + "    os.makedirs('results', exist_ok=True)",
    indent + "    db = 'data/crawler.db'",
    indent + "    out = f'results/crawl_{self.crawl_id}.json'",
    "",
    indent + "    con = sqlite3.connect(db)",
    indent + "    con.row_factory = sqlite3.Row",
    "",
    indent + "    pages = [",
    indent + "        dict(r)",
    indent + "        for r in con.execute(",
    indent + "            '''",
    indent + "            SELECT url, final_url, status_code, depth,",
    indent + "                   content_type, response_time, content_length,",
    indent + "                   title, meta_description, canonical, robots,",
    indent + "                   language, viewport, word_count, is_indexable,",
    indent + "                   redirect, error",
    indent + "            FROM pages",
    indent + "            WHERE crawl_id = ?",
    indent + "            ORDER BY id",
    indent + "            ''',",
    indent + "            (self.crawl_id,)",
    indent + "        )",
    indent + "    ]",
    "",
    indent + "    failed_urls = []",
    indent + "    table_exists = con.execute(",
    indent + "        \"SELECT 1 FROM sqlite_master WHERE type='table' AND name='failed'\"",
    indent + "    ).fetchone()",
    "",
    indent + "    if table_exists:",
    indent + "        failed_urls = [",
    indent + "            r[0]",
    indent + "            for r in con.execute(",
    indent + "                'SELECT url FROM failed WHERE crawl_id = ?',",
    indent + "                (self.crawl_id,)",
    indent + "            )",
    indent + "        ]",
    "",
    indent + "    data = {",
    indent + "        'crawl_id': self.crawl_id,",
    indent + "        'start_url': self.start_url,",
    indent + "        'pages': pages,",
    indent + "        'links': [],",
    indent + "        'failed_urls': failed_urls",
    indent + "    }",
    "",
    indent + "    Path(out).write_text(",
    indent + "        json.dumps(data, indent=2, ensure_ascii=False),",
    indent + "        encoding='utf-8'",
    indent + "    )",
    "",
    indent + "    con.close()",
    indent + "    print(f'[JSON] Saved: {out}')",
    "",
    indent + "except Exception as json_error:",
    indent + "    print(",
    indent + "        f'[JSON ERROR] {type(json_error).__name__}: {json_error}'",
    indent + "    )",
]

lines[close_line + 1:close_line + 1] = block

p.write_text("\n".join(lines) + "\n", encoding="utf-8")

print("SUCCESS: automatic JSON export added.")
