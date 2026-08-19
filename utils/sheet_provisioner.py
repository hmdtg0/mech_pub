"""Create a project's Google Sheet by copying a template.

Copying beats asking someone to paste a sheet id: nobody mistypes an id,
nobody pastes the wrong sheet, the app never touches a sheet it wasn't handed,
and every project starts with the tab structure the readers expect
(Overview / part tabs / Movement Log).

The copy is made by the service account, so:

- with `MECH_PROJECTS_FOLDER_ID` set, the new sheet lands in that shared Drive
  folder and the team sees it straight away (recommended);
- without it, the sheet lives in the service account's own Drive and is shared
  with whoever created it, as writer.
"""
from __future__ import annotations

from typing import Tuple

from config import PROJECTS_FOLDER_ID, TEMPLATE_SHEET_ID
from utils.google_client import get_drive_service


def sheet_url(sheet_id: str) -> str:
    return "https://docs.google.com/spreadsheets/d/%s/edit" % sheet_id


def create_from_template(project_name: str, template_id: str = "",
                         share_with: str = "") -> Tuple[bool, str, str]:
    """Copy `template_id` into a new project sheet.

    Returns (ok, sheet id or error message, warning). The warning is non-empty
    when the sheet was created but something non-fatal failed (e.g. sharing).
    """
    name = (project_name or "").strip()
    if not name:
        return False, "Project name is required.", ""

    template = (template_id or TEMPLATE_SHEET_ID).strip()
    if not template:
        return False, ("No template sheet chosen. Pick one to copy, or set "
                       "MECH_TEMPLATE_SHEET_ID to a blank tracker template."), ""

    service = get_drive_service()
    if service is None:
        return False, "No Google credentials — cannot create sheets.", ""

    body = {"name": "%s — Parts Tracker" % name}
    if PROJECTS_FOLDER_ID:
        body["parents"] = [PROJECTS_FOLDER_ID]

    try:
        created = service.files().copy(
            fileId=template, body=body, fields="id", supportsAllDrives=True,
        ).execute()
    except Exception as e:  # Drive surfaces permission/quota problems here
        return False, "Could not copy the template: %s" % e, ""

    sheet_id = created.get("id", "")
    if not sheet_id:
        return False, "Drive returned no id for the new sheet.", ""

    if share_with:
        try:
            service.permissions().create(
                fileId=sheet_id,
                body={"type": "user", "role": "writer", "emailAddress": share_with},
                sendNotificationEmail=False, supportsAllDrives=True,
            ).execute()
        except Exception as e:
            # The sheet exists and the app can read it; only the human's access
            # failed, so report it rather than failing the whole operation.
            return True, sheet_id, "Sheet created, but sharing it with %s failed: %s" % (share_with, e)

    return True, sheet_id, ""
