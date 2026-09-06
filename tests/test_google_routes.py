from fastapi.testclient import TestClient

import app.integrations.google_routes as google_routes
from app.api import app


client = TestClient(app)


def test_google_health():
    response = client.get("/api/google/health")
    assert response.status_code == 200
    assert "pagespeed" in response.json()["google_integrations"]
    assert "ga4" in response.json()["google_integrations"]


def test_search_console_sites(monkeypatch):
    class FakeClient:
        def __init__(self, credentials=None):
            pass

        def list_sites(self):
            return [{"siteUrl": "https://example.com/", "permissionLevel": "siteOwner"}]

    monkeypatch.setattr(google_routes, "SearchConsoleClient", FakeClient)
    monkeypatch.setattr(google_routes, "_credentials", lambda: object())

    response = client.get("/api/google/search-console/sites")
    assert response.status_code == 200
    assert response.json()["sites"][0]["siteUrl"] == "https://example.com/"


def test_sitemaps(monkeypatch):
    class FakeClient:
        def __init__(self, credentials=None):
            pass

        def list_sitemaps(self, site_url):
            return [{"path": site_url + "sitemap.xml"}]

    monkeypatch.setattr(google_routes, "SearchConsoleClient", FakeClient)
    monkeypatch.setattr(google_routes, "_credentials", lambda: object())

    response = client.get("/api/google/sitemaps", params={"site_url": "https://example.com/"})
    assert response.status_code == 200
    assert response.json()["sitemaps"][0]["path"].endswith("sitemap.xml")


def test_url_inspection(monkeypatch):
    class FakeClient:
        def __init__(self, credentials=None):
            pass

        def inspect(self, inspection_url, site_url):
            return {"inspectionResult": {"indexStatusResult": {"indexingState": "INDEXING_ALLOWED"}}}

    monkeypatch.setattr(google_routes, "URLInspectionClient", FakeClient)
    monkeypatch.setattr(google_routes, "_credentials", lambda: object())

    response = client.post(
        "/api/google/url-inspection",
        json={"inspection_url": "https://example.com/", "site_url": "https://example.com/"},
    )
    assert response.status_code == 200
    assert response.json()["inspectionResult"]["indexStatusResult"]["indexingState"] == "INDEXING_ALLOWED"


def test_pagespeed(monkeypatch):
    class FakeCredentials:
        token = "test-token"

    class FakeClient:
        def analyze(self, url, strategy, categories, oauth_token=None):
            assert oauth_token == "test-token"
            return {"lighthouseResult": {"categories": {"performance": {"score": 0.84}}}}

    monkeypatch.setattr(google_routes, "PageSpeedClient", lambda: FakeClient())
    monkeypatch.setattr(google_routes, "_credentials", lambda: FakeCredentials())

    response = client.post(
        "/api/google/pagespeed",
        json={"url": "https://example.com/", "strategy": "mobile", "categories": ["performance"]},
    )
    assert response.status_code == 200
    assert response.json()["lighthouseResult"]["categories"]["performance"]["score"] == 0.84


def test_ga4_report(monkeypatch):
    class FakeClient:
        def __init__(self, credentials=None):
            pass

        def run_report(self, **kwargs):
            return {"row_count": 1, "rows": [{"dimensions": ["2026-08-26"], "metrics": ["10"]}]}

    monkeypatch.setattr(google_routes, "GA4Client", FakeClient)
    monkeypatch.setattr(google_routes, "_credentials", lambda: object())

    response = client.post(
        "/api/google/analytics/report",
        json={"property_id": "434207160", "dimensions": ["date"], "metrics": ["activeUsers"]},
    )
    assert response.status_code == 200
    assert response.json()["row_count"] == 1
