"""Read-only access to a project's Parts Tracker sheet.

The project's Google Sheet is the database. This module reads the tabs the
team already maintains by hand — Overview, one tab per part, Movement Log —
and never writes to them.

Two sources, same shape:

- **live**: the active project's Google Sheet, via the service account.
- **snapshot**: `data/snapshot_<sheet id>.json`, produced by
  `tools/make_snapshot.py`. Used when no credentials are present (local
  preview) so the UI can be reviewed against real content offline.
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime
from typing import List, Optional

from config import TAB_MOVEMENTS, TAB_OVERVIEW, TRACKER_NON_PART_TABS
from utils import data_cache, settings, tracker_parse
from utils.google_client import active_sheet_id, get_spreadsheet


def _ttl() -> int:
    """Seconds a project's tabs are reused before the Sheet is read again.

    Runtime setting, not a constant: an admin changes it on the Status page,
    which also shows what it costs in API calls. Refresh ignores it entirely —
    it drops the cache and re-reads on the spot.
    """
    return settings.tracker_ttl()


_SNAPSHOT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")


# --- snapshot ---------------------------------------------------------------

def snapshot_path(sheet_id: Optional[str] = None) -> str:
    return os.path.join(_SNAPSHOT_DIR, "snapshot_%s.json" % (sheet_id or active_sheet_id()))


def _load_snapshot(sheet_id: str) -> dict:
    path = snapshot_path(sheet_id)
    if not os.path.exists(path):
        return {}
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (ValueError, OSError):
        return {}


def _snapshot(sheet_id: Optional[str] = None) -> dict:
    sid = sheet_id or active_sheet_id()
    return data_cache.get("%s:tracker_snapshot" % sid, _ttl(), lambda: _load_snapshot(sid))


# --- source selection -------------------------------------------------------
# Every read takes an optional sheet id. Default is the session's project;
# passing one explicitly lets a page read several projects in one render
# without disturbing the selection.

def _live_spreadsheet(sheet_id: Optional[str] = None):
    """A project's Spreadsheet, or None when credentials are absent."""
    try:
        return get_spreadsheet(sheet_id)
    except Exception:
        return None


def is_live(sheet_id: Optional[str] = None) -> bool:
    return _live_spreadsheet(sheet_id) is not None


def _ago(seconds: float) -> str:
    """Human gap: now / 5m ago / 3h ago / 2 days ago."""
    if seconds < 45:
        return "now"
    if seconds < 3600:
        return "%dm ago" % max(1, int(seconds // 60))
    if seconds < 86400:
        return "%dh ago" % int(seconds // 3600)
    days = int(seconds // 86400)
    return "%d day%s ago" % (days, "" if days == 1 else "s")


def _read_stamp(sheet_id: Optional[str]) -> str:
    """When this project's data was last read, as "date time · relative".

    Distinct from a snapshot's own timestamp, which says when the *file* was
    built — that only moves when someone rebuilds it.
    """
    sid = sheet_id or active_sheet_id()
    # Pages read different tabs — Parts the Overview, Movements the log — so
    # report the most recent read of any of them rather than assuming one.
    reads = [data_cache.read_at("%s:%s" % (sid, tab))
             for tab in ("tracker_overview", "tracker_movements", "tracker_parts")]
    reads = [r for r in reads if r is not None]
    if not reads:
        return "not loaded yet"
    wall, seconds = min(reads, key=lambda r: r[1])
    return "%s · %s" % (datetime.fromtimestamp(wall).strftime("%Y-%m-%d %H:%M"),
                        _ago(seconds))


def source_label(sheet_id: Optional[str] = None, with_read: bool = True) -> str:
    """What the data is and when it was read — e.g.
    "live 2026-08-07 10:12 · now" or
    "snapshot 2026-08-06 23:15 · read 2026-08-07 10:12 · 3h ago".

    `with_read=False` drops the read stamp, for chrome that renders before the
    page has loaded anything (the sidebar), where it would always read stale.
    """
    if is_live(sheet_id):
        return "live sheet" if not with_read else "live %s" % _read_stamp(sheet_id)
    if not with_read:
        snap = _snapshot(sheet_id)
        return "snapshot %s" % snap.get("generated", "") if snap else "no data"
    snap = _snapshot(sheet_id)
    if snap:
        stamp = _read_stamp(sheet_id)
        return "snapshot %s · %s" % (
            snap.get("generated", ""),
            stamp if stamp == "not loaded yet" else "read %s" % stamp)
    return "no data"


# --- reads ------------------------------------------------------------------

def _grid(tab: str, sheet_id: Optional[str] = None) -> List[List[str]]:
    ss = _live_spreadsheet(sheet_id)
    if ss is None:
        return []
    try:
        return ss.worksheet(tab).get_all_values()
    except Exception:
        return []


def _load_overview(sheet_id: Optional[str]) -> List[dict]:
    if _live_spreadsheet(sheet_id) is None:
        return _snapshot(sheet_id).get("overview", [])
    return tracker_parse.parse_overview(_grid(TAB_OVERVIEW, sheet_id))


def fetch_overview(sheet_id: Optional[str] = None) -> List[dict]:
    """One row per part — whichever version is flagged Selected for MP."""
    sid = sheet_id or active_sheet_id()
    return data_cache.get("%s:tracker_overview" % sid, _ttl(),
                          lambda: _load_overview(sid), spinner="Loading parts…")


def _load_movements(sheet_id: Optional[str]) -> List[dict]:
    """Movements, from the merged `Movements` log if the project has one.

    The merged log carries an Event and a real Part ID, so only rows the event
    table calls a movement are returned — a QC finding or a schedule note is
    not a movement, however it was worded. A record still on the old two-tab
    shape falls back to its `Movement Log`, so this can be rolled out one
    project at a time.
    """
    if _live_spreadsheet(sheet_id) is None:
        return _snapshot(sheet_id).get("movements", [])

    from utils import movements_store
    merged = movements_store.physical(sheet_id)
    if merged:
        return [{
            "date": r.get("date", ""),
            "item": r.get("description", ""),
            "qty": r.get("qty", ""),
            "from": r.get("from", ""),
            "to": r.get("to", ""),
            "stage": r.get("build", ""),
            "notes": r.get("notes", ""),
            # The fields the old log never had; callers that know about them
            # get the exact link instead of a guess.
            "event": r.get("event", ""),
            "mcode": r.get("part_id", ""),
            "courier": r.get("courier", ""),
            "flag": r.get("flag", ""),
        } for r in merged]
    return tracker_parse.parse_movements(_grid(TAB_MOVEMENTS, sheet_id))


def fetch_movements(sheet_id: Optional[str] = None) -> List[dict]:
    """The Movement Log, oldest first (as kept in the sheet)."""
    sid = sheet_id or active_sheet_id()
    return data_cache.get("%s:tracker_movements" % sid, _ttl(),
                          lambda: _load_movements(sid), spinner="Loading movements…")


def _load_all_parts(sheet_id: Optional[str]) -> dict:
    """{mcode: {"meta":…, "history":…}} for every part tab, in ONE API call.

    A tracker has 45+ part tabs; reading them one at a time would be 45 round
    trips and would blow through the Sheets read quota as soon as a page shows
    more than a handful of parts. values_batch_get fetches every tab at once.
    """
    ss = _live_spreadsheet(sheet_id)
    if ss is None:
        return _snapshot(sheet_id).get("parts", {})

    tabs = part_tabs(sheet_id)
    if not tabs:
        return {}
    try:
        ranges = ["'%s'!A1:Z200" % t.replace("'", "''") for t in tabs]
        response = ss.values_batch_get(ranges)
    except Exception:
        return {}

    out = {}
    for tab, value_range in zip(tabs, response.get("valueRanges", [])):
        grid = value_range.get("values", [])
        if grid:
            out[tab] = tracker_parse.parse_part_tab(grid)
    return out


def fetch_all_parts(sheet_id: Optional[str] = None) -> dict:
    """Every part tab of a project, keyed by tab name (the M-code)."""
    sid = sheet_id or active_sheet_id()
    return data_cache.get("%s:tracker_parts" % sid, _ttl(),
                          lambda: _load_all_parts(sid), spinner="Loading part history…")


def fetch_part(mcode: str, sheet_id: Optional[str] = None) -> dict:
    """Full history for one part: {"meta": {...}, "history": [...]}."""
    return fetch_all_parts(sheet_id).get(mcode, {})


def is_part_tab(title: str) -> bool:
    """Whether a tab title names a part.

    Everything not explicitly excluded counts, so the exclusions have to cover
    the template tab too — otherwise the sample part ships as a real one.
    """
    title = (title or "").strip()
    if not title or title in TRACKER_NON_PART_TABS:
        return False
    # "retired" alongside "template": a superseded tab keeps its old name with
    # a suffix, and without this it comes back as a part the moment it is
    # renamed — which is exactly how the merged log briefly became a 45th part.
    low = title.lower()
    return "template" not in low and "retired" not in low


def part_tabs(sheet_id: Optional[str] = None) -> List[str]:
    """Tab names that hold a part's history."""
    ss = _live_spreadsheet(sheet_id)
    if ss is None:
        return sorted(t for t in _snapshot(sheet_id).get("parts", {})
                      if is_part_tab(t))
    try:
        titles = [ws.title for ws in ss.worksheets()]
    except Exception:
        return []
    return [t for t in titles if is_part_tab(t)]


def movement_links(mcode: str, part_name: str = "",
                   sheet_id=None) -> dict:
    """Movement Log rows that appear to concern this part, split by how sure
    we are:

    - ``by_code``: the row text contains the M-code. Treat as a real link.
    - ``by_name``: every word of the part name appears somewhere in the row.
      A GUESS — "Glass fixing" also matches a row about glass and a fixing —
      shown as "possible", never merged into the confident set.

    Nothing here rewrites the log. Deciding which movement really belongs to
    which part is an interpretation step the sheet doesn't currently record;
    the honest fix is an explicit M-code column on the Movement Log.
    """
    code = (mcode or "").strip().lower()
    tokens = [t for t in re.split(r"\W+", (part_name or "").lower()) if len(t) > 2]
    by_code, by_name = [], []
    if not code and not tokens:
        return {"by_code": by_code, "by_name": by_name}

    # The merged log has a real Part ID column, so the link is exact and the
    # name-guessing below is not needed. That guessing — "Glass fixing" also
    # matches a row about glass and a fixing — is what the column was added to
    # end (19 Aug).
    exact = [m for m in fetch_movements(sheet_id)
             if str(m.get("mcode", "")).strip().lower() == code]
    if exact:
        return {"by_code": exact, "by_name": []}
    for row in fetch_movements(sheet_id):
        blob = " ".join(str(v) for v in row.values()).lower()
        if code and code in blob:
            by_code.append(row)
        elif tokens and all(t in blob for t in tokens):
            by_name.append(row)
    return {"by_code": by_code, "by_name": by_name}


def movements_for(mcode: str, part_name: str = "", sheet_id=None) -> List[dict]:
    """All rows that mention this part, confident matches first. Prefer
    movement_links() where the distinction matters."""
    links = movement_links(mcode, part_name, sheet_id)
    return links["by_code"] + links["by_name"]


def latest_event(mcode: str, part_name: str = "", sheet_id=None) -> Optional[dict]:
    """The most recent Movement Log row for a part, as ``(row, how)`` where
    `how` is "code" for an M-code match or "name" for a name guess.

    The log is kept chronologically with the newest at the bottom, so the last
    match is the latest — no date parsing, which matters because the dates are
    written by hand ("~12 Jan 2026", "24–30 Jul 2026").
    """
    links = movement_links(mcode, part_name, sheet_id)
    if links["by_code"]:
        return {"row": links["by_code"][-1], "how": "code"}
    if links["by_name"]:
        return {"row": links["by_name"][-1], "how": "name"}
    return None


def refresh(sheet_id=None) -> None:
    """Drop cached tracker reads for a project (the active one by default)."""
    sid = sheet_id or active_sheet_id()
    data_cache.invalidate("%s:tracker_overview" % sid, "%s:tracker_movements" % sid,
                          "%s:tracker_parts" % sid, "%s:tracker_snapshot" % sid)


# --- derived views ----------------------------------------------------------

def attention_flags(row: dict) -> List[str]:
    """Why a part needs looking at. Purely derived from the Overview row —
    the flat sheet can't surface these without manual scanning.

    A part nobody has ordered yet is **not started**, not "needs attention".
    Without that distinction a freshly built record flags every part on day one,
    and a page where everything is red says nothing about anything.
    """
    flags = []
    ordered = tracker_parse.to_int(row.get("qty_ordered", ""))
    started = bool(ordered or str(row.get("order_id", "")).strip())
    if not started:
        return flags

    status = str(row.get("status", "")).strip().lower()
    if status.startswith("not pass") or "fail" in status or "reject" in status:
        flags.append("QC not passed")
    received = tracker_parse.to_int(row.get("qty_received", ""))
    if ordered and received < ordered:
        flags.append("Short %d of %d" % (ordered - received, ordered))
    if not str(row.get("version", "")).strip():
        flags.append("No version selected")
    # Only meaningful when the sheet actually carries the column: the current
    # Overview has no Location, so flagging its absence would flag every row.
    if "location" in row:
        location = str(row.get("location", "")).lower()
        if not location or location.startswith("tbc"):
            flags.append("Location unconfirmed")
    return flags
