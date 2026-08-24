"""Submit Order - engineer-facing form to submit a new mech part order."""
from datetime import datetime

import streamlit as st

from utils.auth import require_auth
from utils import (agent_entry, parts_tracker, project_colors,
                   project_registry, tracker_writer)
from utils.google_client import get_gspread_client
from utils.orders_store import create_order
from utils.drive_handler import upload_file
from utils.ui import require_single_project
from config import PROCESS_OPTIONS

# Registering a project lives on the admin **Projects** page — it is setup work
# done once per project, not part of raising an order.

user = require_auth()


def _option_index(options, value, default=0):
    """Return the option index for saved reorder values, falling back safely."""
    try:
        return options.index(value)
    except ValueError:
        return default


def _manual_notes_from_notes(notes: str) -> str:
    """Keep only the engineer-written note section from a stored order note."""
    if "\n---\n" not in notes:
        return notes.strip()
    return notes.split("\n---\n", 1)[1].strip()


st.title("🔩 Submit New Mech Order")
st.markdown(f"Submitting as: **{user['name']}** ({user['email']})")

# Keep reorder data across Streamlit reruns. Do not pop it here: changing any
# widget reruns the page, and a one-shot pop resets the rest of the form back
# to blank/default values.
reorder = st.session_state.get("reorder_data", {})
if reorder:
    st.success(f"Reordering: **{reorder.get('part_name', '')}** — review and update specs below.")
    if reorder.get("source_order_id"):
        st.caption(f"Copied from previous order `{reorder['source_order_id']}`.")
    if st.button("Start blank order instead", key="clear_reorder_draft"):
        st.session_state.pop("reorder_data", None)
        st.rerun()

# Ordering a part that already exists in the project tracker: its identity
# comes from there rather than being retyped (and mistyped).
part_prefill = st.session_state.get("part_prefill", {})
if part_prefill:
    st.info("Ordering tracked part **%s — %s**. Details prefilled from the "
            "project tracker%s."
            % (part_prefill.get("m_code", "?"), part_prefill.get("part_name", ""),
               " and BOM" if part_prefill.get("drive_file_link")
               or part_prefill.get("tolerances") else ""))
    if st.button("Clear part link", key="clear_part_prefill"):
        st.session_state.pop("part_prefill", None)
        st.rerun()
    reorder = dict(reorder)
    for key, value in part_prefill.items():
        if value and not reorder.get(key):
            reorder[key] = value

# An Agent Entry paste prefills exactly like a reorder does — same dict, so
# every field below stays a normal widget with a normal default and there is
# no second way for a value to reach the form.
agent_prefill = st.session_state.get("agent_prefill", {})
if agent_prefill:
    st.success("Filled from **Agent Entry** — check every field, then submit.")
    if st.button("Clear the pasted values", key="clear_agent_prefill"):
        st.session_state.pop("agent_prefill", None)
        st.rerun()
    reorder = dict(reorder)
    reorder.update({k: v for k, v in agent_prefill.items() if v != ""})
    _gaps = [f for f in agent_entry.REQUIRED if not reorder.get(f)]
    if _gaps:
        st.warning("Still needed before this can be submitted: %s."
                   % ", ".join(g.replace("_", " ") for g in _gaps))

previous_drive_link = reorder.get("drive_file_link", "").strip()
default_manual_notes = _manual_notes_from_notes(reorder.get("notes", ""))

# ============================================================
# Project — which sheet this order is written to
# ============================================================
st.subheader("1. Project")
require_single_project("Submit Order")
_active_name, _active_sheet = project_registry.active()

# Stated, not chosen: the project is picked once in the sidebar. An order going
# to the wrong project is expensive to unpick, so this says plainly where this
# one lands rather than offering a fourth place to change it.
st.markdown("Order goes to %s" % project_colors.badge_html(_active_name),
            unsafe_allow_html=True)
st.caption("Change project in the sidebar.")

# The two files this order touches, each openable. A bare sheet id told nobody
# which file it was; the address is also what people paste when they hand the
# sheet to someone else. The main record is deliberately NOT linked — it is the
# app's own database, not a file to browse.
_active_sheets = project_registry.sheets(_active_name)
_not_linked = "_not linked_"
st.caption(
    "**Project record** (orders + history) — %s  \n"
    "**BOM** (what can be ordered) — %s"
    % (project_registry.sheet_link(_active_sheets["tracker"]) or _not_linked,
       project_registry.sheet_link(_active_sheets["bom"]) or _not_linked))

# The vocabulary already in use on the sheet, so locations and holders stay
# consistent enough for the rollups to add up. Read once, here: the form's
# dropdowns AND Agent Entry are checked against the same lists, so a pasted
# order cannot name a holder the form itself would not offer.
_known_locations, _known_holders = set(), set()
for _row in parts_tracker.fetch_overview():
    if _row.get("location"):
        _known_locations.add(_row["location"])
    if _row.get("holder"):
        _known_holders.add(_row["holder"])
_location_options = sorted(_known_locations) or ["UK office"]
_default_from = ("UK office" if "UK office" in _location_options
                 else _location_options[0])
_holder_options = ["-- Select --"] + sorted(_known_holders)

from utils.user_store import fetch_allowed_users
all_users = fetch_allowed_users()
reviewer_names = sorted({info["name"] for email, info in all_users.items()
                         if info["name"] != user["name"]})
reviewer_options = ["-- Select --"] + reviewer_names

# Agent Entry — the whole order as text, for the same reason Order from BOM
# has one: a form of sixteen controls is slow to drive and hard to audit,
# while a pasted list is neither. It fills the form and stops; the review and
# the Submit button below stay the only way an order is actually raised.
with st.expander("🤖 Agent Entry — paste the whole order as fields"):
    st.caption("One `field: value` per line — `part name`, `process`, "
               "`m-code`, `version`, `material`, `finish`, `tolerances`, "
               "`qty`, `priority`, `inspection`, `recipient`, `reviewer`, "
               "`ordered from`, `holder`, `location`, `receipt note`, "
               "`notes`. `#` starts a comment. Anything the form offers as a "
               "dropdown is checked against it.")
    # A FORM, deliberately: Streamlit commits a text area only on blur or
    # Ctrl-Enter, so a plain button beside one fires with the text the server
    # had BEFORE the click. A form submits the text and the click together.
    with st.form("submit_agent_form"):
        _agent_text = st.text_area(
            "Order fields", key="submit_agent", label_visibility="collapsed",
            height=200,
            placeholder=chr(10).join([
                "part name: EZ1 Housing revC", "process: CNC",
                "m-code: M107", "version: 2.1.1", "material: Al 6061",
                "qty: 120", "recipient: Send all to the UK office",
                "reviewer: Ryan Wong"]))
        _agent_apply = st.form_submit_button("Fill the form below")
if _agent_apply:
    _values, _errors, _missing = agent_entry.parse_fields(
        _agent_text, processes=PROCESS_OPTIONS, reviewers=reviewer_names,
        locations=_location_options, holders=sorted(_known_holders))
    for _problem in _errors:
        st.error(_problem)
    if _errors:
        # All or nothing: half a pasted order, silently, is worse than none.
        # No rerun either — a rerun would wipe the errors off the screen.
        st.info("Nothing was filled. Fix the lines above and paste again.")
    elif not _values:
        st.info("Nothing to fill — the box is empty.")
    else:
        st.session_state["agent_prefill"] = _values
        st.rerun()

# ============================================================
# Part + Process
# ============================================================
st.subheader("2. Basic Info")
col_a, col_b = st.columns(2)
with col_a:
    part_name = st.text_input("Part Name *", value=reorder.get("part_name", ""),
                              placeholder="e.g. EZ1_Housing_revC")
with col_b:
    process = st.selectbox("Manufacturing Process *", PROCESS_OPTIONS,
                           index=_option_index(PROCESS_OPTIONS, reorder.get("process", "")),
                           help="How the part should be made")

col_id1, col_id2 = st.columns(2)
with col_id1:
    m_code = st.text_input("M-code (BOM part number)", value=reorder.get("m_code", ""),
                           placeholder="e.g. M107",
                           help="Same numbering as the project BOM / parts tracker. "
                                "Use a suffix for variants (e.g. M101a).")
with col_id2:
    version = st.text_input("Version", value=reorder.get("version", ""),
                            placeholder="e.g. 2.1.1",
                            help="Major(CAD change).Minor(tolerance/tooling).Batch — "
                                 "the NPD convention, e.g. 2.1.1")

# ============================================================
# Specifications
# ============================================================
st.subheader("3. Part Specifications")
col1, col2 = st.columns(2)
with col1:
    material = st.text_input("Material *", value=reorder.get("material", ""),
                             placeholder="e.g. Al 6061 / ABS / PLA / SUS304 / Silicone Shore A30")
with col2:
    finish = st.text_input("Surface Finish", value=reorder.get("finish", ""),
                           placeholder="e.g. Anodized black / Bead blast / As machined")

tolerances = st.text_input(
    "Critical Dimensions / Tolerances (optional)",
    value=reorder.get("tolerances", ""),
    placeholder="e.g. Ø5 H7 bore, ±0.05 on mating face — or 'see drawing'",
    help="Anything the vendor must pay attention to. Put details in the drawing where possible.")

# ============================================================
# Quantity & Priority
# ============================================================
st.subheader("4. Order Details")
col3, col4 = st.columns(2)
with col3:
    reorder_qty = int(reorder.get("quantity", 1) or 1)
    quantity = st.number_input("Quantity", min_value=1, max_value=100000, value=reorder_qty)
    pri_options = ["Normal", "URGENT"]
    pri_idx = pri_options.index(reorder["priority"]) if reorder.get("priority") in pri_options else 0
    priority = st.selectbox("Priority", pri_options, index=pri_idx)
with col4:
    inspection_options = ["No", "Yes"]
    inspection = st.selectbox("Dimension check by engineer on arrival?", inspection_options,
                              index=_option_index(inspection_options, reorder.get("inspection", "No")))
    recipient = st.text_input("Recipient *", value=reorder.get("recipient", ""),
                              placeholder="e.g. Send all to the UK office")

reviewer = st.selectbox("Reviewer (second engineer who can answer vendor questions) *", reviewer_options,
                        index=_option_index(reviewer_options, reorder.get("reviewer", "-- Select --")),
                        help="Pick a colleague who reviewed/will help review this design.")

# ============================================================
# Tracker record — the two rows this order becomes on the part's tab
# ============================================================
st.subheader("5. Tracker Record")
st.caption("Submitting writes **two rows** to the part's tab: an origin row "
           "(you, where you ordered from, note `ordered by`) and a receipt row "
           "(who receives it, where it goes). Needs an M-code with a matching "
           "tab in the project's sheet.")

tr1, tr2 = st.columns(2)
with tr1:
    # Same control shape as the others, but locked: the origin holder is
    # whoever is signed in, never a free choice.
    st.text_input("Ordered by", value=user["name"], disabled=True,
                  help="Taken from your account. The first holder on the part's "
                       "tab is whoever raises the order.")
    ordered_from = st.selectbox(
        "Ordered from", _location_options,
        index=_option_index(_location_options,
                            reorder.get("ordered_from", _default_from),
                            _location_options.index(_default_from)),
        help="Where the order originates — your location.")
with tr2:
    recipient_holder = st.selectbox(
        "Receiving holder", _holder_options,
        index=_option_index(_holder_options,
                            reorder.get("recipient_holder", "-- Select --")),
        help="Who takes delivery. They become the part's current holder.")
    recipient_location = st.selectbox(
        "Receiving location", _location_options,
        index=_option_index(_location_options,
                            reorder.get("recipient_location", "")),
        help="Where the parts are going.")
receipt_note = st.text_input(
    "Receipt note", value=reorder.get("receipt_note", ""),
    placeholder="e.g. shipped on 7/16 SF1572075868095",
    help="Goes on the second row. Left blank if you don't know yet.")

# ============================================================
# Notes
# ============================================================
st.subheader("6. Notes")
notes = st.text_area("Notes (optional)",
                     value=default_manual_notes,
                     placeholder="Assembly context, thread inserts, color, vendor preference...",
                     height=100)

# Show spec summary
with st.expander("Spec Summary (auto-generated)"):
    if m_code.strip() or version.strip():
        st.markdown(f"**Part ID:** {m_code.strip() or '?'} V{version.strip() or '?'}")
    st.markdown(f"**Process:** {process} | **Material:** {material or 'N/A'} | **Finish:** {finish or 'N/A'}")
    st.markdown(f"**Qty:** {quantity} | **Priority:** {priority} | **Inspection:** {inspection}")
    if tolerances.strip():
        st.markdown(f"**Tolerances:** {tolerances.strip()}")

# ============================================================
# File Upload
# ============================================================
st.subheader("7. Upload CAD / Drawing Files")
if previous_drive_link:
    st.info("This reorder will reuse the previous order's Drive folder unless you upload a new file.")
    st.markdown(f"📁 [Previous order Drive folder]({previous_drive_link})")
uploaded_file = st.file_uploader(
    "Upload file (zip preferred for multiple files)",
    type=["zip", "rar", "7z", "step", "stp", "stl", "3mf", "iges", "igs", "dxf", "dwg", "pdf"],
    help="Include STEP files and PDF drawings; STL/3MF for 3D prints",
)

# ============================================================
# Submit
# ============================================================
st.markdown("---")
if st.button("Submit Order", type="primary", key="submit_order_btn"):
    if not part_name.strip():
        st.error("Part Name is required!")
        st.stop()
    if not material.strip():
        st.error("Material is required!")
        st.stop()
    if not recipient.strip():
        st.error("Recipient is required!")
        st.stop()
    if reviewer == "-- Select --":
        st.error("Reviewer is required! Pick a colleague who can answer vendor questions.")
        st.stop()

    client = get_gspread_client()
    if not client:
        st.error("Cannot connect to Google Sheets. Contact admin.")
        st.stop()

    # Reorders reuse the previous Drive folder unless a new file is uploaded.
    drive_link = previous_drive_link
    if uploaded_file:
        with st.spinner("Uploading file to Google Drive..."):
            try:
                file_bytes = uploaded_file.read()
                drive_link = upload_file(
                    file_bytes=file_bytes,
                    filename=uploaded_file.name,
                    part_name=part_name.strip(),
                )
                st.success(f"File uploaded: {uploaded_file.name}")
            except Exception as e:
                st.error(f"File upload failed: {e}")

    # Build full notes with tolerances block
    spec_parts = []
    if tolerances.strip():
        spec_parts.append(f"Tolerances: {tolerances.strip()}")
    full_notes = " | ".join(spec_parts)
    if notes.strip():
        full_notes = f"{full_notes}\n---\n{notes.strip()}" if full_notes else notes.strip()

    # Create order
    with st.spinner("Creating order..."):
        try:
            order_data = {
                "part_name": part_name.strip(),
                "m_code": m_code.strip(),
                "version": version.strip(),
                "process": process,
                "material": material.strip(),
                "finish": finish.strip(),
                "quantity": quantity,
                "priority": priority,
                "inspection": inspection,
                "recipient": recipient.strip(),
                "notes": full_notes,
                "engineer_email": user["email"],
                "engineer_name": user["name"],
                "drive_file_link": drive_link,
                "reviewer": reviewer,
                "project": _active_name,
            }
            order_id = create_order(client, order_data)
            st.session_state.pop("reorder_data", None)
            st.session_state.pop("part_prefill", None)
            st.success(f"Order submitted! Order ID: **{order_id}**")

            # The tracker record: origin + receipt rows on the part's own tab.
            if m_code.strip():
                ok, message = tracker_writer.write_order(
                    m_code.strip(),
                    ordered_by=user["name"],
                    ordered_from=ordered_from,
                    recipient=(recipient_holder
                               if recipient_holder != "-- Select --" else ""),
                    recipient_location=recipient_location,
                    # The app's OrderID goes into the tracker's Order/Sample ID
                    # column — the id-level link between the central Orders row
                    # and the part-tab pair.
                    order_id=order_id,
                    version=version.strip(),
                    date=datetime.now().strftime("%Y-%m-%d"),
                    order_type=process,
                    vendor="",
                    qty_ordered=str(quantity),
                    receipt_note=receipt_note.strip(),
                )
                (st.success if ok else st.warning)(message)
            else:
                st.info("No M-code given, so nothing was written to the project "
                        "tracker — the order lives in the app's Orders tab only.")
            st.balloons()
            st.info("Go to **My Orders** to track progress.")
        except Exception as e:
            st.error(f"Failed to create order: {e}")
