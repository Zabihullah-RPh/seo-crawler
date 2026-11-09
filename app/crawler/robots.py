from urllib.parse import urljoin
from urllib import robotparser

from app.config import ROBOTS_TIMEOUT


class RobotsManager:

    # Conservative stop conditions: do not crawl when access is explicitly
    # denied or when robots.txt cannot be checked due to a server/network error.
    BLOCKING_STATUS_CODES = frozenset({401, 403, 407, 429, 451})

    def __init__(self, base_url, user_agent):
        self.base_url = base_url
        self.user_agent = user_agent
        self.parser = robotparser.RobotFileParser()
        self.raw = ""
        self.status = None
        self.blocked_reason = None
        self.policy_available = False

    async def load(self, http):
        robots_url = urljoin(self.base_url, "/robots.txt")

        # robots.txt is always a single, short request. A block or failure is
        # recorded and not retried during this crawl.
        result = await http.get(
            robots_url,
            retries=0,
            timeout=ROBOTS_TIMEOUT,
            retry_statuses=set(),
        )
        response = result.get("response")

        if response is None:
            self.blocked_reason = (
                "robots.txt could not be checked; skipped without retry. "
                + str(result.get("error") or "No HTTP response")
            )
        else:
            self.status = int(response.status_code)
            self.raw = response.text or ""

            if self.status == 200:
                self.parser.set_url(robots_url)
                self.parser.parse(self.raw.splitlines())
                self.policy_available = True

            elif self.status in self.BLOCKING_STATUS_CODES:
                self.blocked_reason = (
                    f"robots.txt returned HTTP {self.status}; access not retried."
                )

            elif 500 <= self.status <= 599:
                self.blocked_reason = (
                    f"robots.txt returned HTTP {self.status}; "
                    "policy is unreachable, so this site is skipped without retry."
                )

            elif self.status in (404, 410):
                # No robots file published: the REP file is unavailable.
                # There are no robots rules to apply.
                self.policy_available = False

            elif 400 <= self.status <= 499:
                # Other 4xx responses indicate that the robots resource is
                # unavailable rather than server-unreachable.
                self.policy_available = False

            else:
                self.blocked_reason = (
                    f"Unexpected robots.txt response HTTP {self.status}; "
                    "site skipped without retry."
                )

        return {
            "url": robots_url,
            "status": self.status,
            "content": self.raw,
            "allowed": self.blocked_reason is None,
            "blocked_reason": self.blocked_reason,
            "attempts": result.get("attempts", 1),
        }

    def allowed(self, url):
        if self.blocked_reason:
            return False

        if not self.policy_available:
            return True

        try:
            return bool(self.parser.can_fetch(self.user_agent, url))
        except Exception:
            # Parsing/evaluation uncertainty must not trigger another fetch.
            return False

    def crawl_delay(self):
        if not self.policy_available:
            return None
        try:
            return self.parser.crawl_delay(self.user_agent)
        except Exception:
            return None

    def sitemaps(self):
        if not self.policy_available:
            return []
        try:
            return self.parser.site_maps() or []
        except Exception:
            return []
