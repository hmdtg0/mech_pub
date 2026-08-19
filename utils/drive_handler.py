"""Google Drive file upload/download handler."""
from __future__ import annotations

import io
import os
from datetime import datetime
from typing import Optional

from config import DRIVE_FOLDER_ID, LOCAL_DOWNLOAD_BASE, IS_LOCAL


def _get_or_create_folder(service, parent_id: str, folder_name: str) -> str:
    """Find or create a subfolder under parent_id. Returns folder ID."""
    query = (
        f"'{parent_id}' in parents and "
        f"name = '{folder_name}' and "
        f"mimeType = 'application/vnd.google-apps.folder' and "
        f"trashed = false"
    )
    results = service.files().list(
        q=query, fields="files(id, name)",
        supportsAllDrives=True, includeItemsFromAllDrives=True,
    ).execute()
    files = results.get("files", [])

    if files:
        return files[0]["id"]

    # Create folder
    metadata = {
        "name": folder_name,
        "mimeType": "application/vnd.google-apps.folder",
        "parents": [parent_id],
    }
    folder = service.files().create(
        body=metadata, fields="id", supportsAllDrives=True,
    ).execute()
    return folder["id"]


def upload_file(
    file_bytes: bytes,
    filename: str,
    part_name: str,
    order_date: str | None = None,
) -> str:
    """Upload a file to Google Drive with organized folder structure.

    Creates: Mech_Orders/YYYY/YYYY-MM/YYYYMMDD_PartName/filename

    Args:
        file_bytes: raw file content
        filename: original filename (e.g., "housing.step")
        part_name: part/project name
        order_date: date string "YYYY-MM-DD" or None for today

    Returns:
        Google Drive web view link for the uploaded file
    """
    if not DRIVE_FOLDER_ID:
        raise RuntimeError(
            "MECH_DRIVE_FOLDER_ID is not configured — create a Drive folder, "
            "share it with the service account, and set the ID (see SETUP.md)")

    from utils.google_client import get_drive_service
    service = get_drive_service()
    if service is None:
        raise RuntimeError("Google Drive service not available")

    if order_date:
        dt = datetime.strptime(order_date, "%Y-%m-%d")
    else:
        dt = datetime.now()

    year_folder = dt.strftime("%Y")            # e.g. "2026"
    month_folder = dt.strftime("%Y-%m")        # e.g. "2026-03"
    project_folder = f"{dt.strftime('%Y%m%d')}_{part_name}"  # e.g. "20260806_EZ1_Housing_revC"

    # Create nested folders: Year → Month → Project
    year_id = _get_or_create_folder(service, DRIVE_FOLDER_ID, year_folder)
    month_id = _get_or_create_folder(service, year_id, month_folder)
    project_id = _get_or_create_folder(service, month_id, project_folder)

    # Upload file
    from googleapiclient.http import MediaIoBaseUpload

    media = MediaIoBaseUpload(
        io.BytesIO(file_bytes),
        mimetype="application/octet-stream",
        resumable=True,
    )
    file_metadata = {
        "name": filename,
        "parents": [project_id],
    }
    uploaded = service.files().create(
        body=file_metadata,
        media_body=media,
        fields="id, webViewLink",
        supportsAllDrives=True,
    ).execute()

    return uploaded.get("webViewLink", "")


def download_file_bytes(file_link: str) -> tuple[bytes, str]:
    """Download a file from Google Drive by its web link.

    Args:
        file_link: Google Drive web view link

    Returns:
        Tuple of (file_bytes, filename)
    """
    from utils.google_client import get_drive_service
    service = get_drive_service()
    if service is None:
        raise RuntimeError("Google Drive service not available")

    # Extract file ID from link
    file_id = _extract_file_id(file_link)
    if not file_id:
        raise ValueError(f"Cannot extract file ID from link: {file_link}")

    # Get file metadata for filename
    file_meta = service.files().get(
        fileId=file_id, fields="name", supportsAllDrives=True,
    ).execute()
    filename = file_meta.get("name", "download")

    # Download content
    from googleapiclient.http import MediaIoBaseDownload
    request = service.files().get_media(fileId=file_id)
    buffer = io.BytesIO()
    downloader = MediaIoBaseDownload(buffer, request)
    done = False
    while not done:
        _, done = downloader.next_chunk()

    buffer.seek(0)
    return buffer.read(), filename


def download_to_local(file_link: str, part_name: str, order_date: str) -> str:
    """Download file to local filesystem in the correct folder structure.

    Only works in local mode. Creates:
    LOCAL_DOWNLOAD_BASE/YYYY/YYYY-MM/YYYYMMDD_PartName/filename

    Returns:
        Local file path
    """
    file_bytes, filename = download_file_bytes(file_link)

    dt = datetime.strptime(order_date, "%Y-%m-%d")
    year_folder = dt.strftime("%Y")
    month_folder = dt.strftime("%Y-%m")
    project_folder = f"{dt.strftime('%Y%m%d')}_{part_name}"

    local_dir = os.path.join(LOCAL_DOWNLOAD_BASE, year_folder, month_folder, project_folder)
    os.makedirs(local_dir, exist_ok=True)

    local_path = os.path.join(local_dir, filename)
    with open(local_path, "wb") as f:
        f.write(file_bytes)

    return local_path


def _extract_file_id(link: str) -> str | None:
    """Extract Google Drive file ID from various link formats."""
    import re
    # Format: https://drive.google.com/file/d/FILE_ID/view...
    match = re.search(r"/file/d/([a-zA-Z0-9_-]+)", link)
    if match:
        return match.group(1)
    # Format: https://drive.google.com/open?id=FILE_ID
    match = re.search(r"[?&]id=([a-zA-Z0-9_-]+)", link)
    if match:
        return match.group(1)
    # Maybe it's just the file ID
    if re.match(r"^[a-zA-Z0-9_-]{20,}$", link):
        return link
    return None
