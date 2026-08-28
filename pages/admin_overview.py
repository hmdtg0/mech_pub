"""All Orders — the admin's one page: the board, and the orders desk.

🗺 Board: every part, every order thread, and everything moving — the
screen that answers "where does this part stand". 📦 Orders: every order
as a card with actions — the workqueue that WAS the All Orders page,
merged here 28 Aug (Hamid: "less pages to track, there are many"). The
view switch renders ONE section per rerun — deliberately not st.tabs,
which builds every tab's content on every rerun and would make each click
pay for both sections.

`utils/overview_board.py` builds the board rows and owns the colours;
`utils/orders_desk.py` is the orders desk. The board is read-only; orders
are raised on Order from BOM, movements recorded on Process Order.
"""
import streamlit as st

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from utils.auth import require_role
from utils import (movements_store, orders_desk, overview_board,
                   parts_tracker, project_colors, project_registry,
                   shipments_store, stock_store, ui, user_store)

user = require_role("admin", "engineer", "logistics")

st.title("📊 All Orders")

_view = st.radio("View", ["🗺 Board", "📦 Orders"], horizontal=True,
                 key="ov_view", label_visibility="collapsed")

if _view.endswith("Orders"):
    orders_desk.render(user)
    st.stop()

# ---- 🗺 the Board -----------------------------------------------------------
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

# The native grid, via the one shared renderer (ui.native_table, 28 Aug —
# Hamid's engine verdict, applied app-wide). M-Code opens Part Detail in a
# NEW tab; a courier's name in Status means in transit; row colour always
# comes from the real status.
_cells, _bg = [], []
for r in rows:
    _cells.append([
        ui.part_url(r["Project"], r["M-Code"]) if col == "M-Code"
        # The sheet keeps the email; the screen shows the person.
        else user_store.name_of(r.get(col, "")) if col == "Logged by"
        else overview_board.status_label(r) if col == "Status"
        else str(r.get(col, ""))
        for col in overview_board.COLUMNS])
    _bg.append(overview_board.COLOURS.get(r["Status"], ""))
ui.native_table(overview_board.COLUMNS, _cells, _bg, link_col="M-Code")

st.caption(overview_board.LEGEND)
st.caption(
    "One row per **order thread**, so a part with seven orders has seven "
    "rows; movements and what stayed behind follow its orders. **Date** is "
    "when an order was raised, or when a consignment left. A courier's name "
    "in **Status** means that consignment is in transit with them — matched "
    "from the central **Shipments** tab by date and route; where two records "
    "fit the same day the row keeps the word and Attention says so. Tracking "
    "numbers live on **Shipments**, and the search box here still finds a "
    "courier, tracking number, owner or location. **On hand** and what a "
    "sender still holds are read from the **Stock** count, which a Shipping "
    "row has already reduced. An open order whose ETA has passed says so in "
    "Attention without turning the row red — most open threads carry one."
)
