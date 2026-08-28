"""Movements — the log: every update to the orders, newest first.

Redefined 28 Aug 2026 (Hamid: "what goes to movement page is any update
happens to the orderes, it is like Logs"): the page shows the part-history
LEDGER itself — every event of every part in scope, raises and receipts
and QC notes included — not only the rows that moved goods. App-stamped
rows float to the top newest first (logged-at is the app's own clock);
migrated rows carry no stamp and follow in ledger order, part by part —
the dates on those are hand-typed and sorting on them would put the story
in an order nobody wrote. The record's hand-kept Movement Log tab stays
readable under its own tab: data is never hidden, it just stopped being
what this page means.
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from datetime import datetime

import streamlit as st

from utils.auth import require_auth
from utils import (overview_board, parts_tracker, project_colors,
                   project_registry, user_store)
from utils.tracker_parse import event_of
from utils.ui import (movement_header, native_table, part_url,
                      render_movement, require_project)

require_auth()

st.title("🚚 Movements")

# The project comes from the sidebar switcher — one choice for the whole app.
require_project()
active_name, active_sheet = project_registry.active()
_registry = project_registry.all_projects()
_wide = project_registry.is_all()
sources = list(_registry.items()) if _wide else [(active_name, active_sheet)]

# --- the log: every history row of every part in scope ----------------------
entries = []
for _name, _sid in sources:
    for _code, _part in parts_tracker.fetch_all_parts(_sid).items():
        for _idx, _row in enumerate(_part.get("history", [])):
            entries.append(dict(_row, _project=_name, _mcode=_code,
                                _idx=_idx))

c1, c2, c3 = st.columns([3, 1.5, 1.3], vertical_alignment="bottom")
with c1:
    query = st.text_input("🔍 Search",
                          placeholder="part, person, vendor, tracking, note…")
with c2:
    events = st.multiselect("Event",
                            sorted({event_of(e) for e in entries
                                    if event_of(e)}))
with c3:
    if st.button("🔄 Refresh", use_container_width=True):
        for _name, _sid in sources:
            parts_tracker.refresh(_sid)
        st.rerun()

if _wide:
    st.markdown("**Projects** — " + " ".join(
        project_colors.badge_html(name, sheet_id=sid)
        for name, sid in sources), unsafe_allow_html=True)
else:
    st.markdown("**Project** — %s"
                % project_colors.badge_html(
                    active_name, "Source: %s" % parts_tracker.source_label(),
                    sheet_id=active_sheet),
                unsafe_allow_html=True)

if not entries:
    st.info("No history anywhere yet for %s."
            % ("any project" if _wide else "this project"))
    st.stop()

view = entries
if query:
    _q = query.lower()
    view = [e for e in view
            if _q in " ".join(str(v) for v in e.values()).lower()]
if events:
    view = [e for e in view if event_of(e) in events]


def _stamp(row):
    """The app's own clock, where it stamped one. Hand-typed dates are not
    parsed — "~12 Jan", "24-30 Jul" — so unstamped rows keep ledger order."""
    try:
        return datetime.strptime(str(row.get("logged_at", "")).strip(),
                                 "%d %b %Y %H:%M")
    except ValueError:
        return None


_stamped = [e for e in view if _stamp(e)]
_stamped.sort(key=_stamp, reverse=True)
_rest = [e for e in view if not _stamp(e)]
_rest.sort(key=lambda e: (e["_project"],
                          overview_board.sort_code(e["_mcode"]), -e["_idx"]))
log = _stamped + _rest

sheet_logs = {name: parts_tracker.fetch_movements(sid)
              for name, sid in sources}
_sheet_total = sum(len(rows) for rows in sheet_logs.values())

tab_log, tab_sheet = st.tabs([
    "📜 Log (%d)" % len(log),
    "🗒️ Movement Log tab (%d)" % _sheet_total,
])

with tab_log:
    st.markdown("**%d updates** shown · %d app-stamped, newest first · "
                "the rest in ledger order, part by part"
                % (len(log), len(_stamped)))
    _heads = ["Project", "M-Code", "Event", "Date", "Qty ordered",
              "Qty moved", "Qty received", "From", "To",
              "Courier / Tracking", "QC", "Notes", "Logged by", "Logged at"]
    _cells = []
    for e in log:
        _cells.append([
            e["_project"],
            part_url(e["_project"], e["_mcode"]),
            event_of(e),
            e.get("date", ""),
            e.get("qty_ordered", ""),
            e.get("qty_moved", ""),
            e.get("qty_received", ""),
            e.get("place", "") or e.get("from", ""),
            e.get("holder", "") or e.get("to", ""),
            e.get("courier", ""),
            e.get("status", ""),
            e.get("notes", ""),
            # The sheet keeps the email; the screen shows the person.
            user_store.name_of(e.get("logged_by", "")),
            e.get("logged_at", ""),
        ])
    native_table(_heads, _cells, link_col="M-Code")
    st.caption(
        "Every ledger line of every part in scope — raises, receipts, "
        "QC verdicts, holds and corrections included, exactly as the "
        "record keeps them. The M-Code opens the part on **Part Detail** "
        "in a new tab."
    )

with tab_sheet:
    st.caption("The record's own hand-kept Movement Log tab, as written. "
               "The log is the page now; this stays readable so nothing "
               "the team typed is hidden.")
    for _name, _sid in sources:
        rows = sheet_logs[_name]
        st.markdown("#### %s — %d" % (_name, len(rows)))
        if not rows:
            st.caption("No Movement Log rows for this project.")
            continue
        native_table(
            ["Date", "Item / Build", "Qty", "From", "To", "Stage", "Notes"],
            [[m.get("date", ""), m.get("item", ""), m.get("qty", ""),
              m.get("from", ""), m.get("to", ""), m.get("stage", ""),
              m.get("notes", "")] for m in reversed(rows)])
        with st.expander("🗒️ Full notes (%d)" % len(rows)):
            for m in reversed(rows):
                st.markdown("**%s**" % movement_header(m))
                render_movement(m)
                st.markdown("---")
