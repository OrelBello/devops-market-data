# Security Policy

## Reporting a Vulnerability

If you discover a security issue in this project, please:

1. **Do NOT open a public GitHub issue**
2. Email Orel directly at the address listed on https://orelbello.com
3. Include: a description, steps to reproduce, and (if possible) suggested fix
4. Expect a response within 7 days

## Scope

This project is a public read-only data aggregator. It does not:
- Collect user data
- Process payments
- Handle authentication/authorization
- Store secrets or credentials

That said, common concerns we care about:

- **Scraper injection** — if a downstream job site returns malicious HTML/JSON
  that compromises our parsers
- **Output XSS** — if scraped content gets rendered in the landing page without
  proper escaping
- **Supply chain** — if a GitHub Action we depend on becomes compromised

## Out of scope

- The scraped job data itself (it's all public on the source sites)
- Performance / DoS via the public Pages site (mitigated by GitHub's CDN)
- Issues with consuming sites (LinkedIn, Greenhouse, etc.) — report to them directly

Thank you for helping keep the Israeli DevOps community safe.
