"""Write into the project tracker, as the team records it.

Two append-only writes, both onto the tracker sheet:

1. **Order line** (`write_order`) — the convention set by Hamid's reference
   record (the pilot project, tab M105, 18 Aug 2026): raising an order appends
   **one row**, the part's first history line —

    Event = Order   From = who raised it   To = the recipient
    Qty Ordered = n   Qty Received = 0   Notes = "ordered by"

   The receive line comes later, from the receiving flow, NOT from here: it
   is also an `Order` event with the same order id, but carries the received
   quantity, the courier/tracking, and the actual holder in `To`. Raise and
   receive make a pair over the part's lifetime, not at submit time.

2. **Process events** (`append_event`) — history keeps growing after the
   order: "this part is now at X" / "handing N pcs to Z". One row on the
   part's tab, same header-mapped layout.

**The movement log is not written here** (19 Aug 2026). It used to be, by
`append_movement`, which wrote a second row onto the project's `Movement Log`
tab. The merge replaced that tab with one `Movements` log per project, and
`stock_store.record_movement` writes it — applying `config.MOVEMENT_EVENTS` to
decide the stock effect at the same time. A movement therefore has exactly one
writer, which is why it cannot be logged twice or counted twice.

Rules this module keeps to:

- **Append only.** Existing rows are never edited or reordered.
- **Never create a tab.** If the part has no tab, nothing is written and the
  caller is told — inventing part tabs would corrupt a human's workbook.
- **Column order comes from the sheet**, read from its own header row, so a
  tab with extra or reordered columns still gets correct values.
"""
from __future__ import annotations

from typing import List, Optional, Tuple

from utils import tracker_parse
from utils.google_client import get_spreadsheet

ORIGIN_NOTE = "ordered by"
# Note markers for process events, mirrored by the readers in tracker_orders /
# tracker_parse. Kept here in one place so reading and writing can't drift.
LOCATION_NOTE = "location report"
HANDOVER_NOTE = "handed over by"

# Our field name -> the header spellings that accept it, most current first.
# Two generations of the ledger are writable: the current template
# (Event / From / To) and the pilot workbook it replaced (Type / Location /
# Holder). `place` and `holder` are the same two ideas in both — where it is,
# who has it — so one write works against either sheet.
FIELD_HEADERS = {
    "date": ("date",),
    "event": ("event",),
    "order_id": ("ordersampleid",),
    "version": ("version",),
    "build": ("build",),
    "qty_ordered": ("qtyordered",),
    "spare": ("spare",),
    "qty_received": ("qtyreceived",),
    "qty_moved": ("qtymoved",),
    "place": ("from", "location"),
    "holder": ("to", "holder"),
    "eta": ("eta",),
    "lead_time": ("leadtimedays",),
    "status": ("qcpass", "qcpassstatus"),
    "courier": ("couriertracking",),
    "selected": ("selectedformp",),
    "logged_by": ("loggedby",),
    "logged_at": ("loggedat",),
    "notes": ("notes",),
    # Pilot-only: the process went in `Type`. The current ledger uses that
    # column name for nothing, so this never collides with `event`.
    "type": ("type",),
    "vendor": ("vendorsource",),
}

# Columns without which an order row is meaningless: they carry who raised it
# and where it went, and `tracker_orders` reads exactly those back.
REQUIRED_HEADERS = ("place", "holder")


def _header_row(grid: List[List[str]]) -> Tuple[int, List[str]]:
    """(index, cells) of the part tab's header row."""
    for i, row in enumerate(grid[:10]):
        keys = {tracker_parse.norm(c) for c in row if c}
        if "version" in keys and len(keys & set(tracker_parse.PART_FIELDS)) >= 4:
            return i, list(row)
    return -1, []


def _missing_required(header: List[str]) -> List[str]:
    """Required fields this header has no column for.

    Guarding here turns a silent data loss into a refusal. Writing blind into a
    header that lacks From/To (or Location/Holder) produces a row that looks
    written but records nobody — and the order then vanishes from My Orders,
    because it is matched on the holder.
    """
    keys = {tracker_parse.norm(c) for c in header if c}
    return [field for field in REQUIRED_HEADERS
            if not keys & set(FIELD_HEADERS[field])]


def _row_values(header: List[str], values: dict) -> List[str]:
    """Lay a {field: value} dict out in the sheet's own column order."""
    wanted = {}
    for field, value in values.items():
        for spelling in FIELD_HEADERS.get(field, ()):
            wanted[spelling] = value
    return [str(wanted.get(tracker_parse.norm(cell), "")) for cell in header]


def write_order(mcode: str, ordered_by: str, ordered_from: str,
                recipient: str, recipient_location: str, *,
                order_id: str = "", version: str = "", date: str = "",
                order_type: str = "Order", vendor: str = "",
                qty_ordered: str = "", eta: str = "",
                receipt_note: str = "", logged_by: str = "",
                logged_at: str = "", build: str = "",
                sheet_id: Optional[str] = None
                ) -> Tuple[bool, str]:
    """Append THE order line to a part's tab (see the module docstring).

    `ordered_from` and `recipient_location` are kept for the callers'
    signatures but no longer land on the raise line — the source/vendor and
    the goods' location belong to the receive line, recorded at receiving.

    Returns (ok, message). ok=False leaves the sheet untouched.
    """
    if not mcode.strip():
        return False, "No M-code: the order can't be filed against a part."

    spreadsheet = get_spreadsheet(sheet_id)
    if spreadsheet is None:
        return False, "No Google credentials — nothing written."

    try:
        worksheet = spreadsheet.worksheet(mcode.strip())
    except Exception:
        return False, ("No tab named '%s' in this project's sheet. Add the part "
                       "tab first — this app never creates one." % mcode.strip())

    try:
        grid = worksheet.get_all_values()
    except Exception as e:
        return False, "Could not read '%s': %s" % (mcode, e)

    header_idx, header = _header_row(grid)
    if header_idx < 0:
        return False, "Tab '%s' has no recognisable header row." % mcode
    missing = _missing_required(header)
    if missing:
        return False, (
            "Tab '%s' has no %s column, so an order written there would record "
            "nobody. Rebuild the tab from the BOM."
            % (mcode, " or ".join("/".join(FIELD_HEADERS[f]) for f in missing)))

    order_line = {
        "event": tracker_parse.EVENT_ORDER,
        "version": version, "order_id": order_id, "date": date,
        "build": build, "vendor": vendor, "qty_ordered": qty_ordered,
        "qty_received": "0", "place": ordered_by, "holder": recipient,
        "eta": eta, "selected": "FALSE",
        "logged_by": logged_by, "logged_at": logged_at,
        "notes": ORIGIN_NOTE,
        # Pilot ledger only — its `Type` column held the process.
        "type": order_type,
    }

    try:
        worksheet.append_rows([_row_values(header, order_line)],
                              value_input_option="USER_ENTERED")
    except Exception as e:
        return False, "Write failed: %s" % e

    return True, ("Recorded on tab '%s': order line (%s → %s)."
                  % (mcode, ordered_by, recipient))


def write_receipt(mcode: str, *, order_id: str = "", qty_ordered: str = "",
                  qty_received: str = "", received_from: str = "",
                  holder: str = "", courier: str = "", date: str = "",
                  version: str = "", build: str = "", eta: str = "",
                  selected: str = "", note: str = "", logged_by: str = "",
                  logged_at: str = "", sheet_id: Optional[str] = None
                  ) -> Tuple[bool, str]:
    """Append THE receive line for an order — the reference record's second
    line of the pair: also an `Order` event, same order id, but carrying the
    received quantity, the courier/tracking, and the actual holder in To.
    """
    if not mcode.strip():
        return False, "No M-code: nothing to file the receipt against."
    spreadsheet = get_spreadsheet(sheet_id)
    if spreadsheet is None:
        return False, "No Google credentials — nothing written."
    worksheet, header, err = _part_worksheet(spreadsheet, mcode)
    if worksheet is None:
        return False, err
    missing = _missing_required(header)
    if missing:
        return False, (
            "Tab '%s' has no %s column, so a receipt written there would "
            "record nobody." % (mcode, " or ".join(
                "/".join(FIELD_HEADERS[f]) for f in missing)))

    row = {
        "event": tracker_parse.EVENT_ORDER,
        "order_id": order_id, "date": date, "version": version,
        "build": build, "qty_ordered": qty_ordered,
        "qty_received": qty_received,
        "place": received_from, "holder": holder,
        "courier": courier, "eta": eta,
        "selected": selected or "FALSE",
        "logged_by": logged_by, "logged_at": logged_at,
        "notes": note,
    }
    try:
        worksheet.append_rows([_row_values(header, row)],
                              value_input_option="USER_ENTERED")
    except Exception as e:
        return False, "Write failed: %s" % e
    return True, ("Received on tab '%s': %s pcs → %s."
                  % (mcode, qty_received or "?", holder or "?"))


def append_history(mcode: str, fields: dict,
                   sheet_id: Optional[str] = None) -> Tuple[bool, str]:
    """Append ONE arbitrary history line — Process Order's "add entry".

    `fields` uses this module's field names (event, date, order_id, version,
    build, qty_ordered, qty_received, qty_moved, place, holder, eta, status,
    courier, logged_by, logged_at, notes); unknown keys are ignored by the
    header mapping. Append-only, same guards as every other writer here.
    """
    if not mcode.strip():
        return False, "No M-code: nothing to file the entry against."
    spreadsheet = get_spreadsheet(sheet_id)
    if spreadsheet is None:
        return False, "No Google credentials — nothing written."
    worksheet, header, err = _part_worksheet(spreadsheet, mcode)
    if worksheet is None:
        return False, err
    missing = _missing_required(header)
    if missing:
        return False, (
            "Tab '%s' has no %s column, so an entry written there would "
            "record nobody." % (mcode, " or ".join(
                "/".join(FIELD_HEADERS[f]) for f in missing)))

    row = dict(fields)
    row.setdefault("selected", "FALSE")
    try:
        worksheet.append_rows([_row_values(header, row)],
                              value_input_option="USER_ENTERED")
    except Exception as e:
        return False, "Write failed: %s" % e
    return True, "Entry added to tab '%s' (%s)." % (
        mcode, row.get("event", "history"))


def _part_worksheet(spreadsheet, mcode: str):
    """(worksheet, header, error). Never creates a tab."""
    try:
        worksheet = spreadsheet.worksheet(mcode.strip())
    except Exception:
        return None, [], ("No tab named '%s' in this project's sheet. Add the "
                          "part tab first — this app never creates one."
                          % mcode.strip())
    try:
        grid = worksheet.get_all_values()
    except Exception as e:
        return None, [], "Could not read '%s': %s" % (mcode, e)
    header_idx, header = _header_row(grid)
    if header_idx < 0:
        return None, [], "Tab '%s' has no recognisable header row." % mcode
    return worksheet, header, ""


def append_event(mcode: str, *, holder: str, location: str, date: str,
                 qty: str = "", note: str = "", marker: str = LOCATION_NOTE,
                 version: str = "", sheet_id: Optional[str] = None
                 ) -> Tuple[bool, str]:
    """Append ONE history row to a part's tab — a process event.

    Used by "Report location" (marker=LOCATION_NOTE) and "Hand over"
    (marker=HANDOVER_NOTE, note names who handed over). The row records the
    part's new holder/location; append-only, like everything here.
    """
    if not mcode.strip():
        return False, "No M-code: nothing to file the event against."
    spreadsheet = get_spreadsheet(sheet_id)
    if spreadsheet is None:
        return False, "No Google credentials — nothing written."
    worksheet, header, err = _part_worksheet(spreadsheet, mcode)
    if worksheet is None:
        return False, err

    notes = ("%s: %s" % (marker, note.strip())) if note.strip() else marker
    row = {
        "version": version, "date": date,
        "event": tracker_parse.EVENT_MOVEMENT,
        "type": tracker_parse.EVENT_MOVEMENT,   # pilot ledger's column
        # A counted quantity is what MOVED, not what an order received. The
        # current ledger separates the two; the pilot one did not, so it still
        # gets the old column as well.
        "qty_moved": qty, "qty_received": qty,
        "place": location, "holder": holder,
        "selected": "FALSE", "notes": notes,
    }
    try:
        worksheet.append_rows([_row_values(header, row)],
                              value_input_option="USER_ENTERED")
    except Exception as e:
        return False, "Write failed: %s" % e
    return True, "Recorded on tab '%s': %s → %s (%s)." % (
        mcode, holder or "?", location or "?", marker)


# `append_movement` lived here until 19 Aug 2026. It wrote a second row to the
# project's `Movement Log` tab — which the merge renamed `(retired)`, so every
# one of its five callers had been failing silently. It is not restored,
# because `stock_store.record_movement` now writes the merged row itself:
# bringing it back would log every movement twice. See DECISIONS.md §24.


def write_orders(orders, sheet_id: Optional[str] = None) -> dict:
    """File many orders against their part tabs in two API calls.

    `orders` is a list of dicts: mcode, ordered_by, recipient, order_id,
    version, build, date, order_type, vendor, qty_ordered, eta, logged_by,
    logged_at. One raise line per order (see the module docstring).

    Returns {"filed": [mcode], "errors": [message]}.

    The per-part `write_order` costs two calls each — a read of the tab and an
    append. For a whole BOM that is quota-saturating, and a batch that trips
    halfway leaves a record nobody can tell apart from a finished one. So the
    tabs are read in one `values_batch_get` and written in one
    `values_batch_update`.

    That write targets a computed row, which is not safe against someone else
    appending at the same instant. So it is used ONLY for tabs with no history
    yet — the just-built case this exists for. A tab that already holds rows
    falls back to `write_order`, whose append is race-safe. Slower, and correct.
    """
    out = {"filed": [], "errors": []}
    if not orders:
        return out

    spreadsheet = get_spreadsheet(sheet_id)
    if spreadsheet is None:
        out["errors"].append("No Google credentials — nothing written.")
        return out

    wanted = []
    for order in orders:
        mcode = str(order.get("mcode", "")).strip()
        if not mcode:
            out["errors"].append("An order had no Part ID and was skipped.")
            continue
        wanted.append((mcode, order))

    try:
        ranges = ["'%s'!A1:Z200" % m.replace("'", "''") for m, _ in wanted]
        response = spreadsheet.values_batch_get(ranges)
        grids = [vr.get("values", []) for vr in response.get("valueRanges", [])]
    except Exception as exc:
        out["errors"].append("Could not read the part tabs: %s" % exc)
        return out

    data, batched, fallback = [], [], []
    for (mcode, order), grid in zip(wanted, grids):
        if not grid:
            out["errors"].append(
                "No tab named '%s' — build the record from the BOM first." % mcode)
            continue
        header_idx, header = _header_row(grid)
        if header_idx < 0:
            out["errors"].append("Tab '%s' has no recognisable header row." % mcode)
            continue
        missing = _missing_required(header)
        if missing:
            out["errors"].append(
                "Tab '%s' has no %s column, so an order there would record "
                "nobody." % (mcode, " or ".join(
                    "/".join(FIELD_HEADERS[f]) for f in missing)))
            continue
        if len(grid) > header_idx + 1:
            fallback.append((mcode, order))       # already has history
            continue

        order_line = {
            "event": tracker_parse.EVENT_ORDER,
            "version": order.get("version", ""),
            "order_id": order.get("order_id", ""),
            "date": order.get("date", ""),
            "build": order.get("build", ""),
            "vendor": order.get("vendor", ""),
            "qty_ordered": str(order.get("qty_ordered", "")),
            "qty_received": "0",
            "place": order.get("ordered_by", ""),
            "holder": order.get("recipient", ""),
            "eta": order.get("eta", ""),
            "selected": "FALSE",
            "logged_by": order.get("logged_by", ""),
            "logged_at": order.get("logged_at", ""),
            "notes": ORIGIN_NOTE,
            "type": order.get("order_type", ""),
        }
        start = len(grid) + 1
        data.append({"range": "'%s'!A%d" % (mcode.replace("'", "''"), start),
                     "values": [_row_values(header, order_line)]})
        batched.append(mcode)

    if data:
        try:
            spreadsheet.values_batch_update(
                {"valueInputOption": "USER_ENTERED", "data": data})
            out["filed"].extend(batched)
        except Exception as exc:
            out["errors"].append("Batch write failed for %d part(s): %s"
                                 % (len(batched), exc))

    for mcode, order in fallback:
        ok, message = write_order(
            mcode, order.get("ordered_by", ""), order.get("ordered_from", ""),
            order.get("recipient", ""), order.get("recipient_location", ""),
            order_id=order.get("order_id", ""), version=order.get("version", ""),
            date=order.get("date", ""), order_type=order.get("order_type", ""),
            vendor=order.get("vendor", ""),
            qty_ordered=str(order.get("qty_ordered", "")),
            eta=order.get("eta", ""), build=order.get("build", ""),
            logged_by=order.get("logged_by", ""),
            logged_at=order.get("logged_at", ""), sheet_id=sheet_id)
        (out["filed"] if ok else out["errors"]).append(mcode if ok else message)

    return out
