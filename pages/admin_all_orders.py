"""All Orders - Admin dashboard showing all mech orders."""
import json
from datetime import datetime, date

import pandas as pd
import streamlit as st

from utils.auth import require_role
from utils.google_client import get_gspread_client
from utils import (overview_board, project_colors, tracker_order_ui,
                   tracker_orders, ui)
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


def ledger_block(thread: dict, key: str) -> None:
    """The order's other half: what the project record says about it.

    Drawn inside the app card when the order id joins to a ledger thread, so
    ONE card tells the whole story — workflow from the Orders tab, custody
    and quantities from the part's own tab."""
    st.markdown("---")
    st.markdown("**On the project record** — %s · %s ordered · **%s received**"
                % (thread.get("order_id") or "no order id",
                   thread.get("qty_ordered") or "?",
                   thread.get("qty_received", 0)))
    lc1, lc2 = st.columns(2)
    with lc1:
        st.markdown("**Ordered by** — %s" % (thread.get("ordered_by") or "—"))
        st.markdown("**Vendor / Source** — %s" % (thread.get("vendor") or "—"))
    with lc2:
        st.markdown("**Received by** — %s" % (thread.get("recipient") or "not yet"))
        st.markdown("**Ledger status** — %s"
                    % (thread.get("derived") or thread.get("status") or "not recorded"))
    if thread.get("receipt_note"):
        st.markdown("**Receipt note** — %s" % thread["receipt_note"])
    if thread.get("selected"):
        st.success("This is the version selected for MP.")
    if st.button("🔍 Open part history", key="ledger_%s" % key,
                 use_container_width=True):
        project_registry.set_active(thread["project"])
        st.session_state["tracker_part"] = thread["mcode"]
        st.switch_page("pages/tracker_part_detail.py")


@st.fragment
def order_card(order_id: str, user_name: str, progress: str = "",
               thread: dict = None):
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
    # The fragment refetches the order, so the reconciliation happens here
    # too: the ledger moves a status forward past the stored value, exactly
    # as the page-level list decided which band this card belongs to.
    status = tracker_orders.effective_status(
        order.get("Status", "new"), thread.get("derived", "") if thread else "")
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
    # The header follows the tracker cards' shape (Hamid, 24 Aug: "the labels
    # need these info" — part identity, version, received progress, recipient),
    # keeping the app half: the status chip and the engineer. Process, qty and
    # the checklist live inside the card, where they always were.
    header = " | ".join(
        [f"{priority_icon} "
         + (f"{project_colors.tag(_proj)} " if _proj else "")
         + "**%s**" % (" — ".join(
               b for b in (order.get("PartID", "").strip(), part_name) if b))]
        + ["`%s`" % (order.get("Version", "").strip() or "no version")]
        + [progress or f"Qty: {quantity}"]
        + ([f"→ {order.get('Recipient', '').strip()}"]
           if order.get("Recipient", "").strip() else [])
        + [f":{status_color}[{status.upper()}]",
           f"👤 {engineer}"]
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

        if thread:
            ledger_block(thread, "open_%s" % order_id)

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



def history_card(order: dict, progress: str = "",
                 thread: dict = None) -> None:
    """One delivered/cancelled order, read-only. No fragment: nothing in it
    writes, so there is no partial rerun to isolate."""
    status = order.get("Status", "")
    priority = order.get("Priority", "Normal")
    priority_icon = "🔴" if priority == "URGENT" else "🟢"
    status_color = STATUS_COLORS.get(status, "gray")
    created = order.get("CreatedAt", "")
    engineer = order.get("EngineerName", "")
    process = order.get("Process", "")

    _proj = (order.get("Project") or "").strip()
    header = " | ".join(
        [f"{priority_icon} "
         + (f"{project_colors.tag(_proj)} " if _proj else "")
         + "**%s**" % (" — ".join(
               b for b in (order.get("PartID", "").strip(),
                           order.get("PartName", "Unknown")) if b))]
        + ["`%s`" % (order.get("Version", "").strip() or "no version")]
        + [progress or f"Qty: {order.get('Quantity', '')}"]
        + ([f"→ {order.get('Recipient', '').strip()}"]
           if order.get("Recipient", "").strip() else [])
        + [f":{status_color}[{status.upper()}]",
           f"👤 {engineer}", created]
    )
    with st.expander(header, expanded=False):
        c1, c2, c3 = st.columns(3)
        with c1:
            st.markdown(f"**ID:** `{order.get('OrderID', '?')}`")
            st.markdown(f"**Engineer:** {engineer}")
            st.markdown(f"**Recipient:** {order.get('Recipient', 'N/A')}")
            st.markdown(f"**Submitted:** {created}")
        with c2:
            if order.get("PartID") or order.get("Version"):
                st.markdown(f"**Part ID:** {order.get('PartID', '?')} "
                            f"V{order.get('Version', '?')}")
            st.markdown(f"**Process:** {process or 'N/A'}")
            st.markdown(f"**Material:** {order.get('Material', 'N/A')}")
            st.markdown(f"**Finish:** {order.get('Finish', 'N/A')}")
            st.markdown(f"**Inspection:** {order.get('Inspection', 'No')}")
        with c3:
            st.markdown(f"**Vendor:** {order.get('Vendor', 'N/A')}")
            st.markdown(f"**Vendor Order #:** {order.get('VendorOrderNum', 'N/A')}")
            st.markdown(f"**Tracking #:** {order.get('TrackingNum', 'N/A')}")
            st.markdown(f"**ETA:** {order.get('ETA', 'N/A')}")

        parts_c = _to_f(order.get("PartsCostCNY"))
        ship_c = _to_f(order.get("ShippingCostCNY"))
        order_total_cny = parts_c + ship_c
        if order_total_cny > 0:
            order_total_gbp = order_total_cny * CNY_TO_GBP
            st.markdown(f"💰 **Cost:** Parts ¥{parts_c:.0f} + "
                        f"Shipping ¥{ship_c:.0f} = "
                        f"**¥{order_total_cny:,.2f} CNY "
                        f"(£{order_total_gbp:,.2f} GBP)**")

        drive_link = order.get("DriveFileLink", "")
        if drive_link:
            st.markdown(f"📁 [View files on Drive]({drive_link})")
        notes = order.get("Notes", "")
        if notes:
            st.markdown(f"**Notes:** {notes}")
        if thread:
            ledger_block(thread, "done_%s" % order.get("OrderID", "?"))

user = require_role("admin", "engineer", "logistics")

st.title("📊 All Orders")

# ONE list, one card per order (Hamid, 24 Aug: "why do we even have 2?").
# Every app-raised order since 19 Aug carries its central id on the ledger
# raise line, so the two record systems join: workflow (status, vendor,
# tracking, costs, messages) from the Orders tab, custody and quantities from
# the part's own tab — one card, both halves. Orders that exist in only one
# system still get their card: pre-app history has no Orders row, and the
# 18-Aug batch predates id-stamping so it has no ledger thread.

_tracker_all = ui.in_scope(tracker_orders.all_projects_orders())
all_orders = ui.in_scope(fetch_all_orders())

_thread_of = {}
for _t in _tracker_all:
    _oid = str(_t.get("order_id", "")).strip()
    if _oid:
        _thread_of[_oid] = _t
_app_ids = {str(o.get("OrderID", "")).strip() for o in all_orders}
_ledger_only = [t for t in _tracker_all
                if str(t.get("order_id", "")).strip() not in _app_ids]

_closed = ("delivered", "cancelled")


def _progress_text(thread):
    return "%s/%s received" % (thread.get("qty_received", 0),
                               thread.get("qty_ordered", 0) or "?")


# --- one entry per order, whichever system recorded it ----------------------
entries = []
for o in all_orders:
    _oid = str(o.get("OrderID", "")).strip()
    _t = _thread_of.get(_oid)
    # The status shown is the RECONCILED one — the ledger can move an order
    # forward past the Orders tab's stored value, never backward (the same
    # effective_status rule Process Order applies). Without this, an order
    # received on the sheet still read NEW here: the Orders tab lags until
    # someone advances it by hand, and M108 sat delivered-but-invisible
    # (Hamid, 24 Aug: "i see one delivered but not showing up").
    _stored = (o.get("Status") or "new").strip() or "new"
    _eff = tracker_orders.effective_status(
        _stored, _t.get("derived", "") if _t else "")
    entries.append({
        "kind": "app",
        "order": dict(o, Status=_eff) if _eff != _stored else o,
        "thread": _t,
        "status": _eff,
        "priority": o.get("Priority", "Normal"),
        "who": o.get("EngineerName", ""),
        "text": " ".join([o.get("PartName", ""), o.get("PartID", ""), _oid]),
        "when": o.get("CreatedAt", ""),
    })
for _t in _ledger_only:
    entries.append({
        "kind": "ledger", "order": None, "thread": _t,
        # The sheet's own story, in the app's status vocabulary.
        "status": _t.get("derived") or "ordered",
        "priority": "Normal",
        "who": _t.get("ordered_by", ""),
        "text": " ".join([_t.get("mcode", ""), _t.get("part_name", ""),
                          str(_t.get("order_id", ""))]),
        "when": "",
    })

if not entries:
    st.info("No orders anywhere yet — raise the first on **Order from BOM**.")
    st.stop()

# --- Summary metrics: the whole ladder, both record systems -----------------
_statuses = list(ORDER_STATUSES) + ["cancelled"]
for col, status in zip(st.columns(len(_statuses)), _statuses):
    col.metric(status.upper(),
               sum(1 for e in entries if e["status"] == status))

_total_cost_cny = sum(_to_f(o.get("PartsCostCNY")) + _to_f(o.get("ShippingCostCNY"))
                      for o in all_orders)
if _total_cost_cny > 0:
    st.caption("💰 Recorded costs across all orders: "
               "**£%s GBP** (¥%s CNY · rate 1 CNY = %s GBP)"
               % (format(_total_cost_cny * CNY_TO_GBP, ",.0f"),
                  format(_total_cost_cny, ",.0f"), CNY_TO_GBP))

st.markdown("---")

# --- Filters ---
col_f1, col_f2, col_f3, col_f4 = st.columns(4)
with col_f1:
    status_filter = st.selectbox("Status", ["all"] + _statuses, index=0,
                                 key="ao_status")
with col_f2:
    priority_filter = st.selectbox("Priority", ["all", "URGENT", "Normal"],
                                   key="ao_priority")
with col_f3:
    _names = sorted({e["who"] for e in entries if e["who"]})
    who_filter = st.selectbox("Raised by", ["all"] + _names, key="ao_engineer")
with col_f4:
    search = st.text_input("Search part / order id",
                           placeholder="Type to filter...", key="ao_search")

view = entries
if status_filter != "all":
    view = [e for e in view if e["status"] == status_filter]
if priority_filter != "all":
    view = [e for e in view if e["priority"] == priority_filter]
if who_filter != "all":
    view = [e for e in view if e["who"] == who_filter]
if search:
    _s = search.lower()
    view = [e for e in view if _s in e["text"].lower()]

# Sort: open before closed; URGENT first among the open; app entries newest
# first, then ledger-only history in part order. The status chip on every
# card says which band a row is in — an ordering, not a split.
def _band(e):
    if e["status"] in _closed:
        return 2
    return 0 if e["priority"] == "URGENT" else 1

# App entries sort newest first (CreatedAt is ISO, so text order IS date
# order) and ledger-only entries carry when="" so they follow, keeping the
# tab order they were read in — their own dates are hand-typed and unsortable.
# The final stable sort puts the bands in order without disturbing either.
view.sort(key=lambda e: e["when"], reverse=True)
view.sort(key=_band)

_joined = sum(1 for e in entries if e["kind"] == "app" and e["thread"])
st.markdown("**%d orders** shown · %d in both records · %d app-only "
            "· %d ledger-only (pre-app history)"
            % (len(view), _joined,
               sum(1 for e in entries if e["kind"] == "app" and not e["thread"]),
               len(_ledger_only)))
st.markdown("---")

# --- Two views of the same filtered list: cards, and one row per order ------
tab_cards, tab_table = st.tabs(["🗂 Cards (%d)" % len(view),
                                "📋 Table View (%d)" % len(view)])

with tab_cards:
    MAX_SHOW = 20
    shown = view
    if len(view) > MAX_SHOW:
        if not st.checkbox("Show all %d (showing first %d)"
                           % (len(view), MAX_SHOW), key="ao_show_all"):
            shown = view[:MAX_SHOW]

    for e in shown:
        if e["kind"] == "ledger":
            tracker_order_ui.render_order(
                e["thread"], "ao_%s_%s" % (e["thread"]["mcode"],
                                           e["thread"].get("order_id", "")))
        elif e["status"] in _closed:
            history_card(e["order"],
                         _progress_text(e["thread"]) if e["thread"] else "",
                         e["thread"])
        else:
            order_card(e["order"].get("OrderID", "?"), user["name"],
                       _progress_text(e["thread"]) if e["thread"] else "",
                       e["thread"])

with tab_table:
    # One row per order, both record systems, same filters as the cards.
    # Everything cast to str: app quantities are ints, ledger ones are
    # sometimes words, and Arrow refuses the mixture.
    _rows = []
    for e in view:
        o, t = e.get("order") or {}, e.get("thread") or {}
        _rows.append({
            "Project": (o.get("Project") or t.get("project", "")).strip()
                       if o else t.get("project", ""),
            "M-Code": o.get("PartID", "") or t.get("mcode", ""),
            "Part": o.get("PartName", "") or t.get("part_name", ""),
            "Version": o.get("Version", "") or t.get("version", ""),
            "Order ID": (o.get("OrderID", "") if o else "")
                        or str(t.get("order_id", "")),
            "Date": e["when"] or t.get("date", ""),
            "Ordered": str(t.get("qty_ordered", "") if t
                           else o.get("Quantity", "")),
            "Received": str(t.get("qty_received", "")) if t else "",
            "Status": e["status"],
            "Priority": e["priority"] if e["kind"] == "app" else "",
            "Raised by": e["who"],
            "Recipient": (o.get("Recipient", "") if o
                          else t.get("recipient", "")),
        })
    _df = pd.DataFrame(_rows).astype(str)
    _df.index = range(1, len(_df) + 1)

    # The Overview page's palette, one row per status — read from the same
    # constants so the two tables can never drift apart.
    _paint_by = {
        "new": overview_board.COLOURS[overview_board.ORDERED],
        "processing": overview_board.COLOURS[overview_board.ORDERED],
        "ordered": overview_board.COLOURS[overview_board.ORDERED],
        "shipped": overview_board.COLOURS[overview_board.SHIPPED],
        "delivered": overview_board.COLOURS[overview_board.DELIVERED],
        "cancelled": overview_board.COLOURS[overview_board.CANCELLED],
    }

    def _paint(row):
        return ["background-color: %s"
                % _paint_by.get(str(row.get("Status", "")).lower(), "")] * len(row)

    st.dataframe(_df.style.apply(_paint, axis=1), use_container_width=True,
                 hide_index=False, height=ui.table_height(len(_df)))
    st.caption(
        "🟩 light green — open: new, processing or ordered · "
        "shipped keeps the same open green · 🟢 green — "
        "delivered · ⬜ grey — cancelled. Statuses are reconciled "
        "with the part ledger: a receipt on the sheet moves an order forward "
        "even before anyone updates the Orders tab.")
