"""
First-seen tracking for jobs.

Each run loads a persistent JSON file that maps job_id -> first_seen_iso_date.
For each job in the current run:
  - If id is already in the map: use the stored date
  - If id is NEW: stamp with today's date

This lets us answer "which jobs are new in the last 24h / 7d / 30d?".

Two separate persistence files:
  data/seen_jobs.json       - main DevOps market
  data/seen_jr_jobs.json    - junior pipeline

Both are committed to the repo so the GitHub Action's state survives across runs.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple


def _today_iso() -> str:
    """ISO date in UTC (matches what the GitHub Action uses)."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def load_seen(path: str) -> Dict[str, str]:
    """Load the seen-jobs map. Returns empty dict if file doesn't exist."""
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return data
        return {}
    except (json.JSONDecodeError, OSError):
        return {}


def save_seen(path: str, seen: Dict[str, str]) -> None:
    """Persist the seen-jobs map. Pretty-printed for readable diffs."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    # Sort keys for stable diffs
    ordered = dict(sorted(seen.items()))
    with open(path, "w", encoding="utf-8") as f:
        json.dump(ordered, f, indent=2, ensure_ascii=False)


def stamp_jobs(
    jobs: List[Dict[str, Any]],
    seen: Dict[str, str],
    *,
    today: Optional[str] = None,
) -> Tuple[List[Dict[str, Any]], int]:
    """
    Add `first_seen_at` and `is_new_today` fields to each job.
    Updates the `seen` dict in place with newly-discovered IDs.
    Returns (updated_jobs, new_today_count).
    """
    today = today or _today_iso()
    new_count = 0
    for job in jobs:
        jid = job.get("id") or job.get("job_id") or job.get("url")
        if not jid:
            # Fallback: construct from title+company (less reliable but never crashes)
            jid = f"{job.get('source', '?')}::{job.get('title', '')}::{job.get('company', '')}"
        if jid not in seen:
            seen[jid] = today
            job["first_seen_at"] = today
            job["is_new_today"] = True
            new_count += 1
        else:
            job["first_seen_at"] = seen[jid]
            job["is_new_today"] = seen[jid] == today
        # How many days since first seen?
        try:
            seen_dt = datetime.strptime(seen[jid], "%Y-%m-%d").replace(
                tzinfo=timezone.utc
            )
            today_dt = datetime.strptime(today, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            job["days_open"] = max(0, (today_dt - seen_dt).days)
        except ValueError:
            job["days_open"] = 0
    return jobs, new_count


def filter_recent(
    jobs: List[Dict[str, Any]],
    *,
    within_days: int = 1,
    today: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    Return jobs first seen within the last N calendar days.
    within_days=1 => only today (jobs first seen today)
    within_days=7 => today + previous 6 calendar days
    """
    today = today or _today_iso()
    today_dt = datetime.strptime(today, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    # Inclusive cutoff: first_seen >= (today - (within_days - 1) days)
    cutoff = today_dt - timedelta(days=max(0, within_days - 1))
    out = []
    for j in jobs:
        try:
            d = datetime.strptime(j.get("first_seen_at", ""), "%Y-%m-%d").replace(
                tzinfo=timezone.utc
            )
            if d >= cutoff:
                out.append(j)
        except ValueError:
            continue
    return out


def prune_old(seen: Dict[str, str], *, max_age_days: int = 90) -> Dict[str, str]:
    """
    Remove entries older than max_age_days. Keeps the seen-jobs file small.
    A job that was last seen 90+ days ago is almost certainly closed; if it
    reappears, we treat it as new (which is fine).
    """
    today = _today_iso()
    today_dt = datetime.strptime(today, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    cutoff = today_dt - timedelta(days=max_age_days)
    pruned = {}
    for jid, first_seen in seen.items():
        try:
            d = datetime.strptime(first_seen, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            if d > cutoff:
                pruned[jid] = first_seen
        except ValueError:
            continue
    return pruned
