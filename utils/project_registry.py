"""The list of projects, and which one the session is working in.

A project is a display name plus its two sheets: the project RECORD (the
per-project ledger) and the BOM (the input, read-only).

The list itself is **not** held in code. It is the `Projects` tab of the main
record — which is what makes the main record the index it claims to be. Every
instance of the app therefore offers the same projects, and registering one is
a write to the sheet like any other, not a code change and not a per-machine
file.

`config.PROJECTS` / `MECH_PROJECTS` survive only as an offline fallback, for an
instance with no Google credentials where the main record cannot be read at
all. They are empty by default: a project that exists only in code is a fact
with two homes, and the sheet is the one that wins.
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Dict, Optional, Tuple

from config import CENTRAL_SHEET_ID, GOOGLE_SHEET_ID, PROJECTS
from utils import data_cache

PROJECTS_TAB = "Projects"

# Column headers this reads and writes. The tab carries rollup columns too
# (Parts, Orders open, Qty received, …) which nothing writes yet.
#
# A project is a name plus the files that belong to it. Only the name and the
# record are required; the rest are links the team fills in as they exist, and
# an empty one is a normal state, not an error.
COL_NAME = "Project"
COL_RECORD = "Record Sheet ID"
COL_BOM = "BOM Sheet ID"
COL_BOM_TAB = "BOM Tab"             # which tab inside the BOM file holds the BOM
COL_COST = "Cost Sheet ID"
COL_COST_TAB = "Cost Tab"           # the cost estimate usually lives in the BOM file
COL_PCB = "PCB Sheet ID"
COL_PCB_TAB = "PCB Tab"
COL_FOLDER = "Drive Folder ID"      # the project's own folder in the tool's Drive
COL_WRITTEN_AT = "Last write"
COL_WRITTEN_BY = "Updated by"

# field name -> (header, alternative headers accepted when reading). The record
# column was called "Tracker Sheet ID" before the record/tracker rename, and a
# sheet in the field may still carry either.
FIELDS = (
    ("tracker", COL_RECORD, ("Tracker Sheet ID", "Record sheet")),
    ("bom", COL_BOM, ("BOM sheet",)),
    ("bom_tab", COL_BOM_TAB, ("BOM tab name",)),
    ("cost", COL_COST, ("Cost Estimate Sheet ID", "Cost sheet")),
    ("cost_tab", COL_COST_TAB, ("Cost Estimate Tab",)),
    ("pcb", COL_PCB, ("PCB Record Sheet ID", "PCB sheet")),
    ("pcb_tab", COL_PCB_TAB, ("PCB Record Tab",)),
    ("folder", COL_FOLDER, ("Project Folder ID", "Drive folder")),
)

# Tab names, not ids: they must survive verbatim — spaces, case and all — or a
# tab called "Cost Estimate" becomes unfindable.
TAB_FIELDS = {"bom_tab", "cost_tab", "pcb_tab"}

# Every key a project dict carries, so callers can rely on them all existing.
BLANK = {name: "" for name, _, _ in FIELDS}

# The project list changes only when someone registers a project, and that
# path invalidates this cache directly — so a long TTL costs nothing and keeps
# `all_projects()` (called on every badge render) off the API.
_TTL_SECONDS = 300


def _cache_key() -> str:
    return "%s:projects" % CENTRAL_SHEET_ID


def _normalise(entry) -> Dict[str, str]:
    """Either config-fallback shape -> a full project dict."""
    out = dict(BLANK)
    if isinstance(entry, dict):
        for key in out:
            out[key] = str(entry.get(key, "") or "")
    else:
        out["tracker"] = str(entry or "")
    return out


def _column(headers, *candidates) -> int:
    """Index of the first header matching any candidate, or -1.

    Tolerant of the drift real sheets accumulate — case, stray spaces — and of
    the record/tracker naming, so renaming the column in the sheet later does
    not silently empty the project list.
    """
    cleaned = [h.strip().lower() for h in headers]
    for want in candidates:
        w = want.strip().lower()
        if w in cleaned:
            return cleaned.index(w)
    return -1


def _load_projects_tab() -> dict:
    """{"ok": reached the sheet, "projects": {...}}.

    `ok` is carried rather than signalled by an empty dict because those are
    different states with different UIs: no projects registered yet is a normal
    empty list, while "cannot read the main record" must fall back and say so.
    """
    from utils.google_client import with_worksheet

    try:
        values = with_worksheet(PROJECTS_TAB, lambda ws: ws.get_all_values(),
                                sheet_id=CENTRAL_SHEET_ID)
    except Exception:
        return {"ok": False, "projects": {}}
    if values is None:          # no credentials on this instance
        return {"ok": False, "projects": {}}
    if not values:              # tab exists but is completely blank
        return {"ok": True, "projects": {}}

    headers = values[0]
    i_name = _column(headers, COL_NAME, "Project name")
    index = {field: _column(headers, header, *alts)
             for field, header, alts in FIELDS}
    if i_name < 0 or index["tracker"] < 0:
        return {"ok": False, "projects": {}}

    projects: Dict[str, Dict[str, str]] = {}
    for row in values[1:]:
        def cell(idx: int) -> str:
            return row[idx].strip() if 0 <= idx < len(row) else ""
        name = cell(i_name)
        if not name:
            continue
        entry = dict(BLANK)
        for field, _, _ in FIELDS:
            entry[field] = _clean(field, cell(index[field]))
        projects[name] = entry
    return {"ok": True, "projects": projects}


def _bundle() -> dict:
    bundle = data_cache.get(_cache_key(), _TTL_SECONDS, _load_projects_tab)
    if not bundle.get("ok"):
        # Never let a FAILED read sit in the cache: one rate-limit burst
        # would otherwise blank the whole app ("cannot read the project
        # list") for the full TTL. This run reports the failure; the next
        # run retries the sheet.
        data_cache.invalidate(_cache_key())
    return bundle


def refresh() -> None:
    """Drop the cached project list so the next read hits the main record."""
    data_cache.invalidate(_cache_key())


def source_is_live() -> bool:
    """Whether the project list came from the main record (vs the fallback)."""
    return bool(_bundle()["ok"])


def all_sheets() -> Dict[str, Dict[str, str]]:
    """{display name: {"tracker": id, "bom": id}} — from the main record."""
    bundle = _bundle()
    if bundle["ok"]:
        return dict(bundle["projects"])
    return {name: _normalise(entry) for name, entry in PROJECTS.items()}


def all_projects() -> Dict[str, str]:
    """{display name: project record sheet id} — the shape callers expect."""
    return {name: s["tracker"] for name, s in all_sheets().items()}


def sheets(name: str) -> Dict[str, str]:
    """A project's sheets, {"tracker": id, "bom": id-or-empty}."""
    return all_sheets().get(name, {"tracker": "", "bom": ""})


def tracker_sheet(name: str) -> str:
    return sheets(name)["tracker"]


def bom_sheet(name: str) -> Optional[str]:
    """The project's BOM sheet id, or None when it has no BOM sheet."""
    return sheets(name)["bom"] or None


def sheet_id_from(text: str) -> str:
    """Accept either a bare sheet id or a full Google Sheets URL."""
    text = (text or "").strip()
    match = re.search(r"/spreadsheets/d/([a-zA-Z0-9-_]+)", text)
    return match.group(1) if match else text


def folder_id_from(text: str) -> str:
    """Accept either a bare folder id or a full Drive folder URL.

    Separate from `sheet_id_from` because a folder URL has no `/spreadsheets/d/`
    segment — running it through the sheet parser would store the whole URL as
    if it were an id, and fail much later with a confusing 404.
    """
    text = (text or "").strip()
    match = re.search(r"/folders/([a-zA-Z0-9-_]+)", text)
    if match:
        return match.group(1)
    match = re.search(r"[?&]id=([a-zA-Z0-9-_]+)", text)
    return match.group(1) if match else text


def folder_url(ref: str) -> str:
    """A folder id (or URL) -> the URL that opens it in Drive."""
    folder_id = folder_id_from(ref)
    return "https://drive.google.com/drive/folders/%s" % folder_id if folder_id else ""


def folder_link(ref: str, label: str = "") -> str:
    """Markdown link to a Drive folder — "" when there is no folder."""
    url = folder_url(ref)
    return "[%s](%s)" % (label or url, url) if url else ""


def _clean(field: str, raw: str) -> str:
    """Store the id, whatever shape it was pasted in — tab names untouched."""
    if field in TAB_FIELDS:
        return raw
    if field == "folder":
        return folder_id_from(raw)
    return sheet_id_from(raw)


def sheet_url(ref: str) -> str:
    """A sheet id (or URL) -> the URL that opens it.

    The inverse of `sheet_id_from`, and it accepts the same two shapes, so a
    value can be round-tripped without the caller knowing which it holds.
    """
    sheet_id = sheet_id_from(ref)
    return "https://docs.google.com/spreadsheets/d/%s/edit" % sheet_id if sheet_id else ""


def sheet_link(ref: str, label: str = "") -> str:
    """Markdown link to a sheet — "" when there is no sheet.

    The label defaults to the full URL rather than a word: a bare sheet id is
    unreadable and unclickable, and people need to paste the address into a
    message or another sheet about as often as they need to open it.
    """
    url = sheet_url(ref)
    if not url:
        return ""
    return "[%s](%s)" % (label or url, url)


def _field_columns(headers) -> Dict[str, int]:
    return {field: _column(headers, header, *alts) for field, header, alts in FIELDS}


def add_project(name: str, sheet_ref: str, bom_ref: str = "",
                registered_by: str = "", **extra) -> Tuple[bool, str]:
    """Register a project by appending a row to the main record's Projects tab.

    `extra` takes any of the optional links — bom_tab, cost, pcb, folder — so a
    project can be registered complete in one write. Returns (ok, message). The
    write goes to the sheet, not to local state, so a project registered on one
    machine exists for everyone.
    """
    from utils.google_client import with_worksheet

    name = (name or "").strip()
    if not name:
        return False, "Project name is required."
    values = dict(BLANK)
    values["tracker"] = sheet_id_from(sheet_ref)
    values["bom"] = sheet_id_from(bom_ref)
    for field, raw in extra.items():
        if field in values:
            values[field] = _clean(field, raw or "")
    if not values["tracker"]:
        return False, "Project record sheet ID or URL is required."

    refresh()
    bundle = _bundle()
    if not bundle["ok"]:
        return False, ("Cannot reach the main record, so the project cannot be "
                       "registered. Check the app's Google credentials.")
    if name in bundle["projects"]:
        return False, "A project called '%s' is already in the main record." % name

    def write(ws) -> str:
        headers = ws.row_values(1)
        if not headers:
            return "The Projects tab has no header row."
        i_name = _column(headers, COL_NAME, "Project name")
        index = _field_columns(headers)
        if i_name < 0 or index["tracker"] < 0:
            return ("The Projects tab is missing its `%s` / `%s` columns."
                    % (COL_NAME, COL_RECORD))
        row = [""] * len(headers)
        row[i_name] = name
        for field, value in values.items():
            if index.get(field, -1) >= 0:
                row[index[field]] = value
        i_at = _column(headers, COL_WRITTEN_AT)
        i_by = _column(headers, COL_WRITTEN_BY)
        if i_at >= 0:
            row[i_at] = datetime.now().strftime("%d %b %Y %H:%M")
        if i_by >= 0:
            row[i_by] = registered_by
        ws.append_row(row, value_input_option="USER_ENTERED")
        return ""

    try:
        problem = with_worksheet(PROJECTS_TAB, write, sheet_id=CENTRAL_SHEET_ID)
    except Exception as exc:
        return False, "Could not write to the main record: %s" % exc
    if problem:
        return False, problem

    refresh()
    return True, "Registered '%s' in the main record." % name


def update_project(name: str, updates: Dict[str, str],
                   updated_by: str = "") -> Tuple[bool, str]:
    """Change a registered project's links in place.

    `updates` maps field names (tracker, bom, bom_tab, cost, pcb, folder) to new
    values. Only the cells named are touched — the rollup columns and anything
    else on the row are left exactly as they were, because this app is not the
    only thing that will ever write to that tab.
    """
    from gspread.utils import rowcol_to_a1
    from utils.google_client import with_worksheet

    name = (name or "").strip()
    if not name:
        return False, "Project name is required."
    clean = {f: _clean(f, v or "") for f, v in updates.items() if f in BLANK}
    if not clean:
        return False, "Nothing to update."

    def write(ws) -> str:
        values = ws.get_all_values()
        if not values:
            return "The Projects tab is empty."
        headers = values[0]
        i_name = _column(headers, COL_NAME, "Project name")
        index = _field_columns(headers)
        if i_name < 0:
            return "The Projects tab has no `%s` column." % COL_NAME
        target = 0
        for r, row in enumerate(values[1:], start=2):
            if i_name < len(row) and row[i_name].strip() == name:
                target = r
                break
        if not target:
            return "'%s' is not in the main record." % name

        # Bare A1 ranges: Worksheet.batch_update prefixes its own tab title, so
        # passing "Projects!B2" arrives as "'Projects'!Projects!B2" and 400s.
        batch = []
        for field, value in clean.items():
            col = index.get(field, -1)
            if col < 0:
                return ("The Projects tab has no column for `%s`. Run "
                        "`tools/migrate_projects_tab.py`." % field)
            batch.append({"range": rowcol_to_a1(target, col + 1),
                          "values": [[value]]})
        i_at = _column(headers, COL_WRITTEN_AT)
        i_by = _column(headers, COL_WRITTEN_BY)
        if i_at >= 0:
            batch.append({"range": rowcol_to_a1(target, i_at + 1),
                          "values": [[datetime.now().strftime("%d %b %Y %H:%M")]]})
        if i_by >= 0:
            batch.append({"range": rowcol_to_a1(target, i_by + 1),
                          "values": [[updated_by]]})
        ws.batch_update(batch, value_input_option="USER_ENTERED")
        return ""

    try:
        problem = with_worksheet(PROJECTS_TAB, write, sheet_id=CENTRAL_SHEET_ID)
    except Exception as exc:
        return False, "Could not write to the main record: %s" % exc
    if problem:
        return False, problem

    refresh()
    return True, "Updated '%s'." % name


def remove_project(name: str) -> Tuple[bool, str]:
    """Un-register a project: delete its row from the main record's Projects tab.

    Only the index entry goes. The project's own sheets are untouched, and its
    Orders rows keep the project name — so this is "stop listing it", not
    "delete its history", and re-registering restores it exactly.
    """
    from utils.google_client import with_worksheet

    name = (name or "").strip()
    if not name:
        return False, "Project name is required."

    def write(ws) -> str:
        values = ws.get_all_values()
        if not values:
            return "The Projects tab is empty."
        i_name = _column(values[0], COL_NAME, "Project name")
        if i_name < 0:
            return "The Projects tab has no `%s` column." % COL_NAME
        for r, row in enumerate(values[1:], start=2):
            if i_name < len(row) and row[i_name].strip() == name:
                ws.delete_rows(r)
                return ""
        return "'%s' is not in the main record." % name

    try:
        problem = with_worksheet(PROJECTS_TAB, write, sheet_id=CENTRAL_SHEET_ID)
    except Exception as exc:
        return False, "Could not write to the main record: %s" % exc
    if problem:
        return False, problem

    refresh()
    try:
        import streamlit as st
        if st.session_state.get("active_project") == name:
            st.session_state.pop("active_project", None)
            st.session_state.pop("active_sheet_id", None)
    except Exception:
        pass
    return True, "Removed '%s' from the main record." % name


def active() -> Tuple[str, str]:
    """(name, project record sheet id) of the project this session is in.

    ("", "") when no project is registered yet — a real state on a fresh
    instance, and one every caller has to handle rather than crash on.
    """
    projects = all_projects()
    try:
        import streamlit as st
        name = st.session_state.get("active_project", "")
        if name in projects:
            return name, projects[name]
    except Exception:
        pass
    if projects:
        name = next(iter(projects))
        return name, projects[name]
    return "", GOOGLE_SHEET_ID


def active_bom_sheet() -> Optional[str]:
    """The active project's BOM sheet id, or None."""
    name, _ = active()
    return bom_sheet(name) if name else None


# The scope the whole app reads: one project, or every one at once. It is a
# VIEW over the projects, not a project — `active()` still answers "which
# record do I write to", because a page that files an order needs a real
# sheet even while the reader is looking at everything (Hamid, 21 Aug).
ALL = "All projects"


def scope() -> str:
    """`ALL`, or the active project's name."""
    try:
        import streamlit as st
        if st.session_state.get("project_scope") == ALL and len(all_projects()) > 1:
            return ALL
    except Exception:
        pass
    return active()[0]


def is_all() -> bool:
    return scope() == ALL


def set_scope(name: str) -> None:
    """Point the session at one project, or at all of them. Caller reruns.

    Picking `ALL` deliberately leaves `active_project` alone: it is the
    project a write page falls back to, and the one the scope returns to
    when you narrow again.
    """
    import streamlit as st
    if name == ALL:
        st.session_state["project_scope"] = ALL
        return
    st.session_state["project_scope"] = ""
    set_active(name)


def in_scope(value: str) -> bool:
    """Whether a row tagged with this project name belongs on screen."""
    if is_all():
        return True
    return str(value or "").strip() == active()[0]


def set_active(name: str) -> None:
    """Point the session at a project; caller reruns."""
    projects = all_projects()
    if name not in projects:
        return
    import streamlit as st
    st.session_state["active_project"] = name
    # active_sheet_id keeps meaning "the active project's RECORD sheet" — it
    # drives tracker reads and tracker_writer; the app DB is the main record.
    st.session_state["active_sheet_id"] = projects[name]
