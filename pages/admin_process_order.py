"""Process Order — the admin flow for updating an order's lifecycle.

Updating an order is two writes, not one (Hamid, 18 Aug 2026): the Orders
tab carries the workflow state, and the part's history tab in the project
record carries what actually happened. Receiving goods appends the receive
line (the reference record's second line of the pair); anything else worth
recording goes in through "Add history entry". Overview, Parts, Movements
and the rest read that history, so this page is where their data comes from.
"""
import streamlit as st

from datetime import datetime
from utils.auth import require_role
from utils.google_client import get_gspread_client
from utils.orders_store import fetch_all_orders, update_order
from utils import (history_entry, parts_tracker, project_colors,
                   project_registry, tracker_orders, ui)
from utils.tracker_parse import holder_of
from utils.drive_handler import download_file_bytes, download_to_local
from utils.message_store import fetch_messages_for_order, send_message
from config import ORDER_STATUSES, STATUS_COLORS, IS_LOCAL


user = require_role("admin", "engineer", "logistics")

st.title("🔧 Process Order")

# The entry forms and their flash live in utils/history_entry since 28 Aug
# ("I want to unify them") — the page keeps only the worklist, the order
# header and the messages.

orders = fetch_all_orders()
if not orders:
    st.info("No orders to process.")
    st.stop()

# The worklist spans every project (writes are safe either way — each order
# is filed to ITS OWN project's record, resolved per order below).
orders = ui.in_scope(orders)
_multi_project = len({(o.get("Project") or "").strip() for o in orders
                      if (o.get("Project") or "").strip()}) > 1

def _parse_eta(text):
    """A sheet ETA as a date, if one of its known spellings fits."""
    for fmt in ("%Y-%m-%d", "%d %b %Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(str(text).strip(), fmt).date()
        except (ValueError, TypeError):
            continue
    return None


def _history_facts(history):
    """(derived_status, latest_owner, latest_eta, detail) from a part's
    ledger rows. Since 28 Aug the PART-level derived_status only shapes
    the detail string here — order status joins the order's own thread
    (_thread_derived), because one part can carry several order groups.

    The Orders tab said "new" for a whole migrated batch that was in truth
    ordered, shipped or delivered (Hamid, 18 Aug). The part's history knows:
    the last raise line opens the order; a later Order row with a received
    quantity means delivered; courier/tracking without a receipt means
    shipped. Latest owner/ETA = the last row that carries one. `detail` says
    what the status MEANS — the latest row's note or label, with the holder
    in front once delivered ("Austin — for assembly").
    """
    latest_owner = ""
    latest_eta = ""
    last_row = None
    for row in history:
        who = holder_of(row)
        if who:
            latest_owner = who
        eta = str(row.get("eta", "")).strip()
        if eta:
            latest_eta = eta
        if (who or str(row.get("notes", "")).strip()
                or str(row.get("order_id", "")).strip()):
            last_row = row

    # One rulebook for the whole app — the Overview's Latest status column
    # is written with the same function.
    derived = tracker_orders.derived_status(history)

    detail = ""
    if last_row is not None:
        note = str(last_row.get("notes", "")).strip()
        if note.lower().startswith("ordered by"):
            note = ""          # the raise marker, not a story
        label = str(last_row.get("order_id", "")).strip()
        who = holder_of(last_row)
        detail = note or label
        if who and derived == "delivered":
            detail = ("%s — %s" % (who, detail)) if detail else who
        elif who and not detail:
            detail = "→ %s" % who
        if len(detail) > 60:
            detail = detail[:57] + "…"
    return derived, latest_owner, latest_eta, detail


# One cached parts fetch per project; facts keyed by (project, mcode).
_facts = {}
for _proj in {o.get("Project", "") for o in orders}:
    _rec = project_registry.tracker_sheet(_proj) if _proj else ""
    if not _rec:
        continue
    for _pm, _part in parts_tracker.fetch_all_parts(_rec).items():
        _facts[(_proj, _pm)] = _history_facts(_part.get("history", []))

# STATUS comes from the order's OWN ledger thread, not the whole part: a
# part can carry several Order/Sample ID groups, and judging this order
# by another thread's rows buckets it wrong — M108 sat "active" here
# while All Orders and Logistics said delivered (28 Aug 2026). Same
# order-id join the orders desk and Logistics use; _facts stays the
# source for the part-level extras (owner, ETA, detail) only.
_thread_of = {}
for _t in ui.in_scope(tracker_orders.all_projects_orders()):
    _toid = str(_t.get("order_id", "")).strip()
    if _toid:
        _thread_of[_toid] = _t


def _thread_derived(o):
    """The derived status of this order's own ledger thread ("" if the
    order never got an id-stamped thread — stored status then stands)."""
    _t = _thread_of.get(str(o.get("OrderID", "")).strip())
    return _t.get("derived", "") if _t else ""


def order_facts(o):
    """(effective_status, latest_owner, history_eta, detail). The thread
    can only move the status FORWARD past the Orders tab's stored value —
    except "cancelled", which is terminal (tracker_orders.effective_status
    is the one rulebook). Owner/ETA/detail stay part-level: the latest
    custody story is the part's, whichever thread wrote it."""
    _, owner, eta, detail = _facts.get(
        (o.get("Project", ""), str(o.get("PartID", "")).strip()),
        ("", "", "", ""))
    stored = o.get("Status", "new")
    return (tracker_orders.effective_status(stored, _thread_derived(o)),
            owner, eta, detail)


status_icons = {"new": "⚪", "ordered": "🟠",
                "shipped": "🟣", "delivered": "🟢", "cancelled": "🚫"}
selected_order_id = None


# One stable colour per Type across all three tabs.
_all_types = sorted({str(o.get("Process", "") or "—") for o in orders})


def render_orders(order_list, key_prefix):
    """The grouped order list; returns the OrderID whose Open was clicked."""
    if not order_list:
        st.info("Nothing here.")
        return None
    selected = None
    groups = {}
    for o in order_list:
        groups.setdefault(str(o.get("Process", "") or "—"), []).append(o)
    for type_name in sorted(groups):
        slot = project_colors.PALETTE[
            _all_types.index(type_name) % len(project_colors.PALETTE)]
        st.markdown(
            '<div style="background:%s26;border-left:5px solid %s;padding:4px 10px;'
            'border-radius:4px;margin-top:10px;font-weight:600;">%s '
            '<span style="font-weight:400;opacity:.7;">— %d order(s)</span></div>'
            % (slot["hex"], slot["hex"], type_name,
               len(groups[type_name])),
            unsafe_allow_html=True)
        for o in groups[type_name]:
            oid = o.get("OrderID", "?")
            part = o.get("PartName", "?")
            pid = o.get("PartID", "")
            pri = o.get("Priority", "Normal")
            eta = o.get("ETA", "")
            effective, owner, eta_hist, detail = order_facts(o)
            icon = status_icons.get(effective, "⚪")
            pri_icon = "🔴" if pri == "URGENT" else ""

            cols = st.columns([0.4, 3.2, 1.3, 2.4, 1.3, 0.8])
            with cols[0]:
                st.markdown(f"{icon}")
            with cols[1]:
                owner_bit = f" — 👤 {owner}" if owner else ""
                ptag = (project_colors.tag((o.get("Project") or "").strip()) + " "
                        if _multi_project and (o.get("Project") or "").strip()
                        else "")
                st.markdown(f"{ptag}**{part}** `{pid}`{owner_bit} {pri_icon}")
            with cols[2]:
                st.markdown(f"`{effective.upper()}`")
            with cols[3]:
                st.markdown(detail or "—")
            with cols[4]:
                st.markdown(f"ETA: {eta or eta_hist or '-'}")
            with cols[5]:
                if st.button("Open", key=f"open_{key_prefix}_{oid}"):
                    selected = oid
    return selected


# --- Three views: active / delivered / everything submitted ---
_active, _delivered = [], []
for o in orders:
    _eff = order_facts(o)[0]
    if _eff == "delivered":
        _delivered.append(o)
    elif _eff != "cancelled":
        _active.append(o)

tab_active, tab_done, tab_all = st.tabs([
    "🟠 Active (%d)" % len(_active),
    "🟢 Delivered (%d)" % len(_delivered),
    "📋 All orders (%d)" % len(orders),
])
with tab_active:
    selected_order_id = render_orders(_active, "act") or selected_order_id
with tab_done:
    selected_order_id = render_orders(_delivered, "done") or selected_order_id
with tab_all:
    selected_order_id = render_orders(orders, "all") or selected_order_id

# Check session state for selected order
if selected_order_id:
    st.session_state["process_order_id"] = selected_order_id

sel_id = st.session_state.get("process_order_id")
if not sel_id:
    st.info("Click **Open** on an order above to process it.")
    st.stop()

# Find the selected order
order = next((o for o in orders if o.get("OrderID") == sel_id), None)
if not order:
    st.warning("Selected order not found.")
    if st.button("Clear selection"):
        del st.session_state["process_order_id"]
        st.rerun()
    st.stop()

st.markdown("---")
if st.button("⬆ Close Details", key="collapse_btn"):
    del st.session_state["process_order_id"]
    st.rerun()

order_id = order.get("OrderID", "?")
stored_status = order.get("Status", "new")
client = get_gspread_client()

# Where this order's history lives: the part's tab in the project record.
project_name = order.get("Project", "")
record_id = project_registry.tracker_sheet(project_name) if project_name else ""
mcode = str(order.get("PartID", "")).strip()

# The status shown (and saved back to the Orders tab below) is the
# EFFECTIVE one: this order's OWN thread can move it forward past what
# the Orders tab remembers, and a cancellation on either side is
# terminal. Thread, not part — the part-level derived could offer
# another thread's status to the save button.
_, latest_owner, history_eta, history_detail = _facts.get(
    (project_name, mcode), ("", "", "", ""))
derived_status = _thread_derived(order)
status = tracker_orders.effective_status(stored_status, derived_status)
status_idx = ORDER_STATUSES.index(status) if status in ORDER_STATUSES else 0
status_color = STATUS_COLORS.get(status, "gray")

st.markdown(f"### :{status_color}[{status.upper()}] — {order.get('PartName', '')} `{mcode}`")
st.progress(status_idx / (len(ORDER_STATUSES) - 1))
if latest_owner:
    st.caption("Latest owner (from part history): **%s**" % latest_owner)
if history_detail:
    st.caption("Latest activity: **%s**" % history_detail)
_eta_missing = not str(order.get("ETA", "")).strip()
if history_eta and _eta_missing:
    st.caption("ETA (from part history): **%s**" % history_eta)
if status != stored_status:
    _sync_eta = history_eta if (history_eta and _eta_missing) else ""
    sync1, sync2 = st.columns([3, 1.4], vertical_alignment="center")
    with sync1:
        st.caption("**%s** is derived from this order's own ledger thread "
                   "— the Orders tab still says '%s'."
                   % (status.upper(), stored_status))
    with sync2:
        label = ("💾 Save %s + ETA to Orders" % status.upper()
                 if _sync_eta else "💾 Save %s to Orders" % status.upper())
        if st.button(label, use_container_width=True):
            if client:
                updates = {"Status": status}
                if _sync_eta:
                    parsed = _parse_eta(_sync_eta)
                    updates["ETA"] = (parsed.strftime("%d %b %Y")
                                      if parsed else _sync_eta)
                update_order(client, order_id, updates)
                st.rerun()

# --- The part's ledger first: what you are updating against ---
# One shape with Part Detail since 28 Aug (Hamid: "I want to unify them") —
# both pages draw utils/history_entry's table and entry point.
history_entry.history_table(mcode, record_id)

st.markdown("---")

# The Status buttons, the guided Cancel and the Advance-to-DELIVERED door
# lived here until 28 Aug (Hamid: "we dont need these red marks"). Advancing
# and reverting the stored status stays on the All Orders cards; cancelling
# is the Cancelled event on Add history entry; receiving is the Receipt
# event there — the one form does the full receive.

# --- Order details ---
st.subheader("Order Details")
col1, col2 = st.columns(2)
with col1:
    st.markdown(f"**Order ID:** `{order_id}`")
    st.markdown(f"**Engineer:** {order.get('EngineerName', '')} ({order.get('EngineerEmail', '')})")
    if order.get("Reviewer"):
        st.markdown(f"**Reviewer:** {order.get('Reviewer', '')}")
    st.markdown(f"**Submitted:** {order.get('CreatedAt', '')}")
    st.markdown(f"**Part Name:** {order.get('PartName', '')}")
    if order.get("PartID") or order.get("Version"):
        st.markdown(f"**Part ID:** {order.get('PartID', '?')} V{order.get('Version', '?')}")
    st.markdown(f"**Process:** {order.get('Process', '')}")
with col2:
    st.markdown(f"**Material:** {order.get('Material', '')}")
    st.markdown(f"**Finish:** {order.get('Finish', '')}")
    st.markdown(f"**Quantity:** {order.get('Quantity', '')}")
    st.markdown(f"**Priority:** {order.get('Priority', '')}")
    st.markdown(f"**Recipient:** {order.get('Recipient', '')}")
    st.markdown(f"**Inspection by Engineer:** {order.get('Inspection', 'No')}")

st.markdown("---")

# --- File download ---
drive_link = order.get("DriveFileLink", "")
if drive_link:
    st.subheader("📁 Files")
    st.markdown(f"[Open in Google Drive]({drive_link})")

    dcol1, dcol2 = st.columns(2)
    with dcol1:
        if st.button("⬇ Download (Browser)", key="dl_browser"):
            try:
                file_bytes, filename = download_file_bytes(drive_link)
                st.download_button(
                    label=f"Save {filename}",
                    data=file_bytes,
                    file_name=filename,
                    key="dl_btn",
                )
            except Exception as e:
                st.error(f"Download failed: {e}")

    if IS_LOCAL:
        with dcol2:
            if st.button("⬇ Download to Local Folder", key="dl_local"):
                try:
                    created = order.get("CreatedAt", "")
                    order_date = created.split(" ")[0] if created else ""
                    if order_date:
                        local_path = download_to_local(
                            drive_link,
                            order.get("PartName", "Unknown"),
                            order_date,
                        )
                        st.success(f"Saved to: `{local_path}`")
                    else:
                        st.error("Cannot determine order date for folder path")
                except Exception as e:
                    st.error(f"Local download failed: {e}")

st.markdown("---")

# --- Add history entry: the ONE entry point (Hamid, 28 Aug) -----------------
# "the goal is to have one entry point called 'Add history entry'". Since
# 28 Aug the WHOLE section lives in utils/history_entry.py, shared verbatim
# with Part Detail (Hamid: "I want to unify them ... this will be our one
# and only entry point to update the orderes") — this door passes the
# opened order, so Receipt and Costs land on it.
history_entry.render_entry(user, mcode, record_id, project_name,
                           order=order, key_ns=str(order_id))

st.markdown("---")

# --- Messages (isolated fragment: sending reruns only this block) ---
@st.fragment
def messages_section(order_id: str, user_name: str):
    st.subheader("💬 Messages")
    messages = fetch_messages_for_order(order_id)
    if messages:
        for m in messages:
            author = m.get("Author", "")
            ts = m.get("Timestamp", "")
            content = m.get("Content", "")
            is_me = author == user_name
            prefix = "🟢" if is_me else "🔵"
            st.markdown(f"{prefix} **{author}** ({ts}): {content}")
    else:
        st.caption("No messages yet.")

    with st.form("proc_msgform", clear_on_submit=True):
        new_msg = st.text_input("Message", key="proc_msg_input", placeholder="Ask engineer or leave a note...",
                                label_visibility="collapsed")
        sent = st.form_submit_button("Send")
    if sent and new_msg.strip():
        msg_client = get_gspread_client()
        if msg_client:
            try:
                send_message(msg_client, order_id, user_name, new_msg.strip())
            except RuntimeError as _e:
                st.error(str(_e))
            st.rerun(scope="fragment")


messages_section(order_id, user["name"])
