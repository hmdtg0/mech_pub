"""Shared order drafts — the hand-off between whoever fills and whoever files.

An Order-from-BOM selection used to live only in the page's session, so it
died with the browser tab. The team's actual flow is two people (Hamid,
19 Aug): an engineer fills in what to order — quantities, recipients, ETAs —
and the PM reviews and submits. The draft therefore needs a home both can
reach: the **Order Drafts** tab on the main record, one row per part line,
named per draft, readable on the sheet like everything else the app keeps.

A draft is a proposal, not an order: nothing here touches part tabs, the
Orders tab or any count. Loading one seeds the Order-from-BOM page exactly as
if the person had ticked and typed it; the review, the problem checks and the
confirm tick stay the only path to a submit. A successful submit deletes the
draft it came from — a proposal that became real orders is finished, and a
stale draft resubmitted later is the double-order this page exists to stop.
"""
from __future__ import annotations

from datetime import datetime
from typing import Dict, List, Optional

import gspread

from config import CENTRAL_SHEET_ID
from utils.google_client import with_worksheet

TAB_DRAFTS = "Order Drafts"

HEADERS = ["Project", "Draft", "Saved By", "Saved At", "Units", "Build",
           "Part ID", "Qty", "Recipient", "ETA", "Priority", "Notes"]


def _create_ws(ss: gspread.Spreadsheet) -> gspread.Worksheet:
    ws = ss.add_worksheet(title=TAB_DRAFTS, rows=200, cols=len(HEADERS))
    ws.update(values=[HEADERS], range_name="A1")
    return ws


def _on_ws(fn):
    return with_worksheet(TAB_DRAFTS, fn, create=_create_ws,
                          sheet_id=CENTRAL_SHEET_ID)


def _rows() -> List[List[str]]:
    values = _on_ws(lambda ws: ws.get_all_values())
    return values[1:] if values else []


def list_drafts(project: str) -> Dict[str, dict]:
    """{draft name: {saved_by, saved_at, units, build, lines}} for a project.

    Read fresh every time, no cache: a draft exists to be picked up by a
    DIFFERENT person minutes later, and a cached empty list is a hand-off
    that looks lost.
    """
    out: Dict[str, dict] = {}
    for row in _rows():
        row = list(row) + [""] * (len(HEADERS) - len(row))
        if str(row[0]).strip() != str(project).strip():
            continue
        name = str(row[1]).strip()
        if not name:
            continue
        draft = out.setdefault(name, {
            "saved_by": row[2], "saved_at": row[3],
            "units": row[4], "build": row[5], "lines": []})
        if str(row[6]).strip():
            draft["lines"].append({
                "part": row[6].strip(), "qty": row[7], "recipient": row[8],
                "eta": row[9], "priority": row[10], "notes": row[11]})
    return out


def all_drafts() -> List[dict]:
    """Every draft on the tab, one summary row each — the app-wide ledger
    line (Hamid, 28 Aug: "you need to have a real ledger for this draft").
    Keys match what ui.in_scope filters on ("Project")."""
    seen: Dict[tuple, dict] = {}
    for row in _rows():
        row = list(row) + [""] * (len(HEADERS) - len(row))
        proj, name = str(row[0]).strip(), str(row[1]).strip()
        if not name:
            continue
        d = seen.setdefault((proj, name), {
            "Project": proj, "name": name,
            "saved_by": row[2], "saved_at": row[3], "parts": 0})
        if str(row[6]).strip():
            d["parts"] += 1
    return list(seen.values())


_SUMMARY_KEY = "central:order_drafts_summary"


def all_drafts_cached() -> List[dict]:
    """The summary above, cached briefly — it sits on the landing page, so
    it must not cost a sheet read per rerun. Saves and deletes invalidate."""
    from utils import data_cache
    return data_cache.get(_SUMMARY_KEY, 60.0, all_drafts)


def save_draft(project: str, name: str, saved_by: str, units, build: str,
               lines: List[dict]) -> str:
    """Write a draft, replacing any same-named one for this project.

    Returns "" on success, else the reason. Whole-tab rewrite rather than
    surgical deletes: the tab is small, and half-replaced drafts (old rows
    surviving under a new save) are worse than a rare lost race on a tab two
    people touch a few times a week.
    """
    from utils.auth import impersonation_block

    blocked = impersonation_block()
    if blocked:
        return blocked
    name = str(name or "").strip()
    if not name:
        return "The draft needs a name — it is how the PM finds it."
    if not lines:
        return "Nothing to save — no parts are selected."

    stamp = datetime.now().strftime("%d %b %Y %H:%M")
    keep = [r for r in _rows()
            if not (str(r[0]).strip() == str(project).strip()
                    and len(r) > 1 and str(r[1]).strip() == name)]
    new = [[str(project), name, saved_by, stamp, str(units), str(build or ""),
            str(l.get("part", "")), str(l.get("qty", "")),
            str(l.get("recipient", "")), str(l.get("eta", "")),
            str(l.get("priority", "")), str(l.get("notes", ""))]
           for l in lines]

    def _write(ws):
        ws.clear()
        ws.update(values=[HEADERS] + keep + new, range_name="A1")
        return True

    try:
        _on_ws(_write)
    except Exception as exc:
        return "Could not write the draft: %s" % exc
    from utils import data_cache
    data_cache.invalidate(_SUMMARY_KEY)
    return ""


def delete_draft(project: str, name: str) -> str:
    """Remove a draft. Returns "" on success, else the reason."""
    from utils.auth import impersonation_block

    blocked = impersonation_block()
    if blocked:
        return blocked
    keep = [r for r in _rows()
            if not (str(r[0]).strip() == str(project).strip()
                    and len(r) > 1 and str(r[1]).strip() == str(name).strip())]

    def _write(ws):
        ws.clear()
        ws.update(values=[HEADERS] + keep, range_name="A1")
        return True

    try:
        _on_ws(_write)
    except Exception as exc:
        return "Could not delete the draft: %s" % exc
    from utils import data_cache
    data_cache.invalidate(_SUMMARY_KEY)
    return ""
