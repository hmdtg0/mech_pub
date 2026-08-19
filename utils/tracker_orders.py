"""Orders as recorded in the project tracker itself.

Two generations of the convention are readable:

**Current** (Hamid's migrated ledger, 18 Aug 2026): the `Order / Sample ID`
column is the order's identity. Every line of one order carries the same id
— the raise (`Event = Order`, nothing received yet), then movements,
corrections and ETA updates, then the receive line (an `Order`/`Receipt`
event with the received quantity, the courier, and the actual holder in To).
One tab holds several orders over a part's life, told apart by their ids.
Lines the app appends later (Correction, Update…) stamp their own uuid, so
an id the tab has never raised an order under belongs to the order most
recently written above it.

**Pilot** (set on the sheet 7 Aug 2026, no Event column): two adjacent rows,
the first marked by the "ordered by" note with Holder = who ordered and
Location = where from; the next row is the receipt.

This module turns both back into order records so the Orders pages can show
work that was raised on the sheet, not only work raised in the app. Nothing
here writes; see `tracker_writer` for that.
"""
from __future__ import annotations

import re
from typing import List, Optional

from config import ORDER_STATUSES
from utils import parts_tracker, project_registry, tracker_parse
from utils.tracker_parse import is_selected, to_int

# The marker that makes a row an order-origin row. Matched loosely so
# "Ordered by", "ordered by Hamid" and "ORDERED BY:" all count.
ORIGIN_NOTE = re.compile(r"^\s*order(?:ed)?\s*(?:by|origin)\b", re.I)


def is_origin_row(row: dict) -> bool:
    """Whether this row RAISES an order.

    On the current ledger an `Order` event with nothing received yet is the
    raise; the receive line is also an `Order` event, told apart by its
    received quantity (the M105 reference). The pilot workbook had no Event
    column and marked the origin by a note convention, kept as the fallback —
    a note is weaker evidence than a field, so the field is asked first.
    """
    event = tracker_parse.event_of(row)
    if event:
        return (event.lower() == tracker_parse.EVENT_ORDER.lower()
                and to_int(row.get("qty_received", "")) == 0)
    return bool(ORIGIN_NOTE.match(str(row.get("notes", ""))))


def _is_receipt_row(row: dict) -> bool:
    """Whether a row can close an order.

    Current ledger: a `Receipt` event (early app rows), or an `Order` event
    that carries a received quantity (the M105 convention). Pilot: any row
    that isn't itself an origin."""
    event = tracker_parse.event_of(row)
    if event:
        if event.lower() == tracker_parse.EVENT_RECEIPT.lower():
            return True
        return (event.lower() == tracker_parse.EVENT_ORDER.lower()
                and to_int(row.get("qty_received", "")) > 0)
    return bool(row) and not is_origin_row(row)


def derived_status(history) -> str:
    """The lifecycle status a part's ledger implies — one vocabulary for
    Process Order and the Overview's Latest status column (Hamid, 18 Aug):
    a receive line ⇒ delivered; a Cancelled event ⇒ cancelled (unless goods
    actually arrived — delivery is stronger truth); courier without a
    receipt ⇒ shipped; a raise line ⇒ ordered; any history ⇒ processing.
    A LATER raise resets the slate: reordering after a cancel starts over.
    """
    origin_idx = None
    for i, row in enumerate(history):
        if (tracker_parse.event_of(row).lower()
                == tracker_parse.EVENT_ORDER.lower()
                and to_int(row.get("qty_received", "")) == 0):
            origin_idx = i
    if origin_idx is None:
        return "processing" if history else ""
    after = history[origin_idx:]
    if any(tracker_parse.event_of(r).lower()
           == tracker_parse.EVENT_ORDER.lower()
           and to_int(r.get("qty_received", "")) > 0 for r in after):
        return "delivered"
    if any(tracker_parse.event_of(r).lower() == "cancelled" for r in after):
        return "cancelled"
    if any(str(r.get("courier", "")).strip() for r in after):
        return "shipped"
    return "ordered"


def _name_key(name: str) -> List[str]:
    return [w for w in re.split(r"[^\w]+", str(name).lower()) if w]


def holder_matches(person: str, holder: str, email: str = "") -> bool:
    """Whether an app user and a sheet Holder are the same person.

    Two independent paths, exact-first:

    - **Contact email.** If the Holders directory lists a contact for this
      holder, an exact email match settles it. This is the only thing that
      connects registers a string cannot — the user list says "Joseph", the
      sheets say "Joe H", and no substring joins them ("Joseph" does not even
      start with "Joe": J-O-S). The names differ; the address does not.
    - **Name words.** The sheet writes full names ("Sam Smith") while the
      user list often holds first names ("Sam"), so every word of the
      shorter name must appear in the longer one. Whole words only —
      prefix-matching was tried and reverted, because it fixed no real case
      and would happily merge similar names.
    """
    if email and str(holder).strip():
        from utils import holders_store
        contact = str(holders_store.get(holder).get("contact", "")).strip().lower()
        if contact and contact == str(email).strip().lower():
            return True
    a, b = _name_key(person), _name_key(holder)
    if not a or not b:
        return False
    short, long = (a, b) if len(a) <= len(b) else (b, a)
    return all(word in long for word in short)


def _latest(rows: List[dict], field: str) -> str:
    """The last non-empty value a group of rows states for a field —
    "later manual update wins" (Hamid, 18 Aug)."""
    value = ""
    for row in rows:
        text = str(row.get(field, "")).strip()
        if text:
            value = text
    return value


def _latest_qty(rows: List[dict], field: str) -> int:
    """The last POSITIVE quantity stated. Zeros don't count: the history
    form writes 0 for every quantity the user didn't touch, so a later
    ETA-only line would otherwise wipe the order's quantity."""
    value = 0
    for row in rows:
        n = to_int(row.get(field, ""))
        if n > 0:
            value = n
    return value


def _received_total(rows: List[dict]) -> int:
    """Deliveries accumulate: receive lines (Order/Receipt events with a
    received qty) each carry their own batch, as the app's receive flow
    writes them. A Correction/Update line stating a positive received total
    restates it outright — the last one to do so wins over the sum."""
    order_ev = tracker_parse.EVENT_ORDER.lower()
    receipt_ev = tracker_parse.EVENT_RECEIPT.lower()
    total = 0
    restated = None
    for row in rows:
        n = to_int(row.get("qty_received", ""))
        if n <= 0:
            continue
        if tracker_parse.event_of(row).lower() in (order_ev, receipt_ev):
            total += n
        else:
            restated = n
    return total if restated is None else restated


def _grouped_orders(history: List[dict]) -> List[dict]:
    """Split a ledger into per-order row groups by Order / Sample ID.

    A row whose id the tab already knows joins that group. A new id opens a
    group only when the row raises an order (or nothing is open yet — a
    part's pre-order history); any other unseen id (the app stamps its own
    uuid on Correction/Update lines) belongs to the order most recently
    written above it.
    """
    groups: List[dict] = []
    by_id: dict = {}
    for row in history:
        oid = str(row.get("order_id", "")).strip()
        if oid and oid in by_id:
            by_id[oid]["rows"].append(row)
            continue
        is_raise = (tracker_parse.event_of(row).lower()
                    == tracker_parse.EVENT_ORDER.lower())
        if oid and (is_raise or not groups):
            group = {"id": oid, "rows": [row]}
            groups.append(group)
            by_id[oid] = group
            continue
        if groups:
            groups[-1]["rows"].append(row)
        # an id-less row before any group is pre-order history, not an order
    return groups


def _order_from_group(group: dict) -> Optional[dict]:
    """One order record from all the ledger lines that share its id — or
    None when the group never actually raises an order (movement-only
    threads like a part that was never ordered)."""
    rows = group["rows"]
    order_ev = tracker_parse.EVENT_ORDER.lower()
    receipt_ev = tracker_parse.EVENT_RECEIPT.lower()

    raises = [r for r in rows
              if tracker_parse.event_of(r).lower() == order_ev]
    if not raises:
        return None
    first = raises[0]

    receipts = [r for r in rows
                if tracker_parse.event_of(r).lower() in (order_ev, receipt_ev)
                and to_int(r.get("qty_received", "")) > 0]
    received = _received_total(rows)

    last_receipt = receipts[-1] if receipts else {}
    holders = [h for h in (tracker_parse.holder_of(r) for r in rows) if h]
    if received:
        derived = "delivered"
    elif any(tracker_parse.event_of(r).lower() == "cancelled" for r in rows):
        derived = "cancelled"
    elif any(str(r.get("courier", "")).strip() for r in rows):
        derived = "shipped"
    else:
        derived = "ordered"

    return {
        "order_id": group["id"] or first.get("order_id", ""),
        "date": first.get("date", ""),
        "version": _latest(rows, "version"),
        "type": _latest(rows, "type"),
        "vendor": _latest(rows, "vendor"),
        "qty_ordered": _latest_qty(rows, "qty_ordered"),
        "qty_received": received,
        "status": _latest(rows, "status"),
        "eta": _latest(rows, "eta"),
        # The raise line puts the raiser in From and stamps who logged it.
        # The migrated ledger often has the vendor in From instead, which is
        # why membership checks also look at logged_by (see is_mine).
        "ordered_by": tracker_parse.place_of(first),
        "ordered_from": "",
        "logged_by": first.get("logged_by", ""),
        # Who has it / will get it: the last receipt's holder, else the
        # ledger's latest word on a holder (the selected line usually).
        "recipient": (tracker_parse.holder_of(last_receipt)
                      or (holders[-1] if holders else "")),
        "recipient_location": tracker_parse.place_of(last_receipt),
        "receipt_note": last_receipt.get("notes", ""),
        "selected": any(is_selected(r) for r in rows),
        "has_receipt": bool(receipts),
        "derived": derived,
    }


def _pilot_orders(history: List[dict]) -> List[dict]:
    """The pilot workbook's pairing, kept for eventless tabs: an origin row
    (note convention) and the row right after it — but only if that row IS a
    receipt. Taking the next row blindly let a later location report be read
    as an order's receipt, silently adopting its quantity and holder."""
    out = []
    for index, row in enumerate(history):
        if not is_origin_row(row):
            continue
        nxt = history[index + 1] if index + 1 < len(history) else {}
        receipt = nxt if _is_receipt_row(nxt) else {}
        out.append({
            "order_id": row.get("order_id") or receipt.get("order_id", ""),
            "date": row.get("date") or receipt.get("date", ""),
            "version": row.get("version") or receipt.get("version", ""),
            "type": row.get("type") or receipt.get("type", ""),
            "vendor": row.get("vendor") or receipt.get("vendor", ""),
            "qty_ordered": to_int(row.get("qty_ordered", "")
                                  or receipt.get("qty_ordered", "")),
            "qty_received": to_int(receipt.get("qty_received", "")),
            "status": receipt.get("status") or row.get("status", ""),
            "eta": receipt.get("eta") or row.get("eta", ""),
            # Pilot origin rows put the orderer in Holder and the source in
            # Location, with the recipient on the receipt row.
            "ordered_by": tracker_parse.holder_of(row),
            "ordered_from": tracker_parse.place_of(row),
            "logged_by": row.get("logged_by", ""),
            "recipient": tracker_parse.holder_of(receipt),
            "recipient_location": tracker_parse.place_of(receipt),
            "receipt_note": receipt.get("notes", ""),
            "selected": is_selected(receipt),
            "has_receipt": bool(receipt),
            "derived": ("delivered" if to_int(receipt.get("qty_received", ""))
                        else "ordered"),
        })
    return out


def orders_from_tracker(sheet_id: Optional[str] = None,
                        project: str = "") -> List[dict]:
    """Every order recorded on a project's part tabs, newest tab order kept.

    Tabs whose rows carry an Event are read as id-groups — one record per
    Order / Sample ID, folding in every later correction, movement and
    receive line, so the record reflects the ledger's LATEST word, not the
    raise line as first written. Eventless tabs use the pilot pairing.
    """
    parts = parts_tracker.fetch_all_parts(sheet_id)
    names = {r.get("mcode", ""): r.get("part_name", "")
             for r in parts_tracker.fetch_overview(sheet_id)}

    out = []
    for mcode, part in sorted(parts.items()):
        history = part.get("history", [])
        meta = part.get("meta", {})
        if any(tracker_parse.event_of(r) for r in history):
            found = [o for o in (_order_from_group(g)
                                 for g in _grouped_orders(history)) if o]
        else:
            found = _pilot_orders(history)
        for order in found:
            order["project"] = project
            order["sheet_id"] = sheet_id or ""
            order["mcode"] = mcode
            order["part_name"] = meta.get("part_name") or names.get(mcode, "")
            # The pilot ledger put the process in `Type`; the current one
            # uses that column for nothing, so fall back to the part's own
            # category rather than showing "Order" as if it were a process.
            order["type"] = order["type"] or meta.get("category", "")
            out.append(order)
    return out


def all_projects_orders() -> List[dict]:
    """Tracker orders across every registered project."""
    out = []
    for name, sheet_id in project_registry.all_projects().items():
        out.extend(orders_from_tracker(sheet_id, name))
    return out


def part_orders(sheet_id: Optional[str] = None,
                project: str = "") -> List[dict]:
    """ONE order per part tab — the order the tab was OPENED with.

    Hamid (18 Aug): "my orders" is each part's original order —
    "technically speaking it is what we have in the first history line of
    each part tab". The migration keeps that convention: every tab's first
    line is its "ordered by" marker. Quantities and status are the WHOLE
    tab's latest word (the same rulebook the Overview uses), because every
    later line — historical batches, corrections, receives — continues that
    part's order story.
    """
    parts = parts_tracker.fetch_all_parts(sheet_id)
    overview = {r.get("mcode", ""): r
                for r in parts_tracker.fetch_overview(sheet_id)}
    out = []
    for mcode, part in sorted(parts.items()):
        history = part.get("history", [])
        if not history:
            continue
        first = history[0]
        meta = part.get("meta", {})
        ov = overview.get(mcode, {})
        out.append({
            "project": project,
            "sheet_id": sheet_id or "",
            "mcode": mcode,
            "part_name": meta.get("part_name") or ov.get("part_name", ""),
            "order_id": first.get("order_id", ""),
            "date": first.get("date", ""),
            "version": _latest(history, "version"),
            "qty_ordered": _latest_qty(history, "qty_ordered"),
            "qty_received": _received_total(history),
            "eta": _latest(history, "eta"),
            "ordered_by": (tracker_parse.place_of(first)
                           if tracker_parse.event_of(first)
                           else tracker_parse.holder_of(first)),
            "logged_by": first.get("logged_by", ""),
            "derived": derived_status(history),
            # Who the part concerns, for My Orders: responsibility and
            # custody are different questions, so both travel with the row.
            "owner": ov.get("owner", ""),
            "holder": ov.get("holder", ""),
        })
    return out


def all_part_orders() -> List[dict]:
    """Per-part original orders across every registered project."""
    out = []
    for name, sheet_id in project_registry.all_projects().items():
        out.extend(part_orders(sheet_id, name))
    return out


def orders_for_person(person: str, sheet_id: Optional[str] = None,
                      project: str = "") -> List[dict]:
    """Tracker orders this person raised (they are the first holder)."""
    return [o for o in orders_from_tracker(sheet_id, project)
            if holder_matches(person, o["ordered_by"])]


def mine_reasons(order: dict, name: str, email: str = "") -> List[str]:
    """Why this order is this person's — empty when it is not.

    An order concerns you if you are its **owner** (the part's Default Owner
    from the BOM — the responsibility) or its **holder** (where the goods
    physically sit now — the custody). Those answer different questions, so
    both count (Hamid, 19 Aug: "ordered owner and holder"). Being named on
    the raise line, or being the login that wrote it, also counts — you
    raised it, you follow it.

    Returned as words rather than a bool so the page can SAY why a row is
    shown; an inspectable rule gets corrected, an invisible one gets
    distrusted. Before this, matching was against the raise line only —
    which the migration mostly filled with vendor names, so Joe saw an empty
    page and Jimmy saw 21 orders by coincidence of wording.
    """
    reasons = []
    if holder_matches(name, order.get("owner", ""), email):
        reasons.append("owner")
    if holder_matches(name, order.get("holder", ""), email):
        reasons.append("holder")
    logged = str(order.get("logged_by", "")).strip().lower()
    if (holder_matches(name, order.get("ordered_by", ""), email)
            or (bool(email) and logged == str(email).strip().lower())):
        reasons.append("raised it")
    return reasons


def is_mine(order: dict, name: str, email: str = "") -> bool:
    """Whether this order concerns this person — see mine_reasons."""
    return bool(mine_reasons(order, name, email))


def effective_status(stored: str, derived: str) -> str:
    """The status to SHOW for an app order: the part's history can move it
    FORWARD past the Orders tab's stored value, never backward — same rule
    as the Process Order page (Hamid, 18 Aug).

    "cancelled" sits outside the forward ladder and is terminal: once
    either side says so, only an actual delivery outranks it (goods that
    arrived are the stronger truth).
    """
    stored = (stored or "").strip().lower()
    derived = (derived or "").strip().lower()
    if derived == "delivered":
        return "delivered"
    if "cancelled" in (stored, derived):
        return "cancelled"

    def rank(status: str) -> int:
        return ORDER_STATUSES.index(status) if status in ORDER_STATUSES else 0
    return derived if rank(derived) > rank(stored) else stored


def is_complete(order: dict) -> bool:
    """Received everything that was ordered — the tracker's version of
    "delivered"."""
    return bool(order["qty_ordered"]) and order["qty_received"] >= order["qty_ordered"]
