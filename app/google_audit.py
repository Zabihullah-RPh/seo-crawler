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


def _result(status: str, **details: Any) -> dict[str, Any]:
    return {"status": status, **details}


def _error(exc: Exception) -> dict[str, Any]:
    return {"error": f"{type(exc).__name__}: {exc}"}


def run_audit(site_url: str) -> dict[str, Any]:
    site_url = site_url.rstrip("/") + "/"
    results: dict[str, Any] = {
        "site_url": site_url,
        "oauth": _result("PASS"),
    }

    try:
        creds = get_credentials()
    except Exception as exc:
        return {
            "site_url": site_url,
            "oauth": _result("ERROR", **_error(exc)),
            "search_console": _result("DATA_NOT_AVAILABLE", reason="Google OAuth authentication was not available."),
            "sitemaps": _result("DATA_NOT_AVAILABLE", reason="Google OAuth authentication was not available."),
            "url_inspection": _result("DATA_NOT_AVAILABLE", reason="Google OAuth authentication was not available."),
            "search_analytics": _result("DATA_NOT_AVAILABLE", reason="Google OAuth authentication was not available."),
            "pagespeed": _result("DATA_NOT_AVAILABLE", reason="Google OAuth authentication was not available."),
            "ga4": _result("NOT_CONFIGURED", reason="Google OAuth authentication was not available."),
        }

    gsc = SearchConsoleClient(credentials=creds)

    # Search Console data is only available when this account has access to the property.
    try:
        sites = gsc.list_sites()
        matching = [
            s for s in sites
            if s.get("siteUrl", "").rstrip("/") + "/" == site_url
        ]
    except Exception as exc:
        matching = []
        results["search_console"] = _result("ERROR", **_error(exc))

    property_info = matching[0] if matching else None

    if property_info and "search_console" not in results:
        results["search_console"] = _result("PASS", property=property_info)

        try:
            sitemaps = gsc.list_sitemaps(site_url)
            results["sitemaps"] = _result("PASS", count=len(sitemaps), items=sitemaps)
        except Exception as exc:
            results["sitemaps"] = _result("ERROR", **_error(exc))

        try:
            inspection = URLInspectionClient(credentials=creds).inspect(site_url, site_url)
            inspection_result = inspection.get("inspectionResult", {})
            index_status = inspection_result.get("indexStatusResult", {})
            results["url_inspection"] = _result(
                "PASS",
                verdict=inspection_result.get("verdict"),
                coverage_state=index_status.get("coverageState"),
                indexing_state=index_status.get("indexingState"),
            )
        except Exception as exc:
            results["url_inspection"] = _result("ERROR", **_error(exc))

        try:
            search_rows = gsc.search_analytics(
                site_url=site_url,
                dimensions=["query"],
                row_limit=1000,
            )
            results["search_analytics"] = _result("PASS", rows=search_rows)
        except Exception as exc:
            results["search_analytics"] = _result("ERROR", **_error(exc))
    else:
        reason = "Property is not accessible in Search Console for the authenticated Google account."
        results.setdefault("search_console", _result("DATA_NOT_AVAILABLE", reason=reason))
        results["sitemaps"] = _result(
            "DATA_NOT_AVAILABLE",
            reason="Requires accessible Search Console property data for this site.",
        )
        results["url_inspection"] = _result(
            "DATA_NOT_AVAILABLE",
            reason="Requires accessible Search Console property data for this site.",
        )
        results["search_analytics"] = _result(
            "DATA_NOT_AVAILABLE",
            reason="Requires accessible Search Console property data for this site.",
        )

    # PageSpeed is independent of Search Console property access.
    try:
        token = getattr(creds, "token", None)
        pagespeed = PageSpeedClient().analyze(
            site_url,
            strategy="mobile",
            categories=["performance", "accessibility", "best-practices", "seo"],
            oauth_token=token,
        )
        categories = pagespeed.get("lighthouseResult", {}).get("categories", {})
        results["pagespeed"] = _result(
            "PASS",
            performance=categories.get("performance", {}).get("score"),
            accessibility=categories.get("accessibility", {}).get("score"),
            best_practices=categories.get("best-practices", {}).get("score"),
            seo=categories.get("seo", {}).get("score"),
        )
    except Exception as exc:
        results["pagespeed"] = _result("ERROR", **_error(exc))

    # GA4 is opt-in via property ID because GA4 properties are account-specific,
    # not inferable from an arbitrary website URL.
    property_id = os.getenv("GA4_PROPERTY_ID")
    if property_id:
        try:
            report = GA4Client(credentials=creds).run_report(
                property_id=property_id,
                dimensions=["date"],
                metrics=["activeUsers", "sessions", "screenPageViews"],
                limit=30,
            )
            results["ga4"] = _result("PASS", property_id=property_id, report=report)
        except Exception as exc:
            results["ga4"] = _result("ERROR", **_error(exc), property_id=property_id)
    else:
        results["ga4"] = _result(
            "NOT_CONFIGURED",
            reason="No GA4 property ID was configured for this audit.",
        )

    return results


def main() -> int:
    if len(sys.argv) != 2:
        print("Usage: python -m app.google_audit https://example.com")
        return 2

    site_url = sys.argv[1]
    results = run_audit(site_url)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    safe_name = site_url.replace("https://", "").replace("http://", "").rstrip("/")
    safe_name = "".join(c if c.isalnum() or c in ".-_" else "_" for c in safe_name)
    output = OUTPUT_DIR / f"google_audit_{safe_name}.json"
    output.write_text(json.dumps(results, indent=2), encoding="utf-8")

    print("GOOGLE SEO AUDIT")
    print(f"Site: {site_url}")
    print(f"Report: {output}")
    for name, data in results.items():
        if isinstance(data, dict) and "status" in data:
            print(f"{name}: {data['status']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
