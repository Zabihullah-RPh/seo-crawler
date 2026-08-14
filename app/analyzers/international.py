from urllib.parse import urljoin, urlparse

from app.utils.urls import normalize_url


def validate_canonical(
    page_url,
    canonical
):

    issues = []

    if not canonical:

        return issues

    canonical = normalize_url(
        urljoin(
            page_url,
            canonical
        )
    )

    if not canonical:
        return issues

    if canonical == page_url:
        return issues

    issues.append({
        "url": page_url,
        "type": "canonical_to_different_url",
        "severity": "medium",
        "target": canonical
    })

    return issues


def validate_hreflang(
    page_url,
    hreflang
):

    issues = []

    languages = set()

    for item in hreflang:

        lang = (
            item.get("lang")
            or ""
        ).lower().strip()

        target = normalize_url(
            item.get("url")
            or ""
        )

        if not lang:
            issues.append({
                "url": page_url,
                "type": "hreflang_missing_language",
                "severity": "medium"
            })

            continue

        if not target:

            issues.append({
                "url": page_url,
                "type": "hreflang_invalid_url",
                "severity": "medium"
            })

            continue

        if lang in languages:

            issues.append({
                "url": page_url,
                "type": "duplicate_hreflang_language",
                "severity": "medium"
            })

        languages.add(lang)

    return issues
