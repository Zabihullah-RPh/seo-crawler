from pathlib import Path
import re
import subprocess

p = Path("results/complete_seo_report_22.html")
html = p.read_text(encoding="utf-8")

# Only modify the three metric values.
html = re.sub(
    r'(<div class="metric">\s*SEO Score\s*<strong>\s*)74/100(\s*</strong>)',
    r'\1<span class="score-orange">74/100</span>\2',
    html,
    count=1,
    flags=re.I
)

html = re.sub(
    r'(<div class="metric">\s*Site Health\s*<strong>\s*)93/100(\s*</strong>)',
    r'\1<span class="score-green">93/100</span>\2',
    html,
    count=1,
    flags=re.I
)

html = re.sub(
    r'(<div class="metric">\s*Pages\s*<strong>\s*)5(\s*</strong>)',
    r'\1<span class="pages-blue">5</span>\2',
    html,
    count=1,
    flags=re.I
)

p.write_text(html, encoding="utf-8")

print("SUCCESS")
print("SEO Score 74/100 -> ORANGE")
print("Site Health 93/100 -> GREEN")
print("Pages 5 -> BLUE")
print("Existing links and report structure untouched.")

subprocess.Popen(["cmd", "/c", "start", "", str(p.resolve())])
