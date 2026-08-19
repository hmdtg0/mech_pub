"""Shared rendering for orders that live in the project tracker.

Kept out of the pages so My Orders, All Orders and Order History show the same
thing — one expander per order, in the app's usual idiom, with the two rows of
the sheet pair spelled out: who ordered it and from where, who received it and
where it went.
"""
from __future__ import annotations

from typing import List

import pandas as pd
import streamlit as st

from utils import project_colors, project_registry, tracker_orders
from utils.ui import table_height


def _status_icon(order: dict) -> str:
    if tracker_orders.is_complete(order):
        return "✅"
    if order["qty_received"]:
        return "🟠"          # part-delivered
    return "⚪"              # raised, nothing received yet


def order_header(order: dict, show_project: bool = True) -> str:
    received = "%s/%s" % (order["qty_received"], order["qty_ordered"] or "?")
    project = "%s | " % project_colors.tag(order["project"]) if show_project else ""
    return "%s %s**%s** — %s | `%s` | %s received | → %s" % (
        _status_icon(order), project, order["mcode"], order["part_name"],
        order["version"] or "no version", received,
        order["recipient"] or "not received yet")


def render_order(order: dict, key: str) -> None:
    """One tracker order, as the sheet records it."""
    with st.expander(order_header(order), expanded=False):
        st.markdown("**Project** — %s" % project_colors.badge_html(order["project"]),
                    unsafe_allow_html=True)
        st.markdown("**Order** — %s · %s · version `%s`"
                    % (order["order_id"] or "no order id", order["date"] or "no date",
                       order["version"] or "none"))

        c1, c2 = st.columns(2)
        with c1:
            st.markdown("**Ordered by** — %s" % (order["ordered_by"] or "—"))
            st.markdown("**From** — %s" % (order["ordered_from"] or "—"))
            st.markdown("**Vendor / Source** — %s" % (order["vendor"] or "—"))
        with c2:
            st.markdown("**Received by** — %s" % (order["recipient"] or "not yet"))
            st.markdown("**At** — %s" % (order["recipient_location"] or "—"))
            st.markdown("**Status** — %s"
                        % (order.get("derived") or order["status"] or "not recorded"))

        st.markdown("**Quantity** — %s ordered · **%s received**"
                    % (order["qty_ordered"] or "?", order["qty_received"]))
        if order["eta"]:
            st.markdown("**ETA** — %s" % order["eta"])
        if order["receipt_note"]:
            st.markdown("**Receipt note** — %s" % order["receipt_note"])
        if order["selected"]:
            st.success("This is the version selected for MP.")

        if st.button("🔍 Open part history", key="tracker_hist_%s" % key,
                     use_container_width=True):
            project_registry.set_active(order["project"])
            st.session_state["tracker_part"] = order["mcode"]
            st.switch_page("pages/tracker_part_detail.py")


def orders_table(orders: List[dict]) -> pd.DataFrame:
    return pd.DataFrame([{
        "Project": project_colors.tag(o["project"]),
        "M-Code": o["mcode"],
        "Part": o["part_name"],
        "Order / Sample": o["order_id"],
        "Date": o["date"],
        "Version": o["version"],
        "Ordered by": o["ordered_by"],
        "Received by": o["recipient"],
        "Ordered": o["qty_ordered"],
        "Received": o["qty_received"],
        "Status": o.get("derived") or o["status"],
    } for o in orders])


def render_section(orders: List[dict], empty: str, max_show: int = 15,
                   key: str = "tracker") -> None:
    """A list of tracker orders with the app's usual show-all cap."""
    if not orders:
        st.info(empty)
        return

    shown = orders
    if len(orders) > max_show:
        show_all = st.checkbox("Show all %d (showing first %d)" % (len(orders), max_show),
                               key="show_all_%s" % key)
        shown = orders if show_all else orders[:max_show]

    for index, order in enumerate(shown):
        render_order(order, "%s_%s_%d" % (key, order["mcode"], index))

    with st.expander("📋 Table view (%d)" % len(orders)):
        st.dataframe(orders_table(orders), use_container_width=True,
                     hide_index=True, height=table_height(len(orders)))
