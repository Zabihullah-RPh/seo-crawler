"""Windows-safe launcher for the SEO crawler API.

Creates a ProactorEventLoop explicitly before Uvicorn starts so Playwright can
spawn browser subprocesses under Python 3.14 on Windows. Also installs the
backlink-report finalizer without changing the existing SEO audit engine.
"""
import asyncio
import sys

import uvicorn


def main() -> None:
    if sys.platform == "win32":
        loop = asyncio.ProactorEventLoop()
    else:
        loop = asyncio.new_event_loop()

    asyncio.set_event_loop(loop)

    try:
        from app.api import app
        from app import audit_engine
        from app.integrations.backlink_report import finalize_backlink_html

        original_generate_report = audit_engine.generate_report

        def generate_report_with_backlink_cleanup(data, source_path):
            html_path = original_generate_report(data, source_path)
            return finalize_backlink_html(html_path, data)

        audit_engine.generate_report = generate_report_with_backlink_cleanup

        config = uvicorn.Config(
            app,
            host="127.0.0.1",
            port=8000,
            loop="asyncio",
            reload=False,
        )
        server = uvicorn.Server(config)
        loop.run_until_complete(server.serve())
    finally:
        loop.close()


if __name__ == "__main__":
    main()
