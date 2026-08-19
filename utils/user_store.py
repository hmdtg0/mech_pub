"""User management via the CENTRAL database sheet's Users tab.

One user list for the whole app — roles don't vary per project.
"""
from __future__ import annotations

from typing import Optional

import gspread

from config import (CENTRAL_SHEET_ID, PRIMARY_ADMIN_EMAIL,
                    PRIMARY_ADMIN_NAME)
from utils import data_cache, settings
from utils.google_client import with_worksheet

TAB_USERS = "Users"
# TTL is a runtime setting (Status page), not a constant.


def _cache_key() -> str:
    # One central sheet, one cache — shared across every project.
    return f"{CENTRAL_SHEET_ID}:users"

# Who to fall back on when the Users tab is empty or unreadable. Comes from
# the environment (MECH_ADMIN_EMAIL) rather than being written into the
# source, so no one person's address ships with the code. Empty is valid and
# means nobody gets in that way — see config.PRIMARY_ADMIN_EMAIL.
FALLBACK_ADMIN = {"email": PRIMARY_ADMIN_EMAIL, "name": PRIMARY_ADMIN_NAME,
                  "role": "admin"}


def _create_users_ws(ss: gspread.Spreadsheet) -> gspread.Worksheet:
    """Create the Users worksheet (auto-create when missing)."""
    ws = ss.add_worksheet(title=TAB_USERS, rows=50, cols=3)
    ws.update(values=[["Email", "Name", "Role"]], range_name="A1:C1")
    # Seed the first admin, if the installation names one.
    if FALLBACK_ADMIN["email"]:
        ws.update(values=[[FALLBACK_ADMIN["email"], FALLBACK_ADMIN["name"],
                           FALLBACK_ADMIN["role"]]], range_name="A2:C2")
    return ws


def _on_users_ws(fn):
    return with_worksheet(TAB_USERS, fn, create=_create_users_ws,
                          sheet_id=CENTRAL_SHEET_ID)


def _load_users() -> dict:
    """Load users from the Sheet. Returns {email: {name, role}}."""
    try:
        all_values = _on_users_ws(lambda ws: ws.get_all_values())
        if not all_values or len(all_values) < 2:
            if not FALLBACK_ADMIN["email"]:
                return {}
            return {FALLBACK_ADMIN["email"]: {"name": FALLBACK_ADMIN["name"],
                                              "role": FALLBACK_ADMIN["role"]}}

        users = {}
        for row in all_values[1:]:  # Skip header
            if len(row) >= 3 and row[0].strip():
                email = row[0].strip().lower()
                name = row[1].strip()
                role = row[2].strip().lower()
                if role not in ("admin", "engineer", "logistics"):
                    role = "engineer"
                users[email] = {"name": name, "role": role}

        # Always ensure fallback admin exists
        if FALLBACK_ADMIN["email"] not in users:
            users[FALLBACK_ADMIN["email"]] = {"name": FALLBACK_ADMIN["name"], "role": FALLBACK_ADMIN["role"]}

        return users
    except Exception:
        return {FALLBACK_ADMIN["email"]: {"name": FALLBACK_ADMIN["name"], "role": FALLBACK_ADMIN["role"]}}


def fetch_allowed_users() -> dict:
    """Get the allowed users dict (cached 60s)."""
    return data_cache.get(_cache_key(), settings.users_ttl(), _load_users)


def _clear_cache():
    data_cache.invalidate(_cache_key())


def add_user(client: gspread.Client, email: str, name: str, role: str):
    """Add a user to the Users tab."""
    from utils.auth import impersonation_block
    blocked = impersonation_block()
    if blocked:
        # A clean stop, not an exception: the cloud redacts tracebacks into
        # noise, and "why can't I save" deserves a sentence, not a stack.
        import streamlit as st
        st.error(blocked)
        st.stop()
    def _do(ws: gspread.Worksheet):
        col_a = ws.col_values(1)
        next_row = len(col_a) + 1
        ws.update(values=[[email.lower().strip(), name.strip(), role.lower().strip()]],
                  range_name=f"A{next_row}:C{next_row}")

    _on_users_ws(_do)
    _clear_cache()


def remove_user(client: gspread.Client, email: str):
    """Remove a user from the Users tab by email."""
    from utils.auth import impersonation_block
    blocked = impersonation_block()
    if blocked:
        # A clean stop, not an exception: the cloud redacts tracebacks into
        # noise, and "why can't I save" deserves a sentence, not a stack.
        import streamlit as st
        st.error(blocked)
        st.stop()
    if email.lower().strip() == FALLBACK_ADMIN["email"]:
        raise ValueError("Cannot remove the primary admin account")

    target_email = email.lower().strip()

    def _do(ws: gspread.Worksheet):
        all_values = ws.get_all_values()
        for row_idx, row in enumerate(all_values[1:], start=2):
            if row[0].strip().lower() == target_email:
                ws.delete_rows(row_idx)
                _clear_cache()
                return
        raise ValueError(f"User {email} not found")

    _on_users_ws(_do)


def update_user_role(client: gspread.Client, email: str, new_role: str):
    """Update a user's role."""
    from utils.auth import impersonation_block
    blocked = impersonation_block()
    if blocked:
        # A clean stop, not an exception: the cloud redacts tracebacks into
        # noise, and "why can't I save" deserves a sentence, not a stack.
        import streamlit as st
        st.error(blocked)
        st.stop()
    target_email = email.lower().strip()

    def _do(ws: gspread.Worksheet):
        all_values = ws.get_all_values()
        for row_idx, row in enumerate(all_values[1:], start=2):
            if row[0].strip().lower() == target_email:
                ws.update_cell(row_idx, 3, new_role.lower().strip())
                _clear_cache()
                return
        raise ValueError(f"User {email} not found")

    _on_users_ws(_do)
