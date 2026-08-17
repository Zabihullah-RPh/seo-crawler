"""Windows-safe launcher for the SEO crawler API.

Creates a ProactorEventLoop explicitly before Uvicorn starts so Playwright can
spawn browser subprocesses under Python 3.14 on Windows.
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
