from __future__ import annotations

from typing import Any

from googleapiclient.discovery import build

from app.integrations.google_auth import get_credentials


class URLInspectionClient:
    def __init__(self, credentials=None):
        self.credentials = credentials or get_credentials()
        self.service = build(
            "searchconsole",
            "v1",
            credentials=self.credentials,
            cache_discovery=False,
        )

    def inspect(self, inspection_url: str, site_url: str) -> dict[str, Any]:
        body = {
            "inspectionUrl": inspection_url,
            "siteUrl": site_url,
        }
        return (
            self.service.urlInspection()
            .index()
            .inspect(body=body)
            .execute()
        )
