"""Movements — the Movement Log, newest first and searchable.

One project or every project, from the sidebar scope. A movement log belongs
to a project record, so "all projects" cannot be one merged table without
inventing a shared ordering across four hand-kept sheets; instead each
project's log is drawn in turn — stacked in full by default, collapsible
under the second tab (Hamid, 21 Aug: "B as default, but keep the A as
another tab").
"""
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

# The project comes from the sidebar switcher — one choice for the whole app.
require_project()
active_name, active_sheet = project_registry.active()
_registry = project_registry.all_projects()
_wide = project_registry.is_all()
sources = list(_registry.items()) if _wide else [(active_name, active_sheet)]

logs = {}
for _name, _sid in sources:
    logs[_name] = parts_tracker.fetch_movements(_sid)
_every = [m for rows in logs.values() for m in rows]

# Short labels (no wrapping) + bottom alignment keeps the row on one baseline.
c1, c3, c4 = st.columns([3, 1.5, 1.3], vertical_alignment="bottom")
with c1:
    query = st.text_input("🔍 Search", placeholder="part, person, tracking number, issue…")
with c3:
    stages = st.multiselect("Stage",
                            sorted({m.get("stage", "") for m in _every
                                    if m.get("stage")}))
with c4:
    if st.button("🔄 Refresh", use_container_width=True):
        for _name, _sid in sources:
            parts_tracker.refresh(_sid)
        st.rerun()

if _wide:
    st.markdown("**Projects** — " + " ".join(
        project_colors.badge_html(name, sheet_id=sid) for name, sid in sources),
        unsafe_allow_html=True)
else:
    st.markdown("**Project** — %s"
                % project_colors.badge_html(active_name,
                                            "Source: %s" % parts_tracker.source_label(),
                                            sheet_id=active_sheet),
                unsafe_allow_html=True)

if not _every:
    st.info("No Movement Log rows for %s."
            % ("any project" if _wide else "this project"))
    st.stop()


def _visible(rows):
    """The rows this page's filters leave standing, newest first."""
    view = rows
    if query:
        q = query.lower()
        view = [m for m in view if q in " ".join(str(v) for v in m.values()).lower()]
    if stages:
        view = [m for m in view if m.get("stage") in stages]
    return list(reversed(view))


shown = {name: _visible(rows) for name, rows in logs.items()}
_total = sum(len(rows) for rows in shown.values())
st.markdown("**%d movements** shown" % _total)


def _frame(rows):
    return pd.DataFrame([{
        "Date": m.get("date", ""),
        "Item / Build": m.get("item", ""),
        "Qty": m.get("qty", ""),
        "From": m.get("from", ""),
        "To": m.get("to", ""),
        "Stage": m.get("stage", ""),
        "Notes": m.get("notes", ""),
    } for m in rows])


def _table(rows, key, selectable=True, logged=1):
    """One project's table. Returns the selected row index, or None.

    `logged` is how many rows the project has BEFORE filtering, so an empty
    block can say which kind of empty it is — a project with no movements at
    all reads differently from one whose rows the search just excluded.

    Row selection only fires from the checkbox in the left-hand column
    (it appears on hover): Streamlit's table has no double-click event, and
    clicking a cell selects the *cell*, not the row. Click-anywhere would
    need a third-party grid component; see TODO.md.
    """
    if not rows:
        st.caption("No Movement Log rows for this project." if not logged
                   else "Nothing here matches the current filters.")
        return None
    event = st.dataframe(
        _frame(rows), height=table_height(len(rows)), hide_index=True,
        on_select="rerun" if selectable else "ignore",
        selection_mode="single-row", key=key)
    if not selectable:
        return None
    try:
        picked = event["selection"]["rows"]
    except (TypeError, KeyError):
        picked = []
    return picked[0] if picked else None


def _notes(rows, opened=None):
    for i, m in enumerate(rows):
        with st.expander(movement_header(m), expanded=(i == opened)):
            render_movement(m)


if not _wide:
    # One project: exactly the page as it was, selection detail included.
    rows = shown[active_name]
    tab_table, tab_notes = st.tabs(["📋 Table View (%d)" % len(rows),
                                    "🗒️ Full Notes (%d)" % len(rows)])
    with tab_table:
        st.caption("Tick the box at the left edge of a row to open its full "
                   "entry below (and pre-open it in Full Notes).")
        selected = _table(rows, "movements_table")
        if selected is not None:
            st.markdown("---")
            st.markdown(movement_header(rows[selected]))
            render_movement(rows[selected])
    with tab_notes:
        st.caption("The same rows. A table clips Notes to one line, and the "
                   "notes are where the tracking numbers, QC verdicts and "
                   "blockers actually live — here they are in full, as "
                   "written in the sheet.")
        _notes(rows, selected)
else:
    # Every project: the same table drawn per project, twice over — stacked
    # in full, and collapsed under its own tab. Row-selection detail is left
    # out of both: it would need its own state per project per tab, and Full
    # Notes already carries every word of every row.
    tab_stacked, tab_by_project, tab_notes = st.tabs([
        "📋 Stacked (%d)" % _total,
        "🗂 By project (%d)" % len(sources),
        "🗒️ Full Notes (%d)" % _total,
    ])
    with tab_stacked:
        for name, _sid in sources:
            st.markdown("#### %s — %d" % (name, len(shown[name])))
            _table(shown[name], "moves_stacked_%s" % name, selectable=False,
                   logged=len(logs[name]))
    with tab_by_project:
        for name, _sid in sources:
            with st.expander("%s — %d movements" % (name, len(shown[name])),
                             expanded=(name == active_name)):
                _table(shown[name], "moves_grouped_%s" % name,
                       selectable=False, logged=len(logs[name]))
    with tab_notes:
        st.caption("Every row in full, as written in the sheet, project by "
                   "project.")
        for name, _sid in sources:
            st.markdown("#### %s — %d" % (name, len(shown[name])))
            _notes(shown[name])
