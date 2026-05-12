"""
Jobmaster.co.il scraper.

Search URL pattern:
  https://www.jobmaster.co.il/jobs/?q=devops&loc=israel
  https://www.jobmaster.co.il/job-search/?keywords=devops

Best-effort HTML scraper. If Jobmaster changes their markup, this returns []
gracefully and the rest of the pipeline still works.
"""

from __future__ import annotations

import re
import urllib.parse
from typing import Any, Dict, List

from . import _base as B

SEARCH = "https://www.jobmaster.co.il/jobs"
QUERIES = ["devops", "sre", "kubernetes", "cloud", "platform engineer", "סיסטם"]

_JOB_TILE = re.compile(
    r'<a[^>]+href="(/jobs/[^"]+)"[^>]*>(.*?)</a>', re.DOTALL | re.IGNORECASE
)
_TITLE_RE = re.compile(
    r'<(?:h[23]|span|div)[^>]*?class="[^"]*(?:title|job-name)[^"]*"[^>]*>(.*?)</',
    re.DOTALL | re.IGNORECASE,
)
_COMPANY_RE = re.compile(
    r'class="[^"]*(?:company|employer)[^"]*"[^>]*>(.*?)</', re.DOTALL | re.IGNORECASE
)
_LOCATION_RE = re.compile(
    r'class="[^"]*(?:location|area)[^"]*"[^>]*>(.*?)</', re.DOTALL | re.IGNORECASE
)


def _search(query: str) -> List[Dict[str, Any]]:
    url = f"{SEARCH}/?q={urllib.parse.quote(query)}"
    html_text = B.http_get(url, headers={"Referer": "https://www.jobmaster.co.il/"})
    if not isinstance(html_text, str):
        return []
    out: List[Dict[str, Any]] = []
    seen_hrefs = set()
    for href, inner in _JOB_TILE.findall(html_text):
        if href in seen_hrefs:
            continue
        seen_hrefs.add(href)
        # Skip obvious nav links
        if href.count("/") < 3 or "/jobs/category" in href or "/jobs/area" in href:
            continue
        title = ""
        tm = _TITLE_RE.search(inner)
        if tm:
            title = B.strip_html(tm.group(1))
        if not title:
            title = B.strip_html(inner)[:160]
        if not title or len(title) < 5:
            continue
        if not B.looks_devops(title):
            continue
        company = ""
        cm = _COMPANY_RE.search(inner)
        if cm:
            company = B.strip_html(cm.group(1))
        location = "Israel"
        lm = _LOCATION_RE.search(inner)
        if lm:
            location = B.strip_html(lm.group(1)) or "Israel"
        full = href if href.startswith("http") else "https://www.jobmaster.co.il" + href
        out.append(
            {
                "title": title,
                "company": company,
                "location": location,
                "url": full,
                "query": query,
            }
        )
    return out


def scrape(max_jobs: int = 100) -> List[Dict[str, Any]]:
    B.LOG.info("Jobmaster: starting (target=%d)", max_jobs)
    seen: Dict[str, Dict[str, Any]] = {}
    for q in QUERIES:
        if len(seen) >= max_jobs:
            break
        results = _search(q)
        B.LOG.info("Jobmaster: query %r -> %d candidates", q, len(results))
        for r in results:
            jid = B.make_id("jobmaster", r["url"])
            if jid in seen:
                continue
            seen[jid] = B.normalize_job(
                source="jobmaster",
                job_id=jid,
                title=r["title"],
                company=r["company"],
                location=r["location"],
                url=r["url"],
                description="",
                raw={"query": r["query"]},
            )
        B.polite_sleep()
    jobs = list(seen.values())
    B.LOG.info("Jobmaster: collected %d jobs", len(jobs))
    return jobs
