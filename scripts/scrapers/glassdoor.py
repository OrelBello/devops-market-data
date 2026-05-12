"""
Glassdoor (Israel) scraper.

Glassdoor has aggressive bot detection but their sitemap-driven job search
(https://www.glassdoor.com/Job/israel-devops-engineer-jobs-SRCH_IL.0,6_IN119_KO7,22.htm)
is reachable for a few requests at a time.

We're conservative here: a few queries, single page each, and we always
return whatever we got. If Glassdoor blocks us entirely the pipeline keeps working.
"""

from __future__ import annotations

import re
import urllib.parse
from typing import Any, Dict, List

from . import _base as B

# Glassdoor's Israel country IL_IN119, with SRCH_IL.0,6 country tag
QUERIES = [
    ("devops engineer", 7, 22),
    ("site reliability engineer", 7, 32),
    ("platform engineer", 7, 24),
    ("cloud engineer", 7, 21),
]
BASE = (
    "https://www.glassdoor.com/Job/israel-{slug}-jobs-SRCH_IL.0,6_IN119_KO7,{end}.htm"
)

_JOB_LI = re.compile(
    r'<li[^>]*?data-test="jobListing"[^>]*>(.*?)</li>', re.DOTALL | re.IGNORECASE
)
_TITLE = re.compile(r'data-test="job-title"[^>]*>(.*?)</', re.DOTALL | re.IGNORECASE)
_COMPANY = re.compile(
    r'data-test="employer-name"[^>]*>(.*?)</', re.DOTALL | re.IGNORECASE
)
_LOCATION = re.compile(
    r'data-test="emp-location"[^>]*>(.*?)</', re.DOTALL | re.IGNORECASE
)
_LINK = re.compile(r'href="(/job-listing/[^"]+)"', re.IGNORECASE)


def _slug(q: str) -> str:
    return q.replace(" ", "-").lower()


def _search(query: str, kw_start: int, kw_end: int) -> List[Dict[str, Any]]:
    url = BASE.format(slug=_slug(query), end=kw_end)
    html_text = B.http_get(url, headers={"Referer": "https://www.glassdoor.com/"})
    if not isinstance(html_text, str):
        return []
    blocks = _JOB_LI.findall(html_text)
    if not blocks:
        return []
    out = []
    for blk in blocks:
        title = ""
        m = _TITLE.search(blk)
        if m:
            title = B.strip_html(m.group(1))
        company = ""
        m = _COMPANY.search(blk)
        if m:
            company = B.strip_html(m.group(1))
        location = "Israel"
        m = _LOCATION.search(blk)
        if m:
            location = B.strip_html(m.group(1)) or "Israel"
        link = ""
        m = _LINK.search(blk)
        if m:
            link = "https://www.glassdoor.com" + m.group(1)
        if not title or not B.looks_devops(title):
            continue
        out.append(
            {
                "title": title,
                "company": company,
                "location": location,
                "url": link,
            }
        )
    return out


def scrape(max_jobs: int = 80) -> List[Dict[str, Any]]:
    B.LOG.info("Glassdoor: starting (target=%d)", max_jobs)
    seen: Dict[str, Dict[str, Any]] = {}
    for q, ks, ke in QUERIES:
        if len(seen) >= max_jobs:
            break
        results = _search(q, ks, ke)
        B.LOG.info("Glassdoor: query %r -> %d candidates", q, len(results))
        for r in results:
            jid = B.make_id("glassdoor", r["url"] or r["title"])
            if jid in seen:
                continue
            seen[jid] = B.normalize_job(
                source="glassdoor",
                job_id=jid,
                title=r["title"],
                company=r["company"],
                location=r["location"],
                url=r["url"],
                raw={"query": q},
            )
        B.polite_sleep((1.0, 2.5))  # extra polite for Glassdoor
    jobs = list(seen.values())
    B.LOG.info("Glassdoor: collected %d jobs", len(jobs))
    return jobs
