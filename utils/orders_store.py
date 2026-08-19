"""CRUD operations for the Orders tab in the CENTRAL database sheet.

One Orders tab serves every project ("MECH Outsourcing Material Record");
each row carries a Project column. Project sheets never get an Orders tab.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime

import gspread
from gspread.utils import rowcol_to_a1

from config import CENTRAL_SHEET_ID, TAB_ORDERS, ORDERS_HEADERS
from utils import data_cache, settings
from utils.models import Order, generate_checklist
from utils.google_client import with_worksheet

# TTL is a runtime setting (Status page), not a constant.


def _cache_key() -> str:
    # One central sheet, one cache — shared across every project.
    return f"{CENTRAL_SHEET_ID}:orders"


def _create_orders_ws(ss: gspread.Spreadsheet) -> gspread.Worksheet:
    """Create the Orders worksheet (auto-create when missing)."""
    ws = ss.add_worksheet(title=TAB_ORDERS, rows=1000, cols=len(ORDERS_HEADERS))
    ws.insert_row(ORDERS_HEADERS, index=1)
    return ws


def _on_orders_ws(fn):
    """Run fn(ws) on the central sheet's Orders worksheet handle."""
    return with_worksheet(TAB_ORDERS, fn, create=_create_orders_ws,
                          sheet_id=CENTRAL_SHEET_ID)


def _parse_rows(ws: gspread.Worksheet) -> list[dict]:
    """Parse all rows into list of dicts."""
    all_values = ws.get_all_values()
    if len(all_values) < 2:
        return []
    headers = all_values[0]
    records = []
    for row in all_values[1:]:
        record = {}
        for i, header in enumerate(headers):
            if i < len(row) and header:
                record[header] = row[i]
        records.append(record)
    return records


def _load_orders() -> list[dict]:
    result = _on_orders_ws(_parse_rows)
    return result if result is not None else []


def fetch_all_orders() -> list[dict]:
    """Fetch all orders from the Orders tab (cached, write-through)."""
    return data_cache.get(_cache_key(), settings.orders_ttl(), _load_orders, spinner="Loading orders…")


def fetch_orders_by_engineer(email: str) -> list[dict]:
    """Fetch orders for a specific engineer email."""
    all_orders = fetch_all_orders()
    email_lower = email.lower().strip()
    return [o for o in all_orders if o.get("EngineerEmail", "").lower().strip() == email_lower]


def fetch_orders_for_user(email: str, name: str) -> list[dict]:
    """Get orders where user is engineer OR reviewer."""
    all_orders = fetch_all_orders()
    email_lower = email.lower().strip()
    name_lower = name.lower().strip()
    return [o for o in all_orders
            if o.get("EngineerEmail", "").lower().strip() == email_lower
            or o.get("Reviewer", "").lower().strip() == name_lower]


def fetch_orders_for_project(project: str) -> list[dict]:
    """Orders belonging to one project (Project column match).

    Rows written before the Project column existed have it blank; those are
    shown everywhere rather than lost, so an empty Project matches too.
    """
    want = (project or "").strip().lower()
    if not want:
        return fetch_all_orders()
    return [o for o in fetch_all_orders()
            if o.get("Project", "").strip().lower() in (want, "")]


def fetch_orders_for_part(m_code: str, part_name: str = "",
                          project: str = "") -> list[dict]:
    """Orders raised in this app against a tracked part.

    Matched on M-code — the shared identity between the BOM, the tracker and
    this tool. With a central Orders tab the same M-code can exist in two
    projects, so pass `project` to scope the join. Orders with no M-code fall
    back to an exact part-name match so older records still join up.
    """
    code = (m_code or "").strip().lower()
    name = (part_name or "").strip().lower()
    if not code and not name:
        return []
    pool = fetch_orders_for_project(project) if project else fetch_all_orders()
    hits = []
    for o in pool:
        order_code = o.get("PartID", "").strip().lower()
        if code and order_code == code:
            hits.append(o)
        elif not order_code and name and o.get("PartName", "").strip().lower() == name:
            hits.append(o)
    return hits


def fetch_order_by_id(order_id: str) -> dict | None:
    """Find a single order by OrderID."""
    for o in fetch_all_orders():
        if o.get("OrderID") == order_id:
            return o
    return None


def _clear_cache():
    """Drop the orders cache so the next read refetches."""
    data_cache.invalidate(_cache_key())


def refresh() -> None:
    """Public face of _clear_cache, for pages with a refresh control."""
    _clear_cache()


def _order_row(order_data: dict) -> tuple:
    """(order_id, the 26 values) for one order.

    Shared by the single and bulk paths on purpose: two copies of a positional
    26-column layout would drift, and the drift would be invisible until an
    order landed in the wrong columns.
    """
    order_id = str(uuid.uuid4())[:8]
    now = datetime.now().strftime("%Y-%m-%d %H:%M")

    # Build Order object for checklist generation
    order = Order(
        part_name=order_data.get("part_name", ""),
        m_code=order_data.get("m_code", ""),
        version=order_data.get("version", ""),
        process=order_data.get("process", "CNC Machining"),
        material=order_data.get("material", ""),
        finish=order_data.get("finish", ""),
        quantity=order_data.get("quantity", 1),
        priority=order_data.get("priority", "Normal"),
        inspection=order_data.get("inspection", "No"),
        recipient=order_data.get("recipient", ""),
        engineer=order_data.get("engineer_name", ""),
    )
    checklist = generate_checklist(order)
    checklist_json = json.dumps(checklist, ensure_ascii=False)

    row = [
        order_id,                                       # OrderID
        now,                                            # CreatedAt
        order_data.get("engineer_email", ""),            # EngineerEmail
        order_data.get("engineer_name", ""),             # EngineerName
        "new",                                          # Status
        order_data.get("project", ""),                   # Project
        order.part_name,                                # PartName
        order.m_code,                                   # PartID
        order.version,                                  # Version
        order.process,                                  # Process
        order.material,                                 # Material
        order.finish,                                   # Finish
        str(order.quantity),                            # Quantity
        order.priority,                                 # Priority
        order.recipient,                                # Recipient
        "",                                             # Vendor
        "",                                             # VendorOrderNum
        "",                                             # TrackingNum
        order_data.get("eta", ""),                       # ETA
        order_data.get("notes", ""),                    # Notes
        order_data.get("drive_file_link", ""),           # DriveFileLink
        checklist_json,                                 # ChecklistJSON
        order.inspection,                               # Inspection
        "",                                             # PartsCostCNY
        "",                                             # ShippingCostCNY
        order_data.get("reviewer", ""),                  # Reviewer
    ]
    return order_id, row


def create_order(client: gspread.Client, order_data: dict) -> str:
    """Create a new order in the Orders tab. Returns the generated OrderID."""
    from utils.auth import impersonation_block
    blocked = impersonation_block()
    if blocked:
        # A clean stop, not an exception: the cloud redacts tracebacks into
        # noise, and "why can't I save" deserves a sentence, not a stack.
        import streamlit as st
        st.error(blocked)
        st.stop()
    order_id, row = _order_row(order_data)
    _on_orders_ws(lambda ws: ws.insert_row(row, index=2, value_input_option="USER_ENTERED"))
    # write-through: new order goes to the top (insert_row index=2)
    data_cache.insert_row(_cache_key(), dict(zip(ORDERS_HEADERS, row)), top=True)
    return order_id


def create_orders(order_data_list) -> list:
    """Create many orders in ONE write. Returns their OrderIDs, in order.

    A loop of `create_order` would be two API calls each — 88 for a 44-part
    BOM, against a 60-per-minute quota, with a half-written batch if it trips.
    """
    if not order_data_list:
        return []
    built = [_order_row(data) for data in order_data_list]
    rows = [row for _, row in built]
    _on_orders_ws(lambda ws: ws.insert_rows(rows, row=2,
                                            value_input_option="USER_ENTERED"))
    # The cache is newest-first and each insert goes to the top, so replay the
    # batch backwards to leave it in the same order as the sheet.
    for row in reversed(rows):
        data_cache.insert_row(_cache_key(), dict(zip(ORDERS_HEADERS, row)),
                              top=True)
    return [order_id for order_id, _ in built]


def update_order(client: gspread.Client, order_id: str, updates: dict):
    """Update specific fields of an order by OrderID.

    Args:
        client: authenticated gspread client
        order_id: the OrderID to update
        updates: dict of {column_header: new_value}
    """
    from utils.auth import impersonation_block
    blocked = impersonation_block()
    if blocked:
        # A clean stop, not an exception: the cloud redacts tracebacks into
        # noise, and "why can't I save" deserves a sentence, not a stack.
        import streamlit as st
        st.error(blocked)
        st.stop()
    if not updates:
        return

    def _do(ws: gspread.Worksheet):
        # 1 round trip: column A (to find the row) + row 1 (live headers).
        # Always a fresh col-A read — create_order inserts at row 2 and shifts
        # every row, so row indices must never be cached.
        col_a, header_rows = ws.batch_get(["A:A", "1:1"])
        if not header_rows:
            return
        headers = header_rows[0]

        # col_a includes the header at index 0, so sheet row = i + 1
        row_idx = None
        for i, cell in enumerate(col_a):
            if (cell[0] if cell else "") == order_id:
                row_idx = i + 1
                break
        if row_idx is None:
            return

        # Build all cell writes, then send in ONE batch_update call
        data = []
        for col_name, value in updates.items():
            if col_name in headers:
                col_idx = headers.index(col_name) + 1  # 1-indexed
                data.append({
                    "range": rowcol_to_a1(row_idx, col_idx),
                    "values": [[value]],
                })
        if data:
            ws.batch_update(data, value_input_option="USER_ENTERED")  # 1 round trip
            # write-through: patch only the columns we actually wrote (these
            # keys are real Orders headers, so set them verbatim).
            written = {k: v for k, v in updates.items() if k in headers}
            data_cache.patch_rows(
                _cache_key(),
                lambda r: r.get("OrderID") == order_id,
                written,
            )

    _on_orders_ws(_do)


def update_checklist(client: gspread.Client, order_id: str, checklist: list[dict]):
    """Update the checklist JSON for an order."""
    from utils.auth import impersonation_block
    blocked = impersonation_block()
    if blocked:
        # A clean stop, not an exception: the cloud redacts tracebacks into
        # noise, and "why can't I save" deserves a sentence, not a stack.
        import streamlit as st
        st.error(blocked)
        st.stop()
    checklist_json = json.dumps(checklist, ensure_ascii=False)
    update_order(client, order_id, {"ChecklistJSON": checklist_json})
