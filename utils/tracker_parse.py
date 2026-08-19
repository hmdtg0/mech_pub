"""Parsers for the Parts Tracker sheet format (no Google/Streamlit deps).

The tracker is a human-maintained workbook — one tab per part plus an
Overview rollup, a Movement Log and summary tabs. These functions turn the
raw cell grids into normalised dicts, so the same code path works whether the
grid came from gspread (live) or from an .xlsx export (snapshot).

Nothing here writes: the human tabs stay exactly as the team keeps them.
"""
from __future__ import annotations

import re
from typing import Dict, List, Sequence

Grid = Sequence[Sequence[str]]

# Header aliases: normalised header text -> our field name. The sheet's headers
# drift (typos, trailing spaces, line breaks), so matching is done on a
# lowercase alphanumeric-only form.
OVERVIEW_FIELDS = {
    "mcode": "mcode",
    "partid": "mcode",          # the current template's spelling
    "partname": "part_name",
    "category": "category",
    "type": "category",         # "Type" replaced "Category" in the template
    "selectedversion": "version",
    "ordersampleid": "order_id",
    "status": "status",
    "qtyordered": "qty_ordered",
    "qtyreceived": "qty_received",
    "approxqtyonhand": "qty_on_hand",
    "eta": "eta",
    "location": "location",
    "holder": "holder",
    "notes": "notes",
    "defaultowner": "owner",
}

# The part-tab ledger. Two generations of the sheet are readable at once: the
# current template (Event / From / To …) and the pilot workbook it replaced
# (Type / Location / Holder). The pair maps 1:1 — `From` is where it is,
# `To` is who has it — so both spellings land on the same pair of ideas and
# `place_of()` / `holder_of()` below hide the difference from every caller.
PART_FIELDS = {
    # current template — 19 columns
    "date": "date",
    "event": "event",
    "ordersampleid": "order_id",
    "version": "version",
    "build": "build",
    "qtyordered": "qty_ordered",
    "spare": "spare",
    "qtyreceived": "qty_received",
    "qtymoved": "qty_moved",
    "from": "from",
    "to": "to",
    "eta": "eta",
    "leadtimedays": "lead_time",
    "qcpass": "qc",
    "couriertracking": "courier",
    "selectedformp": "selected",
    "loggedby": "logged_by",
    "loggedat": "logged_at",
    "notes": "notes",
    # pilot workbook — still read, never written
    "type": "type",
    "vendorsource": "vendor",
    "qcpassstatus": "status",
    "location": "location",
    "holder": "holder",
}

# Events the ledger records. An order is two rows: Order then Receipt.
EVENT_ORDER = "Order"
EVENT_RECEIPT = "Receipt"
EVENT_MOVEMENT = "Movement"

MOVEMENT_FIELDS = {
    "date": "date",
    "itembuild": "item",
    "qty": "qty",
    "from": "from",
    "to": "to",
    "stage": "stage",
    "notes": "notes",
}


def norm(text: str) -> str:
    """Lowercase alphanumeric form of a header cell, for tolerant matching."""
    return "".join(ch for ch in str(text).lower() if ch.isalnum())


def _cell(row: Sequence[str], i: int) -> str:
    return str(row[i]).strip() if i < len(row) and row[i] is not None else ""


def _find_header(grid: Grid, fields: Dict[str, str], required) -> int:
    """Index of the header row — the first row carrying an identity column and
    at least three other known headers. Tracker tabs put a title and a meta line
    above the table, and the number of those lines varies by tab.

    `required` is one normalised key or several. Several, because the identity
    column has been spelled `MPN`, `M-Code` and `Part ID` across the sheets this
    app reads, and a single accepted spelling silently returns "no data" for the
    other two rather than failing.
    """
    wanted = {required} if isinstance(required, str) else set(required)
    for i, row in enumerate(grid[:10]):
        keys = {norm(c) for c in row if c}
        if (keys & wanted) and len(keys & set(fields)) >= 4:
            return i
    return -1


def _rows(grid: Grid, fields: Dict[str, str], header_idx: int) -> List[dict]:
    """Every row below the header, verbatim.

    Columns the alias map doesn't know are kept under their own header text, so
    a column added to the sheet shows up here instead of vanishing. Cell text
    is never reformatted or truncated — the sheet is the record.
    """
    header = grid[header_idx]
    cols = {}
    for i, cell in enumerate(header):
        key = fields.get(norm(cell))
        if key is None:
            label = str(cell).strip()
            key = label if label else None
        if key and key not in cols:
            cols[key] = i
    out = []
    for row in grid[header_idx + 1:]:
        rec = {key: _cell(row, i) for key, i in cols.items()}
        if any(rec.values()):
            out.append(rec)
    return out


def parse_overview(grid: Grid) -> List[dict]:
    """Overview tab -> one dict per part (the row flagged Selected for MP)."""
    h = _find_header(grid, OVERVIEW_FIELDS, ("mcode", "partid"))
    if h < 0:
        return []
    return [r for r in _rows(grid, OVERVIEW_FIELDS, h) if r.get("mcode")]


# Meta labels -> our key, for the label/value cells in rows 1-2.
_META_LABELS = {
    "partid": "mcode",
    "mcode": "mcode",
    "partname": "part_name",
    "type": "category",
    "category": "category",
    "material": "material",
    "spec": "spec",
    "defaultowner": "defaultowner",
}


def _meta_pairs(row: Sequence[str], meta: Dict[str, str]) -> bool:
    """Read `Label | value | Label | value …` across one row. True if it read
    anything — which is how the caller tells the two meta formats apart."""
    found = False
    for i in range(0, len(row) - 1, 2):
        key = _META_LABELS.get(norm(_cell(row, i)))
        if key:
            meta[key] = _cell(row, i + 1)
            found = True
    return found


def parse_part_tab(grid: Grid) -> dict:
    """A single part tab -> {"meta": {...}, "history": [...]}.

    Two meta formats are accepted. The current template addresses every value
    in its own cell — `Part ID | M105 | Part Name | Top bearing` — which is
    preferred: a value can then contain anything, including the newlines and
    pipes the BOM's Material column actually holds. The pilot workbook packed
    the same facts into two strings, `"M107 — Glass fixing"` and
    `"Category: … | Material: …"`, and those are still read.
    """
    meta: Dict[str, str] = {}
    read_pairs = False
    for r in range(min(2, len(grid))):
        read_pairs |= _meta_pairs(grid[r], meta)

    if not read_pairs:
        title = _cell(grid[0], 0) if grid else ""
        for dash in ("—", "–", "-"):
            if dash in title:
                code, _, name = title.partition(dash)
                meta["mcode"] = code.strip()
                meta["part_name"] = name.strip()
                break
        else:
            meta["mcode"] = title.strip()
            meta["part_name"] = ""

        info = _cell(grid[1], 0) if len(grid) > 1 else ""
        for chunk in info.split("|"):
            key, sep, val = chunk.partition(":")
            if sep:
                meta[norm(key)] = val.strip()

    meta.setdefault("mcode", "")
    meta.setdefault("part_name", "")

    h = _find_header(grid, PART_FIELDS, "version")
    history = _rows(grid, PART_FIELDS, h) if h >= 0 else []
    return {"meta": meta, "history": history}


def place_of(row: dict) -> str:
    """Where the part is, whichever generation of the ledger wrote the row."""
    return (row.get("from") or row.get("location") or "").strip()


def holder_of(row: dict) -> str:
    """Who has the part, whichever generation of the ledger wrote the row."""
    return (row.get("to") or row.get("holder") or "").strip()


def event_of(row: dict) -> str:
    """The row's event. The pilot ledger had no Event column — it put the
    manufacturing process in `Type` — so that is deliberately NOT used as a
    fallback; a process name is not an event."""
    return (row.get("event") or "").strip()


def parse_movements(grid: Grid) -> List[dict]:
    """Movement Log tab -> one dict per row, newest last.

    Every non-empty row is kept, including ones with no Date or Item — the log
    carries summary rows that live only in the Notes column, and dropping them
    silently loses the most detailed entries in the sheet.
    """
    h = _find_header(grid, MOVEMENT_FIELDS, "date")
    if h < 0:
        return []
    return _rows(grid, MOVEMENT_FIELDS, h)


# Who raised an order, as opposed to who holds the parts now. The tracker
# records only the current holder, so the origin is carried either by an
# "Ordered by" column on the part tab or by a note convention:
#   "Order origin: ordered by Sam Smith for Alex Jones"
_ORIGIN_COLUMNS = ("ordered by", "orderedby", "order origin", "orderorigin",
                   "ordered_by", "requested by")
_ORIGIN_NOTE = re.compile(
    r"(?:order origin|ordered by)\s*[:\-]?\s*(?:ordered by\s+)?([^,;.\n]+?)"
    r"(?:\s+for\s+|[,;.\n]|$)", re.I)


def order_origin(row: dict) -> str:
    """Who raised this order, or "" if the row doesn't say.

    Prefers a dedicated column (any of the accepted spellings), falls back to
    the note convention, so a team can adopt the column later without the app
    changing.
    """
    for key, value in row.items():
        if str(key).strip().lower() in _ORIGIN_COLUMNS and str(value).strip():
            return str(value).strip()
    match = _ORIGIN_NOTE.search(str(row.get("notes", "")))
    return match.group(1).strip() if match else ""


def order_recipient(row: dict) -> str:
    """Who the order was raised FOR, from the "… for X" half of the note."""
    match = re.search(r"(?:order origin|ordered by)[^\n]*?\bfor\s+([^,;.\n]+)",
                      str(row.get("notes", "")), re.I)
    return match.group(1).strip() if match else ""


def is_selected(row: dict) -> bool:
    """The "Selected for MP?" flag — TRUE/True/1/yes all count."""
    return str(row.get("selected", "")).strip().lower() in ("true", "1", "yes", "y")


def to_int(value: str) -> int:
    """Best-effort quantity parse; the sheet holds things like "22+1NG"."""
    digits = ""
    for ch in str(value):
        if ch.isdigit():
            digits += ch
        elif digits:
            break
    return int(digits) if digits else 0
