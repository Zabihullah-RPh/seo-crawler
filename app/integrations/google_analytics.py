from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from google.analytics.data_v1beta import BetaAnalyticsDataClient
from google.analytics.data_v1beta.types import DateRange, Dimension, Metric, RunReportRequest

from app.integrations.google_auth import get_credentials


class GA4Client:
    def __init__(self, credentials=None):
        self.credentials = credentials or get_credentials()
        self.client = BetaAnalyticsDataClient(credentials=self.credentials)

    def run_report(
        self,
        property_id: str,
        dimensions: list[str],
        metrics: list[str],
        start_date: str | None = None,
        end_date: str | None = None,
        limit: int = 10000,
    ) -> dict[str, Any]:
        end = date.fromisoformat(end_date) if end_date else date.today()
        start = date.fromisoformat(start_date) if start_date else end - timedelta(days=28)

        request = RunReportRequest(
            property=f"properties/{property_id}",
            date_ranges=[DateRange(start_date=start.isoformat(), end_date=end.isoformat())],
            dimensions=[Dimension(name=name) for name in dimensions],
            metrics=[Metric(name=name) for name in metrics],
            limit=limit,
        )
        response = self.client.run_report(request=request)

        return {
            "dimension_headers": [x.name for x in response.dimension_headers],
            "metric_headers": [x.name for x in response.metric_headers],
            "rows": [
                {
                    "dimensions": [x.value for x in row.dimension_values],
                    "metrics": [x.value for x in row.metric_values],
                }
                for row in response.rows
            ],
            "row_count": response.row_count,
        }
