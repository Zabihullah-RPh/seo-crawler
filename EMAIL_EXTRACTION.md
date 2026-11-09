# Public email extraction and storage

The production SEO crawler now extracts publicly published business email addresses from every HTML page it successfully crawls.

## Sources inspected

- Explicit mailto: links
- Readable page text, including footer/contact sections
- Email fields in JSON-LD structured data
- The server-returned HTML and the rendered browser DOM

It does not scan arbitrary JavaScript bundles for address-like strings. Duplicate addresses found through multiple sources on the same page are stored once with all extraction methods attached.

## Where addresses are saved

For each crawl ID:

- SQLite database: the emails table, with source URL, domain classification, mailbox type and extraction method.
- JSONL: data/crawl_results/crawl_<id>_emails.jsonl
- Full crawl data: results/crawl_<id>.json under the emails key.
- CSV export: results/crawl_<id>_emails.csv
- HTML report: a Public Email Addresses section with source-page links.

The CSV contains one row per unique address. Multiple source pages and extraction methods are retained in their respective columns.

## CSV fields

- email: extracted public address
- email_domain: domain part of the address
- same_domain: whether the email domain matches the crawled website host
- generic_mailbox: whether the local part looks like a shared mailbox (info, sales, support, contact, etc.)
- source_pages: pages where the address was found
- extraction_methods: mailto, visible_text or json_ld

Generic mailboxes are retained and flagged rather than discarded. External email domains are also retained and flagged; review them before use.

## Limitations

- Extraction is not email verification. A public address may be old, invalid or undeliverable.
- The crawler does not reveal private addresses, bypass logins, or query email-provider databases.
- Addresses hidden behind images, Cloudflare obfuscation, interactive forms or complex JavaScript encoding may not be detected.
- same_domain is a simple host-domain comparison, not a formal public-suffix analysis.
- Only pages successfully fetched and allowed by the site's robots.txt policy are scanned.

Treat collected addresses as publicly published contact information and follow applicable marketing/privacy and opt-out requirements before outreach.
