from __future__ import annotations

from unittest.mock import MagicMock

from app.integrations.google_indexing import INDEXING_SCOPE, IndexingClient
from app.integrations.google_pagespeed import PageSpeedClient
from app.integrations.google_search_console import SearchConsoleClient


def test_indexing_client_publish_builds_supported_request(tmp_path):
    session = MagicMock()
    response = MagicMock()
    response.json.return_value = {"urlNotificationMetadata": {"url": "https://example.com/jobs/1"}}
    response.raise_for_status.return_value = None
    session.post.return_value = response

    client = IndexingClient(service_account_path=tmp_path / "unused.json", session=session)
    result = client.publish("https://example.com/jobs/1")

    session.post.assert_called_once_with(
        "https://indexing.googleapis.com/v3/urlNotifications:publish",
        json={"url": "https://example.com/jobs/1", "type": "URL_UPDATED"},
        timeout=30,
    )
    assert result["urlNotificationMetadata"]["url"] == "https://example.com/jobs/1"
    assert INDEXING_SCOPE == "https://www.googleapis.com/auth/indexing"


def test_indexing_client_rejects_invalid_notification_type(tmp_path):
    client = IndexingClient(service_account_path=tmp_path / "unused.json", session=MagicMock())

    try:
        client.publish("https://example.com/", "INVALID")  # type: ignore[arg-type]
    except ValueError as exc:
        assert "URL_UPDATED" in str(exc)
    else:
        raise AssertionError("Invalid indexing notification type was accepted")


def test_pagespeed_client_builds_expected_request(monkeypatch):
    response = MagicMock()
    response.raise_for_status.return_value = None
    response.json.return_value = {"lighthouseResult": {"categories": {}}}

    get = MagicMock(return_value=response)
    monkeypatch.setattr("app.integrations.google_pagespeed.requests.get", get)

    client = PageSpeedClient(api_key="test-key")
    result = client.analyze("https://example.com/", strategy="desktop", categories=["performance", "seo"])

    assert "lighthouseResult" in result
    kwargs = get.call_args.kwargs
    assert kwargs["params"]["url"] == "https://example.com/"
    assert kwargs["params"]["strategy"] == "desktop"
    assert kwargs["params"]["key"] == "test-key"
    assert kwargs["params"]["category"] == ["performance", "seo"]


def test_search_console_client_methods_use_google_service(monkeypatch):
    service = MagicMock()
    service.sites().list().execute.return_value = {
        "siteEntry": [{"siteUrl": "https://example.com/"}]
    }
    service.sitemaps().list(siteUrl="https://example.com/").execute.return_value = {
        "sitemap": [{"path": "https://example.com/sitemap.xml"}]
    }
    service.searchanalytics().query(
        siteUrl="https://example.com/",
        body={"startDate": "2026-08-01", "endDate": "2026-08-07", "rowLimit": 100},
    ).execute.return_value = {"rows": [{"clicks": 1}]}

    monkeypatch.setattr(
        "app.integrations.google_search_console.build",
        lambda *args, **kwargs: service,
    )

    credentials = object()
    client = SearchConsoleClient(credentials=credentials)

    assert client.list_sites()[0]["siteUrl"] == "https://example.com/"
    assert client.list_sitemaps("https://example.com/")[0]["path"].endswith("sitemap.xml")
    rows = client.search_analytics(
        "https://example.com/",
        start_date="2026-08-01",
        end_date="2026-08-07",
        row_limit=100,
    )
    assert rows == [{"clicks": 1}]
