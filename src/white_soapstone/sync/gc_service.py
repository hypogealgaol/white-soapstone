"""Manual, safe cleanup of the shared content pool.

Deliberately separate from a normal sync (see sync_service.run_sync / drive/upload.py)
- deletes only after checking *every* current user's manifest, so one person's sync
can never accidentally remove a file a peer still needs based on stale or incomplete
local knowledge. If any user's manifest can't be downloaded or fails validation, the
whole run aborts rather than proceeding on incomplete information - better to leave
orphans sitting around than to risk deleting something someone still needs.
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass
from pathlib import Path

from ..config import paths, store
from ..drive import client as drive_client
from ..drive import download as drive_download
from ..drive.auth import get_credentials
from ..manifest.reader import ManifestValidationError, parse_manifest_file
from .sync_service import ensure_content_pool_folder


@dataclass
class GcResult:
    users_checked: int
    content_files_checked: int
    orphans_deleted: int


def run_gc(client_secrets_path: str | Path) -> GcResult:
    config = store.load()
    if not config.drive_shared_folder_id:
        raise RuntimeError("No shared Drive folder id configured. Run `white-soapstone init` first.")

    creds = get_credentials(client_secrets_path)
    service = drive_client.build_service(creds)
    config = ensure_content_pool_folder(service, config)

    user_folders = drive_download.list_user_folders(service, config.drive_shared_folder_id)

    referenced_ids: set[str] = set()
    manifest_tmp = paths.cache_dir() / "gc_manifest_tmp.json"
    try:
        for folder in user_folders:
            downloaded = drive_download.download_manifest(service, folder["id"], manifest_tmp)
            if downloaded is None:
                continue  # subfolder exists but nothing was ever published - nothing to reference
            try:
                manifest = parse_manifest_file(manifest_tmp)
            except ManifestValidationError as exc:
                raise RuntimeError(
                    f"Refusing to clean up: {folder['name']}'s manifest.json couldn't be read "
                    f"({exc}). Fix or re-sync it first - gc won't run on incomplete information, "
                    "since that risks deleting content someone else still needs."
                ) from exc
            for track in manifest["tracks"]:
                referenced_ids.add(track["id"])
    finally:
        # See pull_service.py - unlink can transiently fail on Windows (e.g. AV
        # scanning a just-downloaded file); harmless, the path is overwritten next run.
        with contextlib.suppress(OSError):
            manifest_tmp.unlink(missing_ok=True)

    pool_files = drive_client.list_children(service, config.content_pool_folder_id)
    deleted = 0
    for f in pool_files:
        cid = f["name"].removesuffix(".mp3")
        if cid not in referenced_ids:
            drive_client.delete_file(service, f["id"])
            deleted += 1

    return GcResult(
        users_checked=len(user_folders),
        content_files_checked=len(pool_files),
        orphans_deleted=deleted,
    )
