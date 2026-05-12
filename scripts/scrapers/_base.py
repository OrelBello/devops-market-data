"""
Common utilities shared by all scrapers.

Design goals:
  - Standard library ONLY (no pip installs needed)
  - Polite scraping (User-Agent, delays, timeouts)
  - Graceful degradation: if a source blocks us, we return [] instead of crashing
  - Unified job schema so downstream analysis works on any source

Unified Job Schema:
{
  "id": str,                # source-prefixed unique id (e.g. "linkedin:38291...")
  "source": str,            # 'linkedin' | 'remoteok' | 'alljobs' | 'drushim' | 'glassdoor' | 'jobmaster' | 'greenhouse'
  "title": str,
  "company": str,
  "location": str,          # raw location string
  "url": str,
  "posted_at": str,         # ISO timestamp if known, else ''
  "description": str,       # plain text (HTML stripped)
  "raw": dict,              # source-specific extras (kept for debugging)
}
"""

from __future__ import annotations

import gzip
import hashlib
import html
import json
import logging
import random
import re
import socket
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

LOG = logging.getLogger("devops_market_intel")
if not LOG.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(
        logging.Formatter("[%(asctime)s] %(levelname)s %(name)s: %(message)s")
    )
    LOG.addHandler(handler)
    LOG.setLevel(logging.INFO)

# ---------------------------------------------------------------------------
# Common constants
# ---------------------------------------------------------------------------

USER_AGENTS = [
    # Realistic desktop UAs - rotate to be polite
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
]

# Search keywords that map to "DevOps / Platform / SRE / Cloud" roles
DEVOPS_QUERIES = [
    "devops",
    "site reliability",
    "platform engineer",
    "cloud engineer",
    "infrastructure engineer",
    "sre",
]

# Israeli location terms (English + Hebrew)
ISRAEL_LOCATION_TERMS = [
    "israel",
    "tel aviv",
    "jerusalem",
    "haifa",
    "herzliya",
    "ramat gan",
    "petah tikva",
    "netanya",
    "raanana",
    "beer sheva",
    "kfar saba",
    "rehovot",
    "ישראל",
    "תל אביב",
    "תל-אביב",
    "ירושלים",
    "חיפה",
    "הרצליה",
    "רמת גן",
    "פתח תקווה",
    "נתניה",
    "רעננה",
    "באר שבע",
    "כפר סבא",
    "רחובות",
]

# DevOps-related Hebrew terms (used when scraping Hebrew boards)
HEBREW_DEVOPS_TERMS = [
    "devops",
    "DevOps",
    "Devops",
    "סיסטם",
    "סייט ריליאביליטי",
    "אינפרה",
    "אינפרסטרקצ׳ר",
    "ענן",
    "פלטפורמה",
    "קוברנטיס",
    "Kubernetes",
    "AWS",
    "Azure",
    "Cloud",
    "SRE",
]

DEFAULT_TIMEOUT = 20  # seconds
DEFAULT_DELAY = (0.6, 1.6)  # randomized seconds between requests


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------


def _build_request(
    url: str,
    *,
    headers: Optional[Dict[str, str]] = None,
    method: str = "GET",
    data: Optional[bytes] = None,
) -> urllib.request.Request:
    base_headers = {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,application/json;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9,he;q=0.8",
        "Accept-Encoding": "gzip, deflate",
        "Connection": "close",
    }
    if headers:
        base_headers.update(headers)
    return urllib.request.Request(url, data=data, headers=base_headers, method=method)


def http_get(
    url: str,
    *,
    headers: Optional[Dict[str, str]] = None,
    timeout: int = DEFAULT_TIMEOUT,
    expect_json: bool = False,
    max_retries: int = 2,
) -> Optional[Any]:
    """
    GET a URL and return decoded body. Returns None on hard failure.
    Returns a dict/list when expect_json=True (or auto-detected by content-type).
    """
    last_err: Optional[Exception] = None
    for attempt in range(max_retries + 1):
        try:
            req = _build_request(url, headers=headers)
            ctx = ssl.create_default_context()
            with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
                raw = resp.read()
                if resp.headers.get("Content-Encoding", "").lower() == "gzip":
                    raw = gzip.decompress(raw)
                ctype = (resp.headers.get("Content-Type") or "").lower()
                charset = "utf-8"
                m = re.search(r"charset=([a-zA-Z0-9_\-]+)", ctype)
                if m:
                    charset = m.group(1)
                try:
                    text = raw.decode(charset, errors="replace")
                except LookupError:
                    text = raw.decode("utf-8", errors="replace")
                if expect_json or "application/json" in ctype:
                    try:
                        return json.loads(text)
                    except json.JSONDecodeError:
                        # Some endpoints return JSON-ish prefixed; try to recover
                        cleaned = text.strip()
                        if cleaned.startswith(")]}'"):
                            cleaned = cleaned[4:]
                        try:
                            return json.loads(cleaned)
                        except Exception:
                            LOG.warning(
                                "JSON decode failed for %s; returning text", url
                            )
                            return text
                return text
        except urllib.error.HTTPError as e:
            last_err = e
            # 429 / 5xx -> retry with backoff
            if e.code in (429, 500, 502, 503, 504) and attempt < max_retries:
                sleep_s = (attempt + 1) * 2 + random.random()
                LOG.warning(
                    "HTTP %s on %s (attempt %d) - retrying in %.1fs",
                    e.code,
                    url,
                    attempt + 1,
                    sleep_s,
                )
                time.sleep(sleep_s)
                continue
            LOG.info("HTTP error %s for %s: %s", e.code, url, e.reason)
            return None
        except (
            urllib.error.URLError,
            socket.timeout,
            ssl.SSLError,
            ConnectionError,
        ) as e:
            last_err = e
            if attempt < max_retries:
                time.sleep((attempt + 1) * 1.5)
                continue
            LOG.info("Network error for %s: %s", url, e)
            return None
        except Exception as e:  # noqa: BLE001
            LOG.exception("Unexpected error fetching %s: %s", url, e)
            return None
    LOG.info(
        "Giving up on %s after %d attempts (last error: %s)",
        url,
        max_retries + 1,
        last_err,
    )
    return None


def polite_sleep(rng: tuple = DEFAULT_DELAY) -> None:
    time.sleep(random.uniform(*rng))


# ---------------------------------------------------------------------------
# Text helpers
# ---------------------------------------------------------------------------

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def strip_html(s: str) -> str:
    if not s:
        return ""
    s = _TAG_RE.sub(" ", s)
    s = html.unescape(s)
    s = _WS_RE.sub(" ", s)
    return s.strip()


def looks_devops(title: str, description: str = "") -> bool:
    """
    Heuristic: does this job look like a DevOps/Platform/SRE/Cloud role?

    Title-driven: we ONLY consider description as a tie-breaker when the title
    is too generic (e.g. "Engineer"). Keywords like "Kubernetes" or "Terraform"
    appearing only in the description does not qualify - many backend, data,
    and ML roles mention these tools without being DevOps positions.
    """
    t = (title or "").lower().strip()
    if not t:
        return False

    # Strong title needles - any of these in the title means yes
    strong_title = [
        "devops",
        "site reliability",
        "sre",
        " sre ",
        "platform engineer",
        "platform engineering",
        "infrastructure engineer",
        "infra engineer",
        "cloud engineer",
        "cloud architect",
        "cloud ops",
        "cloudops",
        "kubernetes engineer",
        "build engineer",
        "release engineer",
        "production engineer",
        "system engineer",
        "sysadmin",
        "system administrator",
        "linux engineer",
        "reliability engineer",
        "automation engineer",
        "ci/cd",
        "cicd",
        "סיסטם",
        "אינפרה",
        "devsecops",
        "platform team",
        "infrastructure team",
        "cloud platform",
        "cloud security engineer",
    ]
    if any(n in t for n in strong_title):
        # Exclude obvious non-matches that contain the substring
        excludes = [
            "sales",
            "marketing",
            "recruiter",
            "hr ",
            "human resources",
            "manager of devops marketing",
        ]
        if any(x in t for x in excludes):
            return False
        return True

    # Title doesn't strongly match - fall through to description ONLY for very strong phrases
    d = (description or "").lower()
    very_strong_desc = [
        "devops engineer",
        "site reliability engineer",
        "platform engineer",
        "we are looking for a devops",
        "looking for a senior devops",
        "as a devops engineer",
        "as an sre",
    ]
    return any(s in d for s in very_strong_desc)


def looks_israeli(location: str, description: str = "") -> bool:
    """Heuristic for Israeli location."""
    text = ((location or "") + " " + (description or "")).lower()
    return any(term in text for term in (t.lower() for t in ISRAEL_LOCATION_TERMS))


def make_id(source: str, *parts: Any) -> str:
    raw = source + "::" + "::".join(str(p) for p in parts if p is not None)
    return source + ":" + hashlib.md5(raw.encode("utf-8")).hexdigest()[:16]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def normalize_job(
    *,
    source: str,
    job_id: str,
    title: str,
    company: str,
    location: str,
    url: str,
    posted_at: str = "",
    description: str = "",
    raw: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    return {
        "id": job_id,
        "source": source,
        "title": (title or "").strip(),
        "company": (company or "").strip(),
        "location": (location or "").strip(),
        "url": (url or "").strip(),
        "posted_at": posted_at or "",
        "description": strip_html(description or "")[:4000],
        "raw": raw or {},
        "scraped_at": now_iso(),
    }
