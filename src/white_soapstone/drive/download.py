"""Reads other users' published data back out of the shared Drive folder."""

from __future__ import annotations

from pathlib import Path

from googleapiclient.discovery import Resource

from . import client as drive_client
from .upload import CONTENT_POOL_FOLDER_NAME, MANIFEST_NAME


def list_user_folders(service: Resource, shared_folder_id: str) -> list[dict[str, str]]:
    """Every immediate subfolder of the shared folder is one user's publish folder.

    Excludes the shared `_content` pool itself, which lives alongside them as a sibling.
    """
    folders = drive_client.list_children(service, shared_folder_id, mime_type=drive_client.FOLDER_MIME)
    return [f for f in folders if f["name"] != CONTENT_POOL_FOLDER_NAME]


def download_manifest(service: Resource, user_folder_id: str, dest_path: str | Path) -> Path | None:
    """Returns the local path the manifest was downloaded to, or None if this user
    hasn't published a manifest.json yet (e.g. their subfolder exists but is empty)."""
    manifest_id = drive_client.find_child(service, user_folder_id, MANIFEST_NAME)
    if manifest_id is None:
        return None
    return drive_client.download_file(service, manifest_id, dest_path)


def download_content_file(
    service: Resource,
    content_pool_folder_id: str,
    content_filename: str,
    dest_path: str | Path,
) -> Path | None:
    """Downloads one file from the shared `_content` pool, or None if it isn't there."""
    file_id = drive_client.find_child(service, content_pool_folder_id, content_filename)
    if file_id is None:
        return None
    return drive_client.download_file(service, file_id, dest_path)
