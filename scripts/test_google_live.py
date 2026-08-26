from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import requests

from app.integrations.google_auth import get_credentials
from app.integrations.google_indexing import IndexingClient
from app.integrations.google_pagespeed import PageSpeedClient
from app.integrations.google_search_console import SearchConsoleClient
from app.integrations.google_url_inspection import URLInspectionClient
from app.integrations.google_analytics import GA4Client

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "results" / "google_live_test.json"


def record(results: dict[str, Any], name: str, status: str, details: Any = None) -> None:
    results[name] = {"status": status, "details": details}
    print(f"[{status}] {name}")
    if details is not None:
        print(f"  {details}")


def main() -> int:
    results: dict[str, Any] = {}
    creds = None

    try:
        creds = get_credentials()
        record(results, "OAuth", "PASS", "Google OAuth credentials are valid.")
    except Exception as exc:
        record(results, "OAuth", "FAIL", f"{type(exc).__name__}: {exc}")
        return finish(results)

    access_token = getattr(creds, "token", None)

    # 1. Search Console + properties
    sites: list[dict[str, Any]] = []
    try:
        gsc = SearchConsoleClient(credentials=creds)
        sites = gsc.list_sites()
        if not sites:
            record(results, "Search Console", "FAIL", "No accessible Search Console properties returned.")
            return finish(results)
        first = sites[0]
        record(
            results,
            "Search Console",
            "PASS",
            {"site_url": first.get("siteUrl"), "permission": first.get("permissionLevel")},
        )
    except Exception as exc:
        record(results, "Search Console", "FAIL", f"{type(exc).__name__}: {exc}")
        return finish(results)

    site_url = sites[0].get("siteUrl")
    if not site_url:
        record(results, "Search Console property", "FAIL", "First property has no siteUrl.")
        return finish(results)

    # 2. Sitemaps API
    try:
        sitemaps = gsc.list_sitemaps(site_url)
        record(results, "Sitemaps API", "PASS", {"property": site_url, "count": len(sitemaps)})
    except Exception as exc:
        record(results, "Sitemaps API", "FAIL", f"{type(exc).__name__}: {exc}")

    # 3. URL Inspection API
    try:
        inspection = URLInspectionClient(credentials=creds).inspect(site_url, site_url)
        inspection_result = inspection.get("inspectionResult", {})
        index_status = inspection_result.get("indexStatusResult", {})
        record(
            results,
            "URL Inspection API",
            "PASS",
            {
                "inspection_url": site_url,
                "verdict": inspection_result.get("verdict"),
                "coverage_state": index_status.get("coverageState"),
                "indexing_state": index_status.get("indexingState"),
            },
        )
    except Exception as exc:
        record(results, "URL Inspection API", "FAIL", f"{type(exc).__name__}: {exc}")

    # 4. PageSpeed Insights API. OAuth token is supported by the API; API key is optional.
    try:
        pagespeed = PageSpeedClient()
        data = pagespeed.analyze(
            site_url,
            strategy="mobile",
            categories=["performance", "seo"],
            oauth_token=access_token,
        )
        categories = data.get("lighthouseResult", {}).get("categories", {})
        record(
            results,
            "PageSpeed Insights API",
            "PASS",
            {
                "url": site_url,
                "performance": categories.get("performance", {}).get("score"),
                "seo": categories.get("seo", {}).get("score"),
            },
        )
    except Exception as exc:
        record(results, "PageSpeed Insights API", "FAIL", f"{type(exc).__name__}: {exc}")

    # 5. GA4 Data API. Discover an accessible property through the Admin API using analytics.readonly.
    try:
        headers = {"Authorization": f"Bearer {access_token}"}
        response = requests.get(
            "https://analyticsadmin.googleapis.com/v1beta/accountSummaries",
            headers=headers,
            params={"pageSize": 50},
            timeout=30,
        )
        response.raise_for_status()
        account_summaries = response.json().get("accountSummaries", [])

        property_id = None
        property_name = None
        for account in account_summaries:
            for prop in account.get("propertySummaries", []):
                resource = prop.get("property", "")
                if resource.startswith("properties/"):
                    property_id = resource.split("/", 1)[1]
                    property_name = prop.get("displayName")
                    break
            if property_id:
                break

        if not property_id:
            raise RuntimeError("OAuth account has no accessible GA4 properties.")

        report = GA4Client(credentials=creds).run_report(
            property_id=property_id,
            dimensions=["date"],
            metrics=["activeUsers"],
            limit=7,
        )
        record(
            results,
            "GA4 Data API",
            "PASS",
            {
                "property_id": property_id,
                "property_name": property_name,
                "row_count": report.get("row_count"),
            },
        )
    except Exception as exc:
        record(results, "GA4 Data API", "FAIL", f"{type(exc).__name__}: {exc}")

    # 6. Indexing API. Metadata is read-only and does not submit a URL notification.
    try:
        indexing = IndexingClient()
        metadata = indexing.metadata(site_url)
        record(results, "Indexing API", "PASS", metadata)
    except requests.HTTPError as exc:
        status_code = exc.response.status_code if exc.response is not None else None
        if status_code == 404:
            record(
                results,
                "Indexing API",
                "PASS_WITH_NOTE",
                "Authenticated, but Google returned 404 because no notification metadata exists for this URL.",
            )
        else:
            record(results, "Indexing API", "FAIL", f"HTTP {status_code}: {exc}")
    except Exception as exc:
        record(results, "Indexing API", "FAIL", f"{type(exc).__name__}: {exc}")

    return finish(results)


def finish(results: dict[str, Any]) -> int:
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(results, indent=2), encoding="utf-8")

    failures = [name for name, result in results.items() if result["status"] == "FAIL"]
    print("\n========================================")
    print("GOOGLE LIVE INTEGRATION TEST")
    print("========================================")
    print(f"Results: {OUTPUT}")
    print("Overall: FAIL" if failures else "Overall: PASS")
    if failures:
        print("Failures:")
        for name in failures:
            print(f"- {name}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
