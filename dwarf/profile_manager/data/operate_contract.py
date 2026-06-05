"""Data extractor for /operate/contract.

Parses two source markdown files into structured rows for the contract
progress page:

  user/milestones/milestone-1-deliverable-status.md
    -> work_goals() (4 rows): id, title, status, canonical_location
    -> deliverables() (4 rows): id, title, group, status, canonical_location
    -> m1_last_updated() : the "Last updated:" line text

  user/milestones/contract-milestones-tasklist.md
    -> future_milestones() (M2..M9, 8 entries):
       {id, label, target_date, items: [{checked: bool, text: str}, ...]}

M1 in contract-tasklist is deliberately NOT exposed -- slice-18 spec Q4-A
mandates that M1 detail is rendered exclusively from deliverable-status.md.

Defensive: missing source file -> empty list / None. Malformed row dropped
silently.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any


_M1_STATUS_LABELS = {"Not started", "In progress", "Draft", "Finalized"}

_DELIVERABLE_STATUS_PATH = "user/milestones/milestone-1-deliverable-status.md"
_CONTRACT_TASKLIST_PATH = "user/milestones/contract-milestones-tasklist.md"


def _project_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _read_source(rel_path: str) -> str | None:
    try:
        return (_project_root() / rel_path).read_text(encoding="utf-8")
    except OSError:
        return None


# --- milestone-1-deliverable-status.md parsing -----------------------------

# Work goals row: | M1-WG-N | title | status | `path` | next_action |
_WG_ROW_RE = re.compile(
    r"^\|\s*(M1-WG-\d+)\s*\|"
    r"\s*(.+?)\s*\|"
    r"\s*(.+?)\s*\|"
    r"\s*`([^`]+)`\s*\|"
    r"\s*(.+?)\s*\|\s*$"
)

# Deliverable row: | M1-DN | title | group | status | `path` | notes | next_action |
_D_ROW_RE = re.compile(
    r"^\|\s*(M1-D\d+)\s*\|"
    r"\s*(.+?)\s*\|"
    r"\s*(.+?)\s*\|"
    r"\s*(.+?)\s*\|"
    r"\s*`([^`]+)`\s*\|"
    r"\s*(.+?)\s*\|"
    r"\s*(.+?)\s*\|\s*$"
)

_LAST_UPDATED_RE = re.compile(r"^Last updated:\s*(.+?)\.\s*$")


def work_goals() -> list[dict[str, Any]]:
    """Return the 4 M1 work goals from the deliverable-status table."""
    text = _read_source(_DELIVERABLE_STATUS_PATH)
    if text is None:
        return []
    rows: list[dict[str, Any]] = []
    for line in text.splitlines():
        m = _WG_ROW_RE.match(line)
        if not m:
            continue
        wg_id, title, status, path, _next = m.groups()
        if status not in _M1_STATUS_LABELS:
            continue  # defensive: skip rows whose status isn't in the pinned set
        rows.append({
            "id": wg_id,
            "title": title,
            "status": status,
            "canonical_location": path,
        })
    return rows


def deliverables() -> list[dict[str, Any]]:
    """Return the 4 M1 deliverables from the deliverable-status table."""
    text = _read_source(_DELIVERABLE_STATUS_PATH)
    if text is None:
        return []
    rows: list[dict[str, Any]] = []
    for line in text.splitlines():
        m = _D_ROW_RE.match(line)
        if not m:
            continue
        d_id, title, group, status, path, _notes, _next = m.groups()
        if status not in _M1_STATUS_LABELS:
            continue
        rows.append({
            "id": d_id,
            "title": title,
            "group": group,
            "status": status,
            "canonical_location": path,
        })
    return rows


def m1_last_updated() -> str | None:
    """Return the 'Last updated:' line text, or None if missing/unparseable."""
    text = _read_source(_DELIVERABLE_STATUS_PATH)
    if text is None:
        return None
    for line in text.splitlines():
        m = _LAST_UPDATED_RE.match(line)
        if m:
            return m.group(1).strip()
    return None


# --- contract-milestones-tasklist.md parsing -------------------------------

# Section header: "## Milestone N: <Month> <Year>"
_MILESTONE_HEADER_RE = re.compile(r"^## Milestone (\d+):\s*(.+?)\s*$")
# Target date line: "Target date: <date>"
_TARGET_DATE_RE = re.compile(r"^Target date:\s*(.+?)\s*$")
# Item line: "- [ ] text" or "- [x] text"
_ITEM_RE = re.compile(r"^- \[([ x])\]\s*(.+?)\s*$")


def future_milestones() -> list[dict[str, Any]]:
    """Return M2..M9 from contract-milestones-tasklist.md.

    Each entry: {id, label, target_date, items: [{checked, text}, ...]}.
    M1 is deliberately excluded -- its detail is sourced from
    milestone-1-deliverable-status.md instead.
    """
    text = _read_source(_CONTRACT_TASKLIST_PATH)
    if text is None:
        return []
    milestones: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for line in text.splitlines():
        m_header = _MILESTONE_HEADER_RE.match(line)
        if m_header:
            number, label = m_header.groups()
            if current is not None and current["id"] != "M1":
                milestones.append(current)
            current = {
                "id": f"M{number}",
                "label": label,
                "target_date": "",
                "items": [],
            }
            continue
        if current is None:
            continue
        m_date = _TARGET_DATE_RE.match(line)
        if m_date:
            current["target_date"] = m_date.group(1)
            continue
        m_item = _ITEM_RE.match(line)
        if m_item and current["id"] != "M1":
            checked = m_item.group(1) == "x"
            text_str = m_item.group(2)
            current["items"].append({"checked": checked, "text": text_str})
    if current is not None and current["id"] != "M1":
        milestones.append(current)
    return milestones
