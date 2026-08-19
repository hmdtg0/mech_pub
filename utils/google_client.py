"""Google API client using Service Account for headless (Cloud) deployment."""
from __future__ import annotations

import json
import os
import threading
from typing import Callable, Optional

import gspread
from google.oauth2.service_account import Credentials

from config import GOOGLE_SHEET_ID, SERVICE_ACCOUNT_FILE, IS_LOCAL

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

_gspread_client: Optional[gspread.Client] = None
_drive_service = None

# Cached Spreadsheet/Worksheet handles, keyed by sheet id so several project
# Sheets can be open in one process. open_by_key() + worksheet() each cost an
# API round trip (~0.7s combined), so resolving them once per process matters.
_spreadsheets: dict[str, gspread.Spreadsheet] = {}
_worksheets: dict[tuple[str, str], gspread.Worksheet] = {}
_handles_lock = threading.Lock()


def active_sheet_id() -> str:
    """The sheet id of the currently selected project (session-scoped),
    falling back to the configured default outside a Streamlit session."""
    try:
        import streamlit as st
        sid = st.session_state.get("active_sheet_id", "")
        if sid:
            return sid
    except Exception:
        pass
    return GOOGLE_SHEET_ID


# Resolved once per process. Every store asks for credentials several times per
# rerun, so a failure must be remembered — not re-reported on each call.
_creds: Optional[Credentials] = None
_creds_checked = False
CREDENTIALS_ERROR = ""


def _secrets_file_exists() -> bool:
    """Whether Streamlit has a secrets.toml to read (app dir or user home)."""
    app_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    candidates = [
        os.path.join(app_dir, ".streamlit", "secrets.toml"),
        os.path.join(os.getcwd(), ".streamlit", "secrets.toml"),
        os.path.join(os.path.expanduser("~"), ".streamlit", "secrets.toml"),
    ]
    return any(os.path.exists(p) for p in candidates)


def _load_credentials() -> Optional[Credentials]:
    """Load service account credentials from file or Streamlit secrets.

    Returns None when neither is present; the reason is left in
    CREDENTIALS_ERROR for the app to surface once (see credentials_status()).
    """
    global _creds, _creds_checked, CREDENTIALS_ERROR
    if _creds_checked:
        return _creds
    _creds_checked = True

    # Try local file first
    if os.path.exists(SERVICE_ACCOUNT_FILE):
        _creds = Credentials.from_service_account_file(SERVICE_ACCOUNT_FILE, scopes=SCOPES)
        return _creds

    # Try Streamlit secrets (for Cloud deployment). Guarded by the file check:
    # touching st.secrets with no secrets.toml present makes Streamlit paint a
    # red "No secrets found" box before it raises, which try/except can't undo.
    if not _secrets_file_exists():
        CREDENTIALS_ERROR = "No service_account.json and no secrets.toml."
        return None

    try:
        import streamlit as st
        if "gcp_service_account" in st.secrets:
            sa_info = st.secrets["gcp_service_account"]
            # Convert AttrDict to regular dict and ensure proper types
            sa_dict = dict(sa_info)
            # private_key may have literal \n that need to be actual newlines
            if "private_key" in sa_dict:
                sa_dict["private_key"] = sa_dict["private_key"].replace("\\n", "\n")
            _creds = Credentials.from_service_account_info(sa_dict, scopes=SCOPES)
            return _creds
        CREDENTIALS_ERROR = "No [gcp_service_account] entry in Streamlit secrets."
    except Exception as e:
        CREDENTIALS_ERROR = str(e).split("\n")[0]

    return None


def credentials_status() -> str:
    """"" when Google is reachable, else a one-line reason."""
    if _load_credentials() is not None:
        return ""
    return CREDENTIALS_ERROR or "No service account credentials found."


def get_gspread_client() -> Optional[gspread.Client]:
    """Get authenticated gspread client using Service Account."""
    global _gspread_client
    if _gspread_client is not None:
        return _gspread_client

    creds = _load_credentials()
    if creds is None:
        return None

    _gspread_client = gspread.authorize(creds)
    return _gspread_client


def get_spreadsheet(sheet_id: Optional[str] = None) -> Optional[gspread.Spreadsheet]:
    """Get a project's spreadsheet — the active one by default — resolving it
    via the API only once per sheet id.

    Passing an explicit id lets a page read several projects in one render
    (the all-projects parts view) without touching the session's selection.
    """
    sid = sheet_id or active_sheet_id()
    ss = _spreadsheets.get(sid)
    if ss is not None:
        return ss
    client = get_gspread_client()
    if client is None:
        return None
    with _handles_lock:
        ss = _spreadsheets.get(sid)
        if ss is None:
            ss = client.open_by_key(sid)
            _spreadsheets[sid] = ss
    return ss


def get_worksheet(tab: str,
                  create: Optional[Callable[[gspread.Spreadsheet], gspread.Worksheet]] = None,
                  sheet_id: Optional[str] = None) -> Optional[gspread.Worksheet]:
    """Get a worksheet handle by tab name, cached for the process lifetime.

    Defaults to the active project's sheet; pass `sheet_id` for a fixed sheet
    (the central database). If the tab doesn't exist and `create` is given,
    it's called with the spreadsheet to build the tab (used for the
    auto-created Orders/Messages/Users tabs).
    """
    key = (sheet_id or active_sheet_id(), tab)
    ws = _worksheets.get(key)
    if ws is not None:
        return ws
    ss = get_spreadsheet(sheet_id)
    if ss is None:
        return None
    with _handles_lock:
        ws = _worksheets.get(key)
        if ws is None:
            try:
                ws = ss.worksheet(tab)
            except gspread.exceptions.WorksheetNotFound:
                if create is None:
                    raise
                ws = create(ss)
            _worksheets[key] = ws
    return ws


def invalidate_handles() -> None:
    """Drop cached Spreadsheet/Worksheet handles (e.g. after a tab rename)."""
    with _handles_lock:
        _spreadsheets.clear()
        _worksheets.clear()


def _is_stale_handle_error(e: Exception) -> bool:
    """A 400/404 APIError usually means the tab was renamed/deleted under us."""
    if isinstance(e, gspread.exceptions.WorksheetNotFound):
        return True
    if isinstance(e, gspread.exceptions.APIError):
        code = getattr(e, "code", None)
        if code is None:
            try:
                code = e.response.status_code
            except Exception:
                return False
        return code in (400, 404)
    return False


def with_worksheet(tab: str, fn: Callable[[gspread.Worksheet], object],
                   create: Optional[Callable] = None,
                   sheet_id: Optional[str] = None):
    """Run `fn(ws)` against a cached worksheet handle.

    Defaults to the active project's sheet; pass `sheet_id` for a fixed sheet
    (the central database). On a stale-handle error (tab renamed/deleted since
    the handle was cached), drops all handles, re-resolves once and retries
    once. Quota/server errors (429/5xx) are NOT retried.
    """
    ws = get_worksheet(tab, create=create, sheet_id=sheet_id)
    if ws is None:
        return None
    try:
        return fn(ws)
    except (gspread.exceptions.WorksheetNotFound, gspread.exceptions.APIError) as e:
        if not _is_stale_handle_error(e):
            raise
        invalidate_handles()
        ws = get_worksheet(tab, create=create, sheet_id=sheet_id)
        if ws is None:
            raise
        return fn(ws)


def get_drive_service():
    """Get Google Drive API v3 service object."""
    global _drive_service
    if _drive_service is not None:
        return _drive_service

    creds = _load_credentials()
    if creds is None:
        return None

    from googleapiclient.discovery import build
    _drive_service = build("drive", "v3", credentials=creds)
    return _drive_service
