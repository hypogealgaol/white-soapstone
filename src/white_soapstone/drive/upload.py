"""Publishes a user's manifest, and deduplicated preview audio, to the shared Drive folder.

Preview files live in one shared `_content/` pool (a direct child of the shared
folder, not per-user) keyed by content id - see manifest/builder.py:content_id - so
the same song whitelisted by multiple people only gets uploaded once. Because a
content file can be referenced by *other* users' manifests even after this user
un-whitelists it, pruning orphans is deliberately NOT done here as a side effect of a
normal sync - see sync/gc_service.py, which is the only thing that ever deletes from
the pool, and only after checking every current manifest.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from googleapiclient.discovery import Resource

from . import client as drive_client

# Called as on_progress(files_done, files_total) while uploading content files - the
# slow part of a sync once transcoding is done, since it's real network transfer.
ProgressCallback = Callable[[int, int], None]

MANIFEST_MIME = "application/json"
PREVIEW_MIME = "audio/mpeg"
MANIFEST_NAME = "manifest.json"
CONTENT_POOL_FOLDER_NAME = "_content"


def resolve_folder_name(handle: str, google_user_id: str) -> str:
    return f"{handle}__{google_user_id[:8]}"


def ensure_user_folder(service: Resource, shared_folder_id: str, folder_name: str) -> str:
    return drive_client.find_or_create_folder(service, shared_folder_id, folder_name)


def ensure_content_pool_folder(service: Resource, shared_folder_id: str) -> str:
    return drive_client.find_or_create_folder(service, shared_folder_id, CONTENT_POOL_FOLDER_NAME)


def upload_content_pool_files(
    service: Resource,
    content_pool_folder_id: str,
    content_files: dict[str, Path],
    on_progress: ProgressCallback | None = None,
    should_stop: Callable[[], bool] | None = None,
) -> bool:
    """`content_files` maps a content file's name (e.g. "<content_id>.mp3") to its local
    path. A file already present in the pool under that exact name is skipped rather
    than re-uploaded - it's the same content by definition (that's what the name is a
    hash of), whether this user or a peer uploaded it originally.

    Returns True if `should_stop` fired before finishing.
    """
    existing = {f["name"] for f in drive_client.list_children(service, content_pool_folder_id)}
    total = len(content_files)

    for done, (name, local_path) in enumerate(content_files.items(), start=1):
        if should_stop and should_stop():
            return True
        if name not in existing:
            drive_client.upload_file(service, content_pool_folder_id, name, local_path, PREVIEW_MIME)
        if on_progress:
            on_progress(done, total)

    return False


def upload_manifest(service: Resource, user_folder_id: str, manifest_local_path: str | Path) -> None:
    """Uploaded only after upload_content_pool_files() succeeds (see sync_service.run_sync)
    so manifest.json never ends up referencing a content file that isn't actually there yet."""
    existing_manifest_id = drive_client.find_child(service, user_folder_id, MANIFEST_NAME)
    drive_client.upload_file(
        service, user_folder_id, MANIFEST_NAME, manifest_local_path, MANIFEST_MIME, existing_manifest_id
    )
