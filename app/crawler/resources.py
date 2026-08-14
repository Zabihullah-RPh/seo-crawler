import asyncio
from urllib.parse import urljoin

from app.utils.urls import normalize_url


async def crawl_resource(
    http,
    page_url,
    resource_url
):

    target = normalize_url(
        urljoin(
            page_url,
            resource_url
        )
    )

    if not target:
        return None

    result = await http.get(
        target
    )

    response = result["response"]

    if not response:

        return {
            "url": target,
            "status": 0,
            "error": result["error"]
        }

    return {
        "url": target,
        "status": response.status_code,
        "content_type":
            response.headers.get(
                "content-type",
                ""
            ),
        "elapsed":
            result["elapsed"]
    }


async def crawl_resources(
    http,
    resources
):

    tasks = [
        crawl_resource(
            http,
            item["page_url"],
            item["resource_url"]
        )
        for item in resources
    ]

    return await asyncio.gather(
        *tasks,
        return_exceptions=True
    )
