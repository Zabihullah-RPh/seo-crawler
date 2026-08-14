from urllib.parse import urljoin

from urllib import robotparser


class RobotsManager:

    def __init__(
        self,
        base_url,
        user_agent
    ):

        self.base_url = base_url
        self.user_agent = user_agent
        self.parser = robotparser.RobotFileParser()
        self.raw = ""
        self.status = None

    async def load(self, http):

        robots_url = urljoin(
            self.base_url,
            "/robots.txt"
        )

        result = await http.get(
            robots_url
        )

        response = result["response"]

        if response:

            self.status = response.status_code
            self.raw = response.text

            if response.status_code == 200:

                self.parser.set_url(
                    robots_url
                )

                self.parser.parse(
                    response.text.splitlines()
                )

        return {
            "url": robots_url,
            "status": self.status,
            "content": self.raw
        }

    def allowed(self, url):

        try:
            return self.parser.can_fetch(
                self.user_agent,
                url
            )
        except Exception:
            return True

    def crawl_delay(self):

        try:
            return self.parser.crawl_delay(
                self.user_agent
            )
        except Exception:
            return None

    def sitemaps(self):

        try:
            return self.parser.site_maps() or []
        except Exception:
            return []
