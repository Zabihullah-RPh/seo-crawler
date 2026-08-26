from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import requests

from app.integrations.google_analytics import GA4Client
from app.integrations.google_auth import get_credentials
from app.integrations.google_indexing import IndexingClient
from app.integrations.google_pagespeed import PageSpeedClient
from app.integrations.google_search_console import SearchConsoleClient
from app.integrations.google_url_inspection import URLInspectionClient

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "results" / "google_live_test.json"


def record(results: dict[str, Any], name: str, status: str, details: Any = None) -> None:
    results[name] = {"status": status, "details": details}
    print(f"[{status}] {name}")
    if details is not None:
        print(f"  {details}")


def error_detail(exc: Exception) -> str:
    response = getattr(exc, "response", None)
    if response is not None:
        try:
            body = response.json()
            return f"HTTP {response.status_code}: {body}"
        except Exception:
            return f"HTTP {response.status_code}: {exc}"
    return f"{type(exc).__name__}: {exc}"


def main() -> int:
    results: dict[str, Any] = {}

    try:
        creds = get_credentials()
        record(results, "OAuth", "PASS", "Google OAuth credentials are valid.")
    except Exception as exc:
        record(results, "OAuth", "FAIL", error_detail(exc))
        return finish(results)

    access_token = getattr(creds, "token", None)

    # 1. Search Console + properties
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
        record(results, "Search Console", "FAIL", error_detail(exc))
        return finish(results)

    site_url = first.get("siteUrl")
    if not site_url:
        record(results, "Search Console property", "FAIL", "First property has no siteUrl.")
        return finish(results)

    # 2. Sitemaps API
    try:
        sitemaps = gsc.list_sitemaps(site_url)
        record(results, "Sitemaps API", "PASS", {"property": site_url, "count": len(sitemaps)})
    except Exception as exc:
        record(results, "Sitemaps API", "FAIL", error_detail(exc))

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
        record(results, "URL Inspection API", "FAIL", error_detail(exc))

    # 4. PageSpeed Insights API. OAuth is supported; API key remains optional.
    try:
        data = PageSpeedClient().analyze(
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
        record(results, "PageSpeed Insights API", "FAIL", error_detail(exc))

    # 5. GA4 Data API. Prefer an explicitly supplied property ID so the test
    # does not require the Analytics Admin API merely to discover properties.
    property_id = os.getenv("GA4_PROPERTY_ID")
    property_name = os.getenv("GA4_PROPERTY_NAME")

    if property_id:
        try:
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
            record(results, "GA4 Data API", "FAIL", error_detail(exc))
    else:
        # Discovery is useful but requires the separate Google Analytics Admin API.
        try:
            headers = {"Authorization": f"Bearer {access_token}"}
            response = requests.get(
                "https://analyticsadmin.googleapis.com/v1beta/accountSummaries",
                headers=headers,
                params={"pageSize": 50},
                timeout=30,
            )
            response.raise_for_status()
            summaries = response.json().get("accountSummaries", [])
            for account in summaries:
                for prop in account.get("propertySummaries", []):
                    resource = prop.get("property", "")
                    if resource.startswith("properties/"):
                        property_id = resource.split("/", 1)[1]
                        property_name = prop.get("displayName")
                        break
                if property_id:
                    break

            if not property_id:
                raise RuntimeError("No accessible GA4 properties were returned by the Analytics Admin API.")

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
        except requests.HTTPError as exc:
            status_code = exc.response.status_code if exc.response is not None else None
            if status_code == 403:
                record(
                    results,
                    "GA4 Data API",
                    "CONFIG_REQUIRED",
                    "Analytics Admin discovery returned 403. Set GA4_PROPERTY_ID to a GA4 property the OAuth account can access, or enable the Google Analytics Admin API for automatic discovery.",
                )
            else:
                record(results, "GA4 Data API", "FAIL", error_detail(exc))
        except Exception as exc:
            record(results, "GA4 Data API", "CONFIG_REQUIRED", error_detail(exc))

    # 6. Indexing API. Service-account authentication works independently of OAuth.
    try:
        indexing = IndexingClient()
        service_account_email = indexing.service_account_email()
        metadata = indexing.metadata(site_url)
        record(
            results,
            "Indexing API",
            "PASS",
            {"service_account": service_account_email, "metadata": metadata},
        )
    except requests.HTTPError as exc:
        status_code = exc.response.status_code if exc.response is not None else None
        if status_code == 404:
            record(
                results,
                "Indexing API",
                "PASS_WITH_NOTE",
                "Service account authenticated, but Google returned 404 because this URL has no Indexing API notification metadata.",
            )
        elif status_code == 403:
            try:
                service_account_email = IndexingClient().service_account_email()
            except Exception:
                service_account_email = "unknown"
            record(
                results,
                "Indexing API",
                "CONFIG_REQUIRED",
                {
                    "message": "Service account authentication reached Google, but access to this Search Console property is forbidden.",
                    "service_account": service_account_email,
                    "required_action": "Add this exact service-account email as an owner of the intended Search Console property.",
                    "property_tested": site_url,
                },
            )
        else:
            record(results, "Indexing API", "FAIL", error_detail(exc))
    except Exception as exc:
        record(results, "Indexing API", "CONFIG_REQUIRED", error_detail(exc))

    return finish(results)


def finish(results: dict[str, Any]) -> int:
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(results, indent=2), encoding="utf-8")

    blockers = [
        name
        for name, result in results.items()
        if result["status"] in {"FAIL", "CONFIG_REQUIRED"}
    ]
    print("\n========================================")
    print("GOOGLE LIVE INTEGRATION TEST")
    print("========================================")
    print(f"Results: {OUTPUT}")
    print("Overall: PASS" if not blockers else "Overall: BLOCKED")
    if blockers:
        print("Remaining blockers:")
        for name in blockers:
            print(f"- {name}")
    return 1 if blockers else 0


if __name__ == "__main__":
    raise SystemExit(main())
