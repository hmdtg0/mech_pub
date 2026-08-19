"""All Orders - Admin dashboard showing all mech orders."""
import json
from datetime import datetime, date

import streamlit as st

from utils.auth import require_role
from utils.google_client import get_gspread_client
from utils import project_colors, tracker_order_ui, tracker_orders, ui
from utils.orders_store import (
    fetch_all_orders, fetch_order_by_id, update_order,
)
from utils.message_store import fetch_messages_for_order, send_message
from config import ORDER_STATUSES, STATUS_COLORS, CNY_TO_GBP


STATUS_ACTIONS = {
    "new": ("🔧 Start Processing", "processing"),
    "processing": ("📦 Mark as Ordered", "ordered"),
    "ordered": ("🚚 Mark as Shipped", "shipped"),
    "shipped": ("✅ Mark as Delivered", "delivered"),
}


def _to_f(v):
    try:
        return float(str(v).strip()) if str(v).strip() else 0.0
    except (ValueError, TypeError):
        return 0.0


@st.fragment
def order_card(order_id: str, user_name: str):
    """Render one order's expander as an isolated fragment.

    The order is re-read from the (write-through) cache by id on every fragment
    rerun, so an in-card Save or message Send reruns ONLY this card instead of
    rebuilding all 40 expanders. Status changes still do a full st.rerun() so
    the summary metrics and filter buckets stay consistent.
    """
    order = fetch_order_by_id(order_id)
    if order is None or order.get("Status") in ("delivered", "cancelled"):
        # Card no longer belongs on this page -> full rerun to rebuild the list.
        st.rerun()
        return

    client = get_gspread_client()

    part_name = order.get("PartName", "Unknown")
    status = order.get("Status", "new")
    priority = order.get("Priority", "Normal")
    engineer = order.get("EngineerName", "")
    process = order.get("Process", "")
    quantity = order.get("Quantity", "")
    created = order.get("CreatedAt", "")

    # Parse checklist
    try:
        checklist = json.loads(order.get("ChecklistJSON", "[]"))
    except (json.JSONDecodeError, TypeError):
        checklist = []

    done_count = sum(1 for c in checklist if c.get("done"))
    total_count = len(checklist) or 1
    pct = done_count / total_count

    priority_icon = "🔴" if priority == "URGENT" else "🟢"
    status_color = STATUS_COLORS.get(status, "gray")

    _proj = (order.get("Project") or "").strip()
    header = (
        f"{priority_icon} "
        + (f"{project_colors.tag(_proj)} " if _proj else "")
        + f"**{part_name}** | 🔩 {process} | Qty: {quantity} | "
        f"👤 {engineer} | :{status_color}[{status.upper()}] | {pct:.0%} done"
    )

    with st.expander(header, expanded=False):
        # Info row (display only)
        info1, info2 = st.columns(2)
        with info1:
            st.markdown(f"**ID:** `{order_id}`")
            st.markdown(f"**Engineer:** {engineer}")
            reviewer_name = order.get("Reviewer", "")
            if reviewer_name:
                st.markdown(f"**Reviewer:** {reviewer_name}")
            st.markdown(f"**Recipient:** {order.get('Recipient', 'N/A')}")
            st.markdown(f"**Submitted:** {created}")
        with info2:
            if order.get("PartID") or order.get("Version"):
                st.markdown(f"**Part ID:** {order.get('PartID', '?')} V{order.get('Version', '?')}")
            st.markdown(f"**Process:** {process or 'N/A'}")
            st.markdown(f"**Material:** {order.get('Material', 'N/A')}")
            st.markdown(f"**Finish:** {order.get('Finish', 'N/A')}")
            st.markdown(f"**Inspection:** {order.get('Inspection', 'No')}")

        # File link
        drive_link = order.get("DriveFileLink", "")
        if drive_link:
            st.markdown(f"📁 [View files on Drive]({drive_link})")

        # Cost display
        parts_c = _to_f(order.get("PartsCostCNY", ""))
        ship_c = _to_f(order.get("ShippingCostCNY", ""))
        total_cny = parts_c + ship_c
        if total_cny > 0:
            total_gbp = total_cny * CNY_TO_GBP
            st.markdown(f"💰 **Cost:** Parts ¥{parts_c:.0f} + Shipping ¥{ship_c:.0f} = **¥{total_cny:,.2f} CNY (£{total_gbp:,.2f} GBP)**")

        st.progress(pct)

        # --- Edit form (no rerun until Save) ---
        current_eta = order.get("ETA", "")
        try:
            eta_default = datetime.strptime(current_eta, "%Y-%m-%d").date() if current_eta else None
        except ValueError:
            eta_default = None

        with st.form(f"editform_{order_id}"):
            ef1, ef2 = st.columns(2)
            with ef1:
                new_vendor = st.text_input("Vendor", value=order.get("Vendor", ""), key=f"vendor_{order_id}")
                new_vendor_num = st.text_input("Vendor Order #", value=order.get("VendorOrderNum", ""), key=f"vendornum_{order_id}")
                new_tracking = st.text_input("Tracking #", value=order.get("TrackingNum", ""), key=f"tracking_{order_id}")
                new_eta_date = st.date_input("ETA", value=eta_default, key=f"eta_{order_id}")
            with ef2:
                st.markdown("**Checklist:**")
                new_done = []
                for j, item in enumerate(checklist):
                    new_done.append(st.checkbox(
                        item.get("text", ""),
                        value=item.get("done", False),
                        key=f"chk_{order_id}_{item.get('id', j)}",
                    ))
            new_notes = st.text_area("Notes", value=order.get("Notes", ""), key=f"notes_{order_id}", height=160)
            saved = st.form_submit_button("💾 Save Changes", type="primary")

        if saved and client:
            new_eta = new_eta_date.strftime("%Y-%m-%d") if new_eta_date else ""
            payload = {}
            if new_vendor != order.get("Vendor", ""):
                payload["Vendor"] = new_vendor
            if new_vendor_num != order.get("VendorOrderNum", ""):
                payload["VendorOrderNum"] = new_vendor_num
            if new_tracking != order.get("TrackingNum", ""):
                payload["TrackingNum"] = new_tracking
            if new_eta != current_eta:
                payload["ETA"] = new_eta
            if new_notes != order.get("Notes", ""):
                payload["Notes"] = new_notes
            cl_changed = False
            for item, v in zip(checklist, new_done):
                if v != item.get("done", False):
                    item["done"] = v
                    cl_changed = True
            if cl_changed:
                payload["ChecklistJSON"] = json.dumps(checklist, ensure_ascii=False)
            if payload:
                update_order(client, order_id, payload)
                st.success("Saved!")
            # Save doesn't change status/priority/engineer -> card stays put.
            st.rerun(scope="fragment")

        # --- Messages ---
        st.markdown("**💬 Messages:**")
        messages = fetch_messages_for_order(order_id)
        if messages:
            for m in messages:
                author = m.get("Author", "")
                ts = m.get("Timestamp", "")
                content = m.get("Content", "")
                prefix = "🟢" if author == user_name else "🔵"
                st.markdown(f"{prefix} **{author}** ({ts}): {content}")
        else:
            st.caption("No messages.")

        with st.form(f"msgform_{order_id}", clear_on_submit=True):
            new_msg = st.text_input("Message", key=f"msg_{order_id}",
                                    placeholder="Ask engineer or leave note...",
                                    label_visibility="collapsed")
            sent = st.form_submit_button("Send")
        if sent and new_msg.strip() and client:
            send_message(client, order_id, user_name, new_msg.strip())
            st.rerun(scope="fragment")

        # --- Status action buttons (single-click, outside forms) ---
        st.markdown("**Actions:**")
        btn_col2, btn_col3 = st.columns(2)

        if status in STATUS_ACTIONS:
            label, next_status = STATUS_ACTIONS[status]
            with btn_col2:
                if st.button(label, key=f"advance_{order_id}"):
                    if client:
                        update_order(client, order_id, {"Status": next_status})
                        # Status changed -> full rerun (card moves bucket, metrics update)
                        st.rerun()

        if status != "new":
            status_idx = ORDER_STATUSES.index(status) if status in ORDER_STATUSES else 0
            prev_status = ORDER_STATUSES[status_idx - 1]
            with btn_col3:
                if st.button(f"↩ Revert to {prev_status}", key=f"revert_{order_id}"):
                    if client:
                        update_order(client, order_id, {"Status": prev_status})
                        st.rerun()


user = require_role("admin", "engineer", "logistics")

st.title("📊 All Orders")
st.caption("Delivered/cancelled orders are moved to **Order History** — see sidebar.")

# --- Open orders recorded on the project trackers ---------------------------
# On the sheet an order is a pair of rows (origin + receipt); it counts as open
# until the receipt row has taken delivery of everything ordered.
_tracker_all = ui.project_filter(tracker_orders.all_projects_orders(),
                                 key="ao_project")
_tracker_open = [o for o in _tracker_all if not tracker_orders.is_complete(o)]

st.markdown("### From the project trackers — open (%d of %d)"
            % (len(_tracker_open), len(_tracker_all)))
st.caption("Raised on the sheet. Completed ones are on **Order History**.")
tracker_order_ui.render_section(
    _tracker_open, "Every tracker order has received what was ordered.",
    key="allorders")

st.markdown("---")
st.markdown("### Raised in this app")

all_orders = fetch_all_orders()
# Exclude delivered/cancelled orders (they're in Order History page)
orders = [o for o in all_orders if o.get("Status") not in ("delivered", "cancelled")]
if not orders:
    st.info("No active orders raised in this app. Check Order History for "
            "delivered/cancelled ones.")
    st.stop()

# --- Summary metrics ---
col_m1, col_m2, col_m3, col_m4, col_m5 = st.columns(5)
for col, status in zip([col_m1, col_m2, col_m3, col_m4, col_m5], ORDER_STATUSES):
    count = sum(1 for o in orders if o.get("Status") == status)
    col.metric(status.upper(), count)

st.markdown("---")

# --- Filters ---
# Orders are central (one tab, every project) — Project is a filter here,
# not a sheet switch.
col_f1, col_f2, col_f3, col_f4 = st.columns(4)
with col_f1:
    status_filter = st.selectbox("Status", ["all"] + ORDER_STATUSES, index=0)
with col_f2:
    priority_filter = st.selectbox("Priority", ["all", "URGENT", "Normal"])
with col_f3:
    engineers = sorted(set(o.get("EngineerName", "") for o in orders if o.get("EngineerName")))
    engineer_filter = st.selectbox("Engineer", ["all"] + engineers)
with col_f4:
    order_projects = sorted(set(o.get("Project", "").strip() for o in orders
                                if o.get("Project", "").strip()))
    project_filter = st.selectbox("Project", ["all"] + order_projects)

# Filter
filtered = orders
if status_filter != "all":
    filtered = [o for o in filtered if o.get("Status") == status_filter]
if priority_filter != "all":
    filtered = [o for o in filtered if o.get("Priority") == priority_filter]
if engineer_filter != "all":
    filtered = [o for o in filtered if o.get("EngineerName") == engineer_filter]
if project_filter != "all":
    filtered = [o for o in filtered if o.get("Project", "").strip() == project_filter]

# Sort: URGENT first, then newest first within same priority
urgent = [o for o in filtered if o.get("Priority") == "URGENT"]
normal = [o for o in filtered if o.get("Priority") != "URGENT"]
urgent.sort(key=lambda o: o.get("CreatedAt", ""), reverse=True)
normal.sort(key=lambda o: o.get("CreatedAt", ""), reverse=True)
filtered = urgent + normal

st.markdown(f"**{len(filtered)} orders** shown")
st.markdown("---")

# --- Order cards (each an isolated fragment) ---
for order in filtered:
    order_card(order.get("OrderID", "?"), user["name"])
