from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

from app.integrations.google_analytics import GA4Client
from app.integrations.google_auth import get_credentials
from app.integrations.google_pagespeed import PageSpeedClient
from app.integrations.google_search_console import SearchConsoleClient
from app.integrations.google_url_inspection import URLInspectionClient

ROOT = Path(__file__).resolve().parents[1]
OUTPUT_DIR = ROOT / "results"


def run_audit(site_url: str) -> dict[str, Any]:
    site_url = site_url.rstrip("/") + "/"
    results: dict[str, Any] = {"site_url": site_url}

    creds = get_credentials()
    results["oauth"] = {"status": "PASS"}
    gsc = SearchConsoleClient(credentials=creds)

    # Search Console is optional for a site that has not been added to this
    # Google account. Other Google services can still be tested/run.
    sites = gsc.list_sites()
    matching = [
        s for s in sites
        if s.get("siteUrl", "").rstrip("/") + "/" == site_url
    ]
    property_info = matching[0] if matching else None

    if property_info:
        results["search_console"] = {"status": "PASS", "property": property_info}

        try:
            sitemaps = gsc.list_sitemaps(site_url)
            results["sitemaps"] = {
                "status": "PASS",
                "count": len(sitemaps),
                "items": sitemaps,
            }
        except Exception as exc:
            results["sitemaps"] = {"status": "FAIL", "error": str(exc)}

        try:
            inspection = URLInspectionClient(credentials=creds).inspect(site_url, site_url)
            inspection_result = inspection.get("inspectionResult", {})
            index_status = inspection_result.get("indexStatusResult", {})
            results["url_inspection"] = {
                "status": "PASS",
                "verdict": inspection_result.get("verdict"),
                "coverage_state": index_status.get("coverageState"),
                "indexing_state": index_status.get("indexingState"),
            }
        except Exception as exc:
            results["url_inspection"] = {"status": "FAIL", "error": str(exc)}

        try:
            search_rows = gsc.search_analytics(
                site_url=site_url,
                dimensions=["query"],
                row_limit=1000,
            )
            results["search_analytics"] = {
                "status": "PASS",
                "rows": search_rows,
            }
        except Exception as exc:
            results["search_analytics"] = {"status": "FAIL", "error": str(exc)}
    else:
        results["search_console"] = {
            "status": "SKIPPED",
            "reason": "Site is not available in Search Console for this Google account.",
        }
        results["sitemaps"] = {
            "status": "SKIPPED",
            "reason": "Requires a Search Console property for this account.",
        }
        results["url_inspection"] = {
            "status": "SKIPPED",
            "reason": "Requires a Search Console property for this account.",
        }
        results["search_analytics"] = {
            "status": "SKIPPED",
            "reason": "Requires a Search Console property for this account.",
        }

    # PageSpeed does not require the site to be a Search Console property.
    try:
        token = getattr(creds, "token", None)
        pagespeed = PageSpeedClient().analyze(
            site_url,
            strategy="mobile",
            categories=["performance", "accessibility", "best-practices", "seo"],
            oauth_token=token,
        )
        categories = pagespeed.get("lighthouseResult", {}).get("categories", {})
        results["pagespeed"] = {
            "status": "PASS",
            "performance": categories.get("performance", {}).get("score"),
            "accessibility": categories.get("accessibility", {}).get("score"),
            "best_practices": categories.get("best-practices", {}).get("score"),
            "seo": categories.get("seo", {}).get("score"),
        }
    except Exception as exc:
        results["pagespeed"] = {"status": "FAIL", "error": str(exc)}

    # GA4 is optional unless a property ID is configured.
    property_id = os.getenv("GA4_PROPERTY_ID")
    if property_id:
        try:
            report = GA4Client(credentials=creds).run_report(
                property_id=property_id,
                dimensions=["date"],
                metrics=["activeUsers", "sessions", "screenPageViews"],
                limit=30,
            )
            results["ga4"] = {
                "status": "PASS",
                "property_id": property_id,
                "report": report,
            }
        except Exception as exc:
            results["ga4"] = {"status": "FAIL", "error": str(exc)}
    else:
        results["ga4"] = {
            "status": "SKIPPED",
            "reason": "Set GA4_PROPERTY_ID to include GA4 data in the one-command audit.",
        }

    return results


def main() -> int:
    if len(sys.argv) != 2:
        print("Usage: python -m app.google_audit https://example.com")
        return 2

    site_url = sys.argv[1]
    try:
        results = run_audit(site_url)
    except Exception as exc:
        print(f"AUDIT FAILED: {type(exc).__name__}: {exc}")
        return 1

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    safe_name = site_url.replace("https://", "").replace("http://", "").rstrip("/")
    safe_name = "".join(c if c.isalnum() or c in ".-_" else "_" for c in safe_name)
    output = OUTPUT_DIR / f"google_audit_{safe_name}.json"
    output.write_text(json.dumps(results, indent=2), encoding="utf-8")

    statuses = {name: data.get("status") for name, data in results.items() if isinstance(data, dict) and "status" in data}
    print("GOOGLE SEO AUDIT")
    print(f"Site: {site_url}")
    print(f"Report: {output}")
    for name, status in statuses.items():
        print(f"{name}: {status}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
