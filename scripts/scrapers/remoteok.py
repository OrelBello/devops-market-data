"""
RemoteOK scraper - their JSON feed is officially public and unlimited.
We filter to DevOps roles that accept Israeli candidates (or are global remote).
"""

from __future__ import annotations

from typing import Any, Dict, List

from . import _base as B

API_URL = "https://remoteok.com/api"


def _accepts_israel(job: Dict[str, Any]) -> bool:
    # RemoteOK uses "location" string + "tags". Worldwide / Anywhere usually means OK for Israel.
    loc = (job.get("location") or "").lower()
    if not loc:
        return True
    bad = [
        "us only",
        "usa only",
        "united states only",
        "americas only",
        "uk only",
        "canada only",
    ]
    if any(b in loc for b in bad):
        return False
    return True


def scrape(max_jobs: int = 200) -> List[Dict[str, Any]]:
    B.LOG.info("RemoteOK: starting (target=%d)", max_jobs)
    data = B.http_get(API_URL, expect_json=True)
    if not isinstance(data, list):
        B.LOG.info("RemoteOK: bad response shape")
        return []
    out: List[Dict[str, Any]] = []
    for entry in data:
        # First element is metadata (legal disclaimer); skip non-job dicts
        if not isinstance(entry, dict) or "position" not in entry:
            continue
        title = entry.get("position") or ""
        desc = entry.get("description") or ""
        if not B.looks_devops(title, desc):
            continue
        if not _accepts_israel(entry):
            continue
        slug = entry.get("slug") or entry.get("id")
        url = entry.get("url") or f"https://remoteok.com/remote-jobs/{slug}"
        out.append(
            B.normalize_job(
                source="remoteok",
                job_id=B.make_id("remoteok", entry.get("id"), slug),
                title=title,
                company=entry.get("company") or "",
                location=entry.get("location") or "Remote",
                url=url,
                posted_at=entry.get("date") or "",
                description=desc,
                raw={
                    "tags": entry.get("tags") or [],
                    "salary_min": entry.get("salary_min"),
                    "salary_max": entry.get("salary_max"),
                },
            )
        )
        if len(out) >= max_jobs:
            break
    B.LOG.info("RemoteOK: collected %d jobs", len(out))
    return out
