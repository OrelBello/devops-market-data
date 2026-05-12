"""
Job normalization and deduplication.

Same job often appears on multiple boards (e.g. a JFrog DevOps role on
LinkedIn AND on JFrog's Greenhouse board). We dedup by (lowercased title,
lowercased company) - this catches the common case while preserving genuine
variants (e.g. Junior vs Senior at same company).
"""

from __future__ import annotations

import re
from typing import Dict, List


def _norm_str(s: str) -> str:
    s = (s or "").lower()
    s = re.sub(r"\s+", " ", s)
    s = re.sub(r"[^a-z0-9 ]+", "", s)
    return s.strip()


def dedup(jobs: List[Dict]) -> List[Dict]:
    """
    Return a deduplicated job list. When duplicates are found, prefer the source
    in this priority order: greenhouse > linkedin > alljobs > drushim > glassdoor > jobmaster > remoteok.
    Greenhouse data is the most reliable.
    """
    priority = {
        "greenhouse": 0,
        "linkedin": 1,
        "alljobs": 2,
        "drushim": 3,
        "glassdoor": 4,
        "jobmaster": 5,
        "remoteok": 6,
    }
    by_key: Dict[str, Dict] = {}
    for job in jobs:
        title_n = _norm_str(job.get("title", ""))
        company_n = _norm_str(job.get("company", ""))
        # Skip junk
        if not title_n or len(title_n) < 4:
            continue
        key = f"{company_n}::{title_n}" if company_n else f"::{title_n}"
        existing = by_key.get(key)
        if existing is None or priority.get(job.get("source"), 99) < priority.get(
            existing.get("source"), 99
        ):
            by_key[key] = job
    return list(by_key.values())
