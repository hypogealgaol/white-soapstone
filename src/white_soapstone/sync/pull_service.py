"""Pulls every user's manifest.json from the shared Drive folder into the local cache.

A bad manifest from one peer (corrupt upload, still mid-write, unsupported future
schema version) shouldn't block ingesting everyone else's - failures are collected
and returned rather than raised.
"""

from __future__ import annotations

import contextlib
import hashlib
from dataclasses import dataclass, field
from pathlib import Path

from ..cache import db as cache_db
from ..config import paths, store
from ..drive import client as drive_client
from ..drive import download as drive_download
from ..drive.auth import get_credentials
from ..manifest.reader import ManifestValidationError, parse_manifest


@dataclass
class PullResult:
    users_found: int
    users_updated: int
    users_unchanged: int
    users_pruned: int
    failed_folders: list[str] = field(default_factory=list)


def pull_all(client_secrets_path: str | Path) -> PullResult:
    config = store.load()
    if not config.drive_shared_folder_id:
        raise RuntimeError("No shared Drive folder id configured. Run `white-soapstone init` first.")

    creds = get_credentials(client_secrets_path)
    service = drive_client.build_service(creds)
    conn = cache_db.connect()

    user_folders = drive_download.list_user_folders(service, config.drive_shared_folder_id)
    pruned = cache_db.prune_missing_users(conn, {f["name"] for f in user_folders})

    updated = 0
    unchanged = 0
    failed_folders: list[str] = []
    manifest_tmp = paths.cache_dir() / "peer_manifest_tmp.json"

    for folder in user_folders:
        try:
            downloaded = drive_download.download_manifest(service, folder["id"], manifest_tmp)
            if downloaded is None:
                continue

            raw_bytes = downloaded.read_bytes()
            manifest_hash = hashlib.sha256(raw_bytes).hexdigest()

            if cache_db.get_manifest_hash_by_folder(conn, folder["name"]) == manifest_hash:
                unchanged += 1
                continue

            manifest = parse_manifest(raw_bytes)
            is_self = manifest["user"]["id"] == config.user_id
            cache_db.ingest_manifest(
                conn, manifest, manifest_hash, drive_folder_id=folder["id"], is_self=is_self
            )
            updated += 1
        except (ManifestValidationError, OSError):
            failed_folders.append(folder["name"])
        finally:
            # Best-effort cleanup - on Windows a just-downloaded file can transiently
            # stay locked (e.g. AV scanning it) for a moment after the handle that
            # wrote it closes. Harmless either way: the next pull overwrites this
            # same temp path regardless of whether this delete succeeded.
            with contextlib.suppress(OSError):
                manifest_tmp.unlink(missing_ok=True)

    return PullResult(
        users_found=len(user_folders),
        users_updated=updated,
        users_unchanged=unchanged,
        users_pruned=pruned,
        failed_folders=failed_folders,
    )
