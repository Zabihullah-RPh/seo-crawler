# Optional targeted public-email extraction

This feature is deliberately separate from the SEO crawler. The existing crawler files, API routes, and default crawl behavior are unchanged. Email extraction runs only when this separate script is explicitly invoked.

## Run it

Install the existing project requirements, then run:

\`\`\`bash
python extract_public_emails.py --url https://example.com --name "Jane Smith" --title "Founder"
\`\`\`

It checks the homepage and then a limited set of same-site Contact, About, Team, and Leadership pages. It looks for publicly displayed email addresses, including mailto links, and returns at most one address when the page context or email username provides a reasonable match to the supplied person's name. Generic inboxes are excluded.

The CSV includes the source page and match reason. The script does not guess email patterns, attempt to access private LinkedIn data, bypass login or access controls, or verify mailbox delivery. It observes robots.txt, limits page count and response size, and pauses between page requests.

Example for a Hunter contact:

\`\`\`bash
python extract_public_emails.py --url https://solveseoagency.com --name "Fazal Abbas" --title "Founder"
\`\`\`

No changes are made to the normal SEO crawler when this script is not called.
