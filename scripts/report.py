"""
Report generation: professional Markdown report + LinkedIn post draft.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional


def _fmt_skills(skills: List) -> str:
    if not skills:
        return "_No skill data extracted._"
    lines = []
    for i, item in enumerate(skills[:10], 1):
        name, count = item if isinstance(item, (list, tuple)) else (item, 0)
        lines.append(f"{i}. **{name}** — {count} mentions")
    return "\n".join(lines)


def _fmt_companies(companies: List) -> str:
    if not companies:
        return "_No company data._"
    lines = []
    for i, item in enumerate(companies[:10], 1):
        name, count = item if isinstance(item, (list, tuple)) else (item, 0)
        plural = "role" if count == 1 else "roles"
        lines.append(f"{i}. **{name}** — {count} open {plural}")
    return "\n".join(lines)


def _fmt_seniority(seniority: Dict[str, float]) -> str:
    if not seniority:
        return "_No seniority data._"
    order = ["Junior", "Mid", "Senior"]
    rows = []
    for level in order:
        pct = seniority.get(level, 0.0)
        bar = "█" * int(pct / 2.5)  # 40 chars max
        rows.append(f"- **{level}**: {pct:.1f}% `{bar}`")
    return "\n".join(rows)


def _fmt_locations(locs: Dict[str, int]) -> str:
    if not locs:
        return "_No location data._"
    items = sorted(locs.items(), key=lambda x: -x[1])[:8]
    return "\n".join(f"- **{name}** — {count}" for name, count in items)


def _fmt_sources(sources: Dict[str, int]) -> str:
    items = sorted(sources.items(), key=lambda x: -x[1])
    return "\n".join(f"- {name}: {count}" for name, count in items)


def _fmt_trend_block(deltas: Dict[str, Any]) -> str:
    if not deltas or deltas.get("is_first_run"):
        return "_This is the first weekly snapshot — trend data will appear next week._"
    arrow = "📈" if deltas.get("total_jobs_delta", 0) >= 0 else "📉"
    parts = [
        f"{arrow} **Total jobs**: {deltas.get('total_jobs_delta', 0):+d} ({deltas.get('total_jobs_pct', 0):+.1f}%) vs {deltas.get('previous_week')}",
        f"📊 **Junior % change**: {deltas.get('junior_pct_delta', 0):+.1f} pp",
    ]
    rising = deltas.get("rising_skills") or []
    falling = deltas.get("falling_skills") or []
    if rising:
        parts.append("\n**Rising skills:**")
        for name, c, p, d in rising:
            parts.append(f"- {name}: {p} → {c} ({d:+d})")
    if falling:
        parts.append("\n**Falling skills:**")
        for name, c, p, d in falling:
            parts.append(f"- {name}: {p} → {c} ({d:+d})")
    new_co = deltas.get("new_top_companies") or []
    if new_co:
        parts.append("\n**New companies in top 15:** " + ", ".join(new_co))
    return "\n".join(parts)


def render_markdown(
    stats: Dict[str, Any],
    deltas: Optional[Dict[str, Any]] = None,
    *,
    week: str = "",
    sheet_url: str = "",
) -> str:
    week = week or datetime.now().strftime("%Y-W%U")
    generated = datetime.now().strftime("%B %d, %Y")
    salary = stats.get("salary_summary") or {}
    salary_lines = []
    for level in ["Junior", "Mid", "Senior"]:
        info = salary.get(level) or {}
        median = info.get("median_usd")
        n = info.get("n", 0)
        if median:
            salary_lines.append(f"- **{level}**: ~${median:,.0f}/yr (n={n})")
        else:
            salary_lines.append(f"- **{level}**: _insufficient data_ (n={n})")
    salary_block = (
        "\n".join(salary_lines)
        if salary_lines
        else "_No salary data parsed this week._"
    )

    sheet_section = f"\n📊 **Live dashboard:** {sheet_url}\n" if sheet_url else ""

    return f"""# Israeli DevOps Job Market Report — {week}

_Generated {generated} • Maintained by [Orel Bello](https://www.linkedin.com/in/orel-bello/) (FlipTheScript • AWS Community Builder)_
{sheet_section}

## Executive Summary

We tracked **{stats.get("total_jobs", 0)} open DevOps / SRE / Platform / Cloud roles** in Israel this week
across {len(stats.get("by_source", {}))} sources. **{stats.get("junior_count", 0)} of those ({stats.get("junior_pct", 0):.1f}%)
are junior-friendly** — a key metric for FlipTheScript's 600+ mentees.

## Week-over-Week Trends

{_fmt_trend_block(deltas or {})}

## Top 10 In-Demand Skills

{_fmt_skills(stats.get("top_skills", []))}

## Top 10 Hiring Companies

{_fmt_companies(stats.get("top_companies", []))}

## Seniority Distribution

{_fmt_seniority(stats.get("seniority_pct", {}))}

## Salary Insights (USD, where parseable)

{salary_block}

> _Salary data is best-effort: most Israeli postings don't disclose ranges publicly. Numbers are extracted only when explicitly stated._

## Location Breakdown

{_fmt_locations(stats.get("location_distribution", {}))}

## Sources

{_fmt_sources(stats.get("by_source", {}))}

## Methodology

Data is collected weekly from public job-board endpoints (LinkedIn guest API, RemoteOK, Greenhouse boards
of major Israeli tech companies, AllJobs, Drushim, Glassdoor, Jobmaster). Each posting is filtered for
DevOps / SRE / Platform Engineering / Cloud roles in Israel, deduplicated across sources, and analyzed
for skills, seniority, salary, and location. Skill mentions are extracted via a curated regex taxonomy.

**100% free • Open methodology • No paid APIs.**

---

**Made for the Israeli DevOps community by [FlipTheScript](https://www.linkedin.com/groups/12877927/).**
"""


def render_linkedin_post(
    stats: Dict[str, Any],
    deltas: Optional[Dict[str, Any]] = None,
    *,
    sheet_url: str = "",
) -> str:
    """Punchy LinkedIn post draft - the format Orel can copy/paste with minor tweaks."""
    week = datetime.now().strftime("Week %V, %Y")
    total = stats.get("total_jobs", 0)
    junior = stats.get("junior_count", 0)
    junior_pct = stats.get("junior_pct", 0)
    top_skills = stats.get("top_skills", [])[:5]
    top_companies = stats.get("top_companies", [])[:5]

    skills_line = " • ".join(name for name, _ in top_skills) if top_skills else ""
    companies_line = (
        ", ".join(name for name, _ in top_companies) if top_companies else ""
    )

    trend_line = ""
    if deltas and not deltas.get("is_first_run"):
        d = deltas.get("total_jobs_delta", 0)
        pct = deltas.get("total_jobs_pct", 0)
        emoji = "📈" if d >= 0 else "📉"
        trend_line = f"\n{emoji} {d:+d} jobs vs last week ({pct:+.1f}%)"

    sheet_line = (
        f"\n\n📊 Live dashboard (auto-updated weekly): {sheet_url}" if sheet_url else ""
    )

    return f"""🇮🇱 Israeli DevOps Job Market — {week}

I track every open DevOps / SRE / Platform Engineering / Cloud role in Israel and publish the data publicly. Here's what this week looks like:

📊 {total} open roles across major Israeli tech companies + global boards
👥 {junior} of those ({junior_pct:.1f}%) are junior-friendly{trend_line}

🔥 Most-demanded skills:
{skills_line}

🏢 Top hiring companies:
{companies_line}

Why I do this: I mentor 600+ DevOps engineers in @FlipTheScript and they kept asking "what should I learn?" and "who's hiring juniors?" Instead of guessing, I built an automated platform that answers those questions every Sunday — with real data.

100% free, open methodology, no paid APIs. Built on OpenClaw / Accomplish.{sheet_line}

What skills should I add to next week's tracker? Drop them in the comments 👇

#DevOps #IsraeliTech #PlatformEngineering #SRE #CloudEngineering #FlipTheScript #JobMarket
"""
