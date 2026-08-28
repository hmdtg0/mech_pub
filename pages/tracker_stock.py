"""Stock — what is on hand, who holds it, and what has moved.

The counts live on the main record's `Stock` tab; every movement is logged to
the project record's merged `Movements` tab, which replaced the old split
between `Movement Log` and `Stock_History` (Hamid, 19 Aug 2026). This page
READS them; it does not
write. Movements are recorded on **Process Order**, where a movement is first
an event on the part's own history and the count here is calculated from it —
one place to enter it, so the ledger and the count cannot disagree through
the app (Hamid, 18 Aug).

Holders come from the `Holders` directory, never typed, because a near-miss
("Sam" vs "Sam Smith") is how one person's stock silently becomes two
people's. Names already on the sheets that the directory does not define are
named here rather than quietly matched.

A row that states a holder's TOTAL without naming a part is shown and counted,
but flagged: it cannot be checked against an order or traced to a delivery.
"""
import pandas as pd
import streamlit as st

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from utils.auth import require_auth
from utils import (bom_sheet, holders_store, parts_model, project_colors,
                   project_registry, stock_store)
from utils.tracker_parse import to_int
from utils.ui import require_project, table_height

require_auth()

st.title("📦 Stock")

require_project()
active_name, _ = project_registry.active()

c1, c2 = st.columns([4, 1.3], vertical_alignment="bottom")
with c1:
    query = st.text_input("🔍 Search", placeholder="part, holder, project…")
with c2:
    if st.button("🔄 Refresh", use_container_width=True):
        stock_store.refresh()
        holders_store.refresh()
        st.rerun()

_all_scope = project_registry.is_all()
st.markdown("**Project** — %s"
            % ("every registered project" if _all_scope
               else project_colors.badge_html(active_name)),
            unsafe_allow_html=True)


def _mine(row):
    # The Stock tab is central and carries a project per row, so the
    # widest scope is simply no filter at all.
    return _all_scope or stock_store.project_matches(
        row.get("project", ""), active_name)


all_rows = [r for r in stock_store.fetch_stock() if _mine(r)]
parted = [r for r in all_rows if r.get("part_id")]
totals_only = [r for r in all_rows if not r.get("part_id")]
history = [h for h in stock_store.fetch_history(
    "" if _all_scope else active_name) if _mine(h)]
holders = holders_store.fetch_holders()

sheet_total = sum(to_int(r.get("qty", "")) for r in all_rows)
moved_total = sum(to_int(h.get("qty", "")) for h in history)

m1, m2, m3, m4 = st.columns(4)
m1.metric("Pieces on hand", "{:,}".format(sheet_total),
          help="Everything the Stock tab counts for this project, whether or "
               "not the row names a part.")
m2.metric("Parts with stock", len({r["part_id"] for r in parted}))
m3.metric("Holders counted",
          len({r.get("holder", "") for r in all_rows if r.get("holder")}))
m4.metric("Pieces logged as moved", "{:,}".format(moved_total),
          help="From the project's movement log — every arrival and transfer "
               "on record.")

if totals_only:
    st.info(
        "**%d of these rows state a holder's total without naming a part** "
        "(%s pieces). They count towards the figure above, but they cannot be "
        "checked against an order, traced to a delivery, or used to decide a "
        "reorder. Filling in the Part ID column is what turns them into "
        "stock the app can actually work with."
        % (len(totals_only),
           "{:,}".format(sum(to_int(r.get("qty", "")) for r in totals_only))))

unknown = holders_store.unknown(
    [r.get("holder", "") for r in stock_store.fetch_stock()]
    + [h.get("sent_to", "") for h in stock_store.fetch_history(active_name)])
if unknown:
    st.warning(
        "**Holders not in the directory:** %s. The app only ever writes names "
        "from the `Holders` tab, so these came from hand edits. Either add "
        "them to the directory or correct the spelling, otherwise the same "
        "person is counted as two." % ", ".join("`%s`" % h for h in unknown))

if not holders:
    st.error("The `Holders` directory is empty or unreadable, so no movement "
             "can be recorded. Check the Holders tab on the main record.")


# Recording a movement lives on Process Order (Hamid, 18 Aug): a movement is
# an event in the part's history first, and the stock count is calculated from
# it — so there is one place it can be entered, not two.
st.caption("Movements are recorded on **Process Order**, where they append to "
           "the part's own history and update this count in the same action.")

st.markdown("---")


def _match(row):
    if not query:
        return True
    return query.lower() in " ".join(str(v) for v in row.values()).lower()


shown = [r for r in all_rows if _match(r)]
shown_history = [h for h in history if _match(h)]
problems = stock_store.disagreements(active_name)

t1, t2, t3, t4 = st.tabs([
    "📋 Table View (%d)" % len(shown),
    "🧑 By Holder (%d)" % len({r.get("holder", "") for r in all_rows if r.get("holder")}),
    "🔁 Movements (%d)" % len(shown_history),
    "⚠️ Checks (%d)" % (len(problems) + len(totals_only)),
])

with t1:
    if shown:
        from utils.ui import esc, linked_table, part_link
        _heads = ["Part ID", "Description", "Type", "Holder", "Where",
                  "Qty on hand", "Last counted", "Notes"]
        linked_table(_heads, [[
            part_link(r.get("project", "") or active_name,
                      r.get("part_id", "")) or "—",
            esc(r.get("description", "")), esc(r.get("type", "")),
            esc(r.get("holder", "")),
            esc(holders_store.location_of(r.get("holder", "")) or "—"),
            esc(to_int(r.get("qty", ""))), esc(r.get("last_counted", "")),
            esc(r.get("notes", "")),
        ] for r in shown])
        st.caption("A dash in Part ID means the row states a holder's total "
                   "without saying which parts make it up.")
    else:
        st.info("No stock rows for this project yet.")

with t2:
    st.caption("What the Stock tab counts, against what the movement log "
               "implies. They come from different places, so a gap is "
               "information — not an error to hide.")
    counted, implied = {}, {}
    for r in all_rows:
        h = r.get("holder", "") or "(not stated)"
        counted[h] = counted.get(h, 0) + to_int(r.get("qty", ""))
    # Reuses the store's rule rather than repeating it: a supplier is where
    # goods came FROM and never goes negative, only a person or site holds
    # stock. Two copies of that rule is how the two views drift apart.
    for (_part, _proj, holder), qty in \
            stock_store.totals_from_history(active_name).items():
        implied[holder] = implied.get(holder, 0) + qty
    names = sorted(set(counted) | set(implied),
                   key=lambda n: -max(counted.get(n, 0), implied.get(n, 0)))
    if names:
        df = pd.DataFrame([{
            "Holder": n,
            "Kind": holders_store.get(n).get("kind", "—"),
            "Where": holders_store.location_of(n) or "—",
            "Stock tab says": counted.get(n, 0),
            "Movements imply": implied.get(n, 0),
            "Gap": counted.get(n, 0) - implied.get(n, 0),
        } for n in names])
        st.dataframe(df, use_container_width=True, hide_index=True,
                     height=table_height(len(df)))
    else:
        st.info("Nothing counted against a holder yet.")

with t3:
    if shown_history:
        from utils.ui import esc, linked_table, part_link
        linked_table(
            ["Date", "Part ID", "Description", "Qty", "From", "To", "Notes"],
            [[esc(h.get("last_counted", "")),
              part_link(h.get("project", "") or active_name,
                        h.get("part_id", "")),
              esc(h.get("description", "")), esc(to_int(h.get("qty", ""))),
              esc(h.get("holder", "")), esc(h.get("sent_to", "")),
              esc(h.get("notes", ""))] for h in shown_history])
        st.caption("“(external)” means the goods arrived from a supplier "
                   "rather than moving between two holders.")
    else:
        st.info("No movements logged yet. Every movement recorded above is "
                "appended here and never edited afterwards.")

with t4:
    if problems:
        st.markdown("**A part's count and its movement log disagree**")
        df = pd.DataFrame([{
            "Part ID": p["part_id"], "Holder": p["holder"],
            "Stock says": p["counted"], "History implies": p["from_history"],
            "Difference": p["difference"],
        } for p in problems])
        st.dataframe(df, use_container_width=True, hide_index=True,
                     height=table_height(len(df)))
        st.caption("Shown, never auto-corrected — which one is right is a "
                   "human call.")
    if totals_only:
        st.markdown("**Rows with a quantity but no part**")
        df = pd.DataFrame([{
            "Holder": r.get("holder", ""),
            "Qty on hand": to_int(r.get("qty", "")),
            "Project": r.get("project", ""),
            "Notes": r.get("notes", ""),
        } for r in totals_only])
        st.dataframe(df, use_container_width=True, hide_index=True,
                     height=table_height(len(df)))
    if not problems and not totals_only:
        st.success("Every row names a part, and the counts agree with the "
                   "movement log.")

# --- the BOM-derived cross-check -------------------------------------------
bom_id = project_registry.bom_sheet(active_name)
if bom_id:
    with st.expander("🔬 Cross-check against the BOM movement log", expanded=False):
        st.caption(
            "A third, independent count: quantity in minus quantity out per "
            "part per location, computed from the BOM's own movement log. A "
            "different source again, so differences are expected while that "
            "log is incomplete.")
        movements = bom_sheet.fetch_movement_log(bom_id)
        derived = parts_model.stock_by_location(movements)
        if not derived:
            st.info("The BOM movement log has no rows with a part and a "
                    "quantity yet, so nothing can be derived.")
        else:
            out = []
            for code in sorted(derived):
                for location, qty in sorted(derived[code].items()):
                    out.append({"M-Code": code, "Location": location,
                                "Derived qty": qty})
            df = pd.DataFrame(out)
            st.dataframe(df, use_container_width=True, hide_index=True,
                         height=table_height(len(df)))
            st.caption("Derived from %d movement rows." % len(movements))
