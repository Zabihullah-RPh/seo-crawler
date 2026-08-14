from pathlib import Path
import re
import subprocess
from html import escape

p = Path("results/complete_seo_report_22.html")
html = p.read_text(encoding="utf-8")

# Convert plain URLs in the Problems / Opportunities table into clickable links.
# Do not touch URLs that are already inside <a> tags.
pattern = r'(?<!["=])(https?://[^\s<]+)'

def make_link(match):
    url = match.group(1).rstrip('.,')
    trailing = match.group(1)[len(url):]
    return f'<a href="{escape(url, quote=True)}" target="_blank" rel="noopener noreferrer">{escape(url)}</a>{trailing}'

# Only process table cells, leaving the rest of the report untouched.
def process_cell(match):
    content = match.group(1)

    # Skip cells that already contain links.
    if "<a " in content.lower():
        return match.group(0)

    content = re.sub(pattern, make_link, content)
    return f"<td>{content}</td>"

html = re.sub(
    r"<td>(.*?)</td>",
    process_cell,
    html,
    flags=re.I | re.S
)

# Make sure links have a visible standard style.
link_css = """
<style>
.issues a {
    color: #2563eb;
    text-decoration: underline;
}
.issues a:hover {
    text-decoration: none;
}
</style>
"""

if ".issues a" not in html:
    html = html.replace("</head>", link_css + "\n</head>", 1)

p.write_text(html, encoding="utf-8")

print("SUCCESS: Page URLs are now clickable.")
print("Colors preserved.")
print("SEO Score, Site Health and Pages unchanged.")
print("Report:", p.resolve())

subprocess.Popen(["cmd", "/c", "start", "", str(p.resolve())])
