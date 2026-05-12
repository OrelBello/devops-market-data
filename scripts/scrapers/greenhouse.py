"""
Greenhouse-based scraper for Israeli tech companies that publish DevOps jobs publicly.

Many Israeli tech companies (Wix, Monday, Lemonade, JFrog, Riskified, Lightricks,
Melio, Fiverr, Gong, Verbit, etc.) host their careers page on Greenhouse and expose
a public JSON API:
  https://boards-api.greenhouse.io/v1/boards/{board}/jobs?content=true

This is a 100% legitimate, documented public API - no scraping concerns.
We filter to DevOps/SRE/Platform roles in Israel.

This source is GOLD: it's stable, fast, and the data quality is excellent.
"""

from __future__ import annotations

from typing import Any, Dict, List

from . import _base as B

# Greenhouse board tokens for major Israeli tech employers.
# These have all been verified to return real job data via the public API.
# Adding more is easy: just append the company's Greenhouse board token.
ISRAELI_BOARDS = [
    # Israeli HQ - large (100+ open roles)
    "nice",  # 400+ jobs, NICE Systems
    "unity3d",  # 200+, Unity (significant IL R&D)
    "workato",  # 170+
    "via",  # 150+
    "payoneer",  # 140+, Israeli HQ
    "jfrog",  # 110+
    "gongio",  # 100+
    # Israeli HQ - mid-size
    "taboola",  # 77+
    "similarweb",  # 78+
    "appsflyer",  # 58+
    "forter",  # 46+
    "torq",  # 45+
    "yotpo",  # 41+
    "riskified",  # 33+
    "axonius",  # 33+
    "transmitsecurity",
    "lightricks",
    "melio",
    # Israeli HQ - smaller / specialty
    "orcasecurity",
    "optimove",
    "saltsecurity",
    "sisense",
    "pagaya",
    "bringg",
    "cybereason",
    "spotter",
    "clutch",
    # International with major Israel R&D centers (filtered to IL location)
    "elastic",
]

API = "https://boards-api.greenhouse.io/v1/boards/{board}/jobs?content=true"


def _is_israel(loc: str) -> bool:
    return B.looks_israeli(loc or "")


def scrape(max_jobs: int = 400) -> List[Dict[str, Any]]:
    B.LOG.info("Greenhouse: scanning %d Israeli tech boards", len(ISRAELI_BOARDS))
    seen: Dict[str, Dict[str, Any]] = {}
    boards_with_data = 0
    for board in ISRAELI_BOARDS:
        if len(seen) >= max_jobs:
            break
        url = API.format(board=board)
        data = B.http_get(url, expect_json=True, max_retries=1)
        if not isinstance(data, dict) or "jobs" not in data:
            continue
        jobs = data.get("jobs") or []
        if not jobs:
            continue
        boards_with_data += 1
        company_name = board.replace("-", " ").title()
        added_for_board = 0
        for j in jobs:
            title = j.get("title") or ""
            location = (j.get("location") or {}).get("name") or ""
            content = j.get("content") or ""
            if not B.looks_devops(title, content):
                continue
            if not _is_israel(location):
                # If location is empty, fall back to content
                if not B.looks_israeli(location, content):
                    continue
            jid = B.make_id("greenhouse", board, j.get("id"))
            if jid in seen:
                continue
            seen[jid] = B.normalize_job(
                source="greenhouse",
                job_id=jid,
                title=title,
                company=company_name,
                location=location or "Israel",
                url=j.get("absolute_url") or "",
                posted_at=j.get("updated_at") or "",
                description=content,
                raw={"board": board, "gh_id": j.get("id")},
            )
            added_for_board += 1
        if added_for_board:
            B.LOG.info("Greenhouse: %s -> %d DevOps roles", board, added_for_board)
        B.polite_sleep((0.2, 0.5))
    B.LOG.info(
        "Greenhouse: %d boards had data, total %d jobs", boards_with_data, len(seen)
    )
    return list(seen.values())
