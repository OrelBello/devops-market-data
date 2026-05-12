"""
LinkedIn Jobs scraper using the public guest search endpoint.

LinkedIn exposes a public, unauthenticated HTML endpoint at:
  https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search

It returns a chunk of HTML per page (10 jobs/page). No login required.
This is the same endpoint LinkedIn's own public search page uses for pagination,
so it's reasonable to call politely.

If LinkedIn temporarily blocks us (rare but possible), we degrade gracefully
and return whatever we collected so far.
"""

from __future__ import annotations

import re
import urllib.parse
from typing import Any, Dict, List

from . import _base as B

GUEST_SEARCH = "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"
JOB_VIEW = "https://www.linkedin.com/jobs/view/"  # canonical short form: /jobs/view/<id> works without slug

# Geo IDs (LinkedIn's internal location codes)
GEO_ISRAEL = "101620260"

# We pull more pages for primary queries, fewer for variants
PAGES_PER_QUERY = 4  # 4 * 10 = 40 jobs per query; 6 queries -> up to 240 jobs

# Regex extraction (LinkedIn returns server-rendered list items)
# Each job is wrapped in <li>...<div class="base-card ... base-search-card job-search-card" data-entity-urn="urn:li:jobPosting:NNNN">
# We split on data-entity-urn boundaries which is the most reliable anchor.
_LI_BLOCK = re.compile(
    r'<div[^>]+class="[^"]*base-card[^"]*"[^>]+data-entity-urn="urn:li:jobPosting:\d+".*?(?=<div[^>]+class="[^"]*base-card|</body|</html|$)',
    re.DOTALL,
)
_TITLE = re.compile(r"base-search-card__title[^>]*>\s*(.*?)\s*</h3>", re.DOTALL)
_COMPANY = re.compile(
    r"base-search-card__subtitle[^>]*>(?:\s*<a[^>]*>)?\s*(.*?)\s*(?:</a>)?\s*</h4>",
    re.DOTALL,
)
_LOCATION = re.compile(r"job-search-card__location[^>]*>\s*(.*?)\s*</span>", re.DOTALL)
_LINK = re.compile(
    r'<a[^>]+class="[^"]*base-card__full-link[^"]*"[^>]+href="([^"]+)"', re.DOTALL
)
_POSTED = re.compile(r'<time[^>]+datetime="([^"]+)"', re.DOTALL)
_JOB_ID = re.compile(r'data-entity-urn="urn:li:jobPosting:(\d+)"')


def _fetch_page(
    query: str,
    geo: str,
    start: int,
    *,
    experience: str = "",
    time_filter: str = "r604800",
) -> str:
    """
    Fetch one page of LinkedIn search results.

    experience: comma-separated LinkedIn experience codes:
        1=Internship, 2=Entry-level, 3=Associate, 4=Mid-Senior, 5=Director, 6=Executive
    time_filter: r86400 (24h) | r604800 (week) | r2592000 (month) | "" (any)
    """
    params = {
        "keywords": query,
        "location": "Israel",
        "geoId": geo,
        "start": str(start),
    }
    if time_filter:
        params["f_TPR"] = time_filter
    if experience:
        params["f_E"] = experience
    url = GUEST_SEARCH + "?" + urllib.parse.urlencode(params)
    res = B.http_get(url, headers={"Referer": "https://www.linkedin.com/jobs/search/"})
    if not isinstance(res, str):
        return ""
    return res


def _parse_block(html_block: str) -> Dict[str, Any]:
    title = ""
    m = _TITLE.search(html_block)
    if m:
        title = B.strip_html(m.group(1))
    company = ""
    m = _COMPANY.search(html_block)
    if m:
        company = B.strip_html(m.group(1))
    location = ""
    m = _LOCATION.search(html_block)
    if m:
        location = B.strip_html(m.group(1))
    link = ""
    m = _LINK.search(html_block)
    if m:
        # Strip query params + unescape HTML entities (&amp; etc.)
        import html as _html

        link = _html.unescape(m.group(1).split("?")[0])
    posted = ""
    m = _POSTED.search(html_block)
    if m:
        posted = m.group(1)
    job_id = ""
    m = _JOB_ID.search(html_block)
    if m:
        job_id = m.group(1)
    return {
        "title": title,
        "company": company,
        "location": location,
        "url": link,
        "posted_at": posted,
        "job_id": job_id,
    }


def scrape(
    max_jobs: int = 250,
    *,
    experience: str = "",
    time_filter: str = "r604800",
    devops_filter: bool = True,
) -> List[Dict[str, Any]]:
    """
    Scrape jobs in Israel from LinkedIn guest API.

    experience: LinkedIn experience codes (e.g. "1,2" for entry-level + internship)
    time_filter: r604800 (week) | r2592000 (month) | "" (no time limit)
    devops_filter: if True, filter results to DevOps-ish titles. Set False when caller
                   has its own classifier (e.g. junior pipeline classifier).
    """
    B.LOG.info(
        "LinkedIn: starting (target=%d, experience=%r, time=%r, devops_filter=%s)",
        max_jobs,
        experience,
        time_filter,
        devops_filter,
    )
    seen: Dict[str, Dict[str, Any]] = {}
    for query in B.DEVOPS_QUERIES:
        for page in range(PAGES_PER_QUERY):
            if len(seen) >= max_jobs:
                break
            start = page * 10
            html_text = _fetch_page(
                query, GEO_ISRAEL, start, experience=experience, time_filter=time_filter
            )
            if not html_text:
                B.LOG.info(
                    "LinkedIn: no data for %r start=%d (likely rate-limited)",
                    query,
                    start,
                )
                break
            blocks = _LI_BLOCK.findall(html_text)
            if not blocks:
                B.LOG.info(
                    "LinkedIn: no blocks for %r start=%d (end of results)", query, start
                )
                break
            for block in blocks:
                parsed = _parse_block(block)
                if not parsed["title"]:
                    continue
                jid = parsed["job_id"] or B.make_id(
                    "linkedin", parsed["url"], parsed["title"]
                )
                key = f"linkedin:{jid}"
                if key in seen:
                    continue
                # Filter: optionally DevOps-ish, always Israel-ish
                if devops_filter and not B.looks_devops(parsed["title"]):
                    continue
                if not B.looks_israeli(parsed["location"]):
                    continue
                seen[key] = B.normalize_job(
                    source="linkedin",
                    job_id=key,
                    title=parsed["title"],
                    company=parsed["company"],
                    location=parsed["location"],
                    url=parsed["url"] or f"{JOB_VIEW}{jid}",
                    posted_at=parsed["posted_at"],
                    description="",
                    raw={"query": query, "li_job_id": parsed["job_id"]},
                )
            B.polite_sleep()
        if len(seen) >= max_jobs:
            break
    jobs = list(seen.values())
    B.LOG.info("LinkedIn: collected %d jobs", len(jobs))
    return jobs
