"""
Market intelligence engine.

Takes a normalized list of jobs and produces:
  - Total counts by source
  - Top in-demand skills (Kubernetes, AWS, Terraform, etc.)
  - Top hiring companies
  - Seniority distribution (Junior / Mid / Senior / Lead)
  - Junior role percentage (critical for Orel's mentees)
  - Salary ranges where parseable (USD/ILS, hourly/monthly/annual)
  - Location breakdown (Tel Aviv / Herzliya / Remote / etc.)
"""

from __future__ import annotations

import re
from collections import Counter
from typing import Any, Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Skills taxonomy - normalized canonical names with regex aliases
# ---------------------------------------------------------------------------

SKILLS: List[Tuple[str, List[str]]] = [
    ("Kubernetes", [r"\bkubernetes\b", r"\bk8s\b", r"\beks\b", r"\bgke\b", r"\baks\b"]),
    ("AWS", [r"\baws\b", r"\bamazon web services\b"]),
    ("Azure", [r"\bazure\b", r"\bmicrosoft azure\b"]),
    ("GCP", [r"\bgcp\b", r"\bgoogle cloud\b"]),
    ("Terraform", [r"\bterraform\b", r"\bopentofu\b"]),
    ("Ansible", [r"\bansible\b"]),
    ("Docker", [r"\bdocker\b", r"\bcontainerd\b", r"\bcontainers?\b"]),
    (
        "CI/CD",
        [
            r"\bci\s*/\s*cd\b",
            r"\bcicd\b",
            r"\bjenkins\b",
            r"\bgithub actions\b",
            r"\bgitlab ci\b",
            r"\bcircleci\b",
            r"\bargo\s*cd\b",
            r"\bargocd\b",
            r"\bspinnaker\b",
        ],
    ),
    ("Linux", [r"\blinux\b", r"\bubuntu\b", r"\bcentos\b", r"\brhel\b"]),
    ("Python", [r"\bpython\b"]),
    ("Bash/Shell", [r"\bbash\b", r"\bshell scripting\b", r"\bzsh\b"]),
    ("Go", [r"\bgolang\b", r"\bgo programming\b", r"(?<![a-z])go\s+\(?language"]),
    ("Helm", [r"\bhelm\b"]),
    ("Prometheus", [r"\bprometheus\b"]),
    ("Grafana", [r"\bgrafana\b"]),
    ("ELK/Elastic", [r"\belk\b", r"\belasticsearch\b", r"\bkibana\b", r"\blogstash\b"]),
    ("Datadog", [r"\bdatadog\b"]),
    ("New Relic", [r"\bnew relic\b"]),
    ("Splunk", [r"\bsplunk\b"]),
    ("PagerDuty", [r"\bpagerduty\b"]),
    ("Istio", [r"\bistio\b"]),
    ("Service Mesh", [r"\bservice mesh\b", r"\blinkerd\b", r"\bconsul\b"]),
    ("Vault", [r"\bvault\b", r"\bhashicorp vault\b"]),
    ("Packer", [r"\bpacker\b"]),
    ("Pulumi", [r"\bpulumi\b"]),
    ("CloudFormation", [r"\bcloudformation\b"]),
    ("CDK", [r"\baws cdk\b", r"\bcdk\b"]),
    ("Postgres", [r"\bpostgres\b", r"\bpostgresql\b"]),
    ("MySQL", [r"\bmysql\b"]),
    ("MongoDB", [r"\bmongodb\b", r"\bmongo\b"]),
    ("Redis", [r"\bredis\b"]),
    ("Kafka", [r"\bkafka\b"]),
    ("RabbitMQ", [r"\brabbitmq\b"]),
    ("Nginx", [r"\bnginx\b"]),
    ("Networking", [r"\bnetworking\b", r"\btcp/ip\b", r"\bbgp\b", r"\bvpc\b"]),
    (
        "Security",
        [r"\bsecurity\b", r"\bdevsecops\b", r"\bsoc\s*2\b", r"\biso\s*27001\b"],
    ),
    ("Lambda", [r"\bAWS lambda\b", r"\blambda function\b", r"\bserverless\b"]),
    ("ECS/Fargate", [r"\becs\b", r"\bfargate\b"]),
    ("Argo CD", [r"\bargo\s*cd\b", r"\bargocd\b", r"\bgitops\b"]),
    ("Observability", [r"\bobservability\b", r"\bopentelemetry\b", r"\botel\b"]),
    # === 2026 trending tech (CNCF + market data) ===
    ("eBPF/Cilium", [r"\bebpf\b", r"\bcilium\b", r"\btetragon\b"]),
    ("OpenTofu", [r"\bopentofu\b", r"\btofu\b"]),
    (
        "Backstage/IDP",
        [
            r"\bbackstage\b",
            r"\binternal developer platform\b",
            r"\binternal developer portal\b",
            r"\bdeveloper platform\b",
        ],
    ),
    ("Karpenter", [r"\bkarpenter\b"]),
    ("Crossplane", [r"\bcrossplane\b"]),
    ("Flux/GitOps", [r"\bflux\s*cd\b", r"\bfluxcd\b", r"\bflux\b"]),
    ("FinOps", [r"\bfinops\b", r"\bcloud cost\b", r"\bcost optimization\b"]),
    ("Snowflake", [r"\bsnowflake\b"]),
    ("Databricks", [r"\bdatabricks\b"]),
    ("Airflow", [r"\bairflow\b"]),
    ("dbt", [r"\bdbt\b"]),
    ("Spark", [r"\bspark\b", r"\bapache spark\b"]),
    ("Rust", [r"\brust\b(?!\s*belt)"]),
    ("eBPF Security", [r"\bfalco\b", r"\bgvisor\b"]),
    (
        "AI/MLOps",
        [
            r"\bml\s*ops\b",
            r"\bmlops\b",
            r"\bsagemaker\b",
            r"\bvertex\s*ai\b",
            r"\bllmops\b",
            r"\bray\b(?!\s*charles)",
        ],
    ),
    (
        "Snyk/Sec Scanning",
        [r"\bsnyk\b", r"\bdependency-track\b", r"\btrivy\b", r"\bclair\b", r"\bsbom\b"],
    ),
    ("WAF/CDN", [r"\bcloudflare\b", r"\bfastly\b", r"\bakamai\b", r"\bwaf\b"]),
    (
        "Tracing",
        [r"\bjaeger\b", r"\bzipkin\b", r"\btracing\b", r"\bdistributed tracing\b"],
    ),
    ("Service Catalog", [r"\bservice catalog\b", r"\bplatform catalog\b"]),
    (
        "Container Security",
        [r"\bcontainer security\b", r"\baqua\b(?!\s*scope)", r"\bsysdig\b"],
    ),
]

# Compile regexes once
_COMPILED: List[Tuple[str, List[re.Pattern]]] = [
    (name, [re.compile(p, re.IGNORECASE) for p in patterns])
    for name, patterns in SKILLS
]


def extract_skills(text: str) -> List[str]:
    if not text:
        return []
    found = []
    for name, patterns in _COMPILED:
        for pat in patterns:
            if pat.search(text):
                found.append(name)
                break
    return found


# ---------------------------------------------------------------------------
# Seniority detection
# ---------------------------------------------------------------------------

JUNIOR_RE = re.compile(
    r"\b(junior|jr\.?|entry[\s-]?level|graduate|trainee|apprentice|intern|associate|בוגר)\b",
    re.IGNORECASE,
)
SENIOR_RE = re.compile(
    r"\b(senior|sr\.?|staff|principal|lead|architect|head of|expert|בכיר)\b",
    re.IGNORECASE,
)
MID_RE = re.compile(r"\b(mid[\s-]?level|intermediate)\b", re.IGNORECASE)


def classify_seniority(title: str, description: str = "") -> str:
    t = title or ""
    if SENIOR_RE.search(t):
        return "Senior"
    if JUNIOR_RE.search(t):
        return "Junior"
    if MID_RE.search(t):
        return "Mid"
    # Fallback: scan first 400 chars of description
    desc_head = (description or "")[:400]
    if SENIOR_RE.search(desc_head):
        return "Senior"
    if JUNIOR_RE.search(desc_head):
        return "Junior"
    return "Mid"  # default assumption


# ---------------------------------------------------------------------------
# Salary parsing (best-effort)
# ---------------------------------------------------------------------------

# Match patterns like "$120k - $150k", "120,000 - 150,000 USD", "ILS 30,000-40,000 monthly"
_SALARY_RE = re.compile(
    r"(?P<cur>\$|usd|ils|nis|₪|€|eur|£|gbp)?\s*(?P<low>\d{1,3}(?:[,.]?\d{3})*(?:\.\d+)?)(?:\s*[kKK])?(?:\s*[-–to]+\s*(?P<high>\d{1,3}(?:[,.]?\d{3})*(?:\.\d+)?)(?:\s*[kK])?)?(?:\s*(?P<cur2>usd|ils|nis|₪|€|eur|£|gbp|\$))?",
    re.IGNORECASE,
)

_K_RE = re.compile(r"\d+\s*[kK]\b")


def parse_salary(text: str) -> Optional[Dict[str, Any]]:
    """
    Salary parsing is conservative on purpose: we only return a number when
    the text contains an explicit salary signal AND a tight number-pattern
    that looks like a range with a currency symbol. This avoids false positives
    on phrases like "5,000+ employees" or "10,000 customers".
    """
    if not text:
        return None
    # Require BOTH a salary keyword AND a currency symbol nearby
    salary_kw = re.search(
        r"\b(salary|compensation|annual\s+pay|שכר|משכורת)\b", text, re.IGNORECASE
    )
    if not salary_kw:
        return None

    # Look for a tight pattern: $X[k] - $Y[k] or $X,XXX - $Y,XXX with surrounding currency
    tight = re.search(
        r"(?P<cur>\$|USD|ILS|NIS|₪|€|EUR|£|GBP)\s*"
        r"(?P<low>\d{2,3}(?:[,\.]?\d{3})*)\s*(?P<lk>[kK])?\s*"
        r"(?:[-–to]+|to)\s*"
        r"(?P<cur2>\$|USD|ILS|NIS|₪|€|EUR|£|GBP)?\s*"
        r"(?P<high>\d{2,3}(?:[,\.]?\d{3})*)\s*(?P<hk>[kK])?",
        text,
    )
    if not tight:
        return None
    try:
        low = float(tight.group("low").replace(",", "").replace(".", ""))
        high = float(tight.group("high").replace(",", "").replace(".", ""))
    except (ValueError, TypeError):
        return None
    if tight.group("lk"):
        low *= 1000
    if tight.group("hk"):
        high *= 1000
    # Sanity check
    if low < 10_000 or high > 1_000_000 or high < low:
        return None
    cur = (tight.group("cur") or "").upper().replace("$", "USD").replace("₪", "ILS")
    if cur == "NIS":
        cur = "ILS"
    return {"low": low, "high": high, "currency": cur or "USD"}
    # Look for explicit salary signals first
    if not re.search(r"\b(salary|compensation|pay|שכר|משכורת)\b", text, re.IGNORECASE):
        # Without an explicit signal, our regex generates too many false positives
        return None
    # Find the first plausible range
    matches = _SALARY_RE.finditer(text)
    for m in matches:
        low_raw = m.group("low")
        high_raw = m.group("high")
        if not low_raw:
            continue
        try:
            low = (
                float(low_raw.replace(",", "").replace(".", ""))
                if low_raw.count(",") > 0 or len(low_raw) > 4
                else float(low_raw.replace(",", ""))
            )
        except ValueError:
            continue
        # If "k" appears nearby in the original snippet, multiply
        snippet = text[max(0, m.start() - 5) : min(len(text), m.end() + 5)]
        if "k" in snippet.lower() or "K" in snippet:
            low *= 1000
        if low < 1000 or low > 5_000_000:
            continue
        high = None
        if high_raw:
            try:
                high = float(high_raw.replace(",", ""))
                if "k" in snippet.lower():
                    high *= 1000
                if high < low or high > 5_000_000:
                    high = None
            except ValueError:
                pass
        currency = (m.group("cur") or m.group("cur2") or "").upper().strip("$")
        if "$" in (m.group(0) or ""):
            currency = "USD"
        if not currency:
            currency = "USD"  # default assumption
        return {"low": low, "high": high, "currency": currency}
    return None


# ---------------------------------------------------------------------------
# Location bucketing
# ---------------------------------------------------------------------------

LOCATION_BUCKETS = [
    ("Tel Aviv", [r"tel[\s-]?aviv", r"תל[\s-]?אביב"]),
    ("Herzliya", [r"herzliya", r"הרצליה"]),
    ("Ramat Gan", [r"ramat[\s-]?gan", r"רמת[\s-]?גן"]),
    ("Petah Tikva", [r"petah[\s-]?tikva", r"פתח[\s-]?תקוה", r"פתח[\s-]?תקווה"]),
    ("Jerusalem", [r"jerusalem", r"ירושלים"]),
    ("Haifa", [r"haifa", r"חיפה"]),
    ("Netanya", [r"netanya", r"נתניה"]),
    ("Raanana", [r"raanana", r"רעננה"]),
    ("Beer Sheva", [r"beer[\s-]?sheva", r"באר[\s-]?שבע"]),
    ("Remote", [r"\bremote\b", r"work from home", r"wfh"]),
    ("Hybrid", [r"\bhybrid\b"]),
]
_COMPILED_LOCS = [
    (name, [re.compile(p, re.IGNORECASE) for p in pats])
    for name, pats in LOCATION_BUCKETS
]


def bucket_location(loc: str) -> str:
    if not loc:
        return "Israel (other)"
    for name, pats in _COMPILED_LOCS:
        for pat in pats:
            if pat.search(loc):
                return name
    return "Israel (other)"


# ---------------------------------------------------------------------------
# Main analysis
# ---------------------------------------------------------------------------


def analyze(jobs: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Main entry point: take a list of normalized jobs, return a stats dict."""
    total = len(jobs)
    by_source = Counter(j.get("source", "unknown") for j in jobs)

    # Per-job analysis
    skill_counts: Counter = Counter()
    company_counts: Counter = Counter()
    seniority_counts: Counter = Counter()
    location_counts: Counter = Counter()
    salaries: List[Dict[str, Any]] = []

    enriched: List[Dict[str, Any]] = []
    for j in jobs:
        title = j.get("title", "")
        desc = j.get("description", "")
        text = f"{title}\n{desc}"
        skills = extract_skills(text)
        seniority = classify_seniority(title, desc)
        bucket = bucket_location(j.get("location", ""))
        salary = parse_salary(text)

        for s in skills:
            skill_counts[s] += 1
        company = j.get("company", "").strip()
        if company:
            company_counts[company] += 1
        seniority_counts[seniority] += 1
        location_counts[bucket] += 1
        if salary:
            salaries.append({**salary, "title": title, "company": company})

        enriched.append(
            {
                **j,
                "skills_extracted": skills,
                "seniority": seniority,
                "location_bucket": bucket,
                "salary_parsed": salary,
            }
        )

    # Seniority percentages
    seniority_pct = {
        k: round(100 * v / total, 1) if total else 0.0
        for k, v in seniority_counts.items()
    }
    junior_pct = seniority_pct.get("Junior", 0.0)

    # Salary stats
    salary_summary = _salary_summary(salaries, enriched)

    # NEW: Salary disclosure rate (what % of postings disclose salary)
    salary_disclosure_rate = round(100 * len(salaries) / total, 1) if total else 0.0

    # NEW: HiringStrength per company:
    #   = (open_roles_count) × (1 + 0.2 × distinct_seniority_levels) × (1 + 0.1 × distinct_skill_breadth)
    # Captures volume + diversity. Higher means company is hiring across the board.
    company_strength: Dict[str, Dict[str, Any]] = {}
    company_jobs: Dict[str, List[Dict[str, Any]]] = {}
    for j in enriched:
        co = j.get("company", "").strip()
        if not co:
            continue
        company_jobs.setdefault(co, []).append(j)
    for co, co_jobs in company_jobs.items():
        cnt = len(co_jobs)
        senlevels = len({j.get("seniority") for j in co_jobs if j.get("seniority")})
        skills_seen: set = set()
        for j in co_jobs:
            for s in j.get("skills_extracted") or []:
                skills_seen.add(s)
        strength = round(cnt * (1 + 0.2 * senlevels) * (1 + 0.1 * len(skills_seen)), 1)
        company_strength[co] = {
            "open_roles": cnt,
            "seniority_levels": senlevels,
            "skill_breadth": len(skills_seen),
            "hiring_strength": strength,
        }
    top_strength = sorted(
        (
            (co, info["hiring_strength"], info["open_roles"])
            for co, info in company_strength.items()
        ),
        key=lambda x: -x[1],
    )[:15]

    # NEW: Junior-friendly companies (have at least one Junior posting)
    junior_friendly_companies: List[str] = sorted(
        {
            j.get("company", "").strip()
            for j in enriched
            if j.get("seniority") == "Junior" and j.get("company")
        }
    )

    return {
        "total_jobs": total,
        "by_source": dict(by_source),
        "top_skills": skill_counts.most_common(15),
        "top_companies": company_counts.most_common(15),
        "seniority_distribution": dict(seniority_counts),
        "seniority_pct": seniority_pct,
        "junior_pct": junior_pct,
        "junior_count": seniority_counts.get("Junior", 0),
        "junior_friendly_companies": junior_friendly_companies,
        "location_distribution": dict(location_counts.most_common()),
        "salaries_parsed_count": len(salaries),
        "salary_disclosure_rate": salary_disclosure_rate,
        "salary_summary": salary_summary,
        "company_hiring_strength": company_strength,
        "top_hiring_strength": top_strength,
        "enriched_jobs": enriched,
    }


def _salary_summary(
    salaries: List[Dict[str, Any]], enriched_jobs: List[Dict[str, Any]]
) -> Dict[str, Any]:
    """Compute median salary by seniority where possible (USD only for fairness)."""
    by_seniority: Dict[str, List[float]] = {"Junior": [], "Mid": [], "Senior": []}
    # Build a lookup from title to seniority
    seniority_lookup = {
        (j.get("title", ""), j.get("company", "")): j.get("seniority")
        for j in enriched_jobs
    }
    for s in salaries:
        if s.get("currency") != "USD":
            continue
        sn = seniority_lookup.get((s.get("title", ""), s.get("company", "")))
        if sn not in by_seniority:
            continue
        # Use midpoint when range is given
        low = s.get("low") or 0
        high = s.get("high") or low
        if low > 0:
            by_seniority[sn].append((low + high) / 2)

    def _median(xs: List[float]) -> Optional[float]:
        if not xs:
            return None
        xs = sorted(xs)
        n = len(xs)
        return xs[n // 2] if n % 2 else (xs[n // 2 - 1] + xs[n // 2]) / 2

    return {
        sn: {
            "n": len(vals),
            "median_usd": round(_median(vals), 0) if _median(vals) else None,
        }
        for sn, vals in by_seniority.items()
    }
