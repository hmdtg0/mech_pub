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


def _stamp_of(text) -> datetime:
    """A Saved-At cell as a datetime — unparseable stamps sort oldest, so
    any properly stamped block outranks them."""
    try:
        return datetime.strptime(str(text).strip(), "%d %b %Y %H:%M")
    except ValueError:
        return datetime.min


def list_drafts(project: str) -> Dict[str, dict]:
    """{draft name: {saved_by, saved_at, units, build, lines}} for a project.

    Read fresh every time, no cache: a draft exists to be picked up by a
    DIFFERENT person minutes later, and a cached empty list is a hand-off
    that looks lost.

    The NEWEST Saved-At block wins per name (19 Sep 2026): a lost race
    between two surgical saves can leave both versions' rows on the tab,
    and merging them would double a draft's lines. The older block is
    ignored here and cleaned up by the next save or delete of that name.
    """
    rows = []
    for row in _rows():
        row = list(row) + [""] * (len(HEADERS) - len(row))
        if str(row[0]).strip() != str(project).strip():
            continue
        if str(row[1]).strip():
            rows.append(row)
    newest: Dict[str, datetime] = {}
    for row in rows:
        name = str(row[1]).strip()
        when = _stamp_of(row[3])
        if name not in newest or when > newest[name]:
            newest[name] = when
    out: Dict[str, dict] = {}
    for row in rows:
        name = str(row[1]).strip()
        if _stamp_of(row[3]) != newest[name]:
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


def _matching_sheet_rows(rows, project: str, name: str) -> List[int]:
    """1-based SHEET row numbers of a draft's lines (data starts at row 2)."""
    return [i + 2 for i, r in enumerate(rows)
            if str(r[0]).strip() == str(project).strip()
            and len(r) > 1 and str(r[1]).strip() == str(name).strip()]


def _delete_sheet_rows(ws, sheet_rows: List[int]) -> None:
    """Delete 1-based sheet rows bottom-up, in contiguous runs — later
    deletions never shift the rows still waiting to be deleted."""
    if not sheet_rows:
        return
    todo = sorted(set(sheet_rows), reverse=True)
    run_end = run_start = todo[0]
    for r in todo[1:]:
        if r == run_start - 1:
            run_start = r
            continue
        ws.delete_rows(run_start, run_end)
        run_end = run_start = r
    ws.delete_rows(run_start, run_end)


def save_draft(project: str, name: str, saved_by: str, units, build: str,
               lines: List[dict]) -> str:
    """Write a draft, replacing any same-named one for this project.

    Returns "" on success, else the reason. SURGICAL since 19 Sep 2026:
    delete only the draft's own rows, then append — never clear the tab.
    The old read→clear→rewrite let two concurrent writers silently drop
    each other's drafts; "a few times a week" became routine once the
    ~25 s autosave landed. A race can now at worst leave BOTH versions'
    rows, and list_drafts keeps the newest Saved-At block per name, so
    the loser is invisible and harmless.
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
    old_rows = _matching_sheet_rows(_rows(), project, name)
    new = [[str(project), name, saved_by, stamp, str(units), str(build or ""),
            str(l.get("part", "")), str(l.get("qty", "")),
            str(l.get("recipient", "")), str(l.get("eta", "")),
            str(l.get("priority", "")), str(l.get("notes", ""))]
           for l in lines]

    def _write(ws):
        _delete_sheet_rows(ws, old_rows)
        ws.append_rows(new, value_input_option="RAW")
        return True

    try:
        _on_ws(_write)
    except Exception as exc:
        return "Could not write the draft: %s" % exc
    from utils import data_cache
    data_cache.invalidate(_SUMMARY_KEY)
    return ""


def delete_draft(project: str, name: str) -> str:
    """Remove a draft — surgically, its own rows only (19 Sep 2026).
    Returns "" on success, else the reason."""
    from utils.auth import impersonation_block

    blocked = impersonation_block()
    if blocked:
        return blocked
    old_rows = _matching_sheet_rows(_rows(), project, name)
    if not old_rows:
        from utils import data_cache
        data_cache.invalidate(_SUMMARY_KEY)
        return ""

    def _write(ws):
        _delete_sheet_rows(ws, old_rows)
        return True

    try:
        _on_ws(_write)
    except Exception as exc:
        return "Could not delete the draft: %s" % exc
    from utils import data_cache
    data_cache.invalidate(_SUMMARY_KEY)
    return ""
