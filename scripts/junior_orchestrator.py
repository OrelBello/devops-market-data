#!/usr/bin/env python3
"""
Junior Pipeline Orchestrator
============================

Scrapes the same Israeli job boards as devops-market-intel, but applies the
junior-pipeline classifier to find:
  - Entry-level / IT / Help Desk / Support / SysAdmin / Junior DevOps roles
  - That mention REAL DevOps stack tech (Linux, Python, CI/CD, AWS, Docker, K8s...)

Output:
  - reports/jr_report_<YYYY-WW>.md       Markdown report
  - reports/jr_linkedin_<YYYY-WW>.md     LinkedIn post tuned for mentees
  - reports/jr_latest.json               Machine-readable handoff
  - data/snapshots/jr_jobs_<ts>.json     Full snapshot
  - data/history/<YYYY-WW>.json          Slim aggregate for trend tracking

This script REUSES the scraper modules from devops-market-intel by adding
its parent skill's scripts directory to sys.path. No code duplication.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import traceback
from collections import Counter
from datetime import datetime
from typing import Any, Dict, List, Tuple

# Wire up imports — scrapers package lives in this same scripts/ folder
HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
# Backwards compat: if running from the original sibling-skill layout, also try that path
PARENT_SKILL = os.path.normpath(
    os.path.join(HERE, "..", "..", "devops-market-intel", "scripts")
)
if os.path.isdir(PARENT_SKILL) and PARENT_SKILL not in sys.path:
    sys.path.insert(0, PARENT_SKILL)

# Imports — scrapers come from local scripts/scrapers/
from scrapers import _base as B  # noqa: E402
from scrapers import (
    alljobs,
    drushim,
    glassdoor,
    greenhouse,
    jobmaster,
    linkedin,
    remoteok,
)  # noqa: E402

# Imports from this skill
import junior_classifier as JC  # noqa: E402


# ---------------------------------------------------------------------------
# Junior-tuned search queries
# ---------------------------------------------------------------------------

# These override the parent skill's DEVOPS_QUERIES to find junior roles.
# With LinkedIn's f_E=1,2,3 filter applied (Internship/Entry/Associate), even
# generic queries like "devops" or "linux" now return only junior-level roles.
JUNIOR_QUERIES_EN = [
    # Direct junior keywords
    "junior devops",
    "devops",  # with f_E=1,2,3 this returns entry-level DevOps only
    "junior cloud",
    "cloud engineer",
    "junior sre",
    "site reliability",
    "junior platform",
    "platform engineer",
    "junior linux",
    "linux administrator",
    # IT/support pipeline
    "help desk",
    "service desk",
    "it support",
    "technical support",
    "it specialist",
    # SysAdmin pipeline
    "system administrator",
    "system engineer",
    "sysadmin",
    "noc engineer",
    "noc",
    # Trainee programs
    "cloud trainee",
    "devops trainee",
    "devops intern",
    "infrastructure intern",
    "graduate program",
]

JUNIOR_QUERIES_HE = [
    "סיסטם",
    "תמיכה טכנית",
    "תומך טכני",
    "מנהל מערכת",
    "הלפדסק",
    "ג'וניור",
    "תשתיות",
]

# Override the LinkedIn module's DEVOPS_QUERIES with junior queries by
# monkey-patching at runtime. Yes, this is a bit hacky, but cleaner than
# duplicating 150 lines of LinkedIn scraper code.
B.DEVOPS_QUERIES = (
    JUNIOR_QUERIES_EN  # all junior-tuned queries; LinkedIn f_E filter cuts noise
)


# ---------------------------------------------------------------------------
# Local junior-tuned wrappers around the parent skill's scrapers
# ---------------------------------------------------------------------------


def _greenhouse_junior(max_jobs: int = 600) -> List[Dict[str, Any]]:
    """
    Greenhouse is the BEST junior source: it gives us full job descriptions
    so the stack filter actually works. We DON'T use the parent skill's
    looks_devops filter — we want every IL job, then run our junior classifier.
    """
    B.LOG.info("Greenhouse-junior: scanning %d boards", len(greenhouse.ISRAELI_BOARDS))
    out: List[Dict[str, Any]] = []
    for board in greenhouse.ISRAELI_BOARDS:
        url = greenhouse.API.format(board=board)
        data = B.http_get(url, expect_json=True, max_retries=1)
        if not isinstance(data, dict) or "jobs" not in data:
            continue
        company_name = board.replace("-", " ").title()
        for j in data.get("jobs") or []:
            title = j.get("title") or ""
            location = (j.get("location") or {}).get("name") or ""
            content = j.get("content") or ""
            if not B.looks_israeli(location, content):
                continue
            classification = JC.is_junior_pipeline(title, content)
            if not classification.get("match"):
                continue
            jid = B.make_id("greenhouse", board, j.get("id"))
            out.append(
                {
                    **B.normalize_job(
                        source="greenhouse",
                        job_id=jid,
                        title=title,
                        company=company_name,
                        location=location or "Israel",
                        url=j.get("absolute_url") or "",
                        posted_at=j.get("updated_at") or "",
                        description=content,
                        raw={"board": board, "gh_id": j.get("id")},
                    ),
                    "junior_bucket": classification["bucket"],
                    "stack_matched": classification["stack_matched"],
                    "stack_count": classification["stack_count"],
                    "learning_score": classification["learning_score"],
                }
            )
            if len(out) >= max_jobs:
                B.LOG.info("Greenhouse-junior: hit max %d", max_jobs)
                return out
        B.polite_sleep((0.2, 0.5))
    B.LOG.info("Greenhouse-junior: %d junior-pipeline matches", len(out))
    return out


_LI_JD_DESC = __import__("re").compile(
    r"show-more-less-html__markup[^>]*>(.*?)</div>", __import__("re").DOTALL
)


def _fetch_linkedin_jd(job_url: str) -> str:
    """Fetch a LinkedIn job description by its guest URL."""
    if not job_url:
        return ""
    # Convert /jobs-view/<id> or /jobs/view/<slug>-<id> -> /jobs-guest/jobs/api/jobPosting/<id>
    import re as _re

    m = (
        _re.search(r"/jobs-view/(\d+)", job_url)
        or _re.search(r"/jobs/view/[^/?]*?-(\d+)(?:[/?]|$)", job_url)
        or _re.search(r"/jobs/view/(\d+)", job_url)
        or _re.search(r"currentJobId=(\d+)", job_url)
    )
    if not m:
        return ""
    jd_url = f"https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{m.group(1)}"
    html_text = B.http_get(
        jd_url,
        headers={"Referer": "https://www.linkedin.com/jobs/search/"},
        max_retries=1,
    )
    if not isinstance(html_text, str):
        return ""
    desc_match = _LI_JD_DESC.search(html_text)
    if desc_match:
        return B.strip_html(desc_match.group(1))
    # Fallback: strip ALL html
    return B.strip_html(html_text)[:4000]


def _linkedin_junior(
    max_jobs: int = 400, fetch_jd_top_n: int = 150
) -> List[Dict[str, Any]]:
    """
    Phase 1: LinkedIn search returns only titles + company + location.
    Phase 2: For junior-titled hits, fetch each job's JD page (LinkedIn allows
             this without auth) and run the full stack classifier.

    fetch_jd_top_n caps how many JDs we fetch per run (each fetch is ~1 sec, so
    80 fetches = ~80 sec - polite to LinkedIn).
    """
    # Use LinkedIn's entry-level experience filter (Internship + Entry-level + Associate)
    # and remove the time-window restriction so we see all currently-open junior roles.
    # Skip the DevOps title pre-filter — our junior classifier will do its own filtering.
    raw = linkedin.scrape(
        max_jobs=max_jobs,
        experience="1,2,3",
        time_filter="",
        devops_filter=False,
    )
    junior_candidates = []
    for j in raw:
        bucket = JC.classify_title(j["title"])
        if not bucket:
            continue
        junior_candidates.append({**j, "junior_bucket": bucket})
    B.LOG.info(
        "LinkedIn-junior phase 1: %d junior-titled candidates", len(junior_candidates)
    )

    # Phase 2: fetch JDs for top N
    out = []
    fetched = 0
    for j in junior_candidates:
        if fetched >= fetch_jd_top_n:
            # Add remainder as needs_review
            out.append(
                {
                    **j,
                    "stack_matched": [],
                    "stack_count": 0,
                    "learning_score": 0,
                    "needs_jd_review": True,
                }
            )
            continue
        jd = _fetch_linkedin_jd(j.get("url", ""))
        fetched += 1
        if not jd:
            out.append(
                {
                    **j,
                    "stack_matched": [],
                    "stack_count": 0,
                    "learning_score": 0,
                    "needs_jd_review": True,
                }
            )
            continue
        # Apply full classifier on title + fetched JD
        classification = JC.is_junior_pipeline(j["title"], jd)
        if classification.get("match"):
            out.append(
                {
                    **j,
                    "description": jd[:4000],
                    "junior_bucket": classification["bucket"],
                    "stack_matched": classification["stack_matched"],
                    "stack_count": classification["stack_count"],
                    "learning_score": classification["learning_score"],
                }
            )
        # If didn't match, drop it (the JD doesn't have enough DevOps stack)
        B.polite_sleep((0.5, 1.0))
    matches = sum(1 for j in out if not j.get("needs_jd_review"))
    needs = sum(1 for j in out if j.get("needs_jd_review"))
    B.LOG.info(
        "LinkedIn-junior phase 2: %d full matches (with stack), %d still need review",
        matches,
        needs,
    )
    return out


def _remoteok_junior(max_jobs: int = 200) -> List[Dict[str, Any]]:
    """RemoteOK gives full descriptions - apply full classifier."""
    # The parent's scraper already filters to looks_devops which is too strict.
    # Re-fetch raw and apply our classifier instead.
    data = B.http_get(remoteok.API_URL, expect_json=True)
    if not isinstance(data, list):
        return []
    out: List[Dict[str, Any]] = []
    for entry in data:
        if not isinstance(entry, dict) or "position" not in entry:
            continue
        title = entry.get("position") or ""
        desc = entry.get("description") or ""
        loc = (entry.get("location") or "").lower()
        # Skip US-only / EU-only
        bad = ["us only", "usa only", "americas only", "uk only", "canada only"]
        if loc and any(b in loc for b in bad):
            continue
        classification = JC.is_junior_pipeline(title, desc)
        if not classification.get("match"):
            continue
        slug = entry.get("slug") or entry.get("id")
        url = entry.get("url") or f"https://remoteok.com/remote-jobs/{slug}"
        out.append(
            {
                **B.normalize_job(
                    source="remoteok",
                    job_id=B.make_id("remoteok", entry.get("id"), slug),
                    title=title,
                    company=entry.get("company") or "",
                    location=entry.get("location") or "Remote",
                    url=url,
                    posted_at=entry.get("date") or "",
                    description=desc,
                    raw={"tags": entry.get("tags") or []},
                ),
                "junior_bucket": classification["bucket"],
                "stack_matched": classification["stack_matched"],
                "stack_count": classification["stack_count"],
                "learning_score": classification["learning_score"],
            }
        )
        if len(out) >= max_jobs:
            break
    B.LOG.info("RemoteOK-junior: %d junior-pipeline matches", len(out))
    return out


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

ROOT = os.path.dirname(HERE)
DATA_DIR = os.path.join(ROOT, "data")
SNAPSHOTS_DIR = os.path.join(DATA_DIR, "snapshots")
HISTORY_DIR = os.path.join(DATA_DIR, "history")
REPORTS_DIR = os.path.join(ROOT, "reports")

SOURCES = [
    ("greenhouse", _greenhouse_junior, 600),
    ("linkedin", _linkedin_junior, 250),
    ("remoteok", _remoteok_junior, 200),
]


def run_scrapers(only=None, quick=False) -> Tuple[List[Dict], Dict]:
    only = only or []
    all_jobs = []
    diagnostics = {}
    for name, fn, default_max in SOURCES:
        if only and name not in only:
            continue
        max_jobs = 30 if quick else default_max
        t0 = time.time()
        try:
            B.LOG.info(">>> Junior scraper: %s (max=%d)", name, max_jobs)
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


def dedup_jr(jobs: List[Dict]) -> List[Dict]:
    """Dedup by (lowercased title, lowercased company), prefer Greenhouse > RemoteOK > LinkedIn."""
    priority = {"greenhouse": 0, "remoteok": 1, "linkedin": 2}
    seen: Dict[str, Dict] = {}
    for j in jobs:
        title_n = "".join(
            c.lower() for c in j.get("title", "") if c.isalnum() or c == " "
        ).strip()
        company_n = "".join(
            c.lower() for c in j.get("company", "") if c.isalnum() or c == " "
        ).strip()
        key = f"{company_n}::{title_n}"
        existing = seen.get(key)
        if existing is None or priority.get(j.get("source"), 99) < priority.get(
            existing.get("source"), 99
        ):
            seen[key] = j
    return list(seen.values())


def analyze_jr(jobs: List[Dict]) -> Dict[str, Any]:
    bucket_counts = Counter(j.get("junior_bucket", "?") for j in jobs)
    stack_counts: Counter = Counter()
    for j in jobs:
        for s in j.get("stack_matched", []):
            stack_counts[s] += 1
    company_counts = Counter(j.get("company", "?") for j in jobs if j.get("company"))
    by_source = Counter(j.get("source", "?") for j in jobs)
    location_counts = Counter()
    for j in jobs:
        # Re-bucket location using parent's analysis for consistency
        try:
            from analysis import bucket_location  # noqa: WPS433

            location_counts[bucket_location(j.get("location", ""))] += 1
        except Exception:
            location_counts[j.get("location", "?")] += 1

    # Top jobs by learning score
    scored = [j for j in jobs if j.get("learning_score", 0) > 0]
    scored.sort(key=lambda j: -j.get("learning_score", 0))
    needs_review = [j for j in jobs if j.get("needs_jd_review")]

    return {
        "total_jobs": len(jobs),
        "by_source": dict(by_source),
        "bucket_distribution": dict(bucket_counts.most_common()),
        "top_stack": stack_counts.most_common(15),
        "top_companies": company_counts.most_common(15),
        "location_distribution": dict(location_counts.most_common()),
        "top_scored_jobs": scored[:25],
        "needs_review_count": len(needs_review),
        "needs_review_sample": needs_review[:10],
    }


def render_markdown(stats: Dict[str, Any], week: str, sheet_url: str = "") -> str:
    generated = datetime.now().strftime("%B %d, %Y")
    bucket_lines = "\n".join(
        f"- **{name}** — {count}"
        for name, count in stats["bucket_distribution"].items()
    )
    stack_lines = "\n".join(
        f"{i}. **{name}** — required in {count} roles"
        for i, (name, count) in enumerate(stats["top_stack"][:10], 1)
    )
    company_lines = "\n".join(
        f"{i}. **{name}** — {count} role(s)"
        for i, (name, count) in enumerate(stats["top_companies"][:10], 1)
    )

    top_jobs_md = []
    for j in stats["top_scored_jobs"][:15]:
        stack = ", ".join(j.get("stack_matched", [])[:6])
        url = j.get("url") or ""
        top_jobs_md.append(
            f"### {j['title']} — {j.get('company', '?')}\n"
            f"📍 {j.get('location', 'Israel')} • 🎯 Learning Score: {j.get('learning_score', 0)}/100 • 🪜 {j.get('junior_bucket', '?')}\n\n"
            f"**Stack you'll work with:** {stack}\n\n"
            f"🔗 [View role]({url})\n"
        )
    top_jobs_block = (
        "\n".join(top_jobs_md)
        if top_jobs_md
        else "_No scored matches this week — try increasing scraper coverage._"
    )

    sheet_section = f"\n📊 **Live dashboard:** {sheet_url}\n" if sheet_url else ""

    return f"""# 🪜 Israeli DevOps — Junior Pipeline Report — {week}

_Generated {generated} • Maintained by [Orel Bello](https://www.linkedin.com/in/orel-bello/) (FlipTheScript • AWS Community Builder)_
{sheet_section}

> **Who this is for:** anyone in IT / Help Desk / Support / SysAdmin / Bootcamp grad / career-switcher who wants to break into DevOps. Every role on this list **requires DevOps stack tech** (Linux, Python, AWS, Docker, K8s, CI/CD…) — meaning you'll be paid to learn the right tools.

## This week, by the numbers

We tracked **{stats["total_jobs"]} junior-pipeline roles** in Israel that mention real DevOps stack:

{bucket_lines}

({stats["needs_review_count"]} additional LinkedIn-only postings need manual JD review for stack — see "All Jobs" tab in the dashboard.)

## 🔥 What stack do these roles need?

These are the technologies a DevOps-aspiring junior should focus on learning, ranked by demand:

{stack_lines}

## 🏢 Companies hiring for the pipeline

{company_lines}

## ⭐ Top 15 highest-scoring roles this week

(Score = breadth of DevOps stack × bucket relevance. Higher = more learning value.)

{top_jobs_block}

## How the score works

- **+12 points** for each "core" stack item (Linux, Python, Bash, AWS/Azure/GCP, Docker, K8s, CI/CD, Git, Terraform/IaC)
- **+4 points** for each "supporting" item (Networking, Monitoring, DBs, Virtualization, AD)
- **× bucket multiplier** — Junior DevOps (1.4) > Junior SRE/Cloud (1.3) > Junior SysAdmin (1.15) > NOC (1.0) > Help Desk (0.85)

A score of 60+ means you'll genuinely learn modern DevOps in this role.

## Methodology

- Scraped weekly from Greenhouse (23 Israeli tech boards), LinkedIn guest API, RemoteOK
- Title filter: junior bucket regex (English + Hebrew, exclude senior/lead/manager)
- Stack filter: ≥2 stack items mentioned in JD, ≥1 must be a "core" item
- 100% free • Open methodology • No paid APIs

---

**Made for the Israeli DevOps community by [FlipTheScript](https://www.linkedin.com/groups/12877927/).**
"""


def render_linkedin(stats: Dict[str, Any], sheet_url: str = "") -> str:
    week = datetime.now().strftime("Week %V, %Y")
    total = stats["total_jobs"]
    top_buckets = list(stats["bucket_distribution"].items())[:3]
    top_stack = stats["top_stack"][:5]
    top_companies = stats["top_companies"][:4]

    bucket_line = " • ".join(f"{name}: {count}" for name, count in top_buckets)
    stack_line = " • ".join(name for name, _ in top_stack)
    company_line = ", ".join(name for name, _ in top_companies)

    sheet_block = (
        f"\n\n📊 Live dashboard (auto-updated weekly): {sheet_url}" if sheet_url else ""
    )

    return f"""🪜 Israeli DevOps — Junior Pipeline — {week}

For the FlipTheScript mentees asking "how do I break into DevOps?" — here's the data.

I tracked every entry-level / IT / Help Desk / SysAdmin / Junior DevOps role in Israel this week that requires REAL DevOps stack tech (Linux, Python, AWS, Docker, K8s, CI/CD…) — meaning you'll be paid to learn the right tools while you're in the role.

🔢 {total} junior-pipeline roles this week
🪜 {bucket_line}

🛠️ The stack these roles want you to know:
{stack_line}

🏢 Companies hiring junior pipeline:
{company_line}

If you're an IT/Help Desk/SysAdmin and you want to land a DevOps job in 12-24 months, your career path is right here. Apply to roles where you'll be working with the tools you want to grow into — not roles that just keep you on Windows tickets forever.

100% free, real data, weekly updates. Built on OpenClaw / Accomplish.{sheet_block}

What other entry paths should I track? (NOC? QA Automation? Backend with infra exposure?) Drop ideas 👇

#DevOps #JuniorDevOps #ITSupport #HelpDesk #SysAdmin #IsraeliTech #FlipTheScript #CareerInTech
"""


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--quick", action="store_true")
    parser.add_argument("--source", nargs="+", default=None)
    parser.add_argument("--sheet-url", default="")
    args = parser.parse_args()

    os.makedirs(SNAPSHOTS_DIR, exist_ok=True)
    os.makedirs(HISTORY_DIR, exist_ok=True)
    os.makedirs(REPORTS_DIR, exist_ok=True)

    started = datetime.now()
    week = f"{started.isocalendar()[0]}-W{started.isocalendar()[1]:02d}"
    B.LOG.info("=" * 80)
    B.LOG.info(
        "Junior Pipeline Run started %s — week %s",
        started.isoformat(timespec="seconds"),
        week,
    )
    B.LOG.info(
        "Mode: %s | Sources: %s",
        "QUICK" if args.quick else "FULL",
        args.source or "ALL",
    )
    B.LOG.info("=" * 80)

    raw, diagnostics = run_scrapers(only=args.source, quick=args.quick)
    B.LOG.info("Raw junior matches: %d", len(raw))
    deduped = dedup_jr(raw)
    B.LOG.info("After dedup: %d (%d duplicates)", len(deduped), len(raw) - len(deduped))

    stats = analyze_jr(deduped)
    stats["week"] = week
    stats["diagnostics"] = diagnostics
    stats["raw_count"] = len(raw)
    stats["deduped_count"] = len(deduped)

    ts = started.strftime("%Y%m%d_%H%M%S")
    snapshot_path = os.path.join(SNAPSHOTS_DIR, f"jr_jobs_{ts}.json")
    with open(snapshot_path, "w", encoding="utf-8") as f:
        json.dump(
            {
                "started_at": started.isoformat(),
                "jobs": deduped,
                "stats": {
                    k: v
                    for k, v in stats.items()
                    if k != "top_scored_jobs" and k != "needs_review_sample"
                },
            },
            f,
            indent=2,
            ensure_ascii=False,
        )

    md = render_markdown(stats, week, sheet_url=args.sheet_url)
    li = render_linkedin(stats, sheet_url=args.sheet_url)
    md_path = os.path.join(REPORTS_DIR, f"jr_report_{week}.md")
    li_path = os.path.join(REPORTS_DIR, f"jr_linkedin_{week}.md")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(md)
    with open(li_path, "w", encoding="utf-8") as f:
        f.write(li)

    # Latest.json — for the publisher
    latest = {
        "generated_at": started.isoformat(timespec="seconds"),
        "week": week,
        "stats_for_sheets": {
            "total_jobs": stats["total_jobs"],
            "by_source": stats["by_source"],
            "bucket_distribution": stats["bucket_distribution"],
            "top_stack": stats["top_stack"],
            "top_companies": stats["top_companies"],
            "location_distribution": stats["location_distribution"],
            "needs_review_count": stats["needs_review_count"],
        },
        "diagnostics": diagnostics,
        "report_md_path": md_path,
        "linkedin_md_path": li_path,
        "snapshot_path": snapshot_path,
        "jobs_for_sheet": [
            {
                "title": j.get("title", ""),
                "company": j.get("company", ""),
                "location": j.get("location", ""),
                "bucket": j.get("junior_bucket", ""),
                "stack": ", ".join(j.get("stack_matched", []) or []),
                "score": j.get("learning_score", 0),
                "needs_review": j.get("needs_jd_review", False),
                "source": j.get("source", ""),
                "url": j.get("url", ""),
            }
            for j in sorted(deduped, key=lambda x: -x.get("learning_score", 0))
        ],
    }
    latest_path = os.path.join(REPORTS_DIR, "jr_latest.json")
    with open(latest_path, "w", encoding="utf-8") as f:
        json.dump(latest, f, indent=2, ensure_ascii=False)

    print("\n" + "=" * 80)
    print(f"  JUNIOR PIPELINE RUN COMPLETE — Week {week}")
    print("=" * 80)
    print(f"  Total junior-pipeline matches: {stats['total_jobs']}")
    print(f"  Needs JD review (LinkedIn-only): {stats['needs_review_count']}")
    print()
    print("  Buckets:")
    for name, count in stats["bucket_distribution"].items():
        print(f"    - {name}: {count}")
    print()
    print("  Top stack required:")
    for name, count in stats["top_stack"][:8]:
        print(f"    - {name}: {count}")
    print()
    print("  Top companies:")
    for name, count in stats["top_companies"][:8]:
        print(f"    - {name}: {count}")
    print()
    print(f"  📄 Report:    {md_path}")
    print(f"  📱 LinkedIn:  {li_path}")
    print(f"  📊 Handoff:   {latest_path}")
    print("=" * 80)
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
