"""Build a project record's per-part tabs from its BOM.

Creating those tabs by hand was the tool's biggest pain point. `tracker_writer`
still refuses to create a tab — an order for an unknown part must fail loudly —
so tab creation lives here instead, as a deliberate, separate action driven by
the BOM. A tab is only ever created from a BOM row, never invented from a typed
part number.

**This module defines what a part tab IS.** The shape below is the contract the
parsers in `tracker_parse` read back, and keeping the two in one place is the
point: they have drifted apart once already, and the failure was silent — orders
written into a mismatched tab reported success while recording nobody.

Creating tabs is not blocked by the Drive storage wall that stops the app
copying files. That limit is about *owning a file*; `addSheet` adds a tab to a
file that already exists, so the whole build is four API calls however many
parts there are.
"""
from __future__ import annotations

from datetime import datetime
from typing import Dict, List, Optional, Sequence

from config import TAB_MOVEMENTS
from utils import bom_sheet, parts_model, parts_tracker, project_registry

# --- the part tab, as generated ---------------------------------------------
# Rows 1-2 are label/value pairs, every value in its own cell. That matters:
# BOM values contain newlines (Material) and pipes (Spec), which the older
# packed-string format truncated silently.
META_ROW_1 = ("Part ID", "Part Name")
# E1/F1 on every part tab: the tab's CURRENT order, as central Orders knows
# it. The title is written at build time with an empty value — the value is
# stamped by bulk_orders when the order is actually raised. Tabs built before
# 19 Aug 2026 only have the pair because the old order-form flow wrote it;
# the builder itself never did, so a new project's tabs were missing it
# (Hamid, 19 Aug).
META_ORDER_LABEL = "order ID"
META_ROW_2 = ("Type", "Material", "Spec", "Default owner")

# Row 3 — the ledger. Read back by tracker_parse.PART_FIELDS.
LEDGER = [
    "Date", "Event", "Order / Sample ID", "Version", "Build", "Qty Ordered",
    "Spare", "Qty Received", "Qty Moved", "From", "To", "ETA",
    "Lead time (days)", "QC Pass?", "Courier / Tracking", "Selected for MP?",
    "Logged By", "Logged At", "Notes",
]
HEADER_ROW = 3          # ledger header sits on row 3; data starts at row 4

# The Movement Log the record template omits, without which every movement
# write fails.
MOVEMENT_HEADER = ["Date", "Item / Build", "Qty", "From", "To", "Stage",
                   "M-Code", "Notes"]

# Google rejects these in a sheet name; a single bad title fails the whole
# batch, so they are filtered out before anything is sent.
_ILLEGAL = set("[]*?/\\:")
_MAX_TITLE = 100


def _bom_field(row: dict, key: str) -> str:
    """A BOM value as a single clean line. Newlines become spaces so a meta
    cell stays one line; the value itself is never truncated."""
    return " ".join(str(row.get(key, "") or "").split())


def _title_problem(title: str) -> str:
    if not title:
        return "no Part ID"
    if len(title) > _MAX_TITLE:
        return "longer than %d characters" % _MAX_TITLE
    bad = sorted(_ILLEGAL & set(title))
    if bad:
        return "contains %s" % " ".join(bad)
    if not parts_tracker.is_part_tab(title):
        return "collides with a reserved tab name"
    return ""


def plan(project: str, sheet_id: Optional[str] = None,
         build_tab: str = "") -> dict:
    """What a build would do, without doing it.

    Returns {"parts": [...], "to_create": [...], "existing": [...],
             "duplicate": [...], "invalid": [(title, why)], "problem": str}
    """
    sheets = project_registry.sheets(project)
    record_id = sheet_id or sheets.get("tracker", "")
    bom_id = sheets.get("bom", "")
    out = {"parts": [], "to_create": [], "existing": [], "duplicate": [],
           "invalid": [], "movement_log": False, "problem": ""}

    if not record_id:
        out["problem"] = "%s has no project record sheet registered." % project
        return out
    if not bom_id:
        out["problem"] = "%s has no BOM sheet registered." % project
        return out

    tab = build_tab or bom_sheet.default_build(bom_id)
    rows = bom_sheet.fetch_bom(tab, bom_id) if tab else []
    if not rows:
        out["problem"] = ("No parts read from the BOM. Check the BOM tab set on "
                          "this project.")
        return out

    from utils.google_client import get_spreadsheet
    ss = get_spreadsheet(record_id)
    if ss is None:
        out["problem"] = "Cannot open the project record sheet."
        return out
    try:
        titles = [w.title for w in ss.worksheets()]
    except Exception as exc:
        out["problem"] = "Cannot read the record's tabs: %s" % exc
        return out
    existing = set(titles)
    out["movement_log"] = TAB_MOVEMENTS not in existing

    seen: Dict[str, str] = {}
    for row in rows:
        raw = str(row.get("mcode", "") or "").strip()
        title = parts_model.normalise_code(raw)
        part = {"title": title, "raw": raw,
                "part_name": _bom_field(row, "part_name"),
                "type": _bom_field(row, "group"),
                "material": _bom_field(row, "material"),
                "spec": _bom_field(row, "spec")}
        out["parts"].append(part)

        why = _title_problem(title)
        if why:
            out["invalid"].append((raw or "(blank)", why))
        elif title in seen:
            out["duplicate"].append(title)
        elif title in existing:
            seen[title] = "existing"
            out["existing"].append(title)
        else:
            seen[title] = "new"
            out["to_create"].append(part)
    return out


def _meta_rows(part: dict, owner: str) -> List[List[str]]:
    return [
        [META_ROW_1[0], part["title"], META_ROW_1[1], part["part_name"],
         META_ORDER_LABEL, ""],
        [META_ROW_2[0], part["type"], META_ROW_2[1], part["material"],
         META_ROW_2[2], part["spec"], META_ROW_2[3], owner],
        list(LEDGER),
    ]


def _quote(title: str) -> str:
    return "'%s'" % title.replace("'", "''")


def build(project: str, created_by: str, sheet_id: Optional[str] = None,
          build_tab: str = "") -> dict:
    """Create every part tab the BOM names and the record lacks.

    Existing tabs are never touched — not even their header — because rows 4+
    may hold real history. That is what makes this button safe to press twice.

    Returns the `plan()` dict plus {"created": [...], "error": str}.
    """
    result = plan(project, sheet_id, build_tab)
    result["created"] = []
    result["error"] = ""
    if result["problem"]:
        return result

    todo = result["to_create"]
    need_log = result["movement_log"]
    if not todo and not need_log:
        return result

    sheets = project_registry.sheets(project)
    record_id = sheet_id or sheets["tracker"]
    from utils.google_client import get_spreadsheet, invalidate_handles
    ss = get_spreadsheet(record_id)
    if ss is None:
        result["error"] = "Cannot open the project record sheet."
        return result

    cols = max(len(LEDGER), len(MOVEMENT_HEADER)) + 1
    requests = [{"addSheet": {"properties": {
        "title": p["title"], "gridProperties": {"rowCount": 200,
                                                "columnCount": cols}}}}
        for p in todo]
    if need_log:
        requests.append({"addSheet": {"properties": {
            "title": TAB_MOVEMENTS,
            "gridProperties": {"rowCount": 500, "columnCount": cols}}}})

    # 1 call: every tab. addSheet is all-or-nothing, which is why titles were
    # validated in plan() — one bad name would create none of them.
    try:
        reply = ss.batch_update({"requests": requests})
    except Exception as exc:
        result["error"] = "No tabs created: %s" % exc
        return result

    # 1 call: rows 1-3 of every tab created, plus the movement log's header.
    data = [{"range": "%s!A1" % _quote(p["title"]),
             "values": _meta_rows(p, created_by)} for p in todo]
    if need_log:
        data.append({"range": "%s!A1" % _quote(TAB_MOVEMENTS),
                     "values": [MOVEMENT_HEADER]})
    try:
        ss.values_batch_update({"valueInputOption": "USER_ENTERED",
                                "data": data})
    except Exception as exc:
        # The tabs exist but are blank. Say so precisely — a re-run will find
        # them "existing" and skip them, so silence here would strand them.
        result["created"] = [p["title"] for p in todo]
        result["error"] = ("Tabs were created but could not be filled in (%s). "
                           "They are empty and a re-run will skip them; delete "
                           "them and build again." % exc)
        return result

    _format(ss, reply, len(todo))
    result["created"] = [p["title"] for p in todo]
    invalidate_handles()
    parts_tracker.refresh(record_id)
    return result


def _format(ss, reply: dict, n_parts: int) -> None:
    """Bold the header row and freeze the three rows above the data.

    Cosmetic, and deliberately non-fatal: the tabs are already correct without
    it, so a formatting failure must not report the build as failed.
    """
    ids = [r.get("addSheet", {}).get("properties", {}).get("sheetId")
           for r in reply.get("replies", [])]
    requests = []
    for sheet_id in [i for i in ids if i is not None]:
        requests.append({"repeatCell": {
            "range": {"sheetId": sheet_id, "startRowIndex": HEADER_ROW - 1,
                      "endRowIndex": HEADER_ROW},
            "cell": {"userEnteredFormat": {"textFormat": {"bold": True}}},
            "fields": "userEnteredFormat.textFormat.bold"}})
        requests.append({"updateSheetProperties": {
            "properties": {"sheetId": sheet_id,
                           "gridProperties": {"frozenRowCount": HEADER_ROW}},
            "fields": "gridProperties.frozenRowCount"}})
    if not requests:
        return
    try:
        ss.batch_update({"requests": requests})
    except Exception:
        pass


# --- Overview ---------------------------------------------------------------
# The record template stamps Overview "DERIVED — written by the app from the
# part ledgers", and until now nothing wrote it — which is why the Parts page
# stops dead on a new record. It is derived from the PART TABS rather than from
# the BOM, so the same function stays correct after orders and movements land.

OVERVIEW_TAB = "Overview"

# our field -> the header spellings that accept it
_OVERVIEW_HEADERS = {
    "mcode": ("partid", "mcode"),
    "part_name": ("partname",),
    "category": ("type", "category"),
    "version": ("selectedversion",),
    "order_id": ("ordersampleid",),
    "status": ("status", "lateststatus"),
    "qty_ordered": ("qtyordered",),
    "qty_received": ("qtyreceived",),
    "qty_on_hand": ("approxqtyonhand",),
    "eta": ("eta",),
    "holder": ("holder",),
    "location": ("location",),
    "notes": ("notes",),
    "owner": ("defaultowner",),
    "created_by": ("createdby",),
    "created_at": ("createdat",),
}


def _overview_row(mcode: str, part: dict, built_by: str, stamp: str) -> dict:
    """One Overview row from a part tab: identity from its meta, order state
    from the last Order/Receipt pair in its history."""
    from utils import tracker_orders, tracker_parse

    meta = part.get("meta", {})
    history = part.get("history", [])
    row = {
        "mcode": mcode,
        "part_name": meta.get("part_name", ""),
        "category": meta.get("category", ""),
        "owner": meta.get("defaultowner", ""),
        "created_by": built_by,
        "created_at": stamp,
    }
    # The selected-for-MP row is the part's current truth; failing that, the
    # last row that says anything.
    selected = next((r for r in reversed(history)
                     if tracker_parse.is_selected(r)), None)
    latest = selected or (history[-1] if history else None)
    if latest:
        row["version"] = latest.get("version", "")
        row["order_id"] = latest.get("order_id", "")
        row["eta"] = latest.get("eta", "")
    # WHO HAS IT is the ledger's latest word, not the selected row's. The
    # selected line says which version goes to production; it does not follow
    # the goods. A hand-over or a corrected destination is recorded as a later
    # Movement/Correction, and reading the selected row instead left the
    # Overview naming the buyer while the count named the factory (19 Aug).
    held = [tracker_parse.holder_of(r) for r in history
            if tracker_parse.holder_of(r)]
    placed = [tracker_parse.place_of(r) for r in history
              if tracker_parse.place_of(r)]
    row["holder"] = held[-1] if held else ""
    row["location"] = placed[-1] if placed else ""
    # Latest status speaks Process Order's lifecycle vocabulary (Hamid) —
    # the QC text stays on the ledger rows where it was written.
    row["status"] = tracker_orders.derived_status(history).capitalize()
    orders = [r for r in history if tracker_orders.is_origin_row(r)]
    if orders:
        # A Correction/Update line may restate the ordered qty after the
        # raise — the last positive figure anywhere in the ledger is the
        # current truth (zeros are just untouched form fields).
        stated = [str(r.get("qty_ordered", "")).strip() for r in history
                  if tracker_parse.to_int(r.get("qty_ordered", "")) > 0]
        row["qty_ordered"] = stated[-1] if stated else orders[-1].get("qty_ordered", "")
        received = sum(tracker_parse.to_int(r.get("qty_received", ""))
                       for r in history)
        row["qty_received"] = str(received) if received else ""
    return row


def write_overview(project: str, built_by: str, sheet_id: Optional[str] = None,
                   replace: bool = False) -> dict:
    """Rebuild the Overview from the record's part tabs.

    Refuses to overwrite existing rows unless `replace` — the tab is derived,
    but a human may still have typed in it, and this is a full-block write.
    """
    from utils.auth import impersonation_block
    blocked = impersonation_block()
    if blocked:
        return {"written": 0, "problem": blocked}
    from utils.google_client import get_spreadsheet
    from utils import tracker_parse

    record_id = sheet_id or project_registry.sheets(project).get("tracker", "")
    out = {"rows": 0, "problem": "", "existing": 0}
    if not record_id:
        out["problem"] = "%s has no project record sheet." % project
        return out
    ss = get_spreadsheet(record_id)
    if ss is None:
        out["problem"] = "Cannot open the project record sheet."
        return out
    try:
        ws = ss.worksheet(OVERVIEW_TAB)
        grid = ws.get_all_values()
    except Exception as exc:
        out["problem"] = "Cannot read the Overview tab: %s" % exc
        return out
    if not grid:
        out["problem"] = "The Overview tab has no header row."
        return out

    header = grid[0]
    out["existing"] = sum(1 for r in grid[1:] if any(c.strip() for c in r))
    if out["existing"] and not replace:
        out["problem"] = ("Overview already has %d row(s). Tick 'replace' to "
                          "rebuild it." % out["existing"])
        return out

    parts = parts_tracker.fetch_all_parts(record_id)
    stamp = datetime.now().strftime("%d %b %Y")
    rows = []
    for mcode in sorted(parts):
        rec = _overview_row(mcode, parts[mcode], built_by, stamp)
        values = {}
        for field, value in rec.items():
            for spelling in _OVERVIEW_HEADERS.get(field, ()):
                values[spelling] = value
        rows.append([str(values.get(tracker_parse.norm(cell), ""))
                     for cell in header])

    # Pad to the previous height so a shrinking BOM can't strand old rows
    # below the new block.
    while len(rows) < out["existing"]:
        rows.append([""] * len(header))
    if not rows:
        out["problem"] = "No part tabs to derive an Overview from."
        return out

    try:
        ws.update(values=rows, range_name="A2")
    except Exception as exc:
        out["problem"] = "Could not write the Overview: %s" % exc
        return out
    parts_tracker.refresh(record_id)
    out["rows"] = len(parts)
    return out


def summary(result: dict) -> str:
    """One line describing what a plan or build found."""
    if result.get("problem"):
        return result["problem"]
    bits = ["%d part(s) in the BOM" % len(result.get("parts", []))]
    for key, label in (("created", "created"), ("to_create", "to create"),
                       ("existing", "already there"),
                       ("duplicate", "duplicate"), ("invalid", "unusable")):
        items = result.get(key) or []
        if items and not (key == "to_create" and result.get("created")):
            bits.append("%d %s" % (len(items), label))
    if result.get("movement_log"):
        bits.append("Movement Log missing")
    return " · ".join(bits)
