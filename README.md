# 🇮🇱 Israeli DevOps Job Market — Live Data

Public, version-controlled data feed for the Israeli DevOps job market — updated automatically every Sunday at 9:00 AM Israel time.

Maintained by [Orel Bello](https://orelbello.com) for [FlipTheScript](https://www.linkedin.com/groups/12877927/).

## 🔗 Live URLs

| What | URL |
|---|---|
| **Latest data (JSON)** | https://orelbello.github.io/devops-market-data/latest.json |
| **Landing page (HTML)** | https://orelbello.github.io/devops-market-data/ |

> Replace `orelbello` with your actual GitHub username if different.

## 📦 What's in this repo

| Path | Contents |
|---|---|
| `scripts/` | Python platform — scrapers, analysis, report generator |
| `reports/latest.json` | The current week's data (machine-readable) |
| `reports/index.html` | The current week's landing page |
| `reports/report_*.md` | Weekly Markdown reports |
| `reports/linkedin_*.md` | Weekly LinkedIn post drafts |
| `data/history/*.json` | Slim weekly aggregates for trend tracking |
| `.github/workflows/weekly.yml` | The cron job that auto-refreshes everything |

## 🧠 Why this exists

This repo is the *data layer* of an Israeli DevOps job market intelligence platform. Other surfaces consume this data:

- **orelbello.com/devops-jobs-israel** — embeds the JSON to show live market stats
- **Google Sheets dashboard** — pulls structured data for filtering and charts
- **Weekly LinkedIn post** — auto-drafted from the same source

Keeping the data in a separate repo (rather than embedding it in the website) means:
- ✅ Website stays clean (no data commits cluttering its git history)
- ✅ If the platform breaks, the website keeps showing the last good data
- ✅ Anyone can fetch the JSON and build their own dashboard on top

## 🤖 How it runs

GitHub Actions runs `scripts/orchestrator.py` every Sunday at 06:00 UTC. The script:
1. Scrapes 8 public sources (LinkedIn guest API, RemoteOK, Greenhouse boards of 28 Israeli tech companies, AllJobs, Drushim, Jobmaster, Glassdoor, Lever)
2. Filters for DevOps / SRE / Platform / Cloud roles in Israel
3. Deduplicates across sources
4. Analyzes skills, seniority, salary, location, hiring strength
5. Generates `latest.json` + `index.html` + Markdown report + LinkedIn post draft
6. Commits and pushes the new files back to this repo

GitHub Pages serves the result on the public URL above. **Zero infrastructure cost.**

## 🛠️ Tech

- Python 3.12, **standard library only** — no `pip install` needed
- ~3,500 LOC across 16 modules
- Stateless: each run is independent

## 🧪 Run locally

```bash
git clone https://github.com/<your-username>/devops-market-data.git
cd devops-market-data
python3 scripts/orchestrator.py
```

The output appears in `reports/`.

## 📄 License

MIT. Use the data freely. Attribution appreciated but not required.

## 🙋 Questions / requests

Open an issue, or reach out to Orel via [orelbello.com](https://orelbello.com).
