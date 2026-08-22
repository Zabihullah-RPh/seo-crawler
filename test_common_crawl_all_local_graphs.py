from __future__ import annotations

import json
import sys

from app.integrations.common_crawl_multi_release import collect_all_local_releases


def main() -> None:
    target = sys.argv[1] if len(sys.argv) > 1 else "https://avw.au/"
    print(json.dumps(collect_all_local_releases(target), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
