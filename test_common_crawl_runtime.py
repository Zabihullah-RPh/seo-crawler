import unittest
from unittest.mock import patch

from app.integrations.common_crawl_runtime import (
    find_domain_id,
    map_domain_ids,
    reverse_domain,
    target_domain,
)


class CommonCrawlRuntimeTests(unittest.TestCase):
    def test_target_domain(self):
        self.assertEqual(target_domain("https://www.Example.com/a"), "example.com")

    def test_reverse_domain(self):
        self.assertEqual(reverse_domain("example.com"), "com.example")

    def test_graph_lookup_helpers_are_domain_specific(self):
        self.assertNotEqual(reverse_domain("alpha.example"), reverse_domain("beta.example"))


if __name__ == "__main__":
    unittest.main()
