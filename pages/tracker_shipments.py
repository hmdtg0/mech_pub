"""Shipments — everything that travelled, across every project.

The last page to move off the old two-log world (Hamid, 19 Aug 2026). What
travelled is now a fact of each project's own movement log: a row whose
`Event` is Shipping, Delivery or Return. This page collects those across all
projects, so a consignment carrying parts for two projects appears once per
project rather than in a fourth hand-kept tab.

**Two sources, on purpose.** The ledger knows WHAT travelled — part, quantity,
which project, from whom to whom. The main record's `Shipments` tab knows HOW
— courier, tracking number, ETA, whether it arrived. Neither carries the
other's facts, and neither is complete. They are shown side by side and the
gaps are named, because a page that silently merged them would have to guess,
and a guessed tracking number is worse than a missing one.

Same-day courier records are offered as a lead, never asserted as the
shipment's tracking number: dates are hand-typed and one consignment can
carry several parts.

Read-only. Shipping events are recorded on Process Order.
"""
import pandas as pd
import streamlit as st


import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from utils.auth import require_auth
from utils import movements_store, project_colors, shipments_store
from utils import ui
from utils.ui import literal

require_auth()

st.title("🚢 Shipments")

c1, c3, c4 = st.columns([3, 1.5, 1.3], vertical_alignment="bottom")
with c1:
    query = st.text_input("🔍 Search",
                          placeholder="part, item, holder, courier, tracking…")
with c3:
    event_box = st.empty()  # filled once the log is loaded
with c4:
    if st.button("🔄 Refresh", use_container_width=True):
        shipments_store.refresh()
        movements_store.refresh()
        st.rerun()

ledger = movements_store.shipments_across_projects()
courier = shipments_store.fetch_shipments()

# The main record is deliberately not linked anywhere in the UI.
st.caption("**%d shipping events** from every project's movement log, and "
           "**%d courier records** from the central log." % (len(ledger),
                                                             len(courier)))

if not ledger and not courier:
    st.info("Nothing has been logged as shipped yet. Shipping, Delivery and "
            "Return events are recorded on **Process Order**; courier detail "
            "is added to the central Shipments tab by logistics.")
    st.stop()

with event_box:
    events = st.multiselect(
        "Event", sorted({r.get("event", "") for r in ledger if r.get("event")}))


def _matches(row):
    if not query:
        return True
    return query.lower() in " ".join(str(v) for v in row.values()).lower()


view = [r for r in ledger if _matches(r)]
if events:
    view = [r for r in view if r.get("event") in events]
courier_view = [r for r in courier if _matches(r)]

# Which side of each pair has no counterpart. Same-day is the only key the two
# logs share — see shipments_store.same_day for why it stays a lead, not a join.
no_courier = [r for r in ledger if not shipments_store.same_day(r.get("date", ""))]
ledger_days = {shipments_store.calendar_day(r.get("date", "")) for r in ledger}
no_event = [c for c in courier
            if shipments_store.calendar_day(
                c.get("date", "") or c.get("date_text", "")) not in ledger_days]

tab_moves, tab_courier, tab_gaps = st.tabs([
    "📦 What travelled (%d)" % len(view),
    "🚚 Courier records (%d)" % len(courier_view),
    "⚠️ Gaps (%d)" % (len(no_courier) + len(no_event)),
])

with tab_moves:
    if view:
        rows = []
        for r in sorted(view, key=lambda r: shipments_store.calendar_day(
                r.get("date", "")) or (0, 0), reverse=True):
            leads = shipments_store.same_day(r.get("date", ""))
            rows.append({
                "Date": r.get("date", ""),
                "Project": r.get("project", ""),
                "Event": r.get("event", ""),
                "Part": r.get("part_id", "") or "—",
                "Description": r.get("description", ""),
                "Qty": r.get("qty", ""),
                "From": r.get("from", ""),
                "To": r.get("to", ""),
                "Tracking (same day)": "; ".join(
                    c.get("tracking", "") for c in leads if c.get("tracking")) or "—",
                "Notes": r.get("notes", ""),
            })
        st.dataframe(pd.DataFrame(rows), hide_index=True,
                     height=ui.table_height(len(rows)),
                     use_container_width=True)
        st.caption("“Tracking (same day)” is a courier record posted on the "
                   "same date — a lead to confirm, not a stated fact. A dash "
                   "in Part means the row records a batch rather than one "
                   "M-code.")
    else:
        st.info("No shipping events match the current filters.")

with tab_courier:
    st.caption("The central courier log: how things travelled. This is where "
               "tracking numbers, ETAs and delivery outcomes live — the "
               "movement log does not carry them.")
    if courier_view:
        newest_first = list(reversed(courier_view))
        st.dataframe(pd.DataFrame([{
            "Date": m.get("date", "") or m.get("date_text", ""),
            "Item / Build": m.get("item", ""),
            "Order": m.get("order_id", ""),
            "Qty": m.get("qty", ""),
            "From": m.get("from", ""), "To": m.get("to", ""),
            "Courier": m.get("courier", ""),
            "Tracking": m.get("tracking", ""),
            "ETA": m.get("eta", ""),
            "Delivery / receipt": m.get("delivery", ""),
            "Status": m.get("status", ""),
            "Flags": m.get("flags", ""),
            "Notes": m.get("notes", ""),
        } for m in newest_first]), hide_index=True,
            height=ui.table_height(len(newest_first)),
            use_container_width=True)

        # Expanders cannot nest, so this is a heading with a row of expanders
        # under it rather than one collapsible block.
        st.markdown("---")
        st.markdown("**🗒️ Row by row** — every cell verbatim, nothing summarised.")
        for m in newest_first:
            head = "**%s** — %s → %s" % (
                literal(m.get("item", "") or "(no item)"),
                literal(m.get("from", "") or "?"),
                literal(m.get("to", "") or "?"))
            sub = " · ".join(
                "%s %s" % (label, literal(value)) for label, value in (
                    ("Date", m.get("date", "") or m.get("date_text", "")),
                    ("Qty", m.get("qty", "")),
                    ("Courier", m.get("courier", "")),
                    ("Status", m.get("status", "")),
                ) if str(value).strip())
            with st.expander(head + ("  \n" + sub if sub else "")):
                for key, label in (
                        ("order_id", "Order"), ("tracking", "Tracking"),
                        ("eta", "ETA"), ("delivery", "Delivery / receipt"),
                        ("flags", "Flags"), ("date_text", "Date as written"),
                        ("notes", "Notes")):
                    if str(m.get(key, "")).strip():
                        st.markdown("**%s:** %s" % (label, literal(m[key])))
    else:
        st.info("No courier records match the current filters.")

with tab_gaps:
    st.caption("Neither log is complete, and they are filled in by different "
               "people. Shown rather than reconciled — which one is missing a "
               "row is a human call.")

    if no_event:
        st.markdown("**Couriered, but no shipping event on any ledger** — the "
                    "consignment is on record, the parts it carried are not.")
        st.dataframe(pd.DataFrame([{
            "Date": c.get("date", "") or c.get("date_text", ""),
            "Item / Build": c.get("item", ""),
            "From": c.get("from", ""), "To": c.get("to", ""),
            "Tracking": c.get("tracking", ""),
            "Status": c.get("status", ""),
        } for c in no_event]), hide_index=True,
            height=ui.table_height(len(no_event)), use_container_width=True)

    if no_courier:
        st.markdown("**Shipped on a ledger, but no courier record that day** — "
                    "how it travelled, and whether it arrived, is unrecorded.")
        st.dataframe(pd.DataFrame([{
            "Date": r.get("date", ""),
            "Project": r.get("project", ""),
            "Event": r.get("event", ""),
            "Part": r.get("part_id", "") or "—",
            "Description": r.get("description", ""),
            "Qty": r.get("qty", ""),
            "From": r.get("from", ""), "To": r.get("to", ""),
        } for r in no_courier]), hide_index=True,
            height=ui.table_height(len(no_courier)), use_container_width=True)

    if not no_event and not no_courier:
        st.success("Every shipping event has a courier record from the same "
                   "day, and every courier record has an event.")

if ledger:
    seen = sorted({r.get("project", "") for r in ledger if r.get("project")})
    st.markdown("---")
    st.markdown("Projects in this view — " + " ".join(
        project_colors.badge_html(name) for name in seen),
        unsafe_allow_html=True)
