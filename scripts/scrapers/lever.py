"""
Lever scraper - public REST API for companies hosted on Lever.

API: GET https://api.lever.co/v0/postings/<site>?mode=json
Returns a list of job objects with location, team, description, salaryRange, etc.

Lever is less common in Israeli tech than Greenhouse, but companies like
Contentsquare, Mendix, and a few mid-market shops use it. This scraper is a
small-but-real boost. Failures are silent.
"""

from __future__ import annotations

from typing import Any, Dict, List

from . import _base as B

# Verified Lever boards with at least some Israeli or remote-OK presence
LEVER_BOARDS = [
    "contentsquare",
    "mendix",
    # add more as discovered
]


def scrape(max_jobs: int = 100) -> List[Dict[str, Any]]:
    B.LOG.info("Lever: scanning %d boards", len(LEVER_BOARDS))
    out: Dict[str, Dict[str, Any]] = {}
    for site in LEVER_BOARDS:
        if len(out) >= max_jobs:
            break
        url = f"https://api.lever.co/v0/postings/{site}?mode=json"
        data = B.http_get(url, expect_json=True, max_retries=1)
        if not isinstance(data, list):
            continue
        added = 0
        for j in data:
            if not isinstance(j, dict):
                continue
            title = j.get("text") or ""
            cats = j.get("categories") or {}
            location = cats.get("location") or ""
            description = j.get("descriptionPlain") or j.get("description") or ""
            workplace = j.get("workplaceType") or ""
            if not B.looks_devops(title, description):
                continue
            # Israel filter: require IL location or remote with no exclusion
            if not B.looks_israeli(location, description):
                if workplace != "remote":
                    continue
            jid = B.make_id("lever", site, j.get("id"))
            if jid in out:
                continue
            out[jid] = B.normalize_job(
                source="lever",
                job_id=jid,
                title=title,
                company=site.replace("-", " ").title(),
                location=location or "Israel",
                url=j.get("hostedUrl") or j.get("applyUrl") or "",
                posted_at=str(j.get("createdAt") or ""),
                description=description,
                raw={
                    "site": site,
                    "team": cats.get("team"),
                    "department": cats.get("department"),
                    "commitment": cats.get("commitment"),
                    "salaryRange": j.get("salaryRange"),
                    "workplaceType": workplace,
                },
            )
            added += 1
        if added:
            B.LOG.info("Lever: %s -> %d roles", site, added)
        B.polite_sleep((0.3, 0.7))
    B.LOG.info("Lever: collected %d jobs", len(out))
    return list(out.values())
