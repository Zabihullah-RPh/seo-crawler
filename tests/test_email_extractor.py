import unittest

from app.crawler.email_extractor import extract_emails


class EmailExtractionTests(unittest.TestCase):
    def test_extracts_visible_business_email(self):
        records = extract_emails(
            "<html><body>Contact sales at sales@example.com.</body></html>",
            "https://www.example.com/contact/",
        )
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["email"], "sales@example.com")
        self.assertIn("visible_text", records[0]["extraction_methods"])
        self.assertTrue(records[0]["same_domain"])
        self.assertTrue(records[0]["generic_mailbox"])
        self.assertEqual(records[0]["source_url"], "https://www.example.com/contact/")

    def test_extracts_mailto_address_and_removes_query_parameters(self):
        records = extract_emails(
            '<a href="mailto:Jane.Doe@example.com?subject=Sales">Email us</a>',
            "https://example.com/",
        )
        self.assertEqual([row["email"] for row in records], ["jane.doe@example.com"])
        self.assertIn("mailto", records[0]["extraction_methods"])

    def test_extracts_json_ld_contact_email(self):
        html = """
        <script type="application/ld+json">
        {"@context":"https://schema.org","@type":"Organization",
         "contactPoint":{"@type":"ContactPoint","email":"hello@example.com"}}
        </script>
        """
        records = extract_emails(html, "https://example.com/")
        self.assertEqual([row["email"] for row in records], ["hello@example.com"])
        self.assertIn("json_ld", records[0]["extraction_methods"])

    def test_deduplicates_same_email_and_merges_evidence(self):
        html = """
        <a href="mailto:info@example.com">info@example.com</a>
        <p>Write to info@example.com for details.</p>
        <script type="application/ld+json">{"email":"info@example.com"}</script>
        """
        records = extract_emails(html, "https://example.com/contact/")
        self.assertEqual(len(records), 1)
        self.assertEqual(
            records[0]["extraction_methods"],
            ["json_ld", "mailto", "visible_text"],
        )

    def test_ignores_script_emails_unrelated_to_visible_content(self):
        html = """
        <script>window.analyticsConfig = {email: "tracking@example.net"};</script>
        <body>No contact details published.</body>
        """
        self.assertEqual(extract_emails(html, "https://example.com/"), [])

    def test_flags_external_email_domain(self):
        records = extract_emails(
            "<p>Contact us at owner@gmail.com</p>",
            "https://example.com/",
        )
        self.assertEqual(len(records), 1)
        self.assertFalse(records[0]["same_domain"])
        self.assertEqual(records[0]["email_domain"], "gmail.com")

    def test_flags_recruitment_as_a_shared_mailbox(self):
        records = extract_emails(
            "<p>Send CVs to recruitment@example.com</p>",
            "https://example.com/",
        )
        self.assertEqual(len(records), 1)
        self.assertTrue(records[0]["generic_mailbox"])


if __name__ == "__main__":
    unittest.main()
