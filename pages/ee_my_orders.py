"""My Orders — one line per part: the order its tab was opened with.

The old "Raised in this app" card list (the original order-form workflow:
progress bar, per-order messages, file re-upload, delete, reorder) was
removed 18 Aug 2026 (Hamid) — superseded by the ledger view here, Process
Order and My Messages. Submits still WRITE the central Orders tab (Process
Order's worklist); it just isn't rendered on this page any more.
"""
import pandas as pd
import streamlit as st

from config import STATUS_COLORS
from utils import parts_tracker, project_colors, project_registry, tracker_orders
from utils.auth import require_auth
from utils.ui import project_filter, table_height

user = require_auth()

st.title("📦 My Orders")
head1, head2 = st.columns([4, 1.2], vertical_alignment="center")
with head1:
    st.markdown(f"Viewing orders for: **{user['name']}** ({user['email']})")
with head2:
    if st.button("🔄 Refresh", use_container_width=True,
                 help="Re-read every project ledger now, instead of waiting "
                      "out the cache."):
        for _sid in project_registry.all_projects().values():
            parts_tracker.refresh(_sid)
        st.rerun()

# ONE line per part: the order its tab was opened with — "technically
# speaking it is what we have in the first history line of each part tab"
# (Hamid, 18 Aug). Quantities and status are the ledger's latest word.
# Only these fields + a jump to Part Detail; no expanders.
tracker_mine = []
for o in tracker_orders.all_part_orders():
    why = tracker_orders.mine_reasons(o, user["name"], user["email"])
    if why:
        o["why_mine"] = ", ".join(why)
        tracker_mine.append(o)

tracker_mine = project_filter(tracker_mine, key="myo_project")
st.markdown("### From the project tracker (%d)" % len(tracker_mine))
st.caption("One line per part — the order its tab was opened with. Yours if "
           "you are the part's **owner** (responsible for it, from the BOM), "
           "its current **holder** (the goods sit with you), or you **raised "
           "it**. Each row says which. Quantity and status follow the "
           "ledger's latest word.")

if not tracker_mine:
    st.info("Nothing here concerns you yet — no part lists you as its owner "
            "or holder, and none was raised by you.")
else:
    tab_table, tab_rows = st.tabs([
        "📋 Table View (%d)" % len(tracker_mine),
        "🗒️ Row by Row (%d)" % len(tracker_mine),
    ])

    with tab_table:
        st.dataframe(pd.DataFrame([{
            "Project": o["project"],
            "Part ID": o["mcode"],
            "Part Name": o["part_name"],
            "Version": o["version"],
            "Qty (rec/ord)": "%s/%s" % (o["qty_received"],
                                        o["qty_ordered"] or "?"),
            "Status": (o["derived"] or "").capitalize(),
            "Yours as": o.get("why_mine", ""),
        } for o in tracker_mine]), hide_index=True, use_container_width=True,
            height=table_height(len(tracker_mine)))

    with tab_rows:
        for o in tracker_mine:
            row1, row2 = st.columns([5.2, 1.1], vertical_alignment="center")
            status = o["derived"] or ""
            chip = (":%s[%s]" % (STATUS_COLORS.get(status, "gray"),
                                 status.upper()) if status else "—")
            with row1:
                st.markdown("%s | **%s** — %s | `%s` | %s/%s | %s · _%s_" % (
                    project_colors.tag(o["project"]), o["mcode"],
                    o["part_name"] or "?", o["version"] or "no version",
                    o["qty_received"], o["qty_ordered"] or "?", chip,
                    o.get("why_mine", "")))
            with row2:
                if st.button("🔍 Detail", use_container_width=True,
                             key="tr_detail_%s_%s" % (o["project"], o["mcode"])):
                    project_registry.set_active(o["project"])
                    st.session_state["tracker_part"] = o["mcode"]
                    st.switch_page("pages/tracker_part_detail.py")
