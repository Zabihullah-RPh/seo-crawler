import unittest

from app.integrations.common_crawl_backlinks import build_lookup_plan, prepare_runtime_lookup


class CommonCrawlBacklinkPlannerTests(unittest.TestCase):
    def test_universal_domain_normalization(self):
        plan = build_lookup_plan("https://www.Example.com/path/page")
        self.assertEqual(plan.target_domain, "example.com")
        self.assertEqual(plan.reverse_domain, "com.example")
        self.assertEqual(plan.graph_level, "domain")

    def test_runtime_plan_has_no_data_download(self):
        result = prepare_runtime_lookup("https://example.org/")
        self.assertEqual(result["provider"], "Common Crawl")
        self.assertEqual(result["status"], "ready")
        self.assertFalse(result["data_downloaded"])
        self.assertEqual(result["lookup"]["target_domain"], "example.org")

    def test_different_targets_are_processed_independently(self):
        one = prepare_runtime_lookup("https://alpha.example/")
        two = prepare_runtime_lookup("https://beta.example/")
        self.assertNotEqual(one["lookup"]["target_domain"], two["lookup"]["target_domain"])
        self.assertNotEqual(one["lookup"]["reverse_domain"], two["lookup"]["reverse_domain"])


if __name__ == "__main__":
    unittest.main()
