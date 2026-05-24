"""
Junior Career-Pivot Classifier
==============================

Identifies job postings that are GREAT for someone trying to break into DevOps
from an adjacent role.

Two-stage filter:
  1. TITLE filter — is this an entry-level / pivot-friendly role?
     (IT Support, Help Desk, Junior SysAdmin, Junior DevOps, Cloud Trainee, Intern, etc.)
  2. STACK filter — does the JD require/mention enough DevOps stack to be a real
     learning path? (≥2 of Linux, Python, CI/CD, AWS, Docker, Kubernetes, etc.)

Output for each match:
  - "junior_bucket": which bucket the role falls into
  - "stack_matched": which DevOps stack items are required/mentioned
  - "stack_count": how many stack items
  - "learning_score": 0-100, weights stack breadth + bucket relevance
"""

from __future__ import annotations

import re
from typing import Dict, List, Tuple

# ---------------------------------------------------------------------------
# Title buckets (English + Hebrew) — order matters: first match wins
# ---------------------------------------------------------------------------

TITLE_BUCKETS: List[Tuple[str, List[str]]] = [
    (
        "Junior DevOps",
        [
            r"\bjunior\b.*\bdevops\b",
            r"\bjr\.?\b.*\bdevops\b",
            r"\bdevops\b.*\bjunior\b",
            r"\bdevops\b.*\bjr\.?\b",
            r"\bentry[\s-]?level\b.*\bdevops\b",
            r"\bassociate\b.*\bdevops\b",
            r"\bdevops\b.*\bassociate\b",
            r"\bdevops\s*engineer\s*[I1]\b",  # "DevOps Engineer I" or "DevOps Engineer 1"
            r"\bdevops\b.*\b(intern|apprentice|new\s*grad|graduate)\b",
            r"\b(intern|apprentice|new\s*grad|graduate)\b.*\bdevops\b",
        ],
    ),
    (
        "Junior SRE / Cloud / Platform",
        [
            r"\bjunior\b.*\b(sre|cloud|platform|infra|infrastructure)\b",
            r"\bentry[\s-]?level\b.*\b(sre|cloud|platform|infra)\b",
            r"\b(sre|cloud|platform|infra)\b.*\bjunior\b",
            r"\b(graduate|trainee|intern|associate)\b.*\b(devops|sre|cloud|platform|infra)\b",
            r"\b(devops|sre|cloud|platform|infra)\b.*\b(graduate|trainee|intern|associate)\b",
            r"\b(sre|cloud|platform|infra)\s*engineer\s*[I1]\b",
            r"\bsite\s*reliability\s*engineer\s*[I1]\b",
            r"\bcloud\s*solutions\s*engineer\s*[I1]\b",
            r"\bnew\s*grad\b.*\b(cloud|platform|infra|devops|sre|site reliability)\b",
            r"\b(cloud|platform|infra|devops|sre|site reliability)\b.*\bnew\s*grad\b",
        ],
    ),
    (
        "Junior SysAdmin / Linux",
        [
            r"\bjunior\b.*\b(sysadmin|system administrator|linux)\b",
            r"\b(sysadmin|system administrator|linux)\b.*\bjunior\b",
            r"\bjunior\b.*\bsystem(s)? engineer\b",
            r"\bjr\.?\b.*\bsystem(s)? engineer\b",
            r"\bassociate\b.*\b(sysadmin|system administrator|linux)\b",
        ],
    ),
    (
        "Help Desk / IT Support",
        [
            r"\bhelp\s*desk\b",
            r"\bservice\s*desk\b",
            r"\bIT support\b",
            r"\btechnical support\b",
            r"\bdesktop support\b",
            r"\bsupport engineer\b",
            r"\bIT specialist\b",
            r"\bIT technician\b",
            r"\btier\s*[12]\b",
            r"\bL[12]\s*support\b",
            r"\btomech\b",  # תומך - Hebrew
            r"תמיכה",
            r"הלפדסק",
            r"מוקד\s*תמיכה",
        ],
    ),
    (
        "SysAdmin / NOC",
        [
            r"\bsystem administrator\b",
            r"\bsysadmin\b",
            r"\bnoc\b",
            r"\bnetwork operations\b",
            r"\boperations engineer\b(?!.*senior)",
            r"\bIT operations\b",
            r"\binfrastructure technician\b",
            r"\bsystems? engineer\b(?!.*senior)",
            r"מנהל\s*רשת",
            r"מנהל\s*מערכת",
            r"מנהל\s*שרתים",
            r"סיסטם",
        ],
    ),
    (
        "Trainee / Bootcamp Grad",
        [
            r"\btrainee\b",
            r"\bbootcamp\b",
            r"\bapprentice\b",
            r"\bcadet\b",
            r"\bintern\b",
            r"\binternship\b",
            r"\bcareer\s+(transition|switch|change)\b",
            r"\bno\s+experience\b",
            r"\bnew\s*grad(uate)?\b",
            r"\bgraduate\s+program\b",
            r"\buniversity\s+graduate\b",
            r"\bstudent\b",
            r"\bworking\s+student\b",
            r"\bstudies?\s+track\b",
            r"בוגר.*קורס",
            r"מתאמן",
            r"חניך",
            r"מתחיל",
            r"ללא\s+ניסיון",
            r"סטודנט",
        ],
    ),
]

EXCLUDE_TITLE = [
    # Sales/marketing/recruiting/non-tech support
    r"\bsales\b",
    r"\bmarketing\b",
    r"\brecruiter\b",
    r"\baccount manager\b",
    r"\bcustomer success\b",
    r"\bcustomer service\b(?!.*technical)",
    r"\bproduct manager\b",
    r"\bcontent\b",
    r"\bsocial media\b",
    r"\bdesigner\b",
    r"\bgraphic\b",
    r"\bui[/\s]?ux\b",
    # Senior/lead in title means it's not entry-level even if it says "system engineer"
    r"\bsenior\b",
    r"\blead\b(?!.*support)",  # allow "Lead Support" but not generic "Lead"
    r"\bprincipal\b",
    r"\bstaff\b",
    r"\barchitect\b",
    r"\bhead of\b",
    r"\bdirector\b",
    r"\bmanager\b(?!.*support)",  # allow "Support Manager"
    r"\bvp\b",
]

_BUCKET_PATTERNS: List[Tuple[str, List[re.Pattern]]] = [
    (name, [re.compile(p, re.IGNORECASE) for p in patterns])
    for name, patterns in TITLE_BUCKETS
]
_EXCLUDE_PATTERNS = [re.compile(p, re.IGNORECASE) for p in EXCLUDE_TITLE]


# ---------------------------------------------------------------------------
# DevOps stack taxonomy (what we want to see in JD requirements)
# ---------------------------------------------------------------------------

STACK_ITEMS: List[Tuple[str, List[str]]] = [
    (
        "Linux",
        [
            r"\blinux\b",
            r"\bubuntu\b",
            r"\bcentos\b",
            r"\brhel\b",
            r"\bredhat\b",
            r"\bdebian\b",
            r"לינוקס",
        ],
    ),
    ("Python", [r"\bpython\b", r"פייתון"]),
    (
        "Bash/Shell",
        [
            r"\bbash\b",
            r"\bshell scripting\b",
            r"\bzsh\b",
            r"\bshell script\b",
            r"\bscripting\b",
            r"סקריפטים",
            r"באש",
        ],
    ),
    (
        "AWS",
        [r"\baws\b", r"\bamazon web services\b", r"\bec2\b", r"\bs3\b", r"\blambda\b"],
    ),
    ("Azure", [r"\bazure\b", r"\bmicrosoft azure\b"]),
    ("GCP", [r"\bgcp\b", r"\bgoogle cloud\b"]),
    ("Cloud (any)", [r"\bcloud\b", r"ענן", r"שירותי ענן"]),
    ("Docker", [r"\bdocker\b", r"\bcontainer(s|ization)?\b", r"קונטיינר"]),
    (
        "Kubernetes",
        [
            r"\bkubernetes\b",
            r"\bk8s\b",
            r"\beks\b",
            r"\bgke\b",
            r"\baks\b",
            r"קוברנטיס",
        ],
    ),
    (
        "CI/CD",
        [
            r"\bci\s*/\s*cd\b",
            r"\bcicd\b",
            r"\bjenkins\b",
            r"\bgithub actions\b",
            r"\bgitlab ci\b",
            r"\bcircleci\b",
            r"\bpipelines?\b",
        ],
    ),
    ("Git", [r"\bgit\b(?!hub)", r"\bgithub\b", r"\bgitlab\b", r"\bversion control\b"]),
    (
        "Terraform/IaC",
        [
            r"\bterraform\b",
            r"\bopentofu\b",
            r"\binfrastructure as code\b",
            r"\biac\b",
            r"\bcloudformation\b",
            r"\bansible\b",
            r"\bpuppet\b",
            r"\bchef\b",
        ],
    ),
    (
        "Networking",
        [
            r"\bnetworking\b",
            r"\btcp/ip\b",
            r"\bdns\b",
            r"\bvpn\b",
            r"\bload balanc(er|ing)\b",
            r"\brouting\b",
            r"\bfirewall\b",
            r"רשתות",
        ],
    ),
    (
        "Monitoring",
        [
            r"\bprometheus\b",
            r"\bgrafana\b",
            r"\bdatadog\b",
            r"\bnew relic\b",
            r"\bsplunk\b",
            r"\bnagios\b",
            r"\bzabbix\b",
            r"\bobservability\b",
            r"\bmonitoring\b",
        ],
    ),
    (
        "Databases",
        [
            r"\bmysql\b",
            r"\bpostgres(ql)?\b",
            r"\bmongo(db)?\b",
            r"\bredis\b",
            r"\bsql server\b",
        ],
    ),
    (
        "Virtualization",
        [
            r"\bvmware\b",
            r"\bvsphere\b",
            r"\bhyper-?v\b",
            r"\bvirtualization\b",
            r"\besxi\b",
        ],
    ),
    ("Active Directory", [r"\bactive directory\b", r"\bldap\b", r"\bgroup policy\b"]),
    ("Windows Server", [r"\bwindows server\b", r"\bwin(dows)?\s*(server|admin)\b"]),
    ("Automation", [r"\bautomation\b", r"\bautomate\b", r"אוטומציה"]),
    ("Troubleshooting", [r"\btroubleshoot(ing)?\b", r"\bdiagnose\b", r"\bdebugging\b"]),
]

_STACK_PATTERNS: List[Tuple[str, List[re.Pattern]]] = [
    (name, [re.compile(p, re.IGNORECASE) for p in patterns])
    for name, patterns in STACK_ITEMS
]

# Items that count as "core DevOps stack" for our learning-path purpose.
# Active Directory / Virtualization / Databases on their own don't make a role
# DevOps-pivot-friendly — but they add learning value when combined with cloud/scripting.
CORE_STACK = {
    "Linux",
    "Python",
    "Bash/Shell",
    "AWS",
    "Azure",
    "GCP",
    "Docker",
    "Kubernetes",
    "CI/CD",
    "Git",
    "Terraform/IaC",
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def classify_title(title: str) -> str:
    """Return junior bucket name, or '' if not junior-pipeline."""
    if not title:
        return ""
    t = title.lower()
    # First exclude
    for pat in _EXCLUDE_PATTERNS:
        if pat.search(t):
            # Special exception: "Senior DevOps" should be excluded but "Senior Support Engineer" is OK?
            # No — the exclude list is intentional. Senior of anything is not entry-level.
            return ""
    # Then match buckets
    for bucket_name, patterns in _BUCKET_PATTERNS:
        for pat in patterns:
            if pat.search(t):
                return bucket_name
    return ""


def extract_stack(text: str) -> List[str]:
    """Return list of DevOps stack items mentioned in text."""
    if not text:
        return []
    found = []
    for name, patterns in _STACK_PATTERNS:
        for pat in patterns:
            if pat.search(text):
                found.append(name)
                break
    return found


def learning_score(stack_items: List[str], bucket: str) -> int:
    """0-100 score for how good this role is as a DevOps learning path."""
    if not stack_items:
        return 0
    core_count = sum(1 for s in stack_items if s in CORE_STACK)
    other_count = len(stack_items) - core_count

    # Base: core stack matters most
    score = core_count * 12 + other_count * 4

    # Bucket multiplier - junior-titled DevOps roles are higher value than help desk
    bucket_weights = {
        "Junior DevOps": 1.4,
        "Junior SRE / Cloud / Platform": 1.3,
        "Junior SysAdmin / Linux": 1.15,
        "SysAdmin / NOC": 1.0,
        "Help Desk / IT Support": 0.85,
        "Trainee / Bootcamp Grad": 1.1,
    }
    score = int(score * bucket_weights.get(bucket, 1.0))
    return min(score, 100)


# Per-bucket thresholds. Stronger junior buckets get more lenient stack requirements
# because the title alone signals career intent. Help Desk/SysAdmin titles are more
# ambiguous so we require a stronger stack signal in the JD.
# Looser thresholds — junior market is small, prioritize coverage over precision.
# Explicit junior titles (Junior DevOps, Junior SRE, Trainee) require ZERO stack proof.
# Less explicit (SysAdmin, Help Desk) require 1 stack item.
BUCKET_THRESHOLDS = {
    "Junior DevOps": (0, 0),  # title alone is sufficient
    "Junior SRE / Cloud / Platform": (0, 0),
    "Trainee / Bootcamp Grad": (0, 0),
    "Junior SysAdmin / Linux": (1, 0),
    "Help Desk / IT Support": (1, 0),
    "SysAdmin / NOC": (1, 0),
}

# Buckets where the title is so explicit that we skip stack-checking entirely.
EXPLICIT_JUNIOR_BUCKETS = {
    "Junior DevOps",
    "Junior SRE / Cloud / Platform",
    "Trainee / Bootcamp Grad",
}


def is_junior_pipeline(
    title: str, description: str = "", min_stack: int = None, min_core_stack: int = None
) -> Dict:
    """
    Decide if a posting belongs in the Junior Pipeline.

    Returns a dict:
      {match, bucket, stack_matched, stack_count, core_stack_count, learning_score, reason}

    Thresholds default to BUCKET_THRESHOLDS for the matched bucket; override with
    explicit min_stack/min_core_stack if needed.
    """
    bucket = classify_title(title)
    if not bucket:
        return {"match": False, "reason": "title not junior-pipeline"}

    # Always extract stack for scoring + UI even if we don't gate on it
    text = f"{title}\n{description}"
    stack = extract_stack(text)
    core = [s for s in stack if s in CORE_STACK]

    # Short-circuit for explicit junior buckets: title alone is enough proof
    if bucket in EXPLICIT_JUNIOR_BUCKETS:
        return {
            "match": True,
            "bucket": bucket,
            "stack_matched": stack,
            "stack_count": len(stack),
            "core_stack_count": len(core),
            "learning_score": learning_score(stack, bucket),
            "reason": "explicit junior title (no stack proof needed)",
        }

    bucket_min, bucket_core = BUCKET_THRESHOLDS.get(bucket, (2, 1))
    if min_stack is None:
        min_stack = bucket_min
    if min_core_stack is None:
        min_core_stack = bucket_core
    if len(stack) < min_stack:
        return {
            "match": False,
            "bucket": bucket,
            "stack_matched": stack,
            "stack_count": len(stack),
            "reason": f"only {len(stack)} stack item(s) (need {min_stack})",
        }
    if len(core) < min_core_stack:
        return {
            "match": False,
            "bucket": bucket,
            "stack_matched": stack,
            "stack_count": len(stack),
            "core_stack_count": len(core),
            "reason": f"no core stack items (need {min_core_stack})",
        }
    score = learning_score(stack, bucket)
    return {
        "match": True,
        "bucket": bucket,
        "stack_matched": stack,
        "stack_count": len(stack),
        "core_stack_count": len(core),
        "learning_score": score,
        "reason": f"{bucket} | {len(stack)} stack items ({len(core)} core)",
    }
