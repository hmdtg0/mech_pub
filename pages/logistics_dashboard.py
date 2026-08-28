"""Logistics Dashboard — the shipping/receiving view (Jimmy's page).

Same shape as the PCB tool's logistics dashboard: summary metrics, then
collapsible sections of actionable items. Adapted for mech orders — the
things being chased are vendor orders and parts in transit rather than PCBs
and components — and it also surfaces the project tracker's open deliveries
and movement log, read-only.
"""
import streamlit as st


import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from utils.auth import require_auth
from utils.google_client import get_gspread_client
from utils.orders_store import fetch_all_orders, update_order
from utils import (parts_model, parts_tracker, project_colors,
                   project_registry, tracker_orders, ui)
from utils.tracker_parse import to_int

user = require_auth()

st.title("📦 Logistics Dashboard")
st.markdown(f"Welcome, **{user['name']}**")
_active_name, _ = project_registry.active()
_wide = project_registry.is_all()
_sources = (list(project_registry.all_projects().items()) if _wide
            else [(_active_name, "")])
st.caption("Everything here follows the sidebar — currently **%s**. The order "
           "queues are one central tab (rows are tagged when more than one "
           "project is live); *Parts Short* and *Recent Movements* live in "
           "each project's own record, so they are listed project by "
           "project." % ("every project" if _wide else _active_name))

client = get_gspread_client()
# Orders are central: one tab for every project, each row tagged Project.
orders = ui.in_scope(fetch_all_orders())
_multi_project = len({(o.get("Project") or "").strip() for o in orders
                      if (o.get("Project") or "").strip()}) > 1


def _ptag(o: dict) -> str:
    """Project token for an order line, when more than one project is live."""
    name = (o.get("Project") or "").strip()
    return "%s " % project_colors.tag(name) if name and _multi_project else ""

# --- Order categories ---
# Queue membership uses the RECONCILED status, not the raw Orders-tab cell:
# the part's history can move an order forward past a lagging cell, never
# backward — the same effective_status rule every other page applies. This
# page was the last one reading the cell raw (28 Aug 2026): an order raised
# on the ledger but not yet advanced by hand showed in no queue at all, and
# one received on the sheet would have sat in "Awaiting Dispatch" forever.
_thread_of = {}
for _t in ui.in_scope(tracker_orders.all_projects_orders()):
    _oid = str(_t.get("order_id", "")).strip()
    if _oid:
        _thread_of[_oid] = _t

awaiting_dispatch, in_transit, recently_delivered = [], [], []
for _o in orders:
    _t = _thread_of.get(str(_o.get("OrderID", "")).strip())
    _status = tracker_orders.effective_status(
        (_o.get("Status") or "new").strip() or "new",
        _t.get("derived", "") if _t else "")
    _tracking = (_o.get("TrackingNum") or "").strip()
    # Ordered but no tracking number yet -> waiting to leave the vendor.
    if _status == "ordered" and not _tracking:
        awaiting_dispatch.append(_o)
    # On the way -> waiting to be received.
    elif _status == "shipped" or (_status == "ordered" and _tracking):
        in_transit.append(_o)
    elif _status == "delivered":
        recently_delivered.append(_o)

# --- Tracker categories (read-only) ---
# One project record per source, read in turn: these two tabs belong to a
# project's own sheet, so the wide scope is a list of blocks rather than one
# merged table (Hamid, 21 Aug: "show them in the same page one after another").
_short_by_project, _moves_by_project = [], []
for _pname, _psid in _sources:
    _rows = parts_tracker.fetch_overview(_psid or None)
    _short_by_project.append(
        (_pname, [r for r in _rows
                  if to_int(r.get("qty_ordered", "")) > to_int(r.get("qty_received", ""))]))
    _moves_by_project.append((_pname, parts_tracker.fetch_movements(_psid or None)))
open_deliveries = [r for _n, rows in _short_by_project for r in rows]
movements = [m for _n, rows in _moves_by_project for m in rows]

col1, col2, col3, col4 = st.columns(4)
col1.metric("Awaiting Dispatch", len(awaiting_dispatch))
col2.metric("In Transit", len(in_transit))
col3.metric("Parts Short (tracker)", len(open_deliveries))
col4.metric("Delivered", len(recently_delivered))

if not client:
    st.warning("Read-only: no Google credentials, so status updates are disabled.")

st.markdown("---")

# ============================================================
# A. AWAITING DISPATCH — add tracking + ETA
# ============================================================
with st.expander(f"📤 **Awaiting Dispatch** ({len(awaiting_dispatch)})", expanded=False):
    if not awaiting_dispatch:
        st.success("Nothing waiting on a vendor dispatch!")
    else:
        for i, o in enumerate(awaiting_dispatch):
            oid = o.get("OrderID", "?")
            part = o.get("PartName", "N/A")
            mcode = (o.get("PartID") or "").strip()
            vendor = o.get("Vendor", "")
            priority = o.get("Priority", "Normal")
            priority_icon = "🔴" if priority == "URGENT" else "🟢"

            st.markdown(_ptag(o) + f"{priority_icon} **{part}**"
                        + (f" | `{mcode}`" if mcode else "")
                        + f" | Vendor: {vendor or 'TBC'} | To: {o.get('Recipient', '')}")
            with st.form(f"dispatch_{oid}_{i}"):
                dc1, dc2 = st.columns(2)
                with dc1:
                    tracking = st.text_input("Tracking #", key=f"trk_{oid}_{i}",
                                             placeholder="e.g. SF1572075868095 / DHL 69 1758 9722")
                with dc2:
                    eta = st.text_input("ETA (YYYY-MM-DD)", value=o.get("ETA", ""),
                                        key=f"eta_{oid}_{i}")
                do_ship = st.form_submit_button("📦 Mark Shipped", type="primary")

            if do_ship and client:
                if tracking.strip():
                    try:
                        update_order(client, oid, {
                            "TrackingNum": tracking.strip(),
                            "ETA": eta.strip(),
                            "Status": "shipped",
                        })
                        st.toast(f"{part} marked shipped!")
                        st.rerun()
                    except Exception as e:
                        st.error(f"Failed: {e}")
                else:
                    st.warning("Enter a tracking number first.")
            st.divider()

# ============================================================
# B. IN TRANSIT — read-only; receiving is a Receipt entry
# ============================================================
with st.expander(f"📥 **In Transit** ({len(in_transit)})", expanded=False):
    if not in_transit:
        st.success("Nothing in transit!")
    else:
        # The status-only "Mark Received" button left on 28 Aug 2026: it
        # closed an order with no receive line, no movement and no count, so
        # Parts Short kept chasing goods already on the shelf. Receiving is
        # ONE entry now — Process Order's Receipt event books the goods in
        # and closes the order in the same submit.
        st.caption("Arrived? Record it on **Process Order → 📜 Add history "
                   "entry → Event: Receipt** — that books the goods in and "
                   "marks the order delivered.")
        for i, o in enumerate(in_transit[:20]):
            part = o.get("PartName", "N/A")
            mcode = (o.get("PartID") or "").strip()
            tracking = (o.get("TrackingNum") or "").strip()
            eta = o.get("ETA", "")
            priority_icon = "🔴" if o.get("Priority") == "URGENT" else "🟢"

            c1, c2 = st.columns([3, 2])
            with c1:
                st.markdown(_ptag(o) + f"{priority_icon} **{part}**" + (f" | `{mcode}`" if mcode else ""))
                if tracking:
                    st.caption(f"Tracking: {tracking}")
            with c2:
                st.caption(f"ETA: {eta or 'TBC'} | To: {o.get('Recipient', '')}")
            st.divider()

# ============================================================
# C. PARTS SHORT — from the project tracker (read-only)
# ============================================================
with st.expander(f"🔄 **Parts Short — project tracker** ({len(open_deliveries)})", expanded=False):
    if not open_deliveries:
        st.success("Every tracked part has received what was ordered.")
    else:
        st.caption("Parts where the tracker's received quantity is below the ordered "
                   "quantity. Maintained in the project's Sheet — read-only here.")
        for _pname, _rows in _short_by_project:
            if _wide:
                st.markdown("**%s** — %d" % (_pname, len(_rows)))
            for r in _rows[:20]:
                ordered = to_int(r.get("qty_ordered", ""))
                received = to_int(r.get("qty_received", ""))
                st.markdown("**%s** — %s | short %d of %d | → %s | held by %s"
                            % (r.get("mcode", "?"), r.get("part_name", ""),
                               ordered - received, ordered,
                               r.get("location") or "location TBC",
                               r.get("holder") or "unassigned"))

# ============================================================
# D. RECENT MOVEMENTS — from the project tracker (read-only)
# ============================================================
with st.expander(f"🚚 **Recent Movements** ({len(movements)})", expanded=False):
    if not movements:
        st.info("No Movement Log rows for this project.")
    else:
        for _pname, _rows in _moves_by_project:
            if _wide:
                st.markdown("**%s** — %d" % (_pname, len(_rows)))
            for m in list(reversed(_rows))[:15]:
                st.markdown("**%s** — %s | %s → %s | %s"
                            % (m.get("date", "?"), m.get("item", ""),
                               m.get("from") or "?", m.get("to") or "?",
                               m.get("stage") or ""))

# ============================================================
# E. RECENTLY DELIVERED ORDERS
# ============================================================
with st.expander(f"✅ **Recently Delivered** ({len(recently_delivered)})", expanded=False):
    if not recently_delivered:
        st.info("No delivered orders yet.")
    else:
        for o in recently_delivered[:10]:
            st.markdown(_ptag(o) + "**%s** | `%s` | To: %s | %s"
                        % (o.get("PartName", "N/A"), o.get("PartID", "") or "no M-code",
                           o.get("Recipient", ""), o.get("TrackingNum", "")))

# ============================================================
# F. PARTS PIPELINE — one status vocabulary across BOM + tracker
# ============================================================
# Buckets come from parts_model.STATUS_MAP: every spelling the sheets use
# ("Finished", "Recieved", "DeliveredToVendor"…) lands in one ordered
# pipeline. Unknown spellings pass through labelled, never hidden.
_active_project, _ = project_registry.active()
_pipeline_parts = parts_model.unified_parts(_active_project) if _active_project else []
if _pipeline_parts:
    st.markdown("---")
    st.markdown("### Parts pipeline — %s" % _active_project)
    buckets = {}
    for p in _pipeline_parts:
        buckets.setdefault(p.get("status_norm") or "(no status)", []).append(p)
    ordered_keys = ([k for k in parts_model.STATUS_ORDER if k in buckets]
                    + sorted(k for k in buckets if k not in parts_model.STATUS_ORDER))
    mcols = st.columns(len(ordered_keys)) if ordered_keys else []
    for col, key in zip(mcols, ordered_keys):
        col.metric(key, len(buckets[key]))
    for key in ordered_keys:
        with st.expander("%s (%d)" % (key, len(buckets[key]))):
            for p in buckets[key]:
                bits = ["**%s** — %s" % (p["mcode"], p.get("part_name", ""))]
                if p.get("holder"):
                    bits.append("held by %s" % p["holder"])
                if p.get("tracker_location"):
                    bits.append(p["tracker_location"])
                if p.get("stale_cad"):
                    bits.append("⚠️ ordered CAD ≠ latest")
                st.markdown(" · ".join(bits))
