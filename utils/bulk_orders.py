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

    # Stamp each filed tab's E1:F1 with its central order id — the tab-level
    # pointer every pre-19-Aug tab carries. The old order-form flow wrote it;
    # its removal left F1 forever empty on anything new. Writing the label
    # too self-heals a tab built without the pair. One batched call; a
    # failure here is reported, not fatal — the ledger line already carries
    # the same id per row.
    if out["filed"]:
        by_code = {r.get("m_code", ""): oid
                   for r, oid in zip(rows, out["orders"])}
        data = [{"range": "'%s'!E1:F1" % code.replace("'", "''"),
                 "values": [["order ID", by_code[code]]]}
                for code in out["filed"] if by_code.get(code)]
        try:
            from utils.google_client import get_spreadsheet
            ss = get_spreadsheet(record_id)
            if ss is not None and data:
                ss.values_batch_update(
                    {"valueInputOption": "USER_ENTERED", "data": data})
        except Exception as exc:
            out["errors"].append(
                "Orders filed, but the tabs' order-ID cells (E1:F1) were not "
                "stamped: %s" % exc)

    if out["filed"]:
        parts_tracker.refresh(record_id)
    return out


def parse_quick_lines(text, known_codes, open_codes, default_recipient=""):
    """Order lines as text — the entry a person pastes and an agent types.

    The grids are precise but slow to drive: forty checkboxes and cell edits
    for what someone already has as a list (Hamid, 19 Aug: "agentic entry for
    claude co-work, current one is difficult"). One line per order, fields
    comma/pipe/tab-separated, only the part is required:

        M105
        M105 x120
        M105, 120, Ryan Wong, 25 Aug 2026, urgent, note text...
        # comments and blank lines are ignored

    Returns (rows, errors): rows as {code, qty, recipient, eta, priority,
    notes} with qty=None meaning "keep the grid's default", and errors as
    human sentences. A part with an OPEN order is an error here for the same
    reason it is unselectable in the grids — a second raise for something
    already on its way is a double count, not an order.
    """
    import re
    from datetime import datetime

    known = {str(c).strip().lower(): str(c).strip() for c in known_codes}
    open_low = {str(c).strip().lower() for c in open_codes}
    rows, errors = [], []

    def _eta(text_value):
        value = str(text_value or "").strip()
        if not value:
            return None
        for fmt in ("%d %b %Y", "%Y-%m-%d", "%d/%m/%Y", "%d %B %Y"):
            try:
                return datetime.strptime(value, fmt).date()
            except ValueError:
                continue
        return "unreadable"

    for n, raw in enumerate(str(text or "").splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        # "M105 x120" / "M105 120" — the two-word shorthand.
        parts = [p.strip() for p in re.split(r"[,|\t]", line)]
        if len(parts) == 1:
            m = re.match(r"^(\S+)\s+x?(\d+)$", line, re.I)
            if m:
                parts = [m.group(1), m.group(2)]
        code = known.get(parts[0].lower())
        if code is None:
            errors.append("line %d: `%s` is not a part in this BOM."
                          % (n, parts[0]))
            continue
        if parts[0].lower() in open_low:
            errors.append("line %d: %s already has an order on its way — "
                          "not selectable until it is delivered or cancelled."
                          % (n, code))
            continue
        qty = None
        if len(parts) > 1 and parts[1]:
            digits = re.sub(r"^x", "", parts[1], flags=re.I)
            if not digits.isdigit() or int(digits) < 1:
                errors.append("line %d: quantity `%s` is not a whole number "
                              "of at least 1." % (n, parts[1]))
                continue
            qty = int(digits)
        eta = _eta(parts[3] if len(parts) > 3 else "")
        if eta == "unreadable":
            errors.append("line %d: could not read the date `%s` — use "
                          "e.g. 25 Aug 2026 or 2026-08-25." % (n, parts[3]))
            continue
        priority = (parts[4].strip().upper() if len(parts) > 4 and
                    parts[4].strip() else "")
        if priority and priority not in ("URGENT", "NORMAL"):
            errors.append("line %d: priority `%s` — use URGENT or Normal."
                          % (n, parts[4]))
            continue
        rows.append({
            "code": code,
            "qty": qty,
            "recipient": (parts[2].strip() if len(parts) > 2 and
                          parts[2].strip() else default_recipient),
            "eta": eta,
            "priority": ("URGENT" if priority == "URGENT" else
                         "Normal" if priority else None),
            "notes": ", ".join(parts[5:]).strip() if len(parts) > 5 else "",
        })
    seen = set()
    for r in rows:
        if r["code"] in seen:
            errors.append("%s appears more than once — one line per part."
                          % r["code"])
        seen.add(r["code"])
    return rows, errors
