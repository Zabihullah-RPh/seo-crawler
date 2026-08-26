from __future__ import annotations

import json

import pytest

import app.audit as audit


@pytest.mark.asyncio
async def test_run_pipeline_combines_crawl_and_google_layers(tmp_path, monkeypatch):
    crawl_report = tmp_path / "crawl_7.json"
    crawl_report.write_text(
        json.dumps(
            {
                "crawl_id": 7,
                "start_url": "https://example.com/",
                "site": {"domain": {"hostname": "example.com"}},
                "pages": [{"url": "https://example.com/"}],
                "links": [],
                "images": [],
                "backlinks": {"layer1": {"status": "not_configured"}},
            }
        ),
        encoding="utf-8",
    )

    class FakeCrawler:
        crawl_id = 7
        start_url = "https://example.com/"

        async def run(self):
            return None

    monkeypatch.setattr(audit, "initialize", lambda: _noop())
    monkeypatch.setattr(audit, "create_crawl", lambda *args, **kwargs: _return(7))
    monkeypatch.setattr(audit, "ProductionCrawler", lambda **kwargs: FakeCrawler())
    monkeypatch.setattr(audit, "RESULTS_DIR", tmp_path)
    monkeypatch.setattr(
        audit,
        "enrich_google",
        lambda url: {"pagespeed": {"status": "PASS"}, "ga4": {"status": "NOT_CONFIGURED"}},
    )
    monkeypatch.setattr(audit, "generate_report", lambda data, path: path.with_suffix(".html"))

    output = await audit.run_pipeline("https://example.com/")
    result = json.loads(output.read_text(encoding="utf-8"))

    assert output.name == "audit_example.com.json"
    assert result["pipeline"]["status"] == "PASS"
    assert result["google_enrichment"]["pagespeed"]["status"] == "PASS"
    assert result["google_enrichment"]["ga4"]["status"] == "NOT_CONFIGURED"


async def _noop():
    return None


async def _return(value):
    return value
