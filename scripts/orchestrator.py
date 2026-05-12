#!/usr/bin/env python3
"""
Master orchestrator for the Israeli DevOps Job Market Intelligence Platform.

Pipeline:
  1. Run every scraper (graceful failure - one source dying doesn't kill the run)
  2. Normalize + deduplicate jobs
  3. Run the analysis engine
  4. Save weekly snapshot for trend tracking
  5. Compute week-over-week deltas
  6. Generate the Markdown report + LinkedIn post draft
  7. Save:
       - data/snapshots/jobs_<timestamp>.json    (raw enriched jobs)
       - data/history/<YYYY-WW>.json             (slim aggregate snapshot)
       - reports/report_<YYYY-WW>.md             (Markdown report)
       - reports/linkedin_<YYYY-WW>.md           (LinkedIn post draft)
       - reports/latest.json                     (machine-readable handoff for OpenClaw)

The OpenClaw skill driver consumes `reports/latest.json` and pushes data to
Google Sheets via the gws-mcp tool.

Usage:
  python3 orchestrator.py [--quick] [--source SOURCE [SOURCE ...]]
    --quick    : limit each scraper to ~30 jobs (for fast testing)
    --source   : run only specific sources (e.g. --source greenhouse remoteok)
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import traceback
from datetime import datetime
from typing import Any, Callable, Dict, List, Tuple

# Ensure relative imports work whether script is run directly or imported
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

from scrapers import _base as B  # noqa: E402
from scrapers import (
    alljobs,
    drushim,
    glassdoor,
    greenhouse,
    jobmaster,
    lever,
    linkedin,
    remoteok,
)  # noqa: E402
import analysis  # noqa: E402
import normalize  # noqa: E402
import report  # noqa: E402
import trends  # noqa: E402

ROOT = os.path.dirname(SCRIPT_DIR)
DATA_DIR = os.path.join(ROOT, "data")
SNAPSHOTS_DIR = os.path.join(DATA_DIR, "snapshots")
HISTORY_DIR = os.path.join(DATA_DIR, "history")
REPORTS_DIR = os.path.join(ROOT, "reports")

# (name, callable, default_max_jobs) - order matters for the report's "by_source" table
SOURCES: List[Tuple[str, Callable[[int], List[Dict[str, Any]]], int]] = [
    ("greenhouse", greenhouse.scrape, 400),
    ("linkedin", linkedin.scrape, 250),
    ("lever", lever.scrape, 100),
    ("remoteok", remoteok.scrape, 200),
    ("alljobs", alljobs.scrape, 150),
    ("drushim", drushim.scrape, 150),
    ("jobmaster", jobmaster.scrape, 100),
    ("glassdoor", glassdoor.scrape, 80),
]


def run_scrapers(
    only: List[str] = None, quick: bool = False
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    only = only or []
    all_jobs: List[Dict[str, Any]] = []
    diagnostics: Dict[str, Any] = {}
    for name, fn, default_max in SOURCES:
        if only and name not in only:
            continue
        max_jobs = 30 if quick else default_max
        t0 = time.time()
        try:
            B.LOG.info(">>> Running scraper: %s (max=%d)", name, max_jobs)
            jobs = fn(max_jobs=max_jobs) or []
            elapsed = time.time() - t0
            diagnostics[name] = {
                "jobs": len(jobs),
                "elapsed_s": round(elapsed, 1),
                "ok": True,
            }
            B.LOG.info("<<< %s: %d jobs in %.1fs", name, len(jobs), elapsed)
            all_jobs.extend(jobs)
        except Exception as e:  # noqa: BLE001
            elapsed = time.time() - t0
            tb = traceback.format_exc(limit=3)
            diagnostics[name] = {
                "jobs": 0,
                "elapsed_s": round(elapsed, 1),
                "ok": False,
                "error": str(e),
            }
            B.LOG.error("Scraper %s failed: %s\n%s", name, e, tb)
    return all_jobs, diagnostics


def main():
    parser = argparse.ArgumentParser(
        description="Israeli DevOps Job Market Intelligence Platform"
    )
    parser.add_argument(
        "--quick", action="store_true", help="Quick run (max 30 jobs per source)"
    )
    parser.add_argument(
        "--source", nargs="+", help="Only run these sources", default=None
    )
    parser.add_argument(
        "--no-history", action="store_true", help="Don't save snapshot to history"
    )
    parser.add_argument(
        "--sheet-url", default="", help="Optional Google Sheets URL to embed in report"
    )
    args = parser.parse_args()

    os.makedirs(SNAPSHOTS_DIR, exist_ok=True)
    os.makedirs(HISTORY_DIR, exist_ok=True)
    os.makedirs(REPORTS_DIR, exist_ok=True)

    started = datetime.now()
    B.LOG.info("=" * 80)
    B.LOG.info(
        "Israeli DevOps Job Market Intelligence Platform - run started %s",
        started.isoformat(timespec="seconds"),
    )
    B.LOG.info(
        "Mode: %s | Sources: %s",
        "QUICK" if args.quick else "FULL",
        args.source or "ALL",
    )
    B.LOG.info("=" * 80)

    # 1-2. Scrape + dedupe
    raw_jobs, diagnostics = run_scrapers(only=args.source, quick=args.quick)
    B.LOG.info("Raw jobs collected: %d", len(raw_jobs))
    deduped = normalize.dedup(raw_jobs)
    B.LOG.info(
        "After dedup: %d jobs (%d duplicates removed)",
        len(deduped),
        len(raw_jobs) - len(deduped),
    )

    # 2.5. First-seen tagging (for "new in last 24h" feature)
    import seen_jobs as _sj  # noqa: WPS433

    seen_path = os.path.join(DATA_DIR, "seen_jobs.json")
    seen = _sj.load_seen(seen_path)
    deduped, new_today_count = _sj.stamp_jobs(deduped, seen)
    seen = _sj.prune_old(seen, max_age_days=90)
    _sj.save_seen(seen_path, seen)
    new_in_last_24h = _sj.filter_recent(deduped, within_days=1)
    new_in_last_7d = _sj.filter_recent(deduped, within_days=7)
    B.LOG.info(
        "First-seen tagging: %d new today, %d new in last 7d, %d total tracked",
        new_today_count,
        len(new_in_last_7d),
        len(seen),
    )

    # 3. Analyze
    stats = analysis.analyze(deduped)
    stats["week"] = datetime.now().isocalendar()
    week_str = f"{stats['week'][0]}-W{stats['week'][1]:02d}"
    stats["week"] = week_str
    stats["diagnostics"] = diagnostics
    stats["raw_count"] = len(raw_jobs)
    stats["deduped_count"] = len(deduped)

    # 4. Save raw enriched snapshot
    ts = started.strftime("%Y%m%d_%H%M%S")
    snapshot_path = os.path.join(SNAPSHOTS_DIR, f"jobs_{ts}.json")
    with open(snapshot_path, "w", encoding="utf-8") as f:
        json.dump(
            {
                "started_at": started.isoformat(),
                "jobs": stats["enriched_jobs"],
                "stats": {k: v for k, v in stats.items() if k != "enriched_jobs"},
            },
            f,
            indent=2,
            ensure_ascii=False,
        )
    B.LOG.info("Saved raw snapshot: %s", snapshot_path)

    # 5. Save weekly history aggregate (and load previous for delta)
    previous = None
    if not args.no_history:
        history = trends.load_history(HISTORY_DIR)
        # the most recent matches current week if we already saved this week - use the one before
        if history:
            previous = (
                history[-1]
                if history[-1].get("week") != week_str
                else (history[-2] if len(history) >= 2 else None)
            )
        history_path = trends.save_snapshot(stats, HISTORY_DIR)
        B.LOG.info("Saved history snapshot: %s", history_path)

    deltas = trends.compute_deltas(stats, previous)

    # 6. Generate reports
    md = report.render_markdown(stats, deltas, week=week_str, sheet_url=args.sheet_url)
    li = report.render_linkedin_post(stats, deltas, sheet_url=args.sheet_url)

    md_path = os.path.join(REPORTS_DIR, f"report_{week_str}.md")
    li_path = os.path.join(REPORTS_DIR, f"linkedin_{week_str}.md")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(md)
    with open(li_path, "w", encoding="utf-8") as f:
        f.write(li)
    B.LOG.info("Saved Markdown report: %s", md_path)
    B.LOG.info("Saved LinkedIn draft:  %s", li_path)

    # 7. Machine-readable handoff for OpenClaw skill
    latest = {
        "generated_at": started.isoformat(timespec="seconds"),
        "week": week_str,
        "stats_for_sheets": {
            "total_jobs": stats["total_jobs"],
            "by_source": stats["by_source"],
            "top_skills": stats["top_skills"],
            "top_companies": stats["top_companies"],
            "seniority_distribution": stats["seniority_distribution"],
            "seniority_pct": stats["seniority_pct"],
            "junior_pct": stats["junior_pct"],
            "junior_count": stats["junior_count"],
            "junior_friendly_companies": stats.get("junior_friendly_companies", []),
            "location_distribution": stats["location_distribution"],
            "salary_summary": stats["salary_summary"],
            "salary_disclosure_rate": stats.get("salary_disclosure_rate", 0.0),
            "top_hiring_strength": stats.get("top_hiring_strength", []),
            "company_hiring_strength": stats.get("company_hiring_strength", {}),
            # NEW: daily cadence metrics
            "new_count_24h": new_today_count,
            "new_count_7d": len(new_in_last_7d),
            "total_tracked_jobs": len(seen),
        },
        "deltas": deltas,
        "diagnostics": diagnostics,
        "report_md_path": md_path,
        "linkedin_md_path": li_path,
        "snapshot_path": snapshot_path,
        # Slim job rows (for the "All Jobs" tab in the dashboard)
        "jobs_for_sheet": [
            {
                "title": j["title"],
                "company": j["company"],
                "location": j["location"],
                "location_bucket": j.get("location_bucket"),
                "seniority": j.get("seniority"),
                "skills": ", ".join(j.get("skills_extracted", []) or []),
                "source": j["source"],
                "url": j["url"],
                "posted_at": j.get("posted_at", ""),
                "first_seen_at": j.get("first_seen_at", ""),
                "is_new_today": j.get("is_new_today", False),
                "days_open": j.get("days_open", 0),
            }
            for j in stats["enriched_jobs"]
        ],
        # NEW: ready-to-render slim list of "new today"
        "new_in_last_24h": [
            {
                "title": j["title"],
                "company": j.get("company", ""),
                "location": j.get("location", ""),
                "seniority": j.get("seniority", ""),
                "skills": ", ".join(j.get("skills_extracted", []) or []),
                "source": j["source"],
                "url": j.get("url", ""),
                "first_seen_at": j.get("first_seen_at", ""),
            }
            for j in new_in_last_24h
        ],
        # And the wider 7-day window for "fresh this week"
        "new_in_last_7d": [
            {
                "title": j["title"],
                "company": j.get("company", ""),
                "location": j.get("location", ""),
                "seniority": j.get("seniority", ""),
                "skills": ", ".join(j.get("skills_extracted", []) or []),
                "source": j["source"],
                "url": j.get("url", ""),
                "first_seen_at": j.get("first_seen_at", ""),
            }
            for j in new_in_last_7d
        ],
    }
    latest_path = os.path.join(REPORTS_DIR, "latest.json")
    with open(latest_path, "w", encoding="utf-8") as f:
        json.dump(latest, f, indent=2, ensure_ascii=False)
    B.LOG.info("Saved latest.json:     %s", latest_path)

    # 8. Generate static HTML landing page (deploy-ready)
    try:
        import landing_page as _lp  # noqa: WPS433

        _lp.render()
        B.LOG.info("Saved index.html landing page")
    except Exception as e:  # noqa: BLE001
        B.LOG.warning("Landing page generation skipped: %s", e)

    # ---- Summary ----
    print("\n" + "=" * 80)
    print(f"  RUN COMPLETE — Week {week_str}")
    print("=" * 80)
    print(f"  Total jobs (deduped): {stats['total_jobs']}")
    print(f"  🔥 New today:         {new_today_count}")
    print(f"  📅 New last 7d:       {len(new_in_last_7d)}")
    print(
        f"  Junior-friendly:      {stats['junior_count']} ({stats['junior_pct']:.1f}%)"
    )
    print(
        f"  Sources active:       {sum(1 for d in diagnostics.values() if d.get('ok') and d.get('jobs', 0) > 0)} / {len(diagnostics)}"
    )
    print()
    print("  Top 5 skills:")
    for name, count in stats["top_skills"][:5]:
        print(f"    - {name}: {count}")
    print()
    print("  Top 5 companies:")
    for name, count in stats["top_companies"][:5]:
        print(f"    - {name}: {count}")
    print()
    print(f"  📄 Report:    {md_path}")
    print(f"  📱 LinkedIn:  {li_path}")
    print(f"  📦 Snapshot:  {snapshot_path}")
    print(f"  📊 Handoff:   {latest_path}")
    print("=" * 80)
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
