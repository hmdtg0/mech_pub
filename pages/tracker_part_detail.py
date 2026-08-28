"""Part Detail — one part's full version/order history, from its own tab."""
import pandas as pd
import streamlit as st


import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from utils.auth import is_admin, require_auth
from utils import (bom_sheet, history_entry, parts_model, parts_tracker,
                   project_colors, project_registry)
from utils import user_store
from utils.orders_store import fetch_orders_for_part
from utils.tracker_parse import is_selected, order_origin, order_recipient
from utils.ui import (movement_header, render_movement, require_single_project,
                      table_height)

user = require_auth()

st.title("🔍 Part Detail")

# A table cell can be a hyperlink but not a button, so every table's M-Code
# links here by ADDRESS: ?project=...&part=... (a link opens a fresh
# browser tab, and a fresh tab shares no session state). Consumed once and
# cleared — left in place, the URL would keep re-narrowing the scope and
# fight the sidebar switcher. Clearing after reading also makes the links
# bookmarkable without making them sticky.
_qp_part = st.query_params.get("part", "")
if _qp_part:
    _qp_project = st.query_params.get("project", "")
    if _qp_project in project_registry.all_projects():
        project_registry.set_scope(_qp_project)
    st.session_state["tracker_part"] = _qp_part
    st.query_params.clear()

require_single_project("Part Detail")

# Which project's tracker this part comes from — M-codes are only unique
# within a project, so the page never shows a part without naming it.
_project, _project_sheet = project_registry.active()
# Filled after the reads below, so its "read …" stamp is the current one.
project_line = st.empty()

codes = parts_tracker.part_tabs()
if not codes:
    st.info("No per-part tabs found for this project's sheet.")
    st.stop()

# Overview gives us nicer labels (M107 — Glass fixing) than the tab name alone.
names = {r.get("mcode", ""): r.get("part_name", "") for r in parts_tracker.fetch_overview()}
labels = {"%s — %s" % (c, names.get(c, "")) if names.get(c) else c: c for c in codes}

current = st.session_state.get("tracker_part", codes[0])
current_label = next((lbl for lbl, c in labels.items() if c == current), list(labels)[0])
choice = st.selectbox("Part", list(labels), index=list(labels).index(current_label))
mcode = labels[choice]
st.session_state["tracker_part"] = mcode

part = parts_tracker.fetch_part(mcode)
meta = part.get("meta", {})
history = part.get("history", [])

with project_line:
    st.markdown("**Project** — %s"
                % project_colors.badge_html(
                    _project, "Source: %s" % parts_tracker.source_label(),
                    sheet_id=_project_sheet),
                unsafe_allow_html=True)

st.subheader("%s — %s" % (meta.get("mcode", mcode), meta.get("part_name", "")))
i1, i2, i3 = st.columns(3)
i1.markdown("**Category:** %s" % (meta.get("category") or "—"))
i2.markdown("**Material:** %s" % (meta.get("material") or "—"))
i3.markdown("**Default owner:** %s" % (meta.get("defaultowner") or "—"))
if meta.get("spec"):
    st.markdown("**Spec:** %s" % meta["spec"])

# --- The version currently going into the build ---
selected = next((h for h in history if is_selected(h)), None)
st.markdown("---")
if selected:
    st.markdown("### Selected for MP")
    s1, s2, s3, s4 = st.columns(4)
    s1.metric("Version", selected.get("version") or "—")
    s2.metric("Ordered", selected.get("qty_ordered") or "—")
    s3.metric("Received", selected.get("qty_received") or "—")
    s4.metric("Status", selected.get("status") or "—")
    d1, d2 = st.columns(2)
    d1.markdown("**Order / Sample ID:** %s" % (selected.get("order_id") or "—"))
    d1.markdown("**Vendor / Source:** %s" % (selected.get("vendor") or "—"))
    d2.markdown("**Location:** %s" % (selected.get("location") or "—"))
    d2.markdown("**Holder:** %s" % (selected.get("holder") or "—"))
    # The order's origin: who raised it, and who it was raised for. The holder
    # above is wherever the parts have got to, which is usually the recipient.
    _origin = order_origin(selected)
    _for = order_recipient(selected)
    if _origin:
        d1.markdown("**Ordered by:** %s%s" % (_origin, " (for %s)" % _for if _for else ""))
    if selected.get("notes"):
        st.info(selected["notes"])
else:
    st.warning("No row on this part's tab is flagged **Selected for MP** — "
               "the Overview rollup will be blank for this part.")

# --- Every iteration this part has been through ---
# ONE table shape with Process Order since 28 Aug (Hamid: "I want to unify
# them") — both pages draw utils/history_entry's table and entry point.
st.markdown("---")
history_entry.history_table(mcode, _project_sheet)
if history:
    with st.expander("Notes on each row"):
        for h in history:
            if h.get("notes"):
                st.markdown("**%s — %s**" % (h.get("date") or "?",
                                             h.get("order_id") or h.get("version") or ""))
                st.text(h["notes"])  # verbatim: the note is the record

# --- Orders raised through this app for this part ---
st.markdown("---")
orders = fetch_orders_for_part(mcode, meta.get("part_name", ""), project=_project)
st.markdown("### Orders in this app (%d)" % len(orders))
if orders:
    st.dataframe(pd.DataFrame([{
        "Order ID": o.get("OrderID", ""),
        "Submitted": o.get("CreatedAt", ""),
        "Engineer": o.get("EngineerName", ""),
        "Version": o.get("Version", ""),
        "Qty": o.get("Quantity", ""),
        "Status": o.get("Status", ""),
        "Vendor": o.get("Vendor", ""),
        "ETA": o.get("ETA", ""),
        "Tracking #": o.get("TrackingNum", ""),
    } for o in orders]), use_container_width=True, hide_index=True,
        height=table_height(len(orders)))
else:
    st.caption("No orders raised here yet for this M-code. The tracker history "
               "above covers orders placed before/outside this app.")

if st.button("🔩 Order this part", type="primary"):
    st.session_state["part_prefill"] = {
        "part_name": meta.get("part_name", ""),
        "m_code": mcode,
        "version": (selected or {}).get("version", ""),
        "material": meta.get("material", ""),
    }
    st.switch_page("pages/ee_submit_order.py")

# --- 📜 Add history entry: THE entry point (Hamid, 28 Aug) ---------------
# The "Report location" and "Hand over" forms lived here until 28 Aug —
# parallel doors to the same writes, without the entry form's guards
# ("I want to unify them ... this will be our one and only entry point to
# update the orderes"). A location report is an Update whose From names
# the place; a handover is a Hand delivered with From/To and a quantity —
# both reach the ledger, the movement log and the count through the one
# shared form below (utils/history_entry, the exact section Process Order
# draws). Read-only roles still see everything above; only the roles that
# process orders can write.
st.markdown("---")
if is_admin(user) or user.get("role") in ("engineer", "logistics"):
    history_entry.render_entry(
        user, mcode, _project_sheet, _project, order=None,
        key_ns="pd_%s" % mcode,
        part_name=meta.get("part_name", ""),
        part_type=meta.get("category", ""),
        version=(selected or {}).get("version", ""))

# --- Structured shipments from the BOM sheet (exact M-code links) ---
_bom_id = project_registry.bom_sheet(_project)
if _bom_id:
    bom_moves = [m for m in bom_sheet.fetch_movement_log(_bom_id)
                 if parts_model.normalise_code(m.get("mcode", ""))
                 == parts_model.normalise_code(mcode)]
    st.markdown("---")
    st.markdown("### Shipments (BOM movement log, %d)" % len(bom_moves))
    if bom_moves:
        st.dataframe(pd.DataFrame([{
            "Version": m.get("version", ""), "Qty": m.get("qty", ""),
            "From": m.get("from", ""), "To": m.get("to", ""),
            "Shipped": m.get("date_shipped", ""), "Received": m.get("date_received", ""),
            "Logged by": user_store.name_of(m.get("logged_by", "")),
            "Status": m.get("status", ""),
            "Courier / Tracking": m.get("courier", ""), "Notes": m.get("notes", ""),
        } for m in bom_moves]), use_container_width=True, hide_index=True,
            height=table_height(len(bom_moves)))
    else:
        st.caption("No BOM movement-log row names this M-code yet.")

# --- Where it has physically been ---
st.markdown("---")
links = parts_tracker.movement_links(mcode, meta.get("part_name", ""))

st.markdown("### Movements matched on M-code (%d)" % len(links["by_code"]))
if links["by_code"]:
    for m in reversed(links["by_code"]):
        with st.expander(movement_header(m)):
            render_movement(m)
else:
    st.caption("No Movement Log row names this M-code.")

if links["by_name"]:
    st.markdown("### Possible movements — matched on part name (%d)"
                % len(links["by_name"]))
    st.caption("These rows contain every word of the part name, which is a "
               "guess, not a link the sheet records. Adding an M-code column "
               "to the Movement Log would make this exact.")
    for m in reversed(links["by_name"]):
        with st.expander(movement_header(m)):
            render_movement(m)
