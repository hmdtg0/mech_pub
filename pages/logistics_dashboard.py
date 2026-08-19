"""Logistics Dashboard — the shipping/receiving view (Jimmy's page).

Same shape as the PCB tool's logistics dashboard: summary metrics, then
collapsible sections of actionable items. Adapted for mech orders — the
things being chased are vendor orders and parts in transit rather than PCBs
and components — and it also surfaces the project tracker's open deliveries
and movement log, read-only.
"""
from datetime import datetime

import streamlit as st


import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from utils.auth import require_auth
from utils.google_client import get_gspread_client
from utils.orders_store import fetch_all_orders, update_order
from utils import parts_model, parts_tracker, project_colors, project_registry
from utils.tracker_parse import to_int

user = require_auth()

st.title("📦 Logistics Dashboard")
st.markdown(f"Welcome, **{user['name']}**")
_active_name, _ = project_registry.active()
st.caption("Order queues below span **every project** (rows are tagged when "
           "more than one is live). *Recent Movements* and *Parts Short* "
           "follow the sidebar's project — currently **%s**." % _active_name)

client = get_gspread_client()
# Orders are central: one tab for every project, each row tagged Project.
orders = fetch_all_orders()
_multi_project = len({(o.get("Project") or "").strip() for o in orders
                      if (o.get("Project") or "").strip()}) > 1


def _ptag(o: dict) -> str:
    """Project token for an order line, when more than one project is live."""
    name = (o.get("Project") or "").strip()
    return "%s " % project_colors.tag(name) if name and _multi_project else ""

# --- Order categories ---
# Ordered but no tracking number yet -> waiting to leave the vendor.
awaiting_dispatch = [o for o in orders
                     if o.get("Status") == "ordered" and not (o.get("TrackingNum") or "").strip()]
# On the way -> waiting to be received.
in_transit = [o for o in orders
              if o.get("Status") == "shipped"
              or (o.get("Status") == "ordered" and (o.get("TrackingNum") or "").strip())]
recently_delivered = [o for o in orders if o.get("Status") == "delivered"]

# --- Tracker categories (read-only) ---
overview = parts_tracker.fetch_overview()
open_deliveries = [r for r in overview
                   if to_int(r.get("qty_ordered", "")) > to_int(r.get("qty_received", ""))]
movements = parts_tracker.fetch_movements()

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
# B. IN TRANSIT — mark received
# ============================================================
with st.expander(f"📥 **In Transit** ({len(in_transit)})", expanded=False):
    if not in_transit:
        st.success("Nothing in transit!")
    else:
        for i, o in enumerate(in_transit[:20]):
            oid = o.get("OrderID", "?")
            part = o.get("PartName", "N/A")
            mcode = (o.get("PartID") or "").strip()
            tracking = (o.get("TrackingNum") or "").strip()
            eta = o.get("ETA", "")
            priority_icon = "🔴" if o.get("Priority") == "URGENT" else "🟢"

            c1, c2, c3 = st.columns([4, 3, 2])
            with c1:
                st.markdown(_ptag(o) + f"{priority_icon} **{part}**" + (f" | `{mcode}`" if mcode else ""))
                if tracking:
                    st.caption(f"Tracking: {tracking}")
            with c2:
                st.caption(f"ETA: {eta or 'TBC'} | To: {o.get('Recipient', '')}")
            with c3:
                if st.button("✅ Mark Received", key=f"recv_{oid}_{i}") and client:
                    try:
                        note = (o.get("Notes") or "").strip()
                        today = datetime.now().strftime("%Y-%m-%d")
                        update_order(client, oid, {
                            "Status": "delivered",
                            "Notes": (note + f"\nReceived {today} by {user['name']}").strip(),
                        })
                        st.toast(f"{part} marked received!")
                        st.rerun()
                    except Exception as e:
                        st.error(f"Failed: {e}")
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
        for r in open_deliveries[:20]:
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
        for m in list(reversed(movements))[:15]:
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
