"""
AllJobs.co.il scraper - one of Israel's largest job boards.

AllJobs has a public search page at:
  https://www.alljobs.co.il/SearchResultsGuest.aspx?...
  https://www.alljobs.co.il/Search.aspx?...

The site is HTML-only with predictable structure. We scrape DevOps-related
keywords and parse the result list.

If AllJobs blocks our user-agent, this returns [] gracefully.
"""

from __future__ import annotations

import re
import urllib.parse
from typing import Any, Dict, List

from . import _base as B

# AllJobs has separate pages for each occupation. Code 41 = DevOps/IT/SysAdmin family.
# We use the public search page with keyword filtering.
SEARCH_URL = "https://www.alljobs.co.il/SearchResultsGuest.aspx"

QUERIES = ["DevOps", "SRE", "Platform", "Cloud", "Kubernetes", "סיסטם"]

# Job item pattern - AllJobs wraps each result in a div with class="job-content-top"
# We use a tolerant regex that matches the public guest page structure.
_JOB_BLOCK = re.compile(
    r'<div[^>]*?class="[^"]*job-content[^"]*"[^>]*>(.*?)(?=<div[^>]*?class="[^"]*job-content[^"]*"|<footer|</main)',
    re.DOTALL | re.IGNORECASE,
)
_TITLE = re.compile(
    r'<a[^>]*?class="[^"]*JobTitleTitle[^"]*"[^>]*>(.*?)</a>', re.DOTALL | re.IGNORECASE
)
_TITLE_FALLBACK = re.compile(r"<h3[^>]*>(.*?)</h3>", re.DOTALL | re.IGNORECASE)
_HREF = re.compile(
    r'<a[^>]*?class="[^"]*JobTitleTitle[^"]*"[^>]+href="([^"]+)"', re.IGNORECASE
)
_HREF_FALLBACK = re.compile(r'href="([^"]*JobItem[^"]*)"', re.IGNORECASE)
_COMPANY = re.compile(
    r'<div[^>]*?class="[^"]*JobTitleSpan[^"]*"[^>]*>(.*?)</div>',
    re.DOTALL | re.IGNORECASE,
)
_LOCATION = re.compile(
    r'<div[^>]*?class="[^"]*location[^"]*"[^>]*>(.*?)</div>', re.DOTALL | re.IGNORECASE
)


def _search(query: str) -> List[Dict[str, Any]]:
    params = {
        "page": "1",
        "freetext": query,
        "region": "2",
    }  # region 2 = central Israel
    url = SEARCH_URL + "?" + urllib.parse.urlencode(params)
    html_text = B.http_get(url, headers={"Referer": "https://www.alljobs.co.il/"})
    if not isinstance(html_text, str):
        return []
    blocks = _JOB_BLOCK.findall(html_text)
    if not blocks:
        # Try a more permissive split
        blocks = re.findall(
            r"<article[^>]*>(.*?)</article>", html_text, re.DOTALL | re.IGNORECASE
        )
    out = []
    for blk in blocks:
        title = ""
        m = _TITLE.search(blk) or _TITLE_FALLBACK.search(blk)
        if m:
            title = B.strip_html(m.group(1))
        if not title:
            continue
        href = ""
        m = _HREF.search(blk) or _HREF_FALLBACK.search(blk)
        if m:
            href = m.group(1)
            if href.startswith("/"):
                href = "https://www.alljobs.co.il" + href
            elif not href.startswith("http"):
                href = "https://www.alljobs.co.il/" + href
        company = ""
        m = _COMPANY.search(blk)
        if m:
            company = B.strip_html(m.group(1))
        location = ""
        m = _LOCATION.search(blk)
        if m:
            location = B.strip_html(m.group(1))
        if not B.looks_devops(title):
            continue
        out.append(
            {
                "title": title,
                "company": company,
                "location": location or "Israel",
                "url": href,
                "query": query,
            }
        )
    return out


def scrape(max_jobs: int = 150) -> List[Dict[str, Any]]:
    B.LOG.info("AllJobs: starting (target=%d)", max_jobs)
    seen: Dict[str, Dict[str, Any]] = {}
    for q in QUERIES:
        if len(seen) >= max_jobs:
            break
        results = _search(q)
        B.LOG.info("AllJobs: query %r -> %d candidates", q, len(results))
        for r in results:
            jid = B.make_id("alljobs", r["url"] or r["title"])
            if jid in seen:
                continue
            seen[jid] = B.normalize_job(
                source="alljobs",
                job_id=jid,
                title=r["title"],
                company=r["company"],
                location=r["location"],
                url=r["url"],
                posted_at="",
                description="",
                raw={"query": r["query"]},
            )
        B.polite_sleep()
    jobs = list(seen.values())
    B.LOG.info("AllJobs: collected %d jobs", len(jobs))
    return jobs
