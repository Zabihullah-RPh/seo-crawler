import re
import xml.etree.ElementTree as ET
from urllib.parse import urljoin

SITEMAP_NS = {
    "sm": "http://www.sitemaps.org/schemas/sitemap/0.9"
}

async def discover_sitemaps(
    base_url,
    robots,
    http,
    crawl_id
):

    urls = set()

    for sitemap in robots.sitemaps():
        urls.add(sitemap)

    urls.add(
        urljoin(base_url, "/sitemap.xml")
    )

    urls.add(
        urljoin(base_url, "/sitemap_index.xml")
    )

    processed = set()

    while urls:

        sitemap_url = urls.pop()

        if sitemap_url in processed:
            continue

        processed.add(sitemap_url)

        result = await http.get(
            sitemap_url
        )

        response = result["response"]

        if not response:
            continue

        if response.status_code != 200:
            continue

        text = response.text

        try:

            root = ET.fromstring(text)

        except Exception:

            continue

        root_name = root.tag.lower()

        if root_name.endswith("sitemapindex"):

            for node in root.findall(
                "sm:sitemap",
                SITEMAP_NS
            ):

                loc = node.find(
                    "sm:loc",
                    SITEMAP_NS
                )

                if loc is not None and loc.text:

                    urls.add(
                        loc.text.strip()
                    )

        elif root_name.endswith("urlset"):

            for node in root.findall(
                "sm:url",
                SITEMAP_NS
            ):

                loc = node.find(
                    "sm:loc",
                    SITEMAP_NS
                )

                if loc is not None and loc.text:

                    target = loc.text.strip()

                    await save_discovered_url(
                        crawl_id,
                        target,
                        sitemap_url,
                        "sitemap"
                    )
