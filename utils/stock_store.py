"""Stock as the main record counts it, plus the log of what moved.

Two tabs on the central record, added by Hamid on 18 Aug 2026:

- **Stock** — the current count: one row per part, per project, per holder.
- **Stock_History** — append-only, one row per movement. It carries the same
  columns plus `Sent to`, so a row reads "this many of this part left
  *Holder* and went to *Sent to*".

A movement therefore writes twice: a new history line (the record of what
happened, never edited afterwards) and an adjusted count on both sides in
Stock. The history is the truth; Stock is the running total derived from it,
and `rebuild_from_history()` can recompute the whole tab when the two drift.

Every holder written here comes from the `Holders` directory and every part
from the project's own part list — `record_movement` refuses anything else
rather than inventing a holder or a part number, because a typo here is a
part that silently vanishes from the count.
"""
from __future__ import annotations

from datetime import datetime
from typing import Dict, List, Optional, Tuple

from config import CENTRAL_SHEET_ID, TAB_STOCK, TAB_STOCK_HISTORY
from utils import data_cache
from utils.tracker_parse import norm, to_int

_TTL_SECONDS = 120

# Normalised sheet header -> our field name. Both tabs share these; only
# Stock_History carries `sentto`.
_FIELDS = {
    "partid": "part_id",
    "mcode": "part_id",
    "description": "description",
    "partname": "description",
    "type": "type",
    "supplierpartno": "supplier_part_no",
    "supplier": "supplier",
    "project": "project",
    "qtyonhand": "qty",
    "qty": "qty",
    "qtymoved": "qty",
    "holder": "holder",
    "sentto": "sent_to",
    "lastcounted": "last_counted",
    "date": "last_counted",
    "notes": "notes",
}


def _key(tab: str) -> str:
    return "%s:%s" % (CENTRAL_SHEET_ID, tab.lower())


def project_matches(cell: str, project: str) -> bool:
    """Whether a PROJECT cell refers to this project.

    The sheets carry a short form as well as the registered name — the Stock
    tab says "P1" where the registry says "P1 T2" — so an exact compare
    silently hides every row. One is allowed to be a prefix of the other;
    anything looser would let "P1 T2" match "P1 T3".
    """
    a = str(cell or "").strip().lower()
    b = str(project or "").strip().lower()
    if not a or not b:
        return not b
    if a == b:
        return True
    short, long = (a, b) if len(a) <= len(b) else (b, a)
    return long.startswith(short + " ") or long == short


def _parse(values: List[List[str]]) -> List[Dict[str, str]]:
    if not values:
        return []
    header = [norm(h) for h in values[0]]
    out = []
    for row in values[1:]:
        rec: Dict[str, str] = {}
        for i, key in enumerate(header):
            field = _FIELDS.get(key)
            if field and i < len(row) and str(row[i]).strip():
                rec[field] = str(row[i]).strip()
        if rec:
            out.append(rec)
    return out


def _load(tab: str) -> List[Dict[str, str]]:
    from utils.google_client import with_worksheet

    try:
        values = with_worksheet(tab, lambda ws: ws.get_all_values(),
                                sheet_id=CENTRAL_SHEET_ID)
    except Exception:
        return []
    return _parse(values)


def fetch_stock() -> List[Dict[str, str]]:
    """Current stock rows. Rows with no Part ID are kept — the page shows them
    as unusable rather than hiding them, because five such rows are exactly
    how 3,966 units ended up attributed to nobody."""
    return data_cache.get(_key(TAB_STOCK), _TTL_SECONDS,
                          lambda: _load(TAB_STOCK), spinner="Loading stock…")


def _project_sheet(project: str = "") -> str:
    from utils import project_registry
    from utils.google_client import active_sheet_id
    if project:
        return project_registry.tracker_sheet(project) or ""
    return active_sheet_id()


def fetch_history(project: str = "") -> List[Dict[str, str]]:
    """Every recorded movement, oldest first.

    Comes from the project's merged `Movements` log — one log per project,
    where a movement is an event on the part's own story (19 Aug). The central
    `Stock_History` is the previous home and is read only for a project that
    has not been migrated yet, so this rolls out one project at a time.
    """
    from utils import movements_store

    sid = _project_sheet(project)
    merged = movements_store.fetch(sid) if sid else []
    if merged:
        return [{
            "part_id": r.get("part_id", ""),
            "description": r.get("description", ""),
            "type": r.get("type", ""),
            "project": project or "",
            "qty": r.get("qty", ""),
            "holder": r.get("from", "") or "(external)",
            "sent_to": r.get("to", ""),
            "last_counted": r.get("date", ""),
            "notes": r.get("notes", ""),
            "event": r.get("event", ""),
            "flag": r.get("flag", ""),
        } for r in merged if r.get("part_id")]
    return data_cache.get(_key(TAB_STOCK_HISTORY), _TTL_SECONDS,
                          lambda: _load(TAB_STOCK_HISTORY))


def counted_rows() -> List[Dict[str, str]]:
    """Stock rows that actually name a part — the ones that can be counted."""
    return [r for r in fetch_stock() if r.get("part_id")]


def orphan_rows() -> List[Dict[str, str]]:
    """Stock rows with a quantity but no part — countable by nobody."""
    return [r for r in fetch_stock()
            if not r.get("part_id") and to_int(r.get("qty", "")) > 0]


def by_holder(project: str = "") -> Dict[str, int]:
    """Total pieces per holder, optionally for one project."""
    out: Dict[str, int] = {}
    for row in counted_rows():
        if project and not project_matches(row.get("project", ""), project):
            continue
        holder = row.get("holder", "") or "(not stated)"
        out[holder] = out.get(holder, 0) + to_int(row.get("qty", ""))
    return out


def for_part(part_id: str, project: str = "") -> List[Dict[str, str]]:
    """Where a part's stock sits, one row per holder."""
    code = str(part_id or "").strip().lower()
    return [r for r in counted_rows()
            if r.get("part_id", "").lower() == code
            and (not project or project_matches(r.get("project", ""), project))]


def history_for(part_id: str = "", holder: str = "",
                project: str = "") -> List[Dict[str, str]]:
    """Movements touching a part and/or a holder (either side of the move)."""
    code = str(part_id or "").strip().lower()
    who = str(holder or "").strip().lower()
    out = []
    for row in fetch_history(project):
        if code and row.get("part_id", "").lower() != code:
            continue
        if who and who not in (row.get("holder", "").lower(),
                               row.get("sent_to", "").lower()):
            continue
        out.append(row)
    return out


# --- writing -----------------------------------------------------------------

def _header_of(values: List[List[str]]) -> List[str]:
    return [str(c).strip() for c in values[0]] if values else []


def _row_values(header: List[str], rec: Dict[str, str]) -> List[str]:
    """Lay a record out in the sheet's own column order.

    Driven by the header rather than a fixed list, so reordering or renaming a
    column on the sheet cannot silently write a value into the wrong cell.
    """
    out = []
    for cell in header:
        field = _FIELDS.get(norm(cell))
        out.append(str(rec.get(field, "")) if field else "")
    return out


def _matches(rec: Dict[str, str], part_id: str, project: str,
             holder: str) -> bool:
    # Project compared loosely for the same reason as everywhere else: a row
    # labelled "P1" is the same project as "P1 T2", and treating them as
    # different would append a second row for a part a holder already has.
    return (rec.get("part_id", "").strip().lower() == part_id.strip().lower()
            and project_matches(rec.get("project", ""), project)
            and rec.get("holder", "").strip().lower() == holder.strip().lower())


def _holds_stock(name: str) -> bool:
    """Whether a name is a place we carry a balance for.

    The same rule `movements_store.holdings` applies when it reads the log
    back. A supplier CATEGORY hands goods over and never holds a negative
    balance of them; a NAMED vendor (an assembly factory) does hold stock. If the
    write side and the read side disagree about this, the count and the log
    drift apart and the Checks tab fills up with differences nobody caused.
    """
    from utils import holders_store

    if not name or name == "(external)":
        return False
    return holders_store.get(name).get("kind", "person").lower() != "source"


def record_movement(part_id: str, project: str, qty: int, to_holder: str,
                    from_holder: str = "", *, event: str = "Movement",
                    description: str = "", part_type: str = "",
                    supplier: str = "", supplier_part_no: str = "",
                    notes: str = "", courier: str = "", build: str = "",
                    date: str = "", logged_by: str = "") -> Dict[str, object]:
    """Record `event` moving `qty` of a part, and update the count from it.

    **`event` decides what happens to the count**, via `config.MOVEMENT_EVENTS`
    — not the shape of the arguments. A `Receipt` adds at the destination, a
    `Scrap` takes from the source, a `Movement` does both, and a `QC` or
    `Update` changes no count at all but is still logged. Before 19 Aug this
    was inferred from whether `from_holder` was blank, which meant the app
    could only ever record a transfer, and a scrapped batch never left the
    count.

    `from_holder` empty means goods arriving from outside. Returns
    {"ok": bool, "problem": str, "history_row": [...], "stock_changes": [...]}.

    Refuses rather than guesses: an unknown holder, an unknown part or a
    non-positive quantity is a caller error, and writing it would corrupt a
    count that nobody would think to re-check.
    """
    from utils import holders_store
    from utils.google_client import with_worksheet
    from config import event_rule

    out: Dict[str, object] = {"ok": False, "problem": "",
                              "history_row": [], "stock_changes": []}

    part_id = str(part_id or "").strip()
    project = str(project or "").strip()
    to_holder = str(to_holder or "").strip()
    from_holder = str(from_holder or "").strip()
    event = str(event or "Movement").strip() or "Movement"
    effect = event_rule(event)["stock"]

    if not part_id:
        out["problem"] = "No part chosen."
        return out
    if qty <= 0:
        out["problem"] = "Quantity must be more than zero."
        return out
    # An event that only informs still needs somewhere to be filed, but it
    # does not need a destination — a QC result is about a part, not a move.
    if not to_holder and effect in ("in", "transfer"):
        out["problem"] = "No destination holder chosen."
        return out
    if not from_holder and effect == "out":
        out["problem"] = ("%s takes goods out of somewhere — say which holder "
                          "they are leaving." % event)
        return out
    if to_holder and not holders_store.is_known(to_holder):
        out["problem"] = ("%r is not in the Holders directory. Add it there "
                          "first — the directory is the one list of holders."
                          % to_holder)
        return out
    if from_holder and not holders_store.is_known(from_holder):
        out["problem"] = ("%r is not in the Holders directory. Add it there "
                          "first." % from_holder)
        return out
    if from_holder and from_holder.lower() == to_holder.lower():
        out["problem"] = "Source and destination are the same holder."
        return out

    stamp = date or datetime.now().strftime("%d %b %Y")

    # --- read Stock once, decide every change, then write once -------------
    try:
        grid = with_worksheet(TAB_STOCK, lambda ws: ws.get_all_values(),
                              sheet_id=CENTRAL_SHEET_ID)
    except Exception as exc:
        out["problem"] = "Could not read the Stock tab: %s" % exc
        return out
    if not grid:
        out["problem"] = "The Stock tab has no header row."
        return out

    header = _header_of(grid)
    rows = _parse(grid)                       # aligned with grid[1:]
    qty_col = None
    for i, cell in enumerate(header):
        if _FIELDS.get(norm(cell)) == "qty":
            qty_col = i
            break
    if qty_col is None:
        out["problem"] = "The Stock tab has no 'Qty on hand' column."
        return out

    # What the event does to the count, and to whom. `takes` and `gives` are
    # the two halves of the event rule; either can be off, which is the whole
    # point — a Receipt only gives, a Scrap only takes, an Update does neither.
    takes = effect in ("transfer", "out") and _holds_stock(from_holder)
    gives = effect in ("transfer", "in") and _holds_stock(to_holder)

    if takes:
        held = sum(to_int(r.get("qty", "")) for r in rows
                   if _matches(r, part_id, project, from_holder))
        if held < qty:
            out["problem"] = ("%s holds %d of %s, so %d cannot be moved."
                              % (from_holder, held, part_id, qty))
            return out

    updates: List[Tuple[int, int]] = []       # (grid row index, new qty)
    appends: List[List[str]] = []

    def _apply(holder: str, delta: int) -> None:
        for i, rec in enumerate(rows):
            if _matches(rec, part_id, project, holder):
                new = to_int(rec.get("qty", "")) + delta
                updates.append((i + 1, new))   # +1 for the header row
                return
        if delta > 0:
            appends.append(_row_values(header, {
                "part_id": part_id, "description": description,
                "type": part_type, "supplier_part_no": supplier_part_no,
                "supplier": supplier, "project": project, "qty": str(delta),
                "holder": holder, "last_counted": stamp, "notes": notes,
            }))

    if takes:
        _apply(from_holder, -qty)
    if gives:
        _apply(to_holder, qty)

    a1 = _a1_column(qty_col)
    data = [{"range": "%s!%s%d" % (_quote(TAB_STOCK), a1, idx + 1),
             "values": [[str(new)]]} for idx, new in updates]

    try:
        from utils.google_client import get_spreadsheet
        ss = get_spreadsheet(CENTRAL_SHEET_ID)
        if ss is None:
            out["problem"] = "Cannot open the main record."
            return out
        if data:
            ss.values_batch_update({"valueInputOption": "USER_ENTERED",
                                    "data": data})
        if appends:
            with_worksheet(TAB_STOCK,
                           lambda ws: ws.append_rows(
                               appends, value_input_option="USER_ENTERED"),
                           sheet_id=CENTRAL_SHEET_ID)
    except Exception as exc:
        out["problem"] = "Could not update the Stock tab: %s" % exc
        return out

    # --- the history line: written last, so it never claims a move that
    #     did not land -----------------------------------------------------
    # It goes to the PROJECT's merged `Movements` log, not the central
    # Stock_History: one log per project, sitting beside the part ledgers it
    # describes (Hamid, 19 Aug). A project not yet migrated still has its
    # Stock_History, so the old target is the fallback.
    from utils import movements_store
    from config import TAB_MOVEMENTS_MERGED

    # Does this project HAVE the merged log, not "does it have rows in it" —
    # a record built today has the tab and nothing in it, and the empty test
    # would send its first movement to the retired central log.
    project_sid = _project_sheet(project)
    merged = bool(project_sid) and movements_store.has_log(project_sid)
    tab = TAB_MOVEMENTS_MERGED if merged else TAB_STOCK_HISTORY
    target = project_sid if merged else CENTRAL_SHEET_ID
    try:
        hgrid = with_worksheet(tab, lambda ws: ws.get_all_values(),
                               sheet_id=target)
        hheader = _header_of(hgrid)
        if not hheader:
            raise ValueError("the %s tab has no header row" % tab)
        note = notes
        if logged_by:
            note = ("%s (logged by %s)" % (notes, logged_by)).strip()
        if merged:
            # The caller's event, written as given. Guessing it from whether
            # `from_holder` was blank is what made every row either Movement
            # or Receipt, so a Shipping or a Scrap could not be recorded.
            fields = {"Date": stamp, "Event": event,
                      "Part ID": part_id, "Description": description,
                      "Type": part_type, "Qty": str(qty),
                      "From": from_holder or "(external)", "To": to_holder,
                      "Build": build, "Logged By": logged_by,
                      "Logged At": stamp, "Flag": "", "Notes": note,
                      "Courier / Tracking": courier}
            hrow = [str(fields.get(h, "")) for h in hheader]
        else:
            hrow = _row_values(hheader, {
                "part_id": part_id, "description": description,
                "type": part_type, "supplier_part_no": supplier_part_no,
                "supplier": supplier, "project": project, "qty": str(qty),
                "holder": from_holder or "(external)", "sent_to": to_holder,
                "last_counted": stamp, "notes": note})
        with_worksheet(tab, lambda ws: ws.append_rows(
            [hrow], value_input_option="USER_ENTERED"), sheet_id=target)
        out["history_row"] = hrow
        if merged:
            movements_store.refresh(project_sid)
    except Exception as exc:
        out["problem"] = ("Stock was updated but the movement could not be "
                          "logged (%s). Add the %s line by hand so the count "
                          "and the log agree." % (exc, tab))
        refresh()
        return out

    out["ok"] = True
    out["stock_changes"] = []
    if takes:
        out["stock_changes"].append({"holder": from_holder, "delta": -qty})
    if gives:
        out["stock_changes"].append({"holder": to_holder, "delta": qty})
    refresh()
    return out


def _quote(title: str) -> str:
    return "'%s'" % title.replace("'", "''")


def _a1_column(index: int) -> str:
    """0-based column index -> A1 letters."""
    letters = ""
    index += 1
    while index:
        index, rem = divmod(index - 1, 26)
        letters = chr(65 + rem) + letters
    return letters


def totals_from_history(project: str = "") -> Dict[Tuple[str, str, str], int]:
    """What the movement log implies the counts should be.

    Keyed by (part, project, holder). Used to show where Stock and its own
    history disagree — the same "flag it, never silently resolve it" rule the
    derived-stock view already follows.
    """
    from utils import movements_store

    # Delegated, not repeated. `movements_store.holdings` applies the event
    # table — a supplier CATEGORY hands stock over and never carries a balance,
    # a named vendor (an assembly factory) does hold it — and two copies of that rule
    # is how the Overview and this count came to disagree about 2,495 pieces.
    sid = _project_sheet(project)
    merged = movements_store.fetch(sid) if sid else []
    if merged:
        return {(part, project, holder): qty
                for (part, holder), qty in movements_store.holdings(sid).items()}

    out: Dict[Tuple[str, str, str], int] = {}
    for row in fetch_history(project):
        proj = row.get("project", "")
        if project and not project_matches(proj, project):
            continue
        part = row.get("part_id", "")
        if not part:
            continue
        qty = to_int(row.get("qty", ""))
        if qty <= 0:
            continue
        src, dst = row.get("holder", ""), row.get("sent_to", "")
        from utils import holders_store

        def holds(name):
            if not name or name == "(external)":
                return False
            return holders_store.get(name).get("kind", "person").lower() != "source"
        if holds(src):
            out[(part, proj, src)] = out.get((part, proj, src), 0) - qty
        if holds(dst):
            out[(part, proj, dst)] = out.get((part, proj, dst), 0) + qty
    return out


def disagreements(project: str = "") -> List[Dict[str, object]]:
    """Rows where the Stock count and the movement log do not match."""
    implied = totals_from_history(project)
    counted: Dict[Tuple[str, str, str], int] = {}
    for row in counted_rows():
        proj = row.get("project", "")
        if project and not project_matches(proj, project):
            continue
        key = (row.get("part_id", ""), proj, row.get("holder", ""))
        counted[key] = counted.get(key, 0) + to_int(row.get("qty", ""))

    out = []
    for key in sorted(set(implied) | set(counted)):
        want, have = implied.get(key, 0), counted.get(key, 0)
        if want != have:
            out.append({"part_id": key[0], "project": key[1], "holder": key[2],
                        "counted": have, "from_history": want,
                        "difference": have - want})
    return out


def refresh() -> None:
    data_cache.invalidate(_key(TAB_STOCK), _key(TAB_STOCK_HISTORY))
