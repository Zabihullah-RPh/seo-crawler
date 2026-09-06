from app.integrations.google_analytics import GA4Client
from app.integrations.google_auth import get_credentials
from app.integrations.google_indexing import IndexingClient
from app.integrations.google_pagespeed import PageSpeedClient
from app.integrations.google_search_console import SearchConsoleClient
from app.integrations.google_url_inspection import URLInspectionClient


def test_google_integration_symbols_import():
    assert all([
        get_credentials,
        SearchConsoleClient,
        URLInspectionClient,
        PageSpeedClient,
        GA4Client,
        IndexingClient,
    ])
