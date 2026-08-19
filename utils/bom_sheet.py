"""Read-only access to a project's NPD BOM sheet ("…BOM&quote").

Same two-source design as parts_tracker: the live Google Sheet when
credentials are present, otherwise `data/snapshot_<sheet id>.json` built by
`tools/make_snapshot.py --role bom`. Nothing here ever writes — the BOM
sheet belongs to the NPD team.

The whole workbook is fetched in ONE values_batch_get and parsed into a
bundle: {"builds": {tab: rows}, "lifecycle": rows, "movement_log": rows}.
Build tabs are recognised by their header (bom_parse.build_header_idx), so
new builds appear without a code change; costing/quoting tabs simply don't
match and are ignored.
"""
from __future__ import annotations

import json
import os
from typing import Dict, List, Optional

from config import TAB_BOM_LIFECYCLE, TAB_BOM_MOVEMENTS
from utils import bom_parse, data_cache, settings
from utils.google_client import get_spreadsheet
from utils.project_registry import active_bom_sheet

_SNAPSHOT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")

# Tabs that are named rather than header-detected.
_NAMED_TABS = {TAB_BOM_LIFECYCLE, TAB_BOM_MOVEMENTS}


def _ttl() -> int:
    # Same runtime knob as the tracker — both are "project sheet" reads.
    return settings.tracker_ttl()


def _sid(sheet_id: Optional[str]) -> str:
    return sheet_id or active_bom_sheet() or ""


def _registered_tabs(sheet_id: str) -> Dict[str, str]:
    """{"bom": tab name, "cost": tab name} for whichever project uses this BOM
    file, or {} when it isn't registered.

    The admin already answers "which tab is the BOM?" on the Projects page and
    the answer is stored — this is what reads it. Without it, tab choice falls
    back to sniffing headers, and `Cost Estimate` carries the same Part ID /
    Part Name / BOM Qty columns as the BOM, so it is indistinguishable.
    """
    from utils import project_registry

    for entry in project_registry.all_sheets().values():
        if entry.get("bom") and entry["bom"] == sheet_id:
            return {"bom": (entry.get("bom_tab") or "").strip(),
                    "cost": (entry.get("cost_tab") or "").strip()}
    return {}


# --- snapshot ----------------------------------------------------------------

def snapshot_path(sheet_id: str) -> str:
    return os.path.join(_SNAPSHOT_DIR, "snapshot_%s.json" % sheet_id)


def _load_snapshot(sheet_id: str) -> dict:
    path = snapshot_path(sheet_id)
    if not os.path.exists(path):
        return {}
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (ValueError, OSError):
        return {}


# --- live --------------------------------------------------------------------

def _live_spreadsheet(sheet_id: str):
    if not sheet_id:
        return None
    try:
        return get_spreadsheet(sheet_id)
    except Exception:
        return None


def is_live(sheet_id: Optional[str] = None) -> bool:
    return _live_spreadsheet(_sid(sheet_id)) is not None


def _load_bundle_live(sheet_id: str) -> dict:
    """Fetch and classify every tab in one values_batch_get."""
    ss = _live_spreadsheet(sheet_id)
    if ss is None:
        return {}
    try:
        titles = [ws.title for ws in ss.worksheets()]
        ranges = ["'%s'!A1:AZ500" % t.replace("'", "''") for t in titles]
        response = ss.values_batch_get(ranges)
    except Exception:
        return {}

    registered = _registered_tabs(sheet_id)
    named_bom = registered.get("bom")
    skip = {registered["cost"]} if registered.get("cost") else set()

    builds: Dict[str, List[dict]] = {}
    lifecycle: List[dict] = []
    movement_log: List[dict] = []
    for title, value_range in zip(titles, response.get("valueRanges", [])):
        grid = value_range.get("values", [])
        if not grid or title in skip:
            continue
        if title == TAB_BOM_LIFECYCLE:
            lifecycle = bom_parse.parse_lifecycle(grid)
        elif title == TAB_BOM_MOVEMENTS:
            movement_log = bom_parse.parse_movement_log(grid)
        elif named_bom:
            # The project named its BOM tab: that tab and only that tab is the
            # build. Header sniffing is not consulted, so a costing or quoting
            # tab can never be mistaken for a second build.
            if title == named_bom:
                builds[title] = bom_parse.parse_build(grid)
        elif bom_parse.build_header_idx(grid) >= 0:
            rows = bom_parse.parse_build(grid)
            if rows:
                builds[title] = rows
    return {"builds": builds, "lifecycle": lifecycle, "movement_log": movement_log}


# --- bundle ------------------------------------------------------------------

def _load_bundle(sheet_id: str) -> dict:
    if _live_spreadsheet(sheet_id) is None:
        snap = _load_snapshot(sheet_id)
        return {"builds": snap.get("builds", {}),
                "lifecycle": snap.get("lifecycle", []),
                "movement_log": snap.get("movement_log", []),
                "generated": snap.get("generated", "")}
    return _load_bundle_live(sheet_id)


def fetch_bundle(sheet_id: Optional[str] = None) -> dict:
    """Everything the app uses from a BOM sheet, parsed and cached."""
    sid = _sid(sheet_id)
    if not sid:
        return {"builds": {}, "lifecycle": [], "movement_log": []}
    return data_cache.get("%s:bom_bundle" % sid, _ttl(),
                          lambda: _load_bundle(sid), spinner="Loading BOM…")


def build_tabs(sheet_id: Optional[str] = None) -> List[str]:
    """Build tab names, in sheet order (the sheet's own priority)."""
    return list(fetch_bundle(sheet_id).get("builds", {}))


def default_build(sheet_id: Optional[str] = None) -> str:
    """The build the UI should open on: the first tab that tracks CAD
    versions (the current working build), else simply the first build."""
    builds = fetch_bundle(sheet_id).get("builds", {})
    for tab, rows in builds.items():
        if any(r.get("most_recent_cad") for r in rows):
            return tab
    return next(iter(builds), "")


def fetch_bom(tab: str, sheet_id: Optional[str] = None) -> List[dict]:
    """One build tab's part rows."""
    return fetch_bundle(sheet_id).get("builds", {}).get(tab, [])


def fetch_lifecycle(sheet_id: Optional[str] = None) -> List[dict]:
    """The Parts Lifecycle & Order Tracking rows."""
    return fetch_bundle(sheet_id).get("lifecycle", [])


def fetch_movement_log(sheet_id: Optional[str] = None) -> List[dict]:
    """The structured movement log — every row carries M-code + version."""
    return fetch_bundle(sheet_id).get("movement_log", [])


def source_label(sheet_id: Optional[str] = None) -> str:
    """"live" / "snapshot <built>" / "no data" for the BOM source."""
    sid = _sid(sheet_id)
    if not sid:
        return "no BOM sheet"
    if is_live(sid):
        return "live"
    snap = _load_snapshot(sid)
    return "snapshot %s" % snap.get("generated", "") if snap else "no data"


def refresh(sheet_id: Optional[str] = None) -> None:
    """Drop the cached bundle so the next read refetches."""
    sid = _sid(sheet_id)
    if sid:
        data_cache.invalidate("%s:bom_bundle" % sid)
