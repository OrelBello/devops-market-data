"""
Trend tracking: persist weekly snapshots and compute week-over-week deltas.

Each weekly run drops a snapshot into data/history/YYYY-WW.json. We compare
against the previous snapshot to compute deltas (job count change, skill demand
shifts, new top companies, etc.).
"""

from __future__ import annotations

import glob
import json
import os
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

HISTORY_DIR_DEFAULT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data",
    "history",
)


def _iso_week(dt: Optional[datetime] = None) -> str:
    dt = dt or datetime.now()
    iso = dt.isocalendar()
    return f"{iso[0]}-W{iso[1]:02d}"


def save_snapshot(stats: Dict[str, Any], history_dir: str = HISTORY_DIR_DEFAULT) -> str:
    os.makedirs(history_dir, exist_ok=True)
    week = _iso_week()
    path = os.path.join(history_dir, f"{week}.json")
    # Strip enriched_jobs to keep history files small (we only need aggregates)
    slim = {k: v for k, v in stats.items() if k != "enriched_jobs"}
    slim["week"] = week
    slim["saved_at"] = datetime.now().isoformat(timespec="seconds")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(slim, f, indent=2, ensure_ascii=False)
    return path


def load_history(history_dir: str = HISTORY_DIR_DEFAULT) -> List[Dict[str, Any]]:
    if not os.path.isdir(history_dir):
        return []
    paths = sorted(glob.glob(os.path.join(history_dir, "*.json")))
    out = []
    for p in paths:
        try:
            with open(p, "r", encoding="utf-8") as f:
                out.append(json.load(f))
        except Exception:
            continue
    return out


def compute_deltas(
    current: Dict[str, Any], previous: Optional[Dict[str, Any]]
) -> Dict[str, Any]:
    if not previous:
        return {"is_first_run": True}
    cur_total = current.get("total_jobs", 0)
    prev_total = previous.get("total_jobs", 0)
    delta_total = cur_total - prev_total
    pct = round(100 * delta_total / prev_total, 1) if prev_total else 0.0

    cur_skills = dict(current.get("top_skills", []))
    prev_skills = dict(previous.get("top_skills", []))
    skill_deltas: List[Tuple[str, int, int, int]] = []
    all_skills = set(cur_skills) | set(prev_skills)
    for s in all_skills:
        c = cur_skills.get(s, 0)
        p = prev_skills.get(s, 0)
        skill_deltas.append((s, c, p, c - p))
    rising = sorted([x for x in skill_deltas if x[3] > 0], key=lambda x: -x[3])[:5]
    falling = sorted([x for x in skill_deltas if x[3] < 0], key=lambda x: x[3])[:5]

    cur_companies = dict(current.get("top_companies", []))
    prev_companies = dict(previous.get("top_companies", []))
    new_companies = [c for c in cur_companies if c not in prev_companies][:5]

    return {
        "is_first_run": False,
        "previous_week": previous.get("week"),
        "current_week": current.get("week"),
        "total_jobs_delta": delta_total,
        "total_jobs_pct": pct,
        "junior_pct_delta": round(
            current.get("junior_pct", 0) - previous.get("junior_pct", 0), 1
        ),
        "rising_skills": rising,
        "falling_skills": falling,
        "new_top_companies": new_companies,
    }


def get_previous(history_dir: str = HISTORY_DIR_DEFAULT) -> Optional[Dict[str, Any]]:
    history = load_history(history_dir)
    if len(history) < 2:
        return history[0] if history else None
    # Most recent is current, second-to-last is previous
    return history[-2] if len(history) >= 2 else None
