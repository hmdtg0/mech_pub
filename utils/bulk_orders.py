"""Submit a reviewed batch of BOM-derived orders.

One place that turns the draft grid into the two writes an order has always
been: a row in the main record's Orders tab, and an Order/Receipt pair on the
part's own tab. Both are batched, so a 44-part BOM costs a handful of API calls
rather than saturating the quota — and a batch that trips does so before
anything is written, not halfway through.

The order of the two writes matters. The Orders row is written first because it
mints the OrderID that ties the two together; if the ledger write then fails,
the orders still exist and are re-filable, whereas the reverse would leave
history rows pointing at orders nobody raised.
"""
from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from utils import orders_store, parts_tracker, tracker_writer


def _notes(tolerances: str, notes: str) -> str:
    """The Notes cell, composed exactly as Submit Order composes it — the
    reorder path splits it back on the same separator."""
    parts = []
    if tolerances.strip():
        parts.append("Tolerances: %s" % tolerances.strip())
    head = " | ".join(parts)
    if notes.strip():
        return "%s\n---\n%s" % (head, notes.strip()) if head else notes.strip()
    return head


def submit(project: str, record_id: str, user: dict, rows: List[dict],
           ordered_from: str = "", reviewer: str = "",
           inspection: str = "No", build: str = "") -> dict:
    """Create every order in `rows`. Each row may carry its own `eta`
    (already formatted for the sheet, e.g. "17 Aug 2026"); `build` is the
    batch tag (e.g. "T2") stamped on every ledger line. Returns
    {"orders": [order_id], "filed": [mcode], "errors": [message]}."""
    out = {"orders": [], "filed": [], "errors": []}
    if not rows:
        return out

    now = datetime.now()
    order_data = [{
        "part_name": r.get("part_name", ""),
        "m_code": r.get("m_code", ""),
        "version": r.get("version", ""),
        # The BOM's Type stands in for the manufacturing process: it is the
        # vocabulary the BOM actually uses, and `Spec` carries the detail.
        "process": r.get("process", ""),
        "material": r.get("material", ""),
        "finish": "",
        "quantity": int(r.get("quantity", 0) or 0),
        "priority": r.get("priority", "Normal"),
        "inspection": inspection,
        # Per row: parts in one batch can go to different people and places.
        "recipient": r.get("recipient", ""),
        "notes": _notes(r.get("tolerances", ""), r.get("notes", "")),
        "engineer_email": user.get("email", ""),
        "engineer_name": user.get("name", ""),
        "drive_file_link": r.get("drive_file_link", ""),
        "reviewer": reviewer,
        "project": project,
        "eta": r.get("eta", ""),
    } for r in rows]

    try:
        out["orders"] = orders_store.create_orders(order_data)
    except Exception as exc:
        out["errors"].append("No orders were created: %s" % exc)
        return out

    stamp = now.strftime("%d %b %Y")
    ledger = [{
        "mcode": r.get("m_code", ""),
        # From = who raised it (the person logging); To = the recipient.
        # Logged By carries the email, as the reference record does.
        "ordered_by": user.get("name", ""),
        "recipient": r.get("recipient", ""),
        "order_id": order_id,
        "version": r.get("version", ""),
        "build": build,
        "date": stamp,
        "order_type": r.get("process", ""),
        "vendor": "",
        "qty_ordered": r.get("quantity", ""),
        "eta": r.get("eta", ""),
        "logged_by": user.get("email", "") or user.get("name", ""),
        "logged_at": now.strftime("%d %b %Y %H:%M"),
    } for r, order_id in zip(rows, out["orders"])]

    filed = tracker_writer.write_orders(ledger, sheet_id=record_id)
    out["filed"] = filed["filed"]
    out["errors"].extend(filed["errors"])

    if out["filed"]:
        parts_tracker.refresh(record_id)
    return out
