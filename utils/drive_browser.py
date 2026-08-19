"""Read Drive: list folders, find a project's sheets, and (when allowed) create.

Everything here is best-effort and returns a value rather than raising, because
the app has to stay usable when Drive is unreachable, when a folder was moved
out of the shared tree, or when a create is refused for quota reasons.

**A service account owns nothing.** In a My Drive folder, any file it creates or
copies would be owned by the service account, which has zero storage — so those
calls fail with `storage quota exceeded` no matter what permissions it holds.
That is not a bug to work around; it is why the create path is gated behind the
`drive_can_create` setting and why the manual instructions exist. Folders are
zero-byte and *may* still fail for the same ownership reason, so folder creation
is attempted, never assumed.
"""
from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from utils import app_settings

SHEET_MIME = "application/vnd.google-apps.spreadsheet"
FOLDER_MIME = "application/vnd.google-apps.folder"

# Substrings that name a project record, checked against the file name in this
# order. "record" is what the template produces; "tracker" is the older word.
RECORD_HINTS = ("record", "tracker")


def _service():
    from utils.google_client import get_drive_service

    try:
        return get_drive_service()
    except Exception:
        return None


def available() -> bool:
    return _service() is not None


def _escape(text: str) -> str:
    """Quote a value for a Drive `q` string."""
    return (text or "").replace("\\", "\\\\").replace("'", "\\'")


def _list(query: str) -> List[Dict[str, str]]:
    svc = _service()
    if svc is None:
        return []
    out, token = [], None
    try:
        while True:
            resp = svc.files().list(
                q=query, fields="nextPageToken, files(id,name,mimeType)",
                pageSize=200, pageToken=token, orderBy="name",
                supportsAllDrives=True, includeItemsFromAllDrives=True).execute()
            out.extend(resp.get("files", []))
            token = resp.get("nextPageToken")
            if not token:
                break
    except Exception:
        return []
    return out


def folder_name(folder_id: str) -> str:
    """The folder's name, or "" when it cannot be read (moved, unshared, gone)."""
    svc = _service()
    if svc is None or not folder_id:
        return ""
    try:
        return svc.files().get(fileId=folder_id, fields="name",
                               supportsAllDrives=True).execute().get("name", "")
    except Exception:
        return ""


def subfolders(folder_id: str) -> List[Dict[str, str]]:
    if not folder_id:
        return []
    return _list("'%s' in parents and mimeType='%s' and trashed=false"
                 % (_escape(folder_id), FOLDER_MIME))


def spreadsheets(folder_id: str) -> List[Dict[str, str]]:
    if not folder_id:
        return []
    return _list("'%s' in parents and mimeType='%s' and trashed=false"
                 % (_escape(folder_id), SHEET_MIME))


def child_folder(parent_id: str, name: str) -> str:
    """Id of the named subfolder, or "" if it isn't there."""
    if not parent_id or not name:
        return ""
    found = _list("'%s' in parents and mimeType='%s' and name='%s' and trashed=false"
                  % (_escape(parent_id), FOLDER_MIME, _escape(name)))
    return found[0]["id"] if found else ""


def projects_folder() -> Tuple[str, str]:
    """(folder id, problem) for the folder holding one folder per project.

    That is `<drive root>/<projects_subfolder>/`, both from Settings — the root
    is the one link an admin pastes, and the subfolder name is a convention.
    """
    root = app_settings.get(app_settings.DRIVE_ROOT_FOLDER_ID)
    if not root:
        return "", ("No Drive root folder set. Paste the root folder link "
                    "below and save it.")
    if not available():
        return "", "No Google credentials, so Drive cannot be read."
    if not folder_name(root):
        return "", ("The root folder cannot be opened — it may have been moved, "
                    "or not shared with the service account.")
    sub = app_settings.get(app_settings.PROJECTS_SUBFOLDER)
    found = child_folder(root, sub)
    if not found:
        return "", ("The root folder has no `%s` subfolder. Create one, or "
                    "change `projects_subfolder` in the main record's Settings "
                    "tab." % sub)
    return found, ""


def classify(folder_id: str, project_name: str = "") -> Dict[str, object]:
    """What a project folder holds: which sheet is the record, which the BOM.

    Name-based, deliberately: reading every file's tabs to identify it would
    cost an API call per file per render. The caller confirms the BOM by loading
    its tabs when it needs them, and can always override the guess.
    """
    sheets = spreadsheets(folder_id)
    record, bom = "", ""
    rest = []
    for f in sheets:
        low = f["name"].strip().lower()
        if not record and any(h in low for h in RECORD_HINTS):
            record = f["id"]
            continue
        rest.append(f)
    # The BOM is the sheet named after the project, else the only one left.
    want = (project_name or "").strip().lower()
    for f in rest:
        if want and f["name"].strip().lower() == want:
            bom = f["id"]
            break
    if not bom and len(rest) == 1:
        bom = rest[0]["id"]
    return {"sheets": sheets, "record": record, "bom": bom,
            "unmatched": [f for f in rest if f["id"] != bom]}


def tabs_of(sheet_id: str) -> List[str]:
    """Tab names in a spreadsheet, or [] when it can't be opened."""
    from utils.google_client import get_spreadsheet

    if not sheet_id:
        return []
    try:
        ss = get_spreadsheet(sheet_id)
    except Exception:
        return []
    if ss is None:
        return []
    try:
        return [w.title for w in ss.worksheets()]
    except Exception:
        return []


def pick_bom_tab(tabs: List[str]) -> str:
    """The tab that looks like the BOM: exactly `BOM`, else one containing it."""
    for t in tabs:
        if t.strip().lower() == "bom":
            return t
    for t in tabs:
        if "bom" in t.strip().lower():
            return t
    return ""


# --- writes, gated ---------------------------------------------------------

def can_create() -> bool:
    return app_settings.flag(app_settings.DRIVE_CAN_CREATE)


def create_folder(parent_id: str, name: str) -> Tuple[bool, str]:
    """(ok, folder id or error). Refused unless the create switch is on."""
    if not can_create():
        return False, ("Creating in Drive is switched off — the service account "
                       "owns no storage. Turn it on under Status once the folder "
                       "lives in a Shared Drive.")
    svc = _service()
    if svc is None:
        return False, "No Google credentials."
    try:
        f = svc.files().create(
            body={"name": name, "mimeType": FOLDER_MIME, "parents": [parent_id]},
            fields="id", supportsAllDrives=True).execute()
        return True, f["id"]
    except Exception as exc:
        return False, _reason(exc)


def copy_file(file_id: str, name: str, parent_id: str) -> Tuple[bool, str]:
    """(ok, new file id or error). Refused unless the create switch is on."""
    if not can_create():
        return False, ("Copying in Drive is switched off — the service account "
                       "owns no storage, so the copy would fail. Turn it on "
                       "under Status once the folder lives in a Shared Drive.")
    svc = _service()
    if svc is None:
        return False, "No Google credentials."
    try:
        f = svc.files().copy(fileId=file_id,
                             body={"name": name, "parents": [parent_id]},
                             fields="id", supportsAllDrives=True).execute()
        return True, f["id"]
    except Exception as exc:
        return False, _reason(exc)


def _reason(exc: Exception) -> str:
    """The message Google actually gave, not the HTTP wrapper around it."""
    import re

    text = str(exc)
    match = re.search(r'returned "(.*?)"', text)
    detail = match.group(1) if match else text.split("\n")[0]
    if "storage quota" in detail.lower():
        return (detail + " — the service account owns no Drive storage. The "
                "folder has to live in a Shared Drive before the app can "
                "create anything in it.")
    return detail
