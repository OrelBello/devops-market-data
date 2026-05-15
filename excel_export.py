"""
Stdlib-only Excel (.xlsx) writer.

We hand-craft the Office Open XML format: an .xlsx is just a ZIP archive
containing a few XML files. No `openpyxl`, no `pandas`, no `pip install`.

Outputs:
  reports/devops-jobs-israel-<YYYY-WW>.xlsx      — dated weekly snapshot
  reports/devops-jobs-israel-latest.xlsx         — convenience copy for the
                                                   public download URL

Multi-sheet workbook with these tabs:
  Summary, All Jobs, Skills, Companies, Hiring Strength, Seniority,
  Locations, Junior Pipeline, Junior Top Picks, Diagnostics

Features:
  - Bold formatted headers
  - Frozen header row on data tabs
  - Auto-filter on "All Jobs" + "Junior Pipeline"
  - Hyperlinks on the URL columns
  - Numbers formatted as numbers, percents as percents

Run standalone:
  python3 scripts/excel_export.py
"""

from __future__ import annotations

import io
import json
import os
import re
import zipfile
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple
from xml.sax.saxutils import escape as xml_escape

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPORTS = os.path.join(ROOT, "reports")


# ---------------------------------------------------------------------------
# XML builders (Office Open XML / SpreadsheetML)
# ---------------------------------------------------------------------------

CONTENT_TYPES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
  {sheet_overrides}
  <Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>
  <Override PartName="/xl/sharedStrings.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sharedStrings+xml"/>
</Types>"""

ROOT_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
</Relationships>"""

STYLES_XML = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
  <fonts count="3">
    <font><sz val="11"/><name val="Calibri"/></font>
    <font><b/><sz val="11"/><name val="Calibri"/><color rgb="FFFFFFFF"/></font>
    <font><b/><sz val="14"/><name val="Calibri"/></font>
  </fonts>
  <fills count="3">
    <fill><patternFill patternType="none"/></fill>
    <fill><patternFill patternType="gray125"/></fill>
    <fill><patternFill patternType="solid"><fgColor rgb="FF1F3A5F"/><bgColor indexed="64"/></patternFill></fill>
  </fills>
  <borders count="2">
    <border><left/><right/><top/><bottom/><diagonal/></border>
    <border><left style="thin"><color rgb="FFCCCCCC"/></left><right style="thin"><color rgb="FFCCCCCC"/></right><top style="thin"><color rgb="FFCCCCCC"/></top><bottom style="thin"><color rgb="FFCCCCCC"/></bottom></border>
  </borders>
  <cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>
  <cellXfs count="6">
    <xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>
    <xf numFmtId="0" fontId="1" fillId="2" borderId="1" xfId="0" applyFont="1" applyFill="1" applyBorder="1" applyAlignment="1"><alignment horizontal="left" vertical="center"/></xf>
    <xf numFmtId="0" fontId="2" fillId="0" borderId="0" xfId="0" applyFont="1"/>
    <xf numFmtId="0" fontId="0" fillId="0" borderId="1" xfId="0" applyBorder="1"/>
    <xf numFmtId="0" fontId="0" fillId="0" borderId="1" xfId="0" applyBorder="1" applyAlignment="1"><alignment wrapText="1" vertical="top"/></xf>
    <xf numFmtId="9" fontId="0" fillId="0" borderId="1" xfId="0" applyBorder="1" applyNumberFormat="1"/>
  </cellXfs>
  <cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>
</styleSheet>"""


# Style IDs:
#   0 = default
#   1 = header (bold white on dark blue)
#   2 = big title
#   3 = bordered cell
#   4 = bordered + wrap text
#   5 = percentage


def _col_letter(idx: int) -> str:
    """0 -> A, 1 -> B, ..., 26 -> AA."""
    s = ""
    n = idx + 1
    while n > 0:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


def _is_url(s: str) -> bool:
    return isinstance(s, str) and (s.startswith("http://") or s.startswith("https://"))


def _build_sheet_xml(
    rows: List[List[Any]],
    shared_strings: Dict[str, int],
    *,
    freeze_header: bool = True,
    autofilter: bool = False,
    title_row: Optional[str] = None,
    url_columns: Optional[List[int]] = None,
) -> Tuple[str, List[Tuple[str, str]]]:
    """
    Return (sheet_xml, hyperlinks) where hyperlinks is a list of (cell_ref, url).
    rows: 2D array. First row is treated as the column header (bold style).
    """
    url_columns = url_columns or []
    hyperlinks: List[Tuple[str, str, str]] = []  # (cell_ref, url, display)

    row_xml_parts: List[str] = []
    n_rows = len(rows)
    max_cols = max((len(r) for r in rows), default=0)

    # Optional title row at the very top
    excel_row_offset = 1
    if title_row:
        title_cell = (
            f'<c r="A1" s="2" t="inlineStr"><is><t>{xml_escape(title_row)}</t></is></c>'
        )
        row_xml_parts.append(f'<row r="1">{title_cell}</row>')
        excel_row_offset = 3  # title + blank + header

        # Build header row at Excel row 3
        if rows:
            header_cells = []
            for ci, val in enumerate(rows[0]):
                cell_ref = f"{_col_letter(ci)}3"
                idx = _intern_string(str(val), shared_strings)
                header_cells.append(f'<c r="{cell_ref}" s="1" t="s"><v>{idx}</v></c>')
            row_xml_parts.append(f'<row r="3">{"".join(header_cells)}</row>')

        # Body rows
        for ri, row in enumerate(rows[1:], start=4):
            cells = []
            for ci, val in enumerate(row):
                cell_ref = f"{_col_letter(ci)}{ri}"
                cells.append(
                    _cell_xml(
                        cell_ref, val, shared_strings, url_columns, ci, hyperlinks
                    )
                )
            row_xml_parts.append(f'<row r="{ri}">{"".join(cells)}</row>')
    else:
        # Header row at Excel row 1
        if rows:
            header_cells = []
            for ci, val in enumerate(rows[0]):
                cell_ref = f"{_col_letter(ci)}1"
                idx = _intern_string(str(val), shared_strings)
                header_cells.append(f'<c r="{cell_ref}" s="1" t="s"><v>{idx}</v></c>')
            row_xml_parts.append(f'<row r="1">{"".join(header_cells)}</row>')
        # Body rows
        for ri, row in enumerate(rows[1:], start=2):
            cells = []
            for ci, val in enumerate(row):
                cell_ref = f"{_col_letter(ci)}{ri}"
                cells.append(
                    _cell_xml(
                        cell_ref, val, shared_strings, url_columns, ci, hyperlinks
                    )
                )
            row_xml_parts.append(f'<row r="{ri}">{"".join(cells)}</row>')

    # Freeze and autofilter
    sheet_views = ""
    if freeze_header and rows:
        freeze_row = excel_row_offset
        sheet_views = (
            '<sheetViews><sheetView workbookViewId="0">'
            f'<pane ySplit="{freeze_row}" topLeftCell="A{freeze_row + 1}" '
            'activePane="bottomLeft" state="frozen"/>'
            "</sheetView></sheetViews>"
        )

    auto_filter = ""
    if autofilter and rows:
        first_data_row = excel_row_offset if title_row else 1
        last_col = _col_letter(max_cols - 1)
        last_row = excel_row_offset + (n_rows - 1) if title_row else n_rows
        auto_filter = f'<autoFilter ref="A{first_data_row}:{last_col}{last_row}"/>'

    # Column widths — rough autofit based on max content length
    col_widths = []
    for ci in range(max_cols):
        max_len = 10
        for row in rows:
            if ci < len(row):
                v = str(row[ci]) if row[ci] is not None else ""
                if len(v) > max_len:
                    max_len = len(v)
        # Clamp width
        w = min(max(max_len + 2, 12), 60)
        col_widths.append(
            f'<col min="{ci + 1}" max="{ci + 1}" width="{w}" customWidth="1"/>'
        )

    # Hyperlinks block
    hyperlinks_xml = ""
    if hyperlinks:
        items = [
            f'<hyperlink ref="{ref}" r:id="rId{i + 1}"/>'
            for i, (ref, _url, _disp) in enumerate(hyperlinks)
        ]
        hyperlinks_xml = f"<hyperlinks>{''.join(items)}</hyperlinks>"

    sheet_xml = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"
           xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
  {sheet_views}
  <cols>{"".join(col_widths)}</cols>
  <sheetData>{"".join(row_xml_parts)}</sheetData>
  {auto_filter}
  {hyperlinks_xml}
</worksheet>"""

    # Return hyperlinks for the rels file
    return sheet_xml, [(ref, url) for ref, url, _ in hyperlinks]


def _cell_xml(
    cell_ref: str,
    val: Any,
    shared_strings: Dict[str, int],
    url_columns: List[int],
    col_idx: int,
    hyperlinks: List[Tuple[str, str, str]],
) -> str:
    """Build a single <c> cell element."""
    if val is None or val == "":
        return f'<c r="{cell_ref}" s="3"/>'
    if isinstance(val, bool):
        return f'<c r="{cell_ref}" s="3" t="b"><v>{1 if val else 0}</v></c>'
    if isinstance(val, (int, float)) and not isinstance(val, bool):
        return f'<c r="{cell_ref}" s="3"><v>{val}</v></c>'
    s = str(val)
    # If this column is a designated URL column and value is a URL, register hyperlink
    if col_idx in url_columns and _is_url(s):
        hyperlinks.append((cell_ref, s, s))
    idx = _intern_string(s, shared_strings)
    return f'<c r="{cell_ref}" s="3" t="s"><v>{idx}</v></c>'


def _intern_string(s: str, shared: Dict[str, int]) -> int:
    if s in shared:
        return shared[s]
    idx = len(shared)
    shared[s] = idx
    return idx


def _build_shared_strings(shared: Dict[str, int]) -> str:
    items = sorted(shared.items(), key=lambda x: x[1])
    si_list = [
        f'<si><t xml:space="preserve">{xml_escape(s)}</t></si>' for s, _ in items
    ]
    return f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"
     count="{len(items)}" uniqueCount="{len(items)}">
{"".join(si_list)}
</sst>"""


def _build_workbook(sheet_names: List[str]) -> str:
    sheets_xml = "".join(
        f'<sheet name="{xml_escape(name)}" sheetId="{i + 1}" r:id="rId{i + 1}"/>'
        for i, name in enumerate(sheet_names)
    )
    return f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"
          xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
  <sheets>{sheets_xml}</sheets>
</workbook>"""


def _build_workbook_rels(sheet_count: int) -> str:
    parts = []
    for i in range(sheet_count):
        parts.append(
            f'<Relationship Id="rId{i + 1}" '
            f'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
            f'Target="worksheets/sheet{i + 1}.xml"/>'
        )
    # Styles + shared strings come after sheets
    parts.append(
        f'<Relationship Id="rId{sheet_count + 1}" '
        f'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" '
        f'Target="styles.xml"/>'
    )
    parts.append(
        f'<Relationship Id="rId{sheet_count + 2}" '
        f'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/sharedStrings" '
        f'Target="sharedStrings.xml"/>'
    )
    return f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
{"".join(parts)}
</Relationships>"""


def _build_sheet_rels(hyperlinks: List[Tuple[str, str]]) -> Optional[str]:
    if not hyperlinks:
        return None
    parts = []
    for i, (_ref, url) in enumerate(hyperlinks):
        parts.append(
            f'<Relationship Id="rId{i + 1}" '
            f'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink" '
            f'Target="{xml_escape(url)}" TargetMode="External"/>'
        )
    return f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
{"".join(parts)}
</Relationships>"""


# ---------------------------------------------------------------------------
# Data prep — converts the JSON snapshots into per-sheet 2D row arrays
# ---------------------------------------------------------------------------


def _prep_sheets(
    main: Dict[str, Any], jr: Optional[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    s = main.get("stats_for_sheets", {})
    week = main.get("week", "")
    generated = main.get("generated_at", "")

    sheets: List[Dict[str, Any]] = []

    # ----- Summary -----
    summary_rows: List[List[Any]] = [["Metric", "Value"]]
    summary_rows += [
        ["Week", week],
        ["Generated", generated],
        ["Total open roles", s.get("total_jobs", 0)],
        ["New today (24h)", s.get("new_count_24h", 0)],
        ["New last 7 days", s.get("new_count_7d", 0)],
        ["Total tracked (cumulative)", s.get("total_tracked_jobs", 0)],
        ["Junior-friendly count", s.get("junior_count", 0)],
        ["Junior %", f"{s.get('junior_pct', 0)}%"],
        ["Salary disclosure rate %", f"{s.get('salary_disclosure_rate', 0)}%"],
        ["", ""],
        ["Maintained by", "Orel Bello (FlipTheScript • AWS Community Builder)"],
        ["Live site", "https://orelbello.com/devops-jobs-israel"],
        ["Data source", "https://orelbello.github.io/devops-market-data/"],
        ["", ""],
        ["Source", "Jobs"],
    ]
    for src, n in sorted((s.get("by_source") or {}).items(), key=lambda x: -x[1]):
        summary_rows.append([src, n])
    sheets.append(
        {
            "name": "Summary",
            "rows": summary_rows,
            "title": "🇮🇱 Israeli DevOps Job Market — Weekly Snapshot",
        }
    )

    # ----- All Jobs -----
    job_rows: List[List[Any]] = [
        [
            "Title",
            "Company",
            "Location",
            "Bucket",
            "Seniority",
            "Skills",
            "Source",
            "URL",
            "First Seen",
            "Days Open",
        ]
    ]
    for j in main.get("jobs_for_sheet", []):
        job_rows.append(
            [
                j.get("title", ""),
                j.get("company", ""),
                j.get("location", ""),
                j.get("location_bucket", ""),
                j.get("seniority", ""),
                j.get("skills", ""),
                j.get("source", ""),
                j.get("url", ""),
                j.get("first_seen_at", ""),
                j.get("days_open", 0),
            ]
        )
    sheets.append(
        {"name": "All Jobs", "rows": job_rows, "autofilter": True, "url_columns": [7]}
    )

    # ----- New in Last 24h -----
    new_rows: List[List[Any]] = [
        ["Title", "Company", "Location", "Seniority", "Skills", "Source", "URL"]
    ]
    for j in main.get("new_in_last_24h", []):
        new_rows.append(
            [
                j.get("title", ""),
                j.get("company", ""),
                j.get("location", ""),
                j.get("seniority", ""),
                j.get("skills", ""),
                j.get("source", ""),
                j.get("url", ""),
            ]
        )
    if len(new_rows) > 1:
        sheets.append(
            {
                "name": "🔥 Last 24h",
                "rows": new_rows,
                "url_columns": [6],
                "autofilter": True,
            }
        )

    # ----- Skills -----
    skill_rows: List[List[Any]] = [["Skill", "Mentions"]]
    for name, count in (s.get("top_skills") or [])[:25]:
        skill_rows.append([name, count])
    sheets.append({"name": "Skills", "rows": skill_rows})

    # ----- Companies -----
    co_rows: List[List[Any]] = [["Company", "Open Roles"]]
    for name, count in (s.get("top_companies") or [])[:25]:
        co_rows.append([name, count])
    sheets.append({"name": "Companies", "rows": co_rows})

    # ----- Hiring Strength -----
    hs_rows: List[List[Any]] = [["Rank", "Company", "Strength Score", "Open Roles"]]
    for i, item in enumerate((s.get("top_hiring_strength") or [])[:20], 1):
        try:
            company, strength, count = item[0], item[1], item[2]
        except (TypeError, IndexError):
            continue
        hs_rows.append([i, company, strength, count])
    sheets.append({"name": "Hiring Strength", "rows": hs_rows})

    # ----- Seniority -----
    sen_rows: List[List[Any]] = [["Seniority", "Count", "Percent"]]
    sen_dist = s.get("seniority_distribution") or {}
    sen_pct = s.get("seniority_pct") or {}
    for level in ["Junior", "Mid", "Senior"]:
        sen_rows.append([level, sen_dist.get(level, 0), f"{sen_pct.get(level, 0)}%"])
    sheets.append({"name": "Seniority", "rows": sen_rows})

    # ----- Locations -----
    loc_rows: List[List[Any]] = [["Location", "Count"]]
    for name, count in (s.get("location_distribution") or {}).items():
        loc_rows.append([name, count])
    sheets.append({"name": "Locations", "rows": loc_rows})

    # ----- Junior Pipeline (if present) -----
    if jr:
        jrs = jr.get("stats_for_sheets", {})
        # Junior Top Picks
        jr_jobs = sorted(
            jr.get("jobs_for_sheet", []), key=lambda x: -(x.get("score") or 0)
        )
        jr_rows: List[List[Any]] = [
            [
                "Rank",
                "Role",
                "Company",
                "Location",
                "Bucket",
                "Learning Score",
                "Stack",
                "Source",
                "URL",
                "First Seen",
            ]
        ]
        for i, j in enumerate(jr_jobs[:20], 1):
            jr_rows.append(
                [
                    i,
                    j.get("title", ""),
                    j.get("company", ""),
                    j.get("location", ""),
                    j.get("bucket", ""),
                    j.get("score", 0),
                    j.get("stack", ""),
                    j.get("source", ""),
                    j.get("url", ""),
                    j.get("first_seen_at", ""),
                ]
            )
        sheets.append(
            {
                "name": "🪜 Junior Top Picks",
                "rows": jr_rows,
                "url_columns": [8],
                "autofilter": True,
            }
        )

        # Junior buckets
        bucket_rows: List[List[Any]] = [["Bucket", "Count"]]
        for name, count in sorted(
            (jrs.get("bucket_distribution") or {}).items(), key=lambda x: -x[1]
        ):
            bucket_rows.append([name, count])
        sheets.append({"name": "Junior Buckets", "rows": bucket_rows})

        # Junior stack demand
        js_rows: List[List[Any]] = [["Skill", "Roles requiring it"]]
        for name, count in (jrs.get("top_stack") or [])[:25]:
            js_rows.append([name, count])
        sheets.append({"name": "Junior Stack Demand", "rows": js_rows})

    # ----- Diagnostics -----
    diag_rows: List[List[Any]] = [["Source", "Jobs", "Elapsed (s)", "OK", "Error"]]
    for src, info in (main.get("diagnostics") or {}).items():
        diag_rows.append(
            [
                src,
                info.get("jobs", 0),
                info.get("elapsed_s", 0),
                "yes" if info.get("ok") else "no",
                info.get("error", ""),
            ]
        )
    sheets.append({"name": "Diagnostics", "rows": diag_rows})

    return sheets


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def write_xlsx(
    out_path: str, main: Dict[str, Any], jr: Optional[Dict[str, Any]] = None
) -> str:
    """
    Build the .xlsx file at out_path. Returns the path.
    """
    sheets_data = _prep_sheets(main, jr)
    shared_strings: Dict[str, int] = {}

    sheet_xmls: List[str] = []
    sheet_rels_xmls: List[Optional[str]] = []
    for s in sheets_data:
        sheet_xml, hyperlinks = _build_sheet_xml(
            s["rows"],
            shared_strings,
            freeze_header=True,
            autofilter=s.get("autofilter", False),
            title_row=s.get("title"),
            url_columns=s.get("url_columns") or [],
        )
        sheet_xmls.append(sheet_xml)
        sheet_rels_xmls.append(_build_sheet_rels(hyperlinks))

    sheet_names = [s["name"] for s in sheets_data]
    sheet_overrides = "\n  ".join(
        f'<Override PartName="/xl/worksheets/sheet{i + 1}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        for i in range(len(sheets_data))
    )

    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(
            "[Content_Types].xml", CONTENT_TYPES.format(sheet_overrides=sheet_overrides)
        )
        zf.writestr("_rels/.rels", ROOT_RELS)
        zf.writestr("xl/workbook.xml", _build_workbook(sheet_names))
        zf.writestr(
            "xl/_rels/workbook.xml.rels", _build_workbook_rels(len(sheets_data))
        )
        zf.writestr("xl/styles.xml", STYLES_XML)
        zf.writestr("xl/sharedStrings.xml", _build_shared_strings(shared_strings))
        for i, xml in enumerate(sheet_xmls):
            zf.writestr(f"xl/worksheets/sheet{i + 1}.xml", xml)
            if sheet_rels_xmls[i]:
                zf.writestr(
                    f"xl/worksheets/_rels/sheet{i + 1}.xml.rels", sheet_rels_xmls[i]
                )

    return out_path


def render() -> Tuple[str, str]:
    """
    Read latest.json + jr_latest.json from reports/ and write:
      reports/devops-jobs-israel-<WEEK>.xlsx
      reports/devops-jobs-israel-latest.xlsx
    Returns (week_path, latest_path).
    """
    latest = os.path.join(REPORTS, "latest.json")
    jr_latest = os.path.join(REPORTS, "jr_latest.json")
    if not os.path.exists(latest):
        raise FileNotFoundError(f"{latest} not found — run orchestrator.py first")
    with open(latest, "r", encoding="utf-8") as f:
        main = json.load(f)
    jr: Optional[Dict[str, Any]] = None
    if os.path.exists(jr_latest):
        try:
            with open(jr_latest, "r", encoding="utf-8") as f:
                jr = json.load(f)
        except Exception:
            jr = None

    week = main.get("week", datetime.now().strftime("%Y-W%U"))
    safe_week = re.sub(r"[^A-Za-z0-9_-]", "", week)
    week_path = os.path.join(REPORTS, f"devops-jobs-israel-{safe_week}.xlsx")
    latest_path = os.path.join(REPORTS, "devops-jobs-israel-latest.xlsx")
    write_xlsx(week_path, main, jr)
    write_xlsx(latest_path, main, jr)
    print(f"✓ Wrote {week_path} ({os.path.getsize(week_path):,} bytes)")
    print(f"✓ Wrote {latest_path} ({os.path.getsize(latest_path):,} bytes)")
    return week_path, latest_path


if __name__ == "__main__":
    render()
