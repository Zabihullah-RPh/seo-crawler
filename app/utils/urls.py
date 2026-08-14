from urllib.parse import (
    urlparse,
    urlunparse,
    parse_qsl,
    urlencode,
    unquote
)

import tldextract


TRACKING_PARAMETERS = {
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "utm_term",
    "utm_content",
    "gclid",
    "fbclid",
    "msclkid",
    "dclid",
    "gbraid",
    "wbraid"
}


def normalize_url(url: str) -> str:

    if not url:
        return ""

    url = url.strip()

    parsed = urlparse(url)

    if parsed.scheme.lower() not in {"http", "https"}:
        return ""

    scheme = parsed.scheme.lower()

    hostname = parsed.hostname

    if not hostname:
        return ""

    hostname = hostname.lower()

    port = parsed.port

    if port:
        if not (
            (scheme == "http" and port == 80)
            or
            (scheme == "https" and port == 443)
        ):
            netloc = f"{hostname}:{port}"
        else:
            netloc = hostname
    else:
        netloc = hostname

    path = unquote(parsed.path or "/")

    while "//" in path:
        path = path.replace("//", "/")

    if path != "/" and path.endswith("/"):
        path = path.rstrip("/")

    query_items = []

    for key, value in parse_qsl(
        parsed.query,
        keep_blank_values=True
    ):
        if key.lower() in TRACKING_PARAMETERS:
            continue

        query_items.append((key, value))

    query_items.sort()

    query = urlencode(
        query_items,
        doseq=True
    )

    return urlunparse((
        scheme,
        netloc,
        path,
        "",
        query,
        ""
    ))


def origin(url: str) -> str:

    p = urlparse(url)

    return f"{p.scheme}://{p.netloc}"


def hostname(url: str) -> str:

    return urlparse(url).hostname or ""


def same_host(a: str, b: str) -> bool:

    return hostname(a).lower() == hostname(b).lower()


def same_registered_domain(a: str, b: str) -> bool:

    ea = tldextract.extract(a)
    eb = tldextract.extract(b)

    return (
        ea.domain == eb.domain
        and ea.suffix == eb.suffix
    )
