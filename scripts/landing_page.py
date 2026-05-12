#!/usr/bin/env python3
"""
Generate a static HTML landing page from `reports/latest.json`.
Output: reports/index.html  — drop into GitHub Pages, Netlify, or any static host.

100% standalone HTML (single file, embedded CSS, no JS frameworks).
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LATEST = os.path.join(ROOT, "reports", "latest.json")
OUT = os.path.join(ROOT, "reports", "index.html")

JR_LATEST = os.path.join(
    ROOT, "..", "devops-junior-pipeline", "reports", "jr_latest.json"
)


def _esc(s):
    if s is None:
        return ""
    return (
        str(s)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def render():
    with open(LATEST, "r", encoding="utf-8") as f:
        d = json.load(f)
    s = d["stats_for_sheets"]
    diag = d.get("diagnostics") or {}
    week = d.get("week", "")
    generated = d.get("generated_at", "")

    # Try to load junior data too
    jr = None
    try:
        with open(JR_LATEST, "r", encoding="utf-8") as f:
            jr = json.load(f)
    except Exception:
        pass

    # Top skills bar
    skills_html = ""
    max_skill = max((c for _, c in s.get("top_skills", [])), default=1)
    for name, count in s.get("top_skills", [])[:12]:
        pct = int(100 * count / max_skill)
        skills_html += f'<div class="bar"><span class="label">{_esc(name)}</span><div class="track"><div class="fill" style="width:{pct}%"></div></div><span class="count">{count}</span></div>'

    # Top companies
    companies_html = ""
    for name, count in s.get("top_companies", [])[:10]:
        companies_html += (
            f"<li><strong>{_esc(name)}</strong> — {count} open role(s)</li>"
        )

    # HiringStrength
    hs_html = ""
    for co, strength, cnt in s.get("top_hiring_strength", [])[:10]:
        hs_html += f'<tr><td>{_esc(co)}</td><td class="num">{strength:.1f}</td><td class="num">{cnt}</td></tr>'

    # Sources
    src_html = ""
    for src, n in sorted(s.get("by_source", {}).items(), key=lambda x: -x[1]):
        ok = "✓" if (diag.get(src, {}).get("ok")) else "—"
        src_html += f"<li>{ok} <strong>{_esc(src)}</strong>: {n} jobs</li>"

    # Junior section
    jr_html = ""
    if jr:
        jr_jobs = sorted(
            jr.get("jobs_for_sheet", []), key=lambda x: -(x.get("score") or 0)
        )[:5]
        jr_rows = ""
        for j in jr_jobs:
            jr_rows += f'<tr><td><a href="{_esc(j.get("url"))}" target="_blank">{_esc(j.get("title"))}</a></td><td>{_esc(j.get("company"))}</td><td class="num">{j.get("score", 0)}</td></tr>'
        jr_html = f"""
<section>
  <h2>🪜 Junior Pipeline — Top 5 picks</h2>
  <p>For IT / Help Desk / SysAdmin / Junior DevOps career-pivot candidates. <a href="https://docs.google.com/spreadsheets/d/1y6ZXo_rQvffdnKEb_QQGbEw93jWVl09GaBRBlNTSxY8/edit" target="_blank">Full junior dashboard →</a></p>
  <table>
    <thead><tr><th>Role</th><th>Company</th><th>Learning Score</th></tr></thead>
    <tbody>{jr_rows}</tbody>
  </table>
</section>
"""

    main_sheet_url = "https://docs.google.com/spreadsheets/d/1ySTEoA7nDCZRcypvz__2nBbv0pTWKS2tEU7tged1JVs/edit"
    jr_sheet_url = "https://docs.google.com/spreadsheets/d/1y6ZXo_rQvffdnKEb_QQGbEw93jWVl09GaBRBlNTSxY8/edit"

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Israeli DevOps Job Market — {_esc(week)}</title>
<meta name="description" content="The authoritative weekly Israeli DevOps job market report. {s.get("total_jobs", 0)} open roles tracked from LinkedIn, Greenhouse, RemoteOK, and more. 100% free. No paid APIs.">
<meta property="og:title" content="Israeli DevOps Job Market — {_esc(week)}">
<meta property="og:description" content="{s.get("total_jobs", 0)} open DevOps roles in Israel. Top skills, top companies, junior pipeline. Updated weekly.">
<style>
:root {{
  --bg: #0d1117;
  --card: #161b22;
  --text: #e6edf3;
  --muted: #8b949e;
  --accent: #58a6ff;
  --green: #2ea043;
  --orange: #f85149;
  --purple: #a371f7;
}}
* {{ box-sizing: border-box; }}
body {{
  margin: 0;
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif;
  background: var(--bg);
  color: var(--text);
  line-height: 1.6;
}}
.container {{ max-width: 1100px; margin: 0 auto; padding: 2rem 1.5rem; }}
header {{ text-align: center; padding: 3rem 1rem 2rem; border-bottom: 1px solid #30363d; margin-bottom: 2rem; }}
header h1 {{ margin: 0 0 0.5rem; font-size: 2.5rem; background: linear-gradient(135deg, #58a6ff, #a371f7); -webkit-background-clip: text; background-clip: text; -webkit-text-fill-color: transparent; }}
header .subtitle {{ color: var(--muted); font-size: 1.1rem; }}
header .meta {{ color: var(--muted); font-size: 0.9rem; margin-top: 1rem; }}
.hero {{
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
  gap: 1rem;
  margin-bottom: 2.5rem;
}}
.hero .stat {{
  background: var(--card);
  padding: 1.5rem;
  border-radius: 12px;
  border: 1px solid #30363d;
  text-align: center;
}}
.hero .stat .num {{ font-size: 2.5rem; font-weight: 700; color: var(--accent); display: block; }}
.hero .stat .label {{ color: var(--muted); text-transform: uppercase; font-size: 0.85rem; letter-spacing: 0.5px; }}
section {{ background: var(--card); padding: 2rem; border-radius: 12px; border: 1px solid #30363d; margin-bottom: 1.5rem; }}
section h2 {{ margin-top: 0; color: var(--text); }}
.bar {{ display: grid; grid-template-columns: 140px 1fr 50px; align-items: center; gap: 0.75rem; padding: 0.4rem 0; }}
.bar .label {{ color: var(--text); font-weight: 500; }}
.bar .track {{ background: #21262d; border-radius: 6px; overflow: hidden; height: 24px; }}
.bar .fill {{ background: linear-gradient(90deg, var(--accent), var(--purple)); height: 100%; border-radius: 6px; }}
.bar .count {{ color: var(--muted); text-align: right; font-variant-numeric: tabular-nums; }}
ul {{ padding-left: 1.5rem; }}
ul li {{ margin: 0.25rem 0; }}
table {{ width: 100%; border-collapse: collapse; }}
th, td {{ padding: 0.75rem; text-align: left; border-bottom: 1px solid #30363d; }}
th {{ background: #21262d; color: var(--muted); font-weight: 600; text-transform: uppercase; font-size: 0.85rem; letter-spacing: 0.5px; }}
.num {{ text-align: right; font-variant-numeric: tabular-nums; }}
a {{ color: var(--accent); text-decoration: none; }}
a:hover {{ text-decoration: underline; }}
.cta {{ display: inline-block; background: var(--accent); color: var(--bg); padding: 0.75rem 1.5rem; border-radius: 8px; font-weight: 600; margin-top: 1rem; }}
.cta:hover {{ background: var(--purple); color: var(--text); text-decoration: none; }}
footer {{ text-align: center; color: var(--muted); padding: 2rem 0; font-size: 0.9rem; border-top: 1px solid #30363d; margin-top: 3rem; }}
</style>
</head>
<body>
<div class="container">
<header>
  <h1>🇮🇱 Israeli DevOps Job Market</h1>
  <div class="subtitle">The authoritative weekly report — open data, open methodology</div>
  <div class="meta">Week {_esc(week)} · Generated {_esc(generated)} · Maintained by <a href="https://www.linkedin.com/in/orel-bello/">Orel Bello</a> for <a href="https://www.linkedin.com/groups/12877927/">FlipTheScript</a></div>
  <div style="margin-top:1.5rem"><a class="cta" href="{main_sheet_url}" target="_blank">Open the live dashboard →</a></div>
</header>

<div class="hero">
  <div class="stat"><span class="num">{s.get("total_jobs", 0)}</span><span class="label">Open Roles</span></div>
  <div class="stat"><span class="num">{len(s.get("top_companies", []))}</span><span class="label">Top Companies</span></div>
  <div class="stat"><span class="num">{s.get("junior_count", 0)}</span><span class="label">Junior-Friendly</span></div>
  <div class="stat"><span class="num">{len(s.get("by_source", {}))}</span><span class="label">Active Sources</span></div>
</div>

<section>
  <h2>🔥 Top Skills in Demand</h2>
  {skills_html}
</section>

<section>
  <h2>🏢 Top Hiring Companies</h2>
  <ul>{companies_html}</ul>
</section>

<section>
  <h2>📊 Hiring Strength Score</h2>
  <p style="color:var(--muted)">Combines volume + diversity of seniority + skill breadth. Higher = company is hiring across the board.</p>
  <table>
    <thead><tr><th>Company</th><th class="num">Strength</th><th class="num">Open Roles</th></tr></thead>
    <tbody>{hs_html}</tbody>
  </table>
</section>

{jr_html}

<section>
  <h2>📡 Sources</h2>
  <ul>{src_html}</ul>
</section>

<section>
  <h2>📋 Methodology</h2>
  <p>Data is collected weekly from public job-board endpoints (LinkedIn guest API, RemoteOK, Greenhouse boards of 28+ Israeli tech companies, AllJobs, Drushim, Glassdoor, Jobmaster, Lever). Each posting is filtered for DevOps / SRE / Platform / Cloud roles in Israel, deduplicated across sources, and analyzed for skills, seniority, salary, and location. Skill mentions are extracted via a curated regex taxonomy of 50+ tools.</p>
  <p><strong>100% free • Open methodology • No paid APIs.</strong></p>
  <p><a href="{main_sheet_url}" target="_blank">Main dashboard</a> · <a href="{jr_sheet_url}" target="_blank">Junior pipeline dashboard</a></p>
</section>

<footer>
  Built with ☕ by <a href="https://www.linkedin.com/in/orel-bello/">Orel Bello</a> · Senior Platform Engineer at Melio · AWS Community Builder · founder of <a href="https://www.linkedin.com/groups/12877927/">FlipTheScript</a> (600+ DevOps mentees in Israel)
  <br><br>
  Powered by <a href="https://accomplish.ai" target="_blank">Accomplish (OpenClaw)</a> · Updated every Sunday morning · 100% free
</footer>
</div>
</body>
</html>
"""

    with open(OUT, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"Wrote {OUT} ({os.path.getsize(OUT)} bytes)")
    return OUT


if __name__ == "__main__":
    render()
