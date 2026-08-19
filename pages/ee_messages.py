"""My Messages - View all messages across your orders."""
import streamlit as st

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from utils.auth import require_auth, is_admin
from utils.message_store import fetch_all_messages
from utils.orders_store import fetch_all_orders, fetch_orders_by_engineer


user = require_auth()

st.title("💬 My Messages")

# Get user's orders
if is_admin(user):
    orders = fetch_all_orders()
else:
    orders = fetch_orders_by_engineer(user["email"])

my_order_ids = {o.get("OrderID") for o in orders}
order_names = {o.get("OrderID"): o.get("PartName", "Unknown") for o in orders}

# Get all messages
all_messages = fetch_all_messages()

# Filter to messages on my orders
my_messages = [m for m in all_messages if m.get("OrderID") in my_order_ids]

# Sort by timestamp descending
my_messages.sort(key=lambda m: m.get("Timestamp", ""), reverse=True)

if not my_messages:
    st.info("No messages yet. Messages appear when someone comments on your orders.")
    st.stop()

# Summary
total = len(my_messages)
from_others = [m for m in my_messages if m.get("Author") != user["name"]]
st.markdown(f"**{total} messages** total | **{len(from_others)} from others**")

st.markdown("---")

# Group by order
seen_orders = []
for m in my_messages:
    oid = m.get("OrderID", "")
    if oid not in seen_orders:
        seen_orders.append(oid)

for oid in seen_orders:
    pcb_name = order_names.get(oid, "Unknown")
    order_msgs = [m for m in my_messages if m.get("OrderID") == oid]

    with st.expander(f"**{pcb_name}** ({oid}) — {len(order_msgs)} messages", expanded=False):
        for m in order_msgs:
            author = m.get("Author", "")
            ts = m.get("Timestamp", "")
            content = m.get("Content", "")
            is_me = author == user["name"]
            prefix = "🟢" if is_me else "🔵"
            st.markdown(f"{prefix} **{author}** ({ts}): {content}")
