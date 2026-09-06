from __future__ import annotations

import os
from typing import Any

from app.integrations.google_analytics import GA4Client
from app.integrations.google_auth import get_credentials
from app.integrations.google_pagespeed import PageSpeedClient
from app.integrations.google_search_console import SearchConsoleClient
from app.integrations.google_url_inspection import URLInspectionClient


def _result(status: str, **details: Any) -> dict[str, Any]:
    return {"status": status, **details}


def enrich(site_url: str) -> dict[str, Any]:
    """Best-effort Google enrichment for a public-site audit.

    Google Search Console and GA4 are private data sources. They are optional and
    never block the public crawl. PageSpeed is public and is attempted whenever
    possible.
    """
    result: dict[str, Any] = {}

    try:
        credentials = get_credentials()
    except Exception as exc:
        result["oauth"] = _result("DATA_NOT_AVAILABLE", reason=f"Google OAuth unavailable: {type(exc).__name__}: {exc}")
        result["search_console"] = _result("DATA_NOT_AVAILABLE", reason="Google OAuth unavailable.")
        result["sitemaps"] = _result("DATA_NOT_AVAILABLE", reason="Search Console access unavailable.")
        result["url_inspection"] = _result("DATA_NOT_AVAILABLE", reason="Search Console access unavailable.")
        result["search_analytics"] = _result("DATA_NOT_AVAILABLE", reason="Search Console access unavailable.")
        result["pagespeed"] = _run_pagespeed(site_url, None)
        result["ga4"] = _ga4_result(credentials=None)
        return result

    result["oauth"] = _result("PASS")
    token = getattr(credentials, "token", None)

    gsc = SearchConsoleClient(credentials=credentials)
    try:
        sites = gsc.list_sites()
        match = next((s for s in sites if s.get("siteUrl", "").rstrip("/") + "/" == site_url.rstrip("/") + "/"), None)
    except Exception as exc:
        match = None
        result["search_console"] = _result("ERROR", error=f"{type(exc).__name__}: {exc}")

    if match:
        result["search_console"] = _result("PASS", property=match)
        try:
            items = gsc.list_sitemaps(site_url)
            result["sitemaps"] = _result("PASS", count=len(items), items=items)
        except Exception as exc:
            result["sitemaps"] = _result("ERROR", error=f"{type(exc).__name__}: {exc}")
        try:
            inspection = URLInspectionClient(credentials=credentials).inspect(site_url, site_url)
            inspection_result = inspection.get("inspectionResult", {})
            index_status = inspection_result.get("indexStatusResult", {})
            result["url_inspection"] = _result(
                "PASS",
                verdict=inspection_result.get("verdict"),
                coverage_state=index_status.get("coverageState"),
                indexing_state=index_status.get("indexingState"),
            )
        except Exception as exc:
            result["url_inspection"] = _result("ERROR", error=f"{type(exc).__name__}: {exc}")
        try:
            rows = gsc.search_analytics(site_url=site_url, dimensions=["query"], row_limit=1000)
            result["search_analytics"] = _result("PASS", rows=rows)
        except Exception as exc:
            result["search_analytics"] = _result("ERROR", error=f"{type(exc).__name__}: {exc}")
    else:
        reason = "Site is not accessible in Search Console for the authenticated Google account."
        result.setdefault("search_console", _result("DATA_NOT_AVAILABLE", reason=reason))
        result["sitemaps"] = _result("DATA_NOT_AVAILABLE", reason="Requires Search Console property access.")
        result["url_inspection"] = _result("DATA_NOT_AVAILABLE", reason="Requires Search Console property access.")
        result["search_analytics"] = _result("DATA_NOT_AVAILABLE", reason="Requires Search Console property access.")

    result["pagespeed"] = _run_pagespeed(site_url, token)
    result["ga4"] = _ga4_result(credentials)
    return result


def _run_pagespeed(site_url: str, token: str | None) -> dict[str, Any]:
    try:
        data = PageSpeedClient().analyze(
            site_url,
            strategy="mobile",
            categories=["performance", "accessibility", "best-practices", "seo"],
            oauth_token=token,
        )
        categories = data.get("lighthouseResult", {}).get("categories", {})
        return _result(
            "PASS",
            performance=categories.get("performance", {}).get("score"),
            accessibility=categories.get("accessibility", {}).get("score"),
            best_practices=categories.get("best-practices", {}).get("score"),
            seo=categories.get("seo", {}).get("score"),
        )
    except Exception as exc:
        return _result("ERROR", error=f"{type(exc).__name__}: {exc}")


def _ga4_result(credentials) -> dict[str, Any]:
    property_id = os.getenv("GA4_PROPERTY_ID", "").strip()
    if not property_id:
        return _result("NOT_CONFIGURED", reason="GA4_PROPERTY_ID is not configured.")
    if credentials is None:
        return _result("DATA_NOT_AVAILABLE", reason="Google OAuth credentials are unavailable.")
    try:
        report = GA4Client(credentials=credentials).run_report(
            property_id=property_id,
            dimensions=["date"],
            metrics=["activeUsers", "sessions", "screenPageViews"],
            limit=30,
        )
        return _result("PASS", property_id=property_id, report=report)
    except Exception as exc:
        return _result("ERROR", property_id=property_id, error=f"{type(exc).__name__}: {exc}")
