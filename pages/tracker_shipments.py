"""Shipments — everything that travelled, across every project.

The last page to move off the old two-log world (Hamid, 19 Aug 2026). What
travelled is now a fact of each project's own movement log: a row whose
`Event` is Shipping, Delivery or Return. This page collects those across all
projects, so a consignment carrying parts for two projects appears once per
project rather than in a fourth hand-kept tab.

**What a shipment says about itself is believed (19 Sep 2026).** Since the
🚚 Ship entry, the sender types Courier / Tracking and an ETA on the movement
itself: the tracking text rides on the movement row, the ETA on its twin in
the part's ledger (`movements_store.ledger_twin`). Those are stated facts and
are shown as such.

**The central `Shipments` tab stays a lead.** It is the hand-kept courier log
from before the app — courier, tracking, ETA, whether it arrived — and shares
no key with the movement log but the date. Same-day records are offered
beside a leg, never asserted as its tracking number: dates are hand-typed and
one consignment can carry several parts. A guessed tracking number is worse
than a missing one.

Read-only. Shipping events are recorded on Process Order.
"""
import pandas as pd
import streamlit as st


import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from utils.auth import require_auth
from utils import (movements_store, overview_board, project_colors,
                   shipments_store)
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

# in_scope keeps rows that name no project, which is every courier
# record: the central Shipments tab has no project column, so those
# cannot be narrowed and are shown whole rather than guessed at.
ledger = ui.in_scope(movements_store.shipments_across_projects())
courier = shipments_store.fetch_shipments()

# The main record is deliberately not linked anywhere in the UI.
st.caption("**%d shipping events** from every project's movement log, and "
           "**%d courier records** from the central log." % (len(ledger),
                                                             len(courier)))

if not ledger and not courier:
    st.info("Nothing has been logged as shipped yet. A shipment is recorded "
            "with **🚚 Ship** under 📜 Add history entry (Process Order, or "
            "the part's own page) — its tracking and ETA go in there too.")
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
# A leg that names its own courier / tracking documents itself: no gap.
no_courier = [r for r in ledger
              if not str(r.get("courier", "")).strip()
              and not shipments_store.same_day(r.get("date", ""))]
# App-written movements the part's ledger does not record — someone removed
# the history line by hand, so the log (and maybe the count) stands alone.
orphans = [r for r in ledger if overview_board.is_orphan(r)]
ledger_days = {shipments_store.calendar_day(r.get("date", "")) for r in ledger}
no_event = [c for c in courier
            if shipments_store.calendar_day(
                c.get("date", "") or c.get("date_text", "")) not in ledger_days]

tab_moves, tab_courier, tab_gaps = st.tabs([
    "📦 What travelled (%d)" % len(view),
    "🚚 Courier records (%d)" % len(courier_view),
    "⚠️ Gaps (%d)" % (len(no_courier) + len(no_event) + len(orphans)),
])

with tab_moves:
    if view:
        from utils.ui import native_table, part_url
        _heads = ["Date", "Project", "Event", "Part", "Description", "Qty",
                  "From", "To", "Courier / Tracking", "ETA",
                  "Courier log (same day)", "Notes"]
        _cells = []
        for r in sorted(view, key=lambda r: shipments_store.calendar_day(
                r.get("date", "")) or (0, 0), reverse=True):
            leads = shipments_store.same_day(r.get("date", ""))
            twin = movements_store.ledger_twin(r) or {}
            _cells.append([
                r.get("date", ""), r.get("project", ""),
                r.get("event", ""),
                part_url(r.get("project", ""), r.get("part_id", ""))
                or "—",
                r.get("description", ""), r.get("qty", ""),
                r.get("from", ""), r.get("to", ""),
                r.get("courier", "") or "—",
                twin.get("eta", "") or "—",
                "; ".join(c.get("tracking", "") for c in leads
                          if c.get("tracking")) or "—",
                r.get("notes", ""),
            ])
        native_table(_heads, _cells, link_col="Part")
        st.caption("**Courier / Tracking** and **ETA** are what the sender "
                   "entered on the shipment itself — stated facts. “Courier "
                   "log (same day)” is a record in the hand-kept central log "
                   "posted on the same date — a lead to confirm. A dash in "
                   "Part means the row records a batch rather than one "
                   "M-code.")
    else:
        st.info("No shipping events match the current filters.")

with tab_courier:
    st.caption("The hand-kept central courier log: tracking numbers, ETAs and "
               "delivery outcomes recorded outside the app. A shipment "
               "entered through 🚚 Ship carries its own tracking and ETA — "
               "those show under **What travelled**.")
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

    if orphans:
        st.markdown("**On the movement log, but not on the part's ledger** — "
                    "the app writes the history row first and the movement "
                    "second, so the history line was removed by hand. The "
                    "movement still feeds the board (and the count, unless "
                    "the sender is a vendor): restore the history line, or "
                    "remove this row from the project's Movements tab.")
        st.dataframe(pd.DataFrame([{
            "Date": r.get("date", ""),
            "Project": r.get("project", ""),
            "Event": r.get("event", ""),
            "Part": r.get("part_id", "") or "—",
            "Qty": r.get("qty", ""),
            "From": r.get("from", ""), "To": r.get("to", ""),
            "Courier / Tracking": r.get("courier", ""),
            "Logged by": r.get("logged_by", ""),
        } for r in orphans]), hide_index=True,
            height=ui.table_height(len(orphans)), use_container_width=True)

    if no_courier:
        st.markdown("**Shipped, with no tracking of its own and no courier "
                    "record that day** — how it travelled, and whether it "
                    "arrived, is unrecorded.")
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

    if not no_event and not no_courier and not orphans:
        st.success("Every shipping event names its tracking or has a courier "
                   "record from the same day, every courier record has an "
                   "event, and every movement is on its part's ledger.")

if ledger:
    seen = sorted({r.get("project", "") for r in ledger if r.get("project")})
    st.markdown("---")
    st.markdown("Projects in this view — " + " ".join(
        project_colors.badge_html(name) for name in seen),
        unsafe_allow_html=True)
