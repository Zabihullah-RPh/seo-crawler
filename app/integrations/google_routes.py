from __future__ import annotations

from datetime import date
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.integrations.google_analytics import GA4Client
from app.integrations.google_auth import get_credentials
from app.integrations.google_pagespeed import PageSpeedClient
from app.integrations.google_search_console import SearchConsoleClient
from app.integrations.google_url_inspection import URLInspectionClient

router = APIRouter(prefix="/api/google", tags=["Google APIs"])


class SearchAnalyticsRequest(BaseModel):
    site_url: str
    start_date: str | None = None
    end_date: str | None = None
    dimensions: list[str] = Field(default_factory=list)
    row_limit: int = Field(default=25000, ge=1, le=25000)


class URLInspectionRequest(BaseModel):
    inspection_url: str
    site_url: str


class PageSpeedRequest(BaseModel):
    url: str
    strategy: str = Field(default="mobile", pattern="^(mobile|desktop)$")
    categories: list[str] = Field(default_factory=lambda: ["performance", "accessibility", "best-practices", "seo"])


class GA4ReportRequest(BaseModel):
    property_id: str
    dimensions: list[str] = Field(default_factory=list)
    metrics: list[str]
    start_date: str | None = None
    end_date: str | None = None
    limit: int = Field(default=10000, ge=1, le=100000)


def _credentials():
    try:
        return get_credentials()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Google authentication failed: {exc}") from exc


@router.get("/search-console/sites")
def search_console_sites() -> dict[str, Any]:
    try:
        return {"sites": SearchConsoleClient(credentials=_credentials()).list_sites()}
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.post("/search-console/search-analytics")
def search_analytics(request: SearchAnalyticsRequest) -> dict[str, Any]:
    try:
        rows = SearchConsoleClient(credentials=_credentials()).search_analytics(
            site_url=request.site_url,
            start_date=request.start_date,
            end_date=request.end_date,
            dimensions=request.dimensions or None,
            row_limit=request.row_limit,
        )
        return {"site_url": request.site_url, "rows": rows}
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/sitemaps")
def sitemaps(site_url: str) -> dict[str, Any]:
    try:
        items = SearchConsoleClient(credentials=_credentials()).list_sitemaps(site_url)
        return {"site_url": site_url, "sitemaps": items}
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.post("/url-inspection")
def url_inspection(request: URLInspectionRequest) -> dict[str, Any]:
    try:
        result = URLInspectionClient(credentials=_credentials()).inspect(
            request.inspection_url, request.site_url
        )
        return result
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.post("/pagespeed")
def pagespeed(request: PageSpeedRequest) -> dict[str, Any]:
    try:
        credentials = _credentials()
        token = getattr(credentials, "token", None)
        result = PageSpeedClient().analyze(
            request.url,
            strategy=request.strategy,
            categories=request.categories,
            oauth_token=token,
        )
        return result
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.post("/analytics/report")
def analytics_report(request: GA4ReportRequest) -> dict[str, Any]:
    try:
        return GA4Client(credentials=_credentials()).run_report(
            property_id=request.property_id,
            dimensions=request.dimensions,
            metrics=request.metrics,
            start_date=request.start_date,
            end_date=request.end_date,
            limit=request.limit,
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/health")
def google_health() -> dict[str, Any]:
    return {"google_integrations": ["search-console", "sitemaps", "url-inspection", "pagespeed", "ga4"]}
