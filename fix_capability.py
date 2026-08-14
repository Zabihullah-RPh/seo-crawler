from pathlib import Path

p = Path("test_capability.py")
s = p.read_text(encoding="utf-8")

s = s.replace(
'''        platform = detect_platform(
            html + "\\n" + generator,
            dict(response.headers) if response else {},
        )''',
'''        platform = detect_platform(
            html,
            dict(response.headers) if response else {},
        )'''
)

s = s.replace(
'''            "technology": {
                "platform": platform,
                "generator": generator,
                "tracking": tracking,
            },''',
'''            "technology": {
                "platform": platform,
                "tracking": tracking,
            },'''
)

p.write_text(s, encoding="utf-8")

print("SUCCESS: broken generator reference removed.")
