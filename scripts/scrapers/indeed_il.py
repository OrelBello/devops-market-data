"""
Indeed Israel scraper.

Indeed has a real Israeli presence at il.indeed.com. The search page returns
HTML with structured `data-jk` job IDs and a parseable `<h2 class="jobTitle">`
markup pattern. Each job-detail page exposes JSON-LD `JobPosting` schema.

Strategy:
  1. Query the search results page (HTML) for each keyword
  2. Extract job IDs (`data-jk`), titles, companies, locations from listing markup
  3. Output normalized job records

Indeed's anti-bot is moderate; we use realistic UA + polite delays + limited
keyword variations to avoid getting blocked. They occasionally serve a CAPTCHA
to scrapers — when that happens, we get HTML without `data-jk` IDs and return 0.
"""

from __future__ import annotations

import html as _html
import re
import urllib.parse
from typing import Any, Dict, List

from . import _base as B

SEARCH_URL = "https://il.indeed.com/jobs"
QUERIES = [
    "devops",
    "site reliability engineer",
    "platform engineer",
    "cloud engineer",
    "sre",
    "kubernetes engineer",
    "infrastructure engineer",
]

# Indeed listing markup:
#   <h2 class="jobTitle ... ">
#     <a aria-label="..." data-jk="abcdef" href="...">
#       <span title="...">Job Title Here</span>
#     </a>
#   </h2>
#   <span data-testid="company-name">Company</span>
#   <div data-testid="text-location">Tel Aviv</div>

_JOB_BLOCK = re.compile(
    r'<h2[^>]*class="[^"]*jobTitle[^"]*"[^>]*>(.+?)(?=<h2[^>]*class="[^"]*jobTitle|</main|</body)',
    re.DOTALL,
)
_JK = re.compile(r'data-jk="([a-z0-9]+)"', re.IGNORECASE)
_TITLE = re.compile(r'<span[^>]*(?:title|aria-label)="([^"]+)"', re.IGNORECASE)
_TITLE_FALLBACK = re.compile(r"<span[^>]*>([^<]+)</span>")
_COMPANY = re.compile(r'data-testid="company-name"[^>]*>([^<]+)<', re.IGNORECASE)
_LOCATION = re.compile(r'data-testid="text-location"[^>]*>([^<]+)<', re.IGNORECASE)


def _clean(s: str) -> str:
    if not s:
        return ""
    return _html.unescape(re.sub(r"\s+", " ", s)).strip()


def _search(query: str) -> List[Dict[str, Any]]:
    params = {
        "q": query,
        "l": "Israel",
        "fromage": "14",  # last 14 days
        "sort": "date",
    }
    url = SEARCH_URL + "?" + urllib.parse.urlencode(params)
    html_text = B.http_get(
        url,
        headers={
            "Referer": "https://il.indeed.com/",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9",
            "Accept-Language": "en-US,en;q=0.9,he;q=0.8",
        },
    )
    if not isinstance(html_text, str):
        return []
    blocks = _JOB_BLOCK.findall(html_text)
    if not blocks:
        return []
    # Surrounding context for company/location often lives just AFTER the title block
    # So split the page on title boundaries and walk each section
    out = []
    sections = re.split(r'<h2[^>]*class="[^"]*jobTitle[^"]*"', html_text)
    # sections[0] is preamble, sections[1..] each starts with a job
    for sec in sections[1:]:
        m_jk = _JK.search(sec[:2000])
        if not m_jk:
            continue
        m_t = _TITLE.search(sec[:1500])
        title = _clean(m_t.group(1)) if m_t else ""
        if not title:
            m_t = _TITLE_FALLBACK.search(sec[:1500])
            title = _clean(m_t.group(1)) if m_t else ""
        if not title or len(title) < 4:
            continue
        m_c = _COMPANY.search(sec[:4000])
        company = _clean(m_c.group(1)) if m_c else ""
        m_l = _LOCATION.search(sec[:4000])
        location = _clean(m_l.group(1)) if m_l else "Israel"
        out.append(
            {
                "jk": m_jk.group(1),
                "title": title,
                "company": company,
                "location": location,
            }
        )
    return out


def scrape(max_jobs: int = 150) -> List[Dict[str, Any]]:
    B.LOG.info("IndeedIL: starting (target=%d)", max_jobs)
    seen: Dict[str, Dict[str, Any]] = {}
    for query in QUERIES:
        if len(seen) >= max_jobs:
            break
        results = _search(query)
        kept_this = 0
        for r in results:
            if not B.looks_devops(r["title"]):
                continue
            jid = B.make_id("indeed_il", r["jk"])
            if jid in seen:
                continue
            seen[jid] = B.normalize_job(
                source="indeed_il",
                job_id=jid,
                title=r["title"],
                company=r["company"],
                location=r["location"] or "Israel",
                url=f"https://il.indeed.com/viewjob?jk={r['jk']}",
                posted_at="",
                description="",
                raw={"query": query, "jk": r["jk"]},
            )
            kept_this += 1
        if kept_this:
            B.LOG.info("IndeedIL: query %r -> %d kept", query, kept_this)
        B.polite_sleep((0.8, 1.5))  # polite delay for Indeed
    B.LOG.info("IndeedIL: collected %d jobs", len(seen))
    return list(seen.values())
