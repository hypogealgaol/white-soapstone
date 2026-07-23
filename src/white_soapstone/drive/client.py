"""Thin wrapper over the Drive v3 API - just the handful of calls this app needs.

Every listing call is scoped with `supportsAllDrives`/`includeItemsFromAllDrives` off
by default since this targets a regular shared folder, not a Shared Drive; flip those
on later if the group ends up using an actual Shared Drive instead.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import Resource, build
from googleapiclient.http import MediaFileUpload, MediaIoBaseDownload

FOLDER_MIME = "application/vnd.google-apps.folder"


def build_service(creds: Credentials) -> Resource:
    return build("drive", "v3", credentials=creds, cache_discovery=False)


def find_child(service: Resource, parent_id: str, name: str, mime_type: str | None = None) -> str | None:
    """Returns the id of a non-trashed child of `parent_id` named exactly `name`, or None."""
    safe_name = name.replace("'", "\\'")
    query = f"'{parent_id}' in parents and name = '{safe_name}' and trashed = false"
    if mime_type:
        query += f" and mimeType = '{mime_type}'"
    resp = service.files().list(q=query, fields="files(id, name)", pageSize=1).execute()
    files = resp.get("files", [])
    return files[0]["id"] if files else None


def list_children(service: Resource, parent_id: str, mime_type: str | None = None) -> list[dict[str, str]]:
    query = f"'{parent_id}' in parents and trashed = false"
    if mime_type:
        query += f" and mimeType = '{mime_type}'"
    files: list[dict[str, str]] = []
    page_token = None
    while True:
        resp = (
            service.files()
            .list(q=query, fields="nextPageToken, files(id, name)", pageToken=page_token, pageSize=100)
            .execute()
        )
        files.extend(resp.get("files", []))
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
    return files


def create_folder(service: Resource, parent_id: str, name: str) -> str:
    metadata = {"name": name, "mimeType": FOLDER_MIME, "parents": [parent_id]}
    created = service.files().create(body=metadata, fields="id").execute()
    return created["id"]


def find_or_create_folder(service: Resource, parent_id: str, name: str) -> str:
    existing = find_child(service, parent_id, name, mime_type=FOLDER_MIME)
    if existing:
        return existing
    return create_folder(service, parent_id, name)


def upload_file(
    service: Resource,
    parent_id: str,
    name: str,
    local_path: str | Path,
    mime_type: str,
    existing_file_id: str | None = None,
) -> str:
    """Creates the file if `existing_file_id` is None, otherwise updates its content in place."""
    media = MediaFileUpload(str(local_path), mimetype=mime_type, resumable=False)
    if existing_file_id:
        updated = service.files().update(fileId=existing_file_id, media_body=media).execute()
        return updated["id"]
    metadata = {"name": name, "parents": [parent_id]}
    created = service.files().create(body=metadata, media_body=media, fields="id").execute()
    return created["id"]


def download_file(service: Resource, file_id: str, dest_path: str | Path) -> Path:
    dest_path = Path(dest_path)
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    request = service.files().get_media(fileId=file_id)
    with dest_path.open("wb") as fh:
        downloader = MediaIoBaseDownload(fh, request)
        done = False
        while not done:
            _, done = downloader.next_chunk()
    return dest_path


def delete_file(service: Resource, file_id: str) -> None:
    service.files().delete(fileId=file_id).execute()


def rename_file(service: Resource, file_id: str, new_name: str) -> None:
    service.files().update(fileId=file_id, body={"name": new_name}).execute()


def get_about_user(service: Resource) -> dict[str, Any]:
    resp = service.about().get(fields="user").execute()
    return resp["user"]
