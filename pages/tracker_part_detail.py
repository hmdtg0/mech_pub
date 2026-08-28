"""Part Detail — one part's full version/order history, from its own tab."""
import streamlit as st


import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from utils.auth import is_admin, require_auth
from utils import (history_entry, parts_tracker, project_colors,
                   project_registry)
from utils.tracker_parse import is_selected, order_origin, order_recipient
from utils.ui import require_single_project

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

# The per-part app-orders table left on 28 Aug (Hamid: "potential
# removal of this table", validated): it repeated All Orders scoped to
# one part and was the LAST surface showing the raw, lagging Status cell.
# The entry section below names the part's open order; the workflow view
# is All Orders.
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

# The per-part "Shipments (BOM movement log)" and "Movements matched on
# M-code / part name" sections left on 28 Aug (validated before removal):
# the unified history table above IS the part's movement story now, the
# hand-kept Movement Log tab stays readable on the Movements page's second
# tab, and the BOM movement log still feeds Stock and the parts pipeline.
# The name-matched rows were guesses by their own caption.
