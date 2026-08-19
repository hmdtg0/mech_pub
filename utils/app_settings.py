"""App-wide settings, stored in the main record's `Settings` tab.

Key/value, one row each. This is for things that belong to the *installation*
rather than to one project or one machine — the Drive root folder projects are
browsed from, the PCB order sheet — and it lives in the sheet for the same
reason the project list does: a value kept in `data/*.json` is per-machine and
is wiped on every Streamlit Cloud restart, so two people would disagree about
what the app is configured to do.

Not to be confused with `utils/settings.py`, which holds the cache TTLs — those
are a local performance knob, deliberately not shared.
"""
from __future__ import annotations

from datetime import datetime
from typing import Dict

from config import CENTRAL_SHEET_ID
from utils import data_cache

SETTINGS_TAB = "Settings"

COL_KEY = "Key"
COL_VALUE = "Value"
COL_AT = "Updated at"
COL_BY = "Updated by"

# Known keys, so callers don't pass string literals around.
DRIVE_ROOT_FOLDER_ID = "drive_root_folder_id"
PROJECTS_SUBFOLDER = "projects_subfolder"       # folder inside the root holding project folders
RECORD_TEMPLATE_ID = "record_template_id"       # TEMPLATE – Project Record
PCB_ORDER_SHEET_ID = "pcb_order_sheet_id"
DRIVE_CAN_CREATE = "drive_can_create"           # "yes" once Drive allows the app to create files
ORDER_GRID_LAYOUT = "order_grid_layout"         # JSON column widths for the Order-from-BOM grids

DEFAULTS = {
    PROJECTS_SUBFOLDER: "Projects",
}


def flag(key: str) -> bool:
    """A yes/no setting. Anything but an explicit yes is no — the create-files
    switch defaults to off, because guessing wrong there fails mid-write."""
    return get(key, "").strip().lower() in ("yes", "true", "1", "on")


def set_flag(key: str, value: bool, updated_by: str = "") -> bool:
    return set_value(key, "yes" if value else "no", updated_by)

# Changed by hand rarely, and `set()` invalidates directly.
_TTL_SECONDS = 300


def _cache_key() -> str:
    return "%s:settings" % CENTRAL_SHEET_ID


def _column(headers, name: str) -> int:
    cleaned = [h.strip().lower() for h in headers]
    want = name.strip().lower()
    return cleaned.index(want) if want in cleaned else -1


def _load() -> Dict[str, str]:
    from utils.google_client import with_worksheet

    try:
        values = with_worksheet(SETTINGS_TAB, lambda ws: ws.get_all_values(),
                                sheet_id=CENTRAL_SHEET_ID)
    except Exception:
        return {}
    if not values:
        return {}
    headers = values[0]
    i_key = _column(headers, COL_KEY)
    i_val = _column(headers, COL_VALUE)
    if i_key < 0 or i_val < 0:
        return {}
    out = {}
    for row in values[1:]:
        key = row[i_key].strip() if i_key < len(row) else ""
        if key:
            out[key] = row[i_val].strip() if i_val < len(row) else ""
    return out


def all_settings() -> Dict[str, str]:
    settings = data_cache.get(_cache_key(), _TTL_SECONDS, _load)
    if not settings:
        # An empty result here is a failed read in practice (the tab always
        # holds the installation keys) — don't cache it for the TTL, or one
        # rate-limit burst turns every setting into its default for 5 min.
        data_cache.invalidate(_cache_key())
    return settings


def get(key: str, default: str = "") -> str:
    return all_settings().get(key, "") or default or DEFAULTS.get(key, "")


def refresh() -> None:
    data_cache.invalidate(_cache_key())


def set_value(key: str, value: str, updated_by: str = "") -> bool:
    """Write a setting, updating the row in place or appending a new one."""
    from utils.google_client import with_worksheet

    stamp = datetime.now().strftime("%d %b %Y %H:%M")

    def write(ws) -> bool:
        values = ws.get_all_values()
        if not values:
            return False
        headers = values[0]
        i_key = _column(headers, COL_KEY)
        i_val = _column(headers, COL_VALUE)
        i_at = _column(headers, COL_AT)
        i_by = _column(headers, COL_BY)
        if i_key < 0 or i_val < 0:
            return False
        for r, row in enumerate(values[1:], start=2):
            if i_key < len(row) and row[i_key].strip() == key:
                updates = [{"range": _a1(r, i_val + 1), "values": [[value]]}]
                if i_at >= 0:
                    updates.append({"range": _a1(r, i_at + 1), "values": [[stamp]]})
                if i_by >= 0:
                    updates.append({"range": _a1(r, i_by + 1), "values": [[updated_by]]})
                ws.batch_update(updates, value_input_option="USER_ENTERED")
                return True
        row = [""] * len(headers)
        row[i_key] = key
        row[i_val] = value
        if i_at >= 0:
            row[i_at] = stamp
        if i_by >= 0:
            row[i_by] = updated_by
        ws.append_row(row, value_input_option="USER_ENTERED")
        return True

    try:
        ok = with_worksheet(SETTINGS_TAB, write, sheet_id=CENTRAL_SHEET_ID)
    except Exception:
        return False
    refresh()
    return bool(ok)


def _a1(row: int, col: int) -> str:
    """Bare A1, no tab prefix: `Worksheet.batch_update` adds its own title, and
    a prefixed range arrives as `'Settings'!Settings!B2` and is rejected."""
    from gspread.utils import rowcol_to_a1

    return rowcol_to_a1(row, col)
