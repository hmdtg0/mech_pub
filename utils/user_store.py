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
    ws = ss.add_worksheet(title=TAB_USERS, rows=50, cols=4)
    ws.update(values=[["Email", "Name", "Role", "Pinned project"]],
              range_name="A1:D1")
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
                                              "role": FALLBACK_ADMIN["role"],
                                              "pinned": ""}}

        users = {}
        for row in all_values[1:]:  # Skip header
            if len(row) >= 3 and row[0].strip():
                email = row[0].strip().lower()
                name = row[1].strip()
                role = row[2].strip().lower()
                if role not in ("admin", "engineer", "logistics"):
                    role = "engineer"
                # Column D is the project this person lands on. Read with a
                # length check, not an index: every Users tab written before
                # 21 Aug has three columns and must keep working untouched.
                pinned = row[3].strip() if len(row) >= 4 else ""
                users[email] = {"name": name, "role": role, "pinned": pinned}

        # Always ensure fallback admin exists
        if FALLBACK_ADMIN["email"] not in users:
            users[FALLBACK_ADMIN["email"]] = {"name": FALLBACK_ADMIN["name"],
                                              "role": FALLBACK_ADMIN["role"],
                                              "pinned": ""}

        return users
    except Exception:
        return {FALLBACK_ADMIN["email"]: {"name": FALLBACK_ADMIN["name"],
                                          "role": FALLBACK_ADMIN["role"],
                                          "pinned": ""}}


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


def pinned_project(email: str) -> str:
    """The project this person lands on, or "" for the app's own default."""
    return fetch_allowed_users().get(str(email or "").lower().strip(),
                                     {}).get("pinned", "")


def set_pinned_project(email: str, project: str) -> str:
    """Pin (or unpin, with "") this person's landing project.

    Their own row, their own cell — no other user's line is touched, and the
    write happens on the pin click rather than on every project switch, which
    would put a sheet write behind an ordinary UI control.

    Returns "" on success, or a sentence saying why not.
    """
    from utils.auth import impersonation_block
    blocked = impersonation_block()
    if blocked:
        return blocked
    target = str(email or "").lower().strip()
    if not target:
        return "No account to pin against."

    def _do(ws: gspread.Worksheet):
        values = ws.get_all_values()
        header = [h.strip().lower() for h in (values[0] if values else [])]
        if "pinned project" not in header:
            ws.update_cell(1, 4, "Pinned project")
        for row_idx, row in enumerate(values[1:], start=2):
            if row and row[0].strip().lower() == target:
                ws.update_cell(row_idx, 4, str(project or "").strip())
                _clear_cache()
                return ""
        return "%s is not on the Users tab." % email

    try:
        return _on_users_ws(_do) or ""
    except Exception as exc:
        return "Could not save the pin: %s" % exc


# name_of runs once per table ROW, and fetch_allowed_users deep-copies the
# whole users dict on every cache hit — 136 rows cost half a second of pure
# copying (measured, 24 Aug). The map below is rebuilt only when the cache
# actually reloaded, so a hit is a plain dict lookup.
_NAMES_MEMO = {"wall": None, "map": {}}


def _email_names() -> dict:
    stamp = data_cache.read_at(_cache_key())
    wall = stamp[0] if stamp else None
    if wall is None or _NAMES_MEMO["wall"] != wall:
        _NAMES_MEMO["map"] = {e: (i.get("name") or e)
                              for e, i in fetch_allowed_users().items()}
        stamp = data_cache.read_at(_cache_key())
        _NAMES_MEMO["wall"] = stamp[0] if stamp else None
    return _NAMES_MEMO["map"]


def name_of(value: str) -> str:
    """The person behind a logged-by cell, for DISPLAY only.

    The sheet keeps the email — the durable, unambiguous identity — and the
    screen shows the Users tab's Name for it (Hamid, 24 Aug: "use the name
    associated to the email, no need to change the data"). Anything that is
    not a known email passes through untouched, so hand-typed names and
    legacy values keep reading as written.
    """
    raw = str(value or "").strip()
    low = raw.lower()
    if "@" not in low:
        return raw
    return _email_names().get(low) or raw
