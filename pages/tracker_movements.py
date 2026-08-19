"""Movements — the project's Movement Log, newest first and searchable."""
import pandas as pd
import streamlit as st


import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from utils.auth import require_auth
from utils import parts_tracker, project_colors, project_registry
from utils.ui import (movement_header, render_movement, require_project,
                      table_height)

require_auth()

st.title("🚚 Movements")

# --- Controls: search, stage, refresh — all on one line ---
# The project comes from the sidebar switcher — one choice for the whole app.
require_project()
active_name, active_sheet = project_registry.active()
moves = parts_tracker.fetch_movements()

# Short labels (no wrapping) + bottom alignment keeps the row on one baseline.
c1, c3, c4 = st.columns([3, 1.5, 1.3], vertical_alignment="bottom")
with c1:
    query = st.text_input("🔍 Search", placeholder="part, person, tracking number, issue…")
with c3:
    stages = st.multiselect("Stage", sorted({m.get("stage", "") for m in moves if m.get("stage")}))
with c4:
    if st.button("🔄 Refresh", use_container_width=True):
        parts_tracker.refresh()
        st.rerun()

st.markdown("**Project** — %s"
            % project_colors.badge_html(active_name,
                                        "Source: %s" % parts_tracker.source_label(),
                                        sheet_id=active_sheet),
            unsafe_allow_html=True)

if not moves:
    st.info("No Movement Log rows for this project.")
    st.stop()

view = moves
if query:
    q = query.lower()
    view = [m for m in view if q in " ".join(str(v) for v in m.values()).lower()]
if stages:
    view = [m for m in view if m.get("stage") in stages]

st.markdown(f"**{len(view)} movements** shown")

newest_first = list(reversed(view))

tab_table, tab_notes = st.tabs(["📋 Table View (%d)" % len(view),
                                "🗒️ Full Notes (%d)" % len(view)])

with tab_table:
    st.caption("Tick the box at the left edge of a row to open its full entry "
               "below (and pre-open it in Full Notes).")
    event = st.dataframe(
        pd.DataFrame([{
            "Date": m.get("date", ""),
            "Item / Build": m.get("item", ""),
            "Qty": m.get("qty", ""),
            "From": m.get("from", ""),
            "To": m.get("to", ""),
            "Stage": m.get("stage", ""),
            "Notes": m.get("notes", ""),
        } for m in newest_first]),
        height=table_height(len(newest_first)), hide_index=True,
        on_select="rerun", selection_mode="single-row", key="movements_table",
    )

    # Streamlit's table has no double-click event, and clicking a cell selects
    # the *cell*, not the row — row selection only fires from the checkbox in
    # the left-hand column (it appears on hover). Click-anywhere would need a
    # third-party grid component; see TODO.md.
    try:
        picked_rows = event["selection"]["rows"]
    except (TypeError, KeyError):
        picked_rows = []
    selected = picked_rows[0] if picked_rows else None

    if selected is not None:
        row = newest_first[selected]
        st.markdown("---")
        st.markdown(movement_header(row))
        render_movement(row)

with tab_notes:
    st.caption("The same rows. A table clips Notes to one line, and the notes are "
               "where the tracking numbers, QC verdicts and blockers actually live "
               "— here they are in full, as written in the sheet.")
    # Also renders any column added to the Movement Log that the fixed-column
    # table would not show.
    # A row picked in the table is already open here when you switch tabs.
    for i, m in enumerate(newest_first):
        with st.expander(movement_header(m), expanded=(i == selected)):
            render_movement(m)
