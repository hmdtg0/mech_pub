"""User Management - Admin page to manage user access and roles."""
import streamlit as st
import pandas as pd

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from config import DOMAIN_ALIASES, PRIMARY_ADMIN_EMAILS
from utils.auth import require_role
from utils.ui import table_height
from utils.google_client import get_gspread_client
from utils.user_store import fetch_allowed_users, add_user, remove_user, update_user_role, _clear_cache

user = require_role("admin")

st.title("👥 User Management")

client = get_gspread_client()
if not client:
    st.error("Cannot connect to Google Sheets.")
    st.stop()

# --- Current Users ---
st.header("Current Users")

users = fetch_allowed_users()

# Build display table
rows = []
for email, info in sorted(users.items(), key=lambda x: (x[1]["role"], x[1]["name"])):
    rows.append({"Email": email, "Name": info["name"], "Role": info["role"]})

df = pd.DataFrame(rows)

# Color-code roles
def highlight_role(row):
    role = row.get("Role", "")
    if role == "admin":
        return ["background-color: #e8f5e9"] * len(row)
    elif role == "logistics":
        return ["background-color: #e3f2fd"] * len(row)
    return [""] * len(row)

st.dataframe(df.style.apply(highlight_role, axis=1), use_container_width=True,
             hide_index=True, height=table_height(len(df)))

st.markdown(f"**{len(users)} users** total — "
            f"{sum(1 for u in users.values() if u['role'] == 'admin')} admins, "
            f"{sum(1 for u in users.values() if u['role'] == 'logistics')} logistics, "
            f"{sum(1 for u in users.values() if u['role'] == 'engineer')} engineers")

st.markdown("---")

# --- Add User ---
st.header("Add User")

with st.form("add_user_form"):
    col1, col2, col3 = st.columns(3)
    with col1:
        new_email = st.text_input("Email *", placeholder="e.g. name@example.com")
    with col2:
        new_name = st.text_input("Name *", placeholder="e.g. John")
    with col3:
        new_role = st.selectbox("Role", ["engineer", "logistics", "admin"])

    # Some teams use two interchangeable email domains for the same people.
    # Which pair (if any) comes from MECH_DOMAIN_ALIASES, not from source.
    add_both = bool(DOMAIN_ALIASES) and st.checkbox(
        "Also add the @%s / @%s variant" % DOMAIN_ALIASES, value=True,
        help="Adds the same person under the sister domain, so either "
             "sign-in address finds them.")

    if st.form_submit_button("Add User", type="primary"):
        if not new_email.strip() or not new_name.strip():
            st.error("Email and Name are required!")
        elif new_email.lower().strip() in users:
            st.error(f"{new_email} already exists!")
        else:
            try:
                add_user(client, new_email, new_name, new_role)
                st.success(f"Added {new_name} ({new_email}) as {new_role}")

                # Add dual domain variant
                if add_both:
                    email_lower = new_email.lower().strip()
                    variant = None
                    a, b = DOMAIN_ALIASES
                    if email_lower.endswith("@" + a):
                        variant = email_lower.split("@")[0] + "@" + b
                    elif email_lower.endswith("@" + b):
                        variant = email_lower.split("@")[0] + "@" + a

                    if variant and variant not in users:
                        add_user(client, variant, new_name, new_role)
                        st.toast(f"Also added variant: {variant}")

                st.rerun()
            except Exception as e:
                st.error(f"Failed: {e}")

st.markdown("---")

# --- Edit Role ---
st.header("Change Role")

col1, col2 = st.columns(2)
with col1:
    edit_emails = sorted(users.keys())
    edit_email = st.selectbox("Select user", edit_emails, key="edit_select")
with col2:
    if edit_email:
        current_role = users[edit_email]["role"]
        new_edit_role = st.selectbox(
            "New role",
            ["engineer", "logistics", "admin"],
            index=["engineer", "logistics", "admin"].index(current_role),
            key="edit_role",
        )

if edit_email and st.button("Update Role", key="update_role_btn"):
    if new_edit_role != current_role:
        try:
            update_user_role(client, edit_email, new_edit_role)
            st.toast(f"Updated {edit_email}: {current_role} → {new_edit_role}")
            st.rerun()
        except Exception as e:
            st.error(f"Failed: {e}")
    else:
        st.info("No change.")

st.markdown("---")

# --- Remove User ---
st.header("Remove User")
st.warning("This action cannot be undone!")

# The installation's primary admin cannot be removed here — deleting the last
# admin locks everyone out of the page that could undo it. Who that is comes
# from MECH_ADMIN_EMAIL rather than a name written into the source.
# You also cannot remove yourself, which is the same mistake by a shorter route.
_protected = (set(PRIMARY_ADMIN_EMAILS) | {user.get("email", "")}) - {""}
removable = [e for e in sorted(users.keys()) if e not in _protected]
remove_email = st.selectbox("Select user to remove", ["-- Select --"] + removable, key="remove_select")

if remove_email != "-- Select --":
    st.markdown(f"Remove **{users[remove_email]['name']}** ({remove_email}) — Role: {users[remove_email]['role']}")
    if st.button("🗑 Remove User", key="remove_btn", type="primary"):
        try:
            remove_user(client, remove_email)
            st.toast(f"Removed {remove_email}")
            st.rerun()
        except Exception as e:
            st.error(f"Failed: {e}")
