"""Overview — every part, every order thread, and everything moving.

The one screen that answers "where does this part stand": what was ordered,
how many arrived, how many are on hand, what is on its way to whom, with
which courier, and what stayed behind with the sender.

`utils/overview_board.py` builds the rows and owns the colours; this file
filters and draws them.

Read-only. Orders are raised on Order from BOM, movements recorded on
Process Order.
"""
import pandas as pd
import streamlit as st

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from utils.auth import require_role
from utils import (movements_store, overview_board, parts_tracker,
                   project_colors, project_registry, shipments_store,
                   stock_store, ui, user_store)

require_role("admin", "engineer", "logistics")

st.title("📊 Overview")

c1, c2 = st.columns([4, 1.2], vertical_alignment="bottom")
with c1:
    query = st.text_input("🔍 Search",
                          placeholder="part, holder, order id, courier, tracking…")
with c2:
    if st.button("🔄 Refresh", use_container_width=True):
        movements_store.refresh()
        shipments_store.refresh()
        stock_store.refresh()
        parts_tracker.refresh()   # the part tabs the order threads come from
        st.rerun()

rows = overview_board.rows()
if not rows:
    st.info("No project records are registered yet — add one on **Projects**.")
    st.stop()

rows = ui.in_scope(rows)

f1, f2 = st.columns(2)
with f1:
    if st.checkbox("✅ Hide closed", value=False,
                   help="Hide delivered and cancelled rows."):
        rows = [r for r in rows if r["Status"] not in overview_board.CLOSED]
with f2:
    if st.checkbox("🚚 In transit only", value=False,
                   help="Only what is on its way: shipped, in transit, "
                        "overdue or untracked."):
        rows = [r for r in rows if r["Status"] in overview_board.MOVING]

if query:
    rows = [r for r in rows
            if query.lower() in " ".join(str(v) for v in r.values()).lower()]

if not rows:
    st.info("Nothing matches the current filters.")
    st.stop()

orders = [r for r in rows if r["_kind"] == "order"]
m1, m2, m3, m4 = st.columns(4)
m1.metric("Parts", len({(r["Project"], r["M-Code"]) for r in rows
                        if r["M-Code"] != "—"}))
m2.metric("Order threads", len(orders))
m3.metric("Moving", sum(1 for r in rows if r["Status"] in overview_board.MOVING))
m4.metric("Needs a look",
          sum(1 for r in rows if r["Status"] in overview_board.BAD))

sheets = project_registry.all_projects()
st.markdown("**Projects** — " + " ".join(
    project_colors.badge_html(name, sheet_id=sheets.get(name, ""))
    for name in sorted({r["Project"] for r in rows})), unsafe_allow_html=True)


# The native grid — Hamid's pick after the two-engine trial (28 Aug).
# M-Code is a LinkColumn built from ABSOLUTE urls (the grid ignores
# relative ones) and always opens a NEW tab — the widget's hard-coded
# behaviour at any version.
_records = []
for r in rows:
    rec = {col: str(r.get(col, "")) for col in overview_board.COLUMNS}
    rec["M-Code"] = ui.part_url(r["Project"], r["M-Code"])
    # The sheet keeps the email; the screen shows the person.
    rec["Logged by"] = user_store.name_of(r.get("Logged by", ""))
    _records.append(rec)
_df = pd.DataFrame(_records)[overview_board.COLUMNS]
_bg = [overview_board.COLOURS.get(r["Status"], "") for r in rows]


def _paint(row):
    return ["background-color: %s" % _bg[row.name]] * len(row)


def _code_of(url):
    # The Styler's display layer overrides LinkColumn.display_text on
    # this Streamlit, so the code-instead-of-URL text comes from the
    # Styler itself — the one place that coexists with the row colours.
    from urllib.parse import parse_qs, urlparse
    try:
        return parse_qs(urlparse(str(url)).query).get("part", [""])[0]
    except Exception:
        return str(url)


st.dataframe(
    _df.style.apply(_paint, axis=1).format(_code_of, subset=["M-Code"]),
    hide_index=True,
    height=ui.table_height(len(_df)), use_container_width=True,
    column_config={
        "M-Code": st.column_config.LinkColumn(
            "M-Code",
            help="Opens the part on Part Detail — the grid always opens "
                 "links in a new tab."),
    })

st.caption(overview_board.LEGEND)
st.caption(
    "One row per **order thread**, so a part with seven orders has seven "
    "rows; movements and what stayed behind follow its orders. **Date** is "
    "when an order was raised, or when a consignment left. Courier, tracking "
    "and ETA come from the central **Shipments** tab, matched to a movement "
    "by date and route — where two records fit the same day the cells stay "
    "empty and Attention says so. **On hand** and what a sender still holds "
    "are read from the **Stock** count, which a Shipping row has already "
    "reduced. An open order whose ETA has passed says so in Attention "
    "without turning the row red — most open threads carry one."
)
