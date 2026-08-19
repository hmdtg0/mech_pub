"""Parsers for the NPD BOM workbook format (no Google/Streamlit deps).

The BOM sheet ("P1 dial EVT BOM&quote" style) is the NPD team's working
document: one tab per build (T1 / T2 / colourway exports…), a lifecycle tab
("Parts and Order Tracking") and a structured movement log ("Parts and
Samples Movement Log"), plus costing tabs this app ignores.

Same philosophy as tracker_parse: headers are matched tolerantly (the sheet
has drift like "Recived Qty" and "UP-CNY origin"), unmapped columns are kept
under their raw header text, and cell content is never reformatted — the
sheet is the record. Nothing here writes.
"""
from __future__ import annotations

from typing import Dict, List, Sequence

from utils.tracker_parse import _find_header, _rows, norm

Grid = Sequence[Sequence[str]]

# --- build tabs (the BOM per build) -----------------------------------------
# Observed headers across POC2 / Stock in HK / T2 / DVT T1 / *_BOM_* tabs.
BUILD_FIELDS = {
    "part": "group",                 # "Parts" / section label
    "type": "group",                 # renamed "Type" in the current template
    "mpn": "mcode",                  # the M-code — the bridge key
    "partid": "mcode",               # the current template's spelling
    "partname": "part_name",
    "bomqty": "bom_qty",             # first occurrence wins (identification)
    "material": "material",
    "specnotes": "spec",
    "specmaterialssurfaceprocess": "spec",   # POC2 spelling
    "sampleidspec": "spec",                  # current template spelling
    "quote": "quote",
    "notes": "notes",
    "drawing": "drawing",
    "cad": "cad",
    "lastorderedcadversion": "last_ordered_cad",
    "mostrecentcadversion": "most_recent_cad",
    "cadversionrelease": "most_recent_cad",  # current template spelling
    "livecadlink": "cad_link",
    "designfilelink": "cad_link",            # POC2 spelling
    "version": "version",
    "readytoorder": "ready",
    "orderstatus": "order_status",
    "upcny": "unit_cost",
    "upcnyorigin": "unit_cost",
    "spare": "spare",
    "ordertotal": "order_qty",
    "orderqtytotal": "order_qty",
    "t1orderqtytotal": "order_qty",
    "t2orderqtytotal": "order_qty",
    "extendedcost": "extended_cost",
    "partsowner": "owner",
    "leadtime": "lead_time",
    "leadtimedays": "lead_time",
    "orderedqty": "ordered_qty",
    "eta": "eta",
    "destinationreceiver": "destination",
    "receivedqty": "received_qty",
    "recivedqty": "received_qty",            # sheet's spelling
    "qcpass": "qc",
    "remark": "remark",
}

# --- "Parts and Order Tracking" (titled "Parts Lifecycle & Order Tracking") --
LIFECYCLE_FIELDS = {
    "part": "group",
    "mpn": "mcode",
    "partid": "mcode",
    "version": "version",
    "partname": "part_name",
    "bomqty": "bom_qty",
    "orderdate": "order_date",
    "orderstatus": "order_status",
    "orderedqty": "ordered_qty",
    "location": "location",
    "inproductionqty": "in_production_qty",
    "ukqty": "uk_qty",
    "assembledqty": "assembled_qty",
    "finalunitqty": "final_unit_qty",
    "notes": "notes",
}

# --- "Parts and Samples Movement Log" ----------------------------------------
# Unlike the pilot tracker's free-text log, every row here carries the part
# identity — M-code + version — so linking is exact, not a guess.
MOVEMENT_LOG_FIELDS = {
    "partnumber": "mcode",
    "version": "version",
    "qtymoving": "qty",
    "fromlocation": "from",
    "tolocation": "to",
    "dateshipped": "date_shipped",
    "datereceived": "date_received",
    "loggedby": "logged_by",
    "status": "status",
    "couriertracking": "courier",
    "notes": "notes",
}


def build_header_idx(grid: Grid) -> int:
    """Index of a build tab's header row, or -1 if this isn't a build tab.

    A build tab is recognised by its own header — an identity column (`MPN` or
    the current template's `Part ID`) plus Part Name plus BOM Qty and at least
    one more known column — never by tab name, so renamed or added builds keep
    working.

    Detection alone is not enough to tell a build from a costing tab: the
    lifecycle tab and `Cost Estimate` both carry identity/Part Name/BOM Qty
    too. Callers exclude those by name — and `bom_sheet` prefers the tab the
    admin registered, which settles it outright.
    """
    idx = _find_header(grid, BUILD_FIELDS, ("mpn", "partid"))
    if idx < 0:
        return -1
    keys = {norm(c) for c in grid[idx] if c}
    if "partname" in keys and "bomqty" in keys:
        return idx
    return -1


def parse_build(grid: Grid) -> List[dict]:
    """A build tab -> one dict per part row (rows without an M-code dropped:
    they are spacers, section labels or grand-total rows)."""
    idx = build_header_idx(grid)
    if idx < 0:
        return []
    return [r for r in _rows(grid, BUILD_FIELDS, idx) if r.get("mcode")]


def parse_lifecycle(grid: Grid) -> List[dict]:
    """The lifecycle tab -> one dict per part row.

    The real header sits under a title row and a merged group-header row
    (BOM IDENTIFICATION / ORDER & INVENTORY / ASSEMBLY PIPELINE);
    _find_header skips both because neither contains the known columns.
    """
    idx = _find_header(grid, LIFECYCLE_FIELDS, ("mpn", "partid"))
    if idx < 0:
        return []
    return [r for r in _rows(grid, LIFECYCLE_FIELDS, idx) if r.get("mcode")]


def parse_movement_log(grid: Grid) -> List[dict]:
    """The movement log -> one dict per row, newest last, kept literal.

    Every non-empty row is kept, even without an M-code — same rule as the
    pilot tracker's log: dropping rows silently loses record.
    """
    idx = _find_header(grid, MOVEMENT_LOG_FIELDS, "partnumber")
    if idx < 0:
        return []
    return _rows(grid, MOVEMENT_LOG_FIELDS, idx)
