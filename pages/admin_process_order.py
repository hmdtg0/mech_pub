"""Process Order — the admin flow for updating an order's lifecycle.

Updating an order is two writes, not one (Hamid, 18 Aug 2026): the Orders
tab carries the workflow state, and the part's history tab in the project
record carries what actually happened. Receiving goods appends the receive
line (the reference record's second line of the pair); anything else worth
recording goes in through "Add history entry". Overview, Parts, Movements
and the rest read that history, so this page is where their data comes from.
"""
import pandas as pd
import streamlit as st

from datetime import datetime
from utils.auth import require_role
from utils.google_client import get_gspread_client
from utils.orders_store import fetch_all_orders, update_order
from utils import (holders_store, parts_tracker, project_colors,
                   project_registry, record_builder, stock_store,
                   tracker_orders, tracker_writer, ui)
from utils.tracker_parse import event_of, holder_of, place_of, to_int
from utils.drive_handler import download_file_bytes, download_to_local
from utils.message_store import fetch_messages_for_order, send_message
import config
from config import ORDER_STATUSES, STATUS_COLORS, IS_LOCAL, CNY_TO_GBP


user = require_role("admin", "engineer", "logistics")

st.title("🔧 Process Order")


def flash(kind: str, text: str) -> None:
    """Say something that has to survive `st.rerun()`.

    `st.toast` does not (verified in Streamlit 1.38): the rerun tears the
    page down before it is painted, so every action that saved and reran was
    silently confirming nothing. Stashing the message and rendering it on the
    next run is what actually reaches the user.
    """
    st.session_state["proc_flash"] = (kind, text)


_flash = st.session_state.pop("proc_flash", None)
if _flash:
    {"success": st.success, "warning": st.warning,
     "error": st.error}.get(_flash[0], st.info)(_flash[1])

orders = fetch_all_orders()
if not orders:
    st.info("No orders to process.")
    st.stop()

# The worklist spans every project (writes are safe either way — each order
# is filed to ITS OWN project's record, resolved per order below).
orders = ui.in_scope(orders)
_multi_project = len({(o.get("Project") or "").strip() for o in orders
                      if (o.get("Project") or "").strip()}) > 1

def _rank(s: str) -> int:
    return ORDER_STATUSES.index(s) if s in ORDER_STATUSES else 0


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
    ledger rows.

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


def order_facts(o):
    """(effective_status, latest_owner, history_eta, detail). History can
    only move the status FORWARD past the Orders tab's stored value —
    except "cancelled", which is terminal (tracker_orders.effective_status
    is the one rulebook)."""
    derived, owner, eta, detail = _facts.get(
        (o.get("Project", ""), str(o.get("PartID", "")).strip()),
        ("", "", "", ""))
    stored = o.get("Status", "new")
    return (tracker_orders.effective_status(stored, derived),
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

# The status shown (and advanced from) is the EFFECTIVE one: the part's
# history can move it forward past what the Orders tab remembers, and a
# cancellation on either side is terminal.
derived_status, latest_owner, history_eta, history_detail = _facts.get(
    (project_name, mcode), ("", "", "", ""))
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
        st.caption("**%s** is derived from the part's history — the Orders "
                   "tab still says '%s'." % (status.upper(), stored_status))
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
if record_id and mcode:
    _hist = parts_tracker.fetch_all_parts(record_id).get(mcode, {}).get(
        "history", [])
    st.subheader("📜 %s history (%d)" % (mcode, len(_hist)))
    if _hist:
        st.dataframe(pd.DataFrame([{
            "Date": r.get("date", ""),
            "Event": r.get("event", "") or r.get("type", ""),
            "Order / Sample ID": r.get("order_id", ""),
            "Build": r.get("build", ""),
            "Qty ordered": r.get("qty_ordered", ""),
            "Qty received": r.get("qty_received", ""),
            "Qty moved": r.get("qty_moved", ""),
            "From": place_of(r),
            "To": holder_of(r),
            "ETA": r.get("eta", ""),
            "QC": r.get("status", ""),
            "Courier / Tracking": r.get("courier", ""),
            "Notes": r.get("notes", ""),
        } for r in _hist]), hide_index=True, use_container_width=True,
            height=ui.table_height(len(_hist)))
    else:
        st.caption("No history rows on this part's tab yet — the entries "
                   "you add below become its first.")

st.markdown("---")

# The Status buttons, the guided Cancel and the Advance-to-DELIVERED door
# lived here until 28 Aug (Hamid: "we dont need these red marks"). Advancing
# and reverting the stored status stays on the All Orders cards; cancelling
# is the Cancelled event on Add history entry; Receive goods — the one of
# the three that writes the LEDGER — moved into the entry picker below.

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
# "the goal is to have one entry point called 'Add history entry'". The
# picker decides which fields show; each branch is the exact form that used
# to stand on its own — same keys, same writes, same wiring. The old
# stock-movement form went first as redundant (a History entry with a
# moves-stock event and a quantity reaches the movement log and the count
# through the same writer); Order info followed ("I dont see any use for
# order info for now") — its five fields stay fully editable on the All
# Orders order cards, which carry the identical form.
st.subheader("📜 Add history entry")

_entry_kind = st.radio(
    "What are you recording?",
    ["📜 History entry", "📥 Receive goods", "💰 Costs"],
    horizontal=True, key="proc_entry_kind", label_visibility="collapsed")

if _entry_kind.endswith("History entry"):
    st.caption("Appends one line to **%s**'s history in the project record — "
               "the same ledger the board, Parts, Part Detail and Movements "
               "read." % (mcode or "the part"))
    # The whole vocabulary, straight from the event table — not a second list
    # typed out here. The old hardcoded pair got out of step with it: `Scrap`
    # was offered but treated as informational, so a scrapped batch never left
    # the count, and `Receipt` and `Delivery` could not be entered at all
    # (19 Aug).
    HISTORY_EVENTS = list(config.EVENT_CHOICES)

    if not record_id or not mcode:
        st.info("Needs a registered project record and a Part ID on the order.")
    else:
        with st.form("history_form_%s" % order_id):
            h1, h2, h3 = st.columns(3)
            with h1:
                h_event = st.selectbox("Event", HISTORY_EVENTS)
                h_date = st.date_input("Date", value=datetime.now().date())
                h_eta = st.date_input("New ETA (optional)", value=None,
                                      help="Lands in the row's ETA column — "
                                           "the order's shown ETA follows the "
                                           "latest history row that carries "
                                           "one.")
            with h2:
                # Directory pickers with a type-a-name escape (Hamid, 28 Aug
                # interview). A TYPED name is registered in the Holders
                # directory BEFORE the entry is written — From as a "source"
                # (a vendor; never counted negative), To as a "person".
                _holders = holders_store.names()
                h_from_pick = st.selectbox(
                    "From", [""] + _holders,
                    format_func=lambda h: h or "—")
                h_from_typed = st.text_input(
                    "…or type a vendor / outside source",
                    help="A new name is saved to the Holders directory as a "
                         "'source' before the entry is recorded.")
                h_to_pick = st.selectbox(
                    "To", [""] + _holders,
                    format_func=lambda h: h or "—")
                h_to_typed = st.text_input(
                    "…or type a new name",
                    help="A new name is saved to the Holders directory as a "
                         "'person' before the entry is recorded.")
                h_courier = st.text_input("Courier / Tracking", value="")
            with h3:
                h_qty_ordered = st.text_input("Qty ordered", value="")
                h_qty_moved = st.text_input("Qty moved", value="")
                h_qty_received = st.text_input("Qty received", value="")
            h4, h5, h6 = st.columns(3)
            with h4:
                h_qc = st.text_input("QC Pass?",
                                     placeholder="e.g. Pass / FAILED QC")
            with h5:
                h_build = st.text_input("Build", value="")
            with h6:
                h_lead = st.text_input("Lead time (days)", value="")
            h_notes = st.text_input("Notes")
            h_selected = st.checkbox("Selected for MP?", value=False)
            add_entry = st.form_submit_button("➕ Append to part history",
                                              type="primary")
        if add_entry:
            h_from = (h_from_typed.strip() or h_from_pick).strip()
            h_to = (h_to_typed.strip() or h_to_pick).strip()
            # Only a STOCK-MOVING entry has to name somebody — a Cancelled,
            # QC, Update or Hold line legitimately names nobody (28 Aug;
            # the old blanket guard would have blocked every cancellation).
            if config.moves_stock(h_event) and not h_to and not h_from:
                st.error("From or To is needed — a stock-moving entry that "
                         "names nobody records nothing.")
                st.stop()
            # A typed name reaches the directory FIRST (Hamid: "make sure
            # the vendor will be recorded ... before user submits it") — if
            # that write fails, no entry is recorded at all.
            _reg = ""
            if h_from and not holders_store.is_known(h_from):
                _reg = holders_store.register(
                    h_from, kind="source", notes="added from Process Order")
            if not _reg and h_to and not holders_store.is_known(h_to):
                _reg = holders_store.register(
                    h_to, kind="person", notes="added from Process Order")
            if _reg:
                st.error("Not recorded — the new name could not be saved to "
                         "the Holders directory first: %s" % _reg)
            else:
                now = datetime.now()
                ok, message = tracker_writer.append_history(mcode, {
                    "event": h_event,
                    "date": h_date.strftime("%d %b %Y"),
                    "order_id": order_id,
                    "version": order.get("Version", ""),
                    "build": h_build.strip(),
                    "qty_ordered": h_qty_ordered.strip(),
                    "qty_moved": h_qty_moved.strip(),
                    "qty_received": h_qty_received.strip(),
                    "place": h_from.strip(),
                    "holder": h_to.strip(),
                    "eta": h_eta.strftime("%d %b %Y") if h_eta else "",
                    "lead_time": h_lead.strip(),
                    "status": h_qc.strip(),
                    "courier": h_courier.strip(),
                    "selected": "TRUE" if h_selected else "FALSE",
                    "logged_by": user.get("email", "") or user.get("name", ""),
                    "logged_at": now.strftime("%d %b %Y %H:%M"),
                    "notes": h_notes.strip(),
                    # The pilot ledger kept the kind of row in `Type`.
                    "type": h_event,
                }, sheet_id=record_id)
                # Goods on the move go through stock_store.record_movement —
                # the ONE writer for the merged movement log and the count —
                # so a moves-stock event with a quantity reaches both, which
                # is why the old separate movement form was redundant and was
                # removed (28 Aug). Before 19 Aug this branch called a writer
                # aimed at a tab that no longer existed and ignored the
                # result, so a Shipping entered here landed on the part tab
                # and nowhere else.
                stock_note = ""
                h_qty = to_int(h_qty_moved.strip() or h_qty_received.strip())
                if ok and config.moves_stock(h_event) and h_qty > 0:
                    res = stock_store.record_movement(
                        mcode, project_name, h_qty,
                        h_to.strip(), h_from.strip(), event=h_event,
                        description=order.get("PartName", ""),
                        part_type=order.get("Process", ""),
                        notes=h_notes.strip(), courier=h_courier.strip(),
                        build=h_build.strip(),
                        date=h_date.strftime("%d %b %Y"),
                        logged_by=user.get("email", "") or user.get("name", ""))
                    stock_note = (" · logged and counted" if res.get("ok") else
                                  " — but the movement log and count were NOT "
                                  "updated: %s"
                                  % res.get("problem", "unknown error"))
                elif ok and config.moves_stock(h_event):
                    stock_note = (" · no quantity given, so nothing was "
                                  "counted — fill in Qty moved to have it "
                                  "reach the count")
                if ok:
                    parts_tracker.refresh(record_id)
                    ov = record_builder.write_overview(
                        project_name,
                        user.get("email", "") or user.get("name", ""),
                        sheet_id=record_id, replace=True)
                    if ov.get("problem"):
                        st.warning("Overview not refreshed: %s" % ov["problem"])
                    (st.warning if "NOT" in stock_note else st.success)(
                        message + stock_note)
                else:
                    st.error(message)

elif _entry_kind.endswith("Receive goods"):
    # THE receive flow — was reached through "Advance to DELIVERED" until
    # 28 Aug; now a branch of the one entry point. Same writes as ever:
    # the paired receive line (write_receipt), the movement log + count
    # (record_movement), Status=delivered and the tracking sync.
    st.caption("Writes the receive line to **%s**'s history in the project "
               "record — same order id as the raise line, received quantity, "
               "courier, and who holds the parts now." % (mcode or "?"))
    with st.form("receive_form_%s" % order_id):
        r1, r2 = st.columns(2)
        with r1:
            try:
                _qty_default = int(float(order.get("Quantity", 1) or 1))
            except (TypeError, ValueError):
                _qty_default = 1
            qty_rec = st.number_input("Qty received", min_value=0,
                                      value=_qty_default, step=1)
            received_by = st.text_input("Received by (To)",
                                        value=order.get("Recipient", ""))
            received_from = st.text_input("From (vendor / sender)",
                                          value=order.get("Vendor", ""))
        with r2:
            courier = st.text_input("Courier / Tracking",
                                    value=order.get("TrackingNum", ""))
            rec_date = st.date_input("Date received",
                                     value=datetime.now().date())
            rec_note = st.text_input("Note",
                                     placeholder="e.g. Arrived, QC pending")
        confirm_receive = st.form_submit_button(
            "✅ Receive & mark DELIVERED", type="primary")
    if confirm_receive:
        if not record_id:
            st.error("No project record registered for '%s' — the receive "
                     "line has nowhere to go." % (project_name or "?"))
        elif not mcode:
            st.error("This order has no Part ID, so it cannot be filed "
                     "against a part tab.")
        elif not received_by.strip():
            st.error("Received by is empty — a receipt that names nobody "
                     "records nothing.")
        else:
            now = datetime.now()
            ok, message = tracker_writer.write_receipt(
                mcode, order_id=order_id,
                qty_ordered=str(order.get("Quantity", "")),
                qty_received=str(qty_rec),
                received_from=received_from.strip(),
                holder=received_by.strip(),
                courier=courier.strip(),
                date=rec_date.strftime("%d %b %Y"),
                version=order.get("Version", ""),
                eta=order.get("ETA", ""),
                note=rec_note.strip()
                or ("received %s" % rec_date.strftime("%d %b %Y")),
                logged_by=user.get("email", "") or user.get("name", ""),
                logged_at=now.strftime("%d %b %Y %H:%M"),
                sheet_id=record_id)
            if ok:
                # Goods arriving ARE stock arriving. Until 19 Aug this flow
                # wrote the part's history and stopped, so a delivery never
                # reached the count — `Receipt` is `stock: "in"` in the event
                # table and the code simply never asked it.
                stock_note = ""
                if int(qty_rec) > 0:
                    res = stock_store.record_movement(
                        mcode, project_name, int(qty_rec),
                        received_by.strip(), received_from.strip(),
                        event="Receipt",
                        description=order.get("PartName", ""),
                        part_type=order.get("Process", ""),
                        notes=rec_note.strip(), courier=courier.strip(),
                        build=order.get("Version", ""),
                        date=rec_date.strftime("%d %b %Y"),
                        logged_by=user.get("email", "")
                        or user.get("name", ""))
                    stock_note = ("" if res.get("ok") else
                                  " The stock count was NOT updated: %s"
                                  % res.get("problem", "unknown error"))
                if client:
                    updates = {"Status": "delivered"}
                    if courier.strip():
                        updates["TrackingNum"] = courier.strip()
                    update_order(client, order_id, updates)
                parts_tracker.refresh(record_id)
                # The Overview is derived — recompute it so the edit shows
                # everywhere immediately, not only on the part tab.
                ov = record_builder.write_overview(
                    project_name, user.get("email", "") or user.get("name", ""),
                    sheet_id=record_id, replace=True)
                if ov.get("problem"):
                    st.warning("Overview not refreshed: %s" % ov["problem"])
                flash("warning" if stock_note else "success",
                      message + stock_note)
                st.rerun()
            else:
                st.error(message)

else:   # 💰 Costs
    def _to_float(v):
        try:
            return float(str(v).strip()) if str(v).strip() else 0.0
        except (ValueError, TypeError):
            return 0.0

    current_parts = _to_float(order.get("PartsCostCNY", ""))
    current_ship = _to_float(order.get("ShippingCostCNY", ""))

    with st.form("cost_form"):
        cc1, cc2 = st.columns(2)
        with cc1:
            parts_cost = st.number_input("Parts (CNY)", min_value=0.0, value=current_parts, step=1.0, format="%.2f")
        with cc2:
            ship_cost = st.number_input("Shipping (CNY)", min_value=0.0, value=current_ship, step=1.0, format="%.2f")

        total_cny = parts_cost + ship_cost
        total_gbp = total_cny * CNY_TO_GBP

        st.markdown(f"**Total: ¥{total_cny:,.2f} CNY ≈ £{total_gbp:,.2f} GBP**  *(rate: 1 CNY = {CNY_TO_GBP} GBP)*")

        if st.form_submit_button("Save Costs", type="primary"):
            if client:
                cost_updates = {}
                if parts_cost != current_parts:
                    cost_updates["PartsCostCNY"] = str(parts_cost)
                if ship_cost != current_ship:
                    cost_updates["ShippingCostCNY"] = str(ship_cost)
                if cost_updates:
                    update_order(client, order_id, cost_updates)
                    flash("success", "Costs saved.")
                    st.rerun()
                else:
                    st.info("No changes.")

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
