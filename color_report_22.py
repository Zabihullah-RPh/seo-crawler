from pathlib import Path
import re
import subprocess

p = Path("results/complete_seo_report_22.html")
html = p.read_text(encoding="utf-8")

# Add metric color CSS
css = """
<style>
.metric .score-red {
    color: #dc2626 !important;
}
.metric .score-orange {
    color: #d97706 !important;
}
.metric .score-green {
    color: #16a34a !important;
}
.metric .pages-blue {
    color: #2563eb !important;
}
</style>
"""

if "metric .score-orange" not in html:
    html = html.replace("</head>", css + "\n</head>", 1)

# SEO Score: 74/100 -> orange
html = re.sub(
    r'(<div class="metric">\s*SEO Score\s*<strong>)74/100(</strong>)',
    r'\1<span class="score-orange">74/100</span>\2',
    html,
    count=1,
    flags=re.I
)

# Site Health: 93/100 -> green
html = re.sub(
    r'(<div class="metric">\s*Site Health\s*<strong>)93/100(</strong>)',
    r'\1<span class="score-green">93/100</span>\2',
    html,
    count=1,
    flags=re.I
)

# Pages: 5 -> blue
html = re.sub(
    r'(<div class="metric">\s*Pages\s*<strong>)5(</strong>)',
    r'\1<span class="pages-blue">5</span>\2',
    html,
    count=1,
    flags=re.I
)

p.write_text(html, encoding="utf-8")

print("SUCCESS")
print("SEO Score 74/100 = ORANGE")
print("Site Health 93/100 = GREEN")
print("Pages 5 = BLUE")
print("Report updated:", p.resolve())

subprocess.Popen(
    ["cmd", "/c", "start", "", str(p.resolve())]
)
