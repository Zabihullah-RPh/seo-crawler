from pathlib import Path
import re
import subprocess

source = Path("results/complete_seo_report_22.html")
out = Path("results/complete_seo_report_22_modified.html")

html = source.read_text(encoding="utf-8")

css = """
<style>
.seo-score-red {
    color: #b91c1c !important;
    font-weight: 800 !important;
}
.seo-score-orange {
    color: #d97706 !important;
    font-weight: 800 !important;
}
.seo-score-green {
    color: #15803d !important;
    font-weight: 800 !important;
}
.pages-blue {
    color: #2563eb !important;
    font-weight: 800 !important;
}
.page-link {
    color: #2563eb !important;
    text-decoration: underline !important;
}
</style>
"""

if "</head>" in html.lower():
    pos = html.lower().find("</head>")
    html = html[:pos] + css + html[pos:]

def score_class(value):
    value = int(value)
    if value < 50:
        return "seo-score-red"
    elif value < 90:
        return "seo-score-orange"
    else:
        return "seo-score-green"

# SEO SCORE
html = re.sub(
    r'(?<![\w>])(\d{1,3})\s*/\s*100',
    lambda m: f'<span class="{score_class(m.group(1))}">{m.group(1)}/100</span>',
    html,
    count=1
)

# SITE HEALTH
remaining = html

matches = list(re.finditer(r'(?<![\w>])(\d{1,3})\s*/\s*100', remaining))

if matches:
    # First /100 is SEO score, second /100 is Site Health.
    if len(matches) >= 2:
        m = matches[1]
        original = m.group(0)
        value = m.group(1)
        replacement = f'<span class="{score_class(value)}">{value}/100</span>'
        html = html[:m.start()] + replacement + html[m.end():]

# PAGES = 5
html = re.sub(
    r'(?i)(>\s*Pages\s*</[^>]+>\s*)(\d+)(\s*</[^>]+>)',
    lambda m: m.group(1)
              + f'<span class="pages-blue">{m.group(2)}</span>'
              + m.group(3),
    html,
    count=1
)

# Make Weekly Pakistan page URLs clickable, avoiding URLs already inside href.
html = re.sub(
    r'(?<!["=])https://weeklypakistan\.com\.pk(?:/[A-Za-z0-9_./?=&%#:+~-]*)?',
    lambda m: (
        f'<a class="page-link" href="{m.group(0)}" target="_blank">'
        f'{m.group(0)}</a>'
    ),
    html
)

out.write_text(html, encoding="utf-8")

print("========================================")
print("MODIFIED REPORT CREATED")
print("========================================")
print(f"SEO Score: colored")
print(f"Site Health: colored")
print(f"Pages: blue")
print("Page links: clickable")
print(f"Output: {out.resolve()}")

subprocess.Popen(
    ["cmd", "/c", "start", "", str(out.resolve())]
)

print("Report opened.")
