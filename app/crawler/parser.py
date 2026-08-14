from bs4 import BeautifulSoup
from urllib.parse import urljoin


def parse_html(html: str, base_url: str):

    # Normalize URL inputs before urllib.urljoin().
    # httpx can return URL objects instead of plain strings.
    base_url = str(base_url or "")

    if not isinstance(html, str):
        html = str(html or "")

    soup = BeautifulSoup(
        html,
        "html.parser"
    )

    title_tag = soup.find("title")

    title = (
        title_tag.get_text(
            " ",
            strip=True
        )
        if title_tag
        else ""
    )

    meta_description = ""

    meta = soup.find(
        "meta",
        attrs={"name": lambda x:
            x and x.lower() == "description"}
    )

    if meta:
        meta_description = (
            meta.get("content", "") or ""
        ).strip()

    canonical = ""

    canonical_tag = soup.find(
        "link",
        rel=lambda value:
            value and "canonical" in (
                value if isinstance(value, list)
                else [value]
            )
    )

    if canonical_tag:
        canonical = urljoin(
            base_url,
            canonical_tag.get("href", "")
        )

    robots = ""

    robots_tag = soup.find(
        "meta",
        attrs={"name": lambda x:
            x and x.lower() == "robots"}
    )

    if robots_tag:
        robots = (
            robots_tag.get("content", "") or ""
        ).strip()

    language = ""

    if soup.html:
        language = (
            soup.html.get("lang", "") or ""
        ).strip()

    viewport = ""

    viewport_tag = soup.find(
        "meta",
        attrs={"name": lambda x:
            x and x.lower() == "viewport"}
    )

    if viewport_tag:
        viewport = (
            viewport_tag.get("content", "") or ""
        ).strip()

    # ---------------------------------------------------------
    # LINKS
    # ---------------------------------------------------------

    links = []

    for anchor in soup.find_all(
        "a",
        href=True
    ):

        raw_href = (
            anchor.get("href") or ""
        ).strip()

        if not raw_href:
            continue

        if raw_href.startswith(
            (
                "#",
                "javascript:",
                "mailto:",
                "tel:",
                "data:"
            )
        ):
            continue

        absolute_url = urljoin(
            base_url,
            raw_href
        )

        rel = anchor.get("rel") or []

        if isinstance(rel, str):
            rel = rel.split()

        links.append({
            "url": absolute_url,
            "anchor": anchor.get_text(
                " ",
                strip=True
            )[:1000],
            "rel": " ".join(rel),
            "nofollow": (
                "nofollow" in [
                    x.lower()
                    for x in rel
                ]
            ),
            "ugc": (
                "ugc" in [
                    x.lower()
                    for x in rel
                ]
            ),
            "sponsored": (
                "sponsored" in [
                    x.lower()
                    for x in rel
                ]
            )
        })

    # ---------------------------------------------------------
    # IMAGES
    # ---------------------------------------------------------

    images = []

    for image in soup.find_all(
        "img"
    ):

        src = (
            image.get("src")
            or image.get("data-src")
            or ""
        ).strip()

        if not src:
            continue

        images.append({
            "url": urljoin(
                base_url,
                src
            ),
            "alt": (
                image.get("alt", "")
                or ""
            ).strip(),
            "width": image.get("width"),
            "height": image.get("height"),
            "loading": image.get("loading")
        })

    # ---------------------------------------------------------
    # JAVASCRIPT
    # ---------------------------------------------------------

    scripts = []

    for script in soup.find_all(
        "script",
        src=True
    ):

        scripts.append(
            urljoin(
                base_url,
                script.get("src")
            )
        )

    # ---------------------------------------------------------
    # CSS
    # ---------------------------------------------------------

    stylesheets = []

    for link in soup.find_all(
        "link",
        href=True
    ):

        rel = link.get("rel") or []

        if isinstance(rel, str):
            rel = [rel]

        if "stylesheet" in [
            x.lower()
            for x in rel
        ]:

            stylesheets.append(
                urljoin(
                    base_url,
                    link.get("href")
                )
            )

    # ---------------------------------------------------------
    # HREFLANG
    # ---------------------------------------------------------

    hreflang = []

    for link in soup.find_all(
        "link",
        href=True
    ):

        lang = (
            link.get("hreflang")
            or ""
        ).strip()

        href = (
            link.get("href")
            or ""
        ).strip()

        if lang and href:

            hreflang.append({
                "lang": lang,
                "url": urljoin(
                    base_url,
                    href
                )
            })

    # ---------------------------------------------------------
    # STRUCTURED DATA
    # ---------------------------------------------------------

    schemas = []

    for script in soup.find_all(
        "script",
        attrs={
            "type":
            lambda x:
                x and
                "ld+json" in x.lower()
        }
    ):

        content = (
            script.string
            or script.get_text()
            or ""
        ).strip()

        if content:
            schemas.append(content)

    # ---------------------------------------------------------
    # TEXT / WORD COUNT
    # ---------------------------------------------------------

    for element in soup([
        "script",
        "style",
        "noscript",
        "svg"
    ]):
        element.decompose()

    text = soup.get_text(
        " ",
        strip=True
    )

    words = text.split()

    return {
        "title": title,
        "meta_description":
            meta_description,
        "canonical": canonical,
        "robots": robots,
        "language": language,
        "viewport": viewport,
        "text": text,
        "word_count": len(words),
        "links": links,
        "images": images,
        "scripts": scripts,
        "stylesheets": stylesheets,
        "hreflang": hreflang,
        "schemas": schemas
    }
