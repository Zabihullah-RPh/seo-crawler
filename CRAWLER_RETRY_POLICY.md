# Crawler retry and access policy

## Request attempts

- Standard page requests use a 15-second default timeout.
- A standard request gets at most one additional attempt (two total) for a temporary network/transport error or HTTP 408, 500, 502, 503 or 504.
- The retry backoff is capped at two seconds.
- Permanent client responses such as 401, 403, 404, 410, 429 and 451 are not retried.
- The configured maximum is capped at one retry, even if CRAWLER_MAX_RETRIES is set higher.

## robots.txt

- robots.txt is checked once per crawl with an 8-second timeout.
- An explicit Disallow rule stops crawling the affected URL without retrying.
- A 401, 403, 407, 429 or 451 response from robots.txt stops the site check immediately.
- A 5xx response or a network/timeout failure while retrieving robots.txt stops that site for this run. It is not retried.
- A missing robots.txt (404 or 410) is treated as an unavailable robots file; normal crawling may proceed.
- Child URLs disallowed by a successful robots.txt policy are skipped before requesting them.

## Why these limits

The previous shared HTTP client allowed three additional retries, used a 30-second request timeout, and applied exponential waits. A single slow robots.txt request could therefore consume roughly two minutes. The new policy bounds robots checks and avoids repeated requests to sites that explicitly deny access, rate-limit, or cannot be checked. A single retry remains only for transient failures likely to recover immediately.

The automated tests cover blocked statuses, a temporary 503, transport failures, robots.txt timeouts, explicit Disallow, and missing robots.txt.
