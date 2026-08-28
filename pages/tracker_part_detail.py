"""Part Detail — one part's full version/order history, from its own tab."""
import pandas as pd
import streamlit as st


import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from datetime import datetime

from utils.auth import require_auth
from utils import (bom_sheet, holders_store, parts_model, parts_tracker,
                   project_colors, project_registry, stock_store,
                   tracker_writer)
from utils.orders_store import fetch_orders_for_part
from utils.tracker_parse import (is_selected, order_origin, order_recipient,
                                 to_int)
from utils.ui import (movement_header, render_movement, require_single_project,
                      table_height)

user = require_auth()

st.title("🔍 Part Detail")

# A table cell can be a hyperlink but not a button, so the All Orders Table
# View links here by ADDRESS: ?project=...&part=... (a link opens a fresh
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
st.markdown("---")
st.markdown("### History (%d rows, oldest first)" % len(history))
if history:
    # "Holder" is who has the parts at that point in the chain; "Ordered by"
    # is who raised the order — the first row of an order is the orderer, the
    # delivery row is the recipient, who becomes the current holder.
    hist_df = pd.DataFrame([{
        "Date": h.get("date", ""),
        "Version": h.get("version", ""),
        "Order / Sample": h.get("order_id", ""),
        "Type": h.get("type", ""),
        "Vendor / Source": h.get("vendor", ""),
        "Ordered": h.get("qty_ordered", ""),
        "Received": h.get("qty_received", ""),
        "Status": h.get("status", ""),
        "Location": h.get("location", ""),
        "Holder": h.get("holder", ""),
        "Ordered by": order_origin(h),
        "Selected": "✅" if is_selected(h) else "",
    } for h in history])
    if not hist_df["Ordered by"].str.strip().any():
        hist_df = hist_df.drop(columns=["Ordered by"])  # nothing recorded yet
    st.dataframe(hist_df, use_container_width=True, hide_index=True,
                 height=table_height(len(hist_df)))

    with st.expander("Notes on each row"):
        for h in history:
            if h.get("notes"):
                st.markdown("**%s — %s**" % (h.get("date") or "?",
                                             h.get("order_id") or h.get("version") or ""))
                st.text(h["notes"])  # verbatim: the note is the record
else:
    st.caption("No history rows on this tab yet.")

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

# --- Process actions: history keeps growing through the app ---
# Each action appends ONE row to this part's tab, and a handover also goes
# through `stock_store.record_movement` so the movement log and the count get
# it too. Append-only, like everything the app writes — no row is ever edited
# or deleted.
#
# Holders come from the DIRECTORY, not from whatever is already on the
# Overview (19 Aug). Reading the existing holders back made every hand-typed
# spelling a valid option, and the free-text "someone not listed" box wrote
# names the count could never match — the exact drift the directory exists to
# stop.
st.markdown("---")
st.markdown("### Process — report where it is, or hand it over")

_locations = sorted({r.get("location", "") for r in parts_tracker.fetch_overview()
                     if r.get("location")}) or ["UK office"]
_holders = holders_store.names()
_today = datetime.now().strftime("%Y-%m-%d")

if not _holders:
    st.info("The Holders directory is empty — add holders on the main "
            "record's Holders tab before reporting a location or a handover.")

act_report, act_handover = st.tabs(["📍 Report location", "🤝 Hand over"])

with act_report:
    st.caption("\"This part is now at X, held by Y.\" Appends one history row "
               "and one movement row; nothing is overwritten.")
    with st.form("report_location_form"):
        r1, r2 = st.columns(2)
        with r1:
            rep_location = st.selectbox("Location now", _locations, key="rep_loc")
            rep_qty = st.text_input("Qty there (optional)", key="rep_qty",
                                    placeholder="e.g. 120")
        with r2:
            rep_holder = st.selectbox("Held by", [user["name"]] +
                                      [h for h in _holders if h != user["name"]],
                                      key="rep_holder")
            rep_note = st.text_input("Note (optional)", key="rep_note",
                                     placeholder="e.g. counted after QC, 2 NG set aside")
        if st.form_submit_button("Record location", type="primary"):
            ok1, msg1 = tracker_writer.append_event(
                mcode, holder=rep_holder, location=rep_location, date=_today,
                qty=rep_qty.strip(), note=rep_note.strip(),
                marker=tracker_writer.LOCATION_NOTE,
                version=(selected or {}).get("version", ""))
            (st.success if ok1 else st.warning)(msg1)
            if ok1:
                # A location report is a statement, not a move: it says where
                # something already is. It goes on the part's history and
                # nowhere else — counting it would invent a movement that
                # never happened.
                parts_tracker.refresh()
                st.caption("Recorded on the part's history. A location report "
                           "does not change the stock count — use **Hand "
                           "over** when goods actually move.")

with act_handover:
    st.caption("\"I'm handing N pcs to Z at W.\" The new holder becomes the "
               "part's current holder — same shape as an order's receipt row.")
    with st.form("handover_form"):
        h1, h2 = st.columns(2)
        with h1:
            st.text_input("Handing over", value=user["name"], disabled=True,
                          help="Taken from your account — the person handing over.")
            new_holder = st.selectbox(
                "To (new holder)",
                [h for h in _holders if h != user["name"]] or ["—"],
                key="ho_holder",
                help="From the Holders directory. A name that is not in it "
                     "cannot be counted, so add it there first.")
        with h2:
            ho_location = st.selectbox("At location", _locations, key="ho_loc")
            ho_qty = st.text_input("Qty handed over", key="ho_qty",
                                   placeholder="e.g. 12")
            ho_note = st.text_input("Note (optional)", key="ho_note",
                                    placeholder="e.g. for EVT assembly")
        if st.form_submit_button("Record handover", type="primary"):
            target = new_holder if new_holder != "—" else ""
            if not target:
                st.error("Who receives the parts? Pick a holder from the "
                         "directory.")
            else:
                note_txt = user["name"] + (" — %s" % ho_note.strip() if ho_note.strip() else "")
                ok1, msg1 = tracker_writer.append_event(
                    mcode, holder=target, location=ho_location, date=_today,
                    qty=ho_qty.strip(), note=note_txt,
                    marker=tracker_writer.HANDOVER_NOTE,
                    version=(selected or {}).get("version", ""))
                (st.success if ok1 else st.warning)(msg1)
                if ok1:
                    parts_tracker.refresh()
                    # Same writer as Process Order's movement form, so a
                    # handover reaches the log and the count identically
                    # wherever it was entered.
                    qty = to_int(ho_qty)
                    if qty > 0:
                        res = stock_store.record_movement(
                            mcode, _project, qty, target, user["name"],
                            event="Movement",
                            description=meta.get("part_name", ""),
                            notes=note_txt, date=_today,
                            logged_by=user.get("email", "") or user["name"])
                        (st.success if res.get("ok") else st.warning)(
                            "Logged and counted: %s → %s, %d."
                            % (user["name"], target, qty) if res.get("ok")
                            else "Recorded on the history, but the movement "
                                 "log and count were not updated: %s"
                                 % res.get("problem", "unknown error"))
                    else:
                        st.caption("No quantity given, so nothing was counted "
                                   "— fill in Qty handed over to have this "
                                   "reach the stock count.")

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
            "Logged by": m.get("logged_by", ""), "Status": m.get("status", ""),
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
