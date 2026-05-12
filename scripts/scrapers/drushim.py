"""
Drushim.co.il scraper.

Drushim's public search uses URL paths like:
  https://www.drushim.co.il/jobs/search/devops/
  https://www.drushim.co.il/jobs/search/sre/
  https://www.drushim.co.il/jobs/search/cloud/

The page is server-rendered. We extract job cards via regex.
"""

from __future__ import annotations

import re
import urllib.parse
from typing import Any, Dict, List

from . import _base as B

BASE = "https://www.drushim.co.il/jobs/search/{slug}/"

QUERIES = ["devops", "sre", "platform-engineer", "cloud-engineer", "kubernetes"]

# Drushim uses Vue/Nuxt - inspect and pull from inline JSON if present
_NUXT_DATA = re.compile(r"window\.__NUXT__\s*=\s*(.*?);\s*</script>", re.DOTALL)
_JOB_LINK = re.compile(
    r'<a[^>]+href="(/job/\d+[^"]*)"[^>]*>(.*?)</a>', re.DOTALL | re.IGNORECASE
)
_TITLE_INLINE = re.compile(
    r'<span[^>]*class="[^"]*job-title[^"]*"[^>]*>(.*?)</span>',
    re.DOTALL | re.IGNORECASE,
)
_COMPANY_INLINE = re.compile(
    r'<span[^>]*class="[^"]*company[^"]*"[^>]*>(.*?)</span>', re.DOTALL | re.IGNORECASE
)


def _search(slug: str) -> List[Dict[str, Any]]:
    url = BASE.format(slug=slug)
    html_text = B.http_get(url, headers={"Referer": "https://www.drushim.co.il/"})
    if not isinstance(html_text, str):
        return []

    # Try to find embedded JSON first (most reliable)
    out: List[Dict[str, Any]] = []
    m = _NUXT_DATA.search(html_text)
    if m:
        # The NUXT payload is JS, not strict JSON. Try to extract job objects with regex.
        payload = m.group(1)
        # Look for objects with "JobTitle" / "JobNumber" / "EmployerName" patterns
        for match in re.finditer(
            r'\{\s*"JobNumber"\s*:\s*(\d+)[^}]*?"JobTitle"\s*:\s*"([^"]+)"[^}]*?"EmployerName"\s*:\s*"([^"]*)"[^}]*?"CityName"\s*:\s*"([^"]*)"',
            payload,
        ):
            job_num, title, emp, city = (
                match.group(1),
                match.group(2),
                match.group(3),
                match.group(4),
            )
            if not B.looks_devops(title):
                continue
            out.append(
                {
                    "title": title,
                    "company": emp,
                    "location": city or "Israel",
                    "url": f"https://www.drushim.co.il/job/{job_num}/",
                    "job_num": job_num,
                }
            )
        if out:
            return out

    # HTML fallback
    for href, inner in _JOB_LINK.findall(html_text):
        title = B.strip_html(inner)
        # Strip embedded HTML noise
        if "<" in title:
            tm = _TITLE_INLINE.search(inner)
            if tm:
                title = B.strip_html(tm.group(1))
        if not title or not B.looks_devops(title):
            continue
        full = "https://www.drushim.co.il" + href if href.startswith("/") else href
        out.append(
            {
                "title": title,
                "company": "",
                "location": "Israel",
                "url": full,
                "job_num": re.search(r"/job/(\d+)", href).group(1)
                if re.search(r"/job/(\d+)", href)
                else "",
            }
        )
    return out


def scrape(max_jobs: int = 150) -> List[Dict[str, Any]]:
    B.LOG.info("Drushim: starting (target=%d)", max_jobs)
    seen: Dict[str, Dict[str, Any]] = {}
    for q in QUERIES:
        if len(seen) >= max_jobs:
            break
        results = _search(q)
        B.LOG.info("Drushim: query %r -> %d candidates", q, len(results))
        for r in results:
            jid = B.make_id("drushim", r.get("job_num") or r["url"])
            if jid in seen:
                continue
            seen[jid] = B.normalize_job(
                source="drushim",
                job_id=jid,
                title=r["title"],
                company=r.get("company", ""),
                location=r.get("location", "Israel"),
                url=r["url"],
                posted_at="",
                description="",
                raw={"query": q, "job_num": r.get("job_num")},
            )
        B.polite_sleep()
    jobs = list(seen.values())
    B.LOG.info("Drushim: collected %d jobs", len(jobs))
    return jobs
