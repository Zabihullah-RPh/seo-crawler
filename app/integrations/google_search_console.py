from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from googleapiclient.discovery import build

from app.integrations.google_auth import get_credentials


class SearchConsoleClient:
    def __init__(self, credentials=None):
        self.credentials = credentials or get_credentials()
        self.service = build(
            "searchconsole",
            "v1",
            credentials=self.credentials,
            cache_discovery=False,
        )

    def list_sites(self) -> list[dict[str, Any]]:
        response = self.service.sites().list().execute()
        return response.get("siteEntry", [])

    def search_analytics(
        self,
        site_url: str,
        start_date: str | None = None,
        end_date: str | None = None,
        dimensions: list[str] | None = None,
        row_limit: int = 25000,
        data_state: str | None = None,
    ) -> list[dict[str, Any]]:
        end = date.fromisoformat(end_date) if end_date else date.today() - timedelta(days=2)
        start = date.fromisoformat(start_date) if start_date else end - timedelta(days=28)

        body: dict[str, Any] = {
            "startDate": start.isoformat(),
            "endDate": end.isoformat(),
            "rowLimit": min(max(row_limit, 1), 25000),
        }
        if dimensions:
            body["dimensions"] = dimensions
        if data_state:
            body["dataState"] = data_state

        response = (
            self.service.searchanalytics()
            .query(siteUrl=site_url, body=body)
            .execute()
        )
        return response.get("rows", [])

    def inspect_url(self, inspection_url: str, site_url: str) -> dict[str, Any]:
        body = {"inspectionUrl": inspection_url, "siteUrl": site_url}
        return (
            self.service.urlInspection()
            .index()
            .inspect(body=body)
            .execute()
        )

    def list_sitemaps(self, site_url: str) -> list[dict[str, Any]]:
        response = self.service.sitemaps().list(siteUrl=site_url).execute()
        return response.get("sitemap", [])

    def get_sitemap(self, site_url: str, sitemap_url: str) -> dict[str, Any]:
        return self.service.sitemaps().get(siteUrl=site_url, feedpath=sitemap_url).execute()

    def submit_sitemap(self, site_url: str, sitemap_url: str) -> None:
        self.service.sitemaps().submit(siteUrl=site_url, feedpath=sitemap_url).execute()

    def delete_sitemap(self, site_url: str, sitemap_url: str) -> None:
        self.service.sitemaps().delete(siteUrl=site_url, feedpath=sitemap_url).execute()
