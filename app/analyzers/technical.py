import re


def analyze(page):

    issues = []

    url = page["url"]

    status = page.get(
        "status_code"
    )

    title = page.get(
        "title",
        ""
    )

    description = page.get(
        "meta_description",
        ""
    )

    canonical = page.get(
        "canonical",
        ""
    )

    robots = page.get(
        "robots",
        ""
    ).lower()

    headings = page.get(
        "headings",
        {}
    )

    word_count = page.get(
        "word_count",
        0
    )

    if status >= 400:

        severity = (
            "critical"
            if status >= 500
            else "high"
        )

        issues.append({
            "url": url,
            "type": f"http_{status}",
            "severity": severity
        })

    if not title:

        issues.append({
            "url": url,
            "type": "missing_title",
            "severity": "high"
        })

    elif len(title) > 60:

        issues.append({
            "url": url,
            "type": "title_too_long",
            "severity": "medium"
        })

    elif len(title) < 30:

        issues.append({
            "url": url,
            "type": "title_too_short",
            "severity": "low"
        })

    if not description:

        issues.append({
            "url": url,
            "type": "missing_meta_description",
            "severity": "medium"
        })

    elif len(description) > 160:

        issues.append({
            "url": url,
            "type": "meta_description_too_long",
            "severity": "low"
        })

    h1 = headings.get(
        "h1",
        []
    )

    if not h1:

        issues.append({
            "url": url,
            "type": "missing_h1",
            "severity": "high"
        })

    elif len(h1) > 1:

        issues.append({
            "url": url,
            "type": "multiple_h1",
            "severity": "medium"
        })

    if not canonical:

        issues.append({
            "url": url,
            "type": "missing_canonical",
            "severity": "medium"
        })

    if word_count < 200:

        issues.append({
            "url": url,
            "type": "thin_content",
            "severity": "medium"
        })

    if "noindex" in robots:

        issues.append({
            "url": url,
            "type": "noindex",
            "severity": "info"
        })

    if "nofollow" in robots:

        issues.append({
            "url": url,
            "type": "nofollow",
            "severity": "info"
        })

    if not page.get(
        "language"
    ):

        issues.append({
            "url": url,
            "type": "missing_html_language",
            "severity": "low"
        })

    if not page.get(
        "viewport"
    ):

        issues.append({
            "url": url,
            "type": "missing_viewport",
            "severity": "low"
        })

    for image in page.get(
        "images",
        []
    ):

        if image.get("alt") is None:

            issues.append({
                "url": url,
                "type": "image_missing_alt",
                "severity": "low"
            })

    return issues
