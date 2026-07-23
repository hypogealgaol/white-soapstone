"""Orchestrates one full sync: read Rekordbox -> resolve whitelisted tracks to shared
content ids -> transcode only what isn't already in the shared Drive pool -> publish
manifest.json + any newly-needed content files.

The manifest is regenerated from scratch every run (see manifest/builder.py). Preview
files are content-addressed and shared across every user (see manifest/builder.py:
content_id) - a track already sitting in the shared `_content/` pool (whether uploaded
by this user previously or by a peer) is never re-transcoded or re-uploaded.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from ..cache import db as cache_db
from ..config import paths, store
from ..config.store import Config
from ..drive import client as drive_client
from ..drive import upload as drive_upload
from ..drive.auth import get_credentials
from ..manifest.builder import PreviewInfo, build_manifest, content_id, derive_user_id
from ..manifest.writer import write_manifest_atomic
from ..rekordbox.extractor import LibraryDump, dump_library
from ..transcode.ffmpeg import transcode_to_preview_mp3

PREVIEW_BITRATE_KBPS = 128

# Called as on_progress(tracks_done, tracks_total) while transcoding - the slowest part
# of a sync - so callers (e.g. the UI) can show real progress instead of a spinner.
ProgressCallback = Callable[[int, int], None]


def _app_version() -> str:
    try:
        return version("white-soapstone")
    except PackageNotFoundError:
        return "0.0.0-dev"


@dataclass
class SyncResult:
    whitelisted_playlists: int
    tracks_published: int
    tracks_transcoded: int
    tracks_skipped_no_file: int
    manifest_path: Path | None
    cancelled: bool = False


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _ensure_user_folder(service, config: Config) -> Config:
    if config.my_drive_folder_id and config.my_folder_name:
        return config
    if not config.drive_shared_folder_id:
        raise RuntimeError(
            "No shared Drive folder id configured. Run `white-soapstone init "
            "--shared-folder-id <id>` first."
        )
    google_user = drive_client.get_about_user(service)
    # Adopt an identity derived from the Google account itself, not whatever random
    # local id this config started with - this is what a second environment (e.g. WSL)
    # signing in with the *same* Google account needs in order to resolve to the same
    # identity as an already-onboarded install, instead of looking like someone else.
    # Safe to overwrite here specifically because this whole branch only runs once,
    # before this config has ever published anything (see the early return above).
    config.user_id = derive_user_id(google_user["permissionId"])
    folder_name = drive_upload.resolve_folder_name(config.handle, google_user["permissionId"])
    folder_id = drive_upload.ensure_user_folder(service, config.drive_shared_folder_id, folder_name)
    config.my_folder_name = folder_name
    config.my_drive_folder_id = folder_id
    store.save(config)
    return config


def update_handle(new_handle: str, client_secrets_path: str | Path | None = None) -> Config:
    """Changes the local display handle. If a Drive folder was already published under
    the old handle, it's renamed in place (same folder id) rather than abandoned, so
    existing shared content/history carries over instead of forking into a new folder.

    Rejects a handle already used by anyone else *currently known locally* (i.e.
    anyone already pulled into the local cache) - not a global guarantee (there's no
    central registry to check against), but enough to catch the common case for a
    small group where everyone regularly pulls.
    """
    new_handle = new_handle.strip()
    if not new_handle:
        raise ValueError("Handle cannot be empty")

    config = store.load()
    if config.handle == new_handle:
        return config

    conn = cache_db.connect()
    for user in cache_db.list_users(conn):
        if user["id"] != config.user_id and user["handle"].lower() == new_handle.lower():
            raise ValueError(
                f"Handle {new_handle!r} is already used by another user. Pick a different one."
            )

    old_folder_name = config.my_folder_name
    config.handle = new_handle

    if config.my_drive_folder_id and old_folder_name:
        # folder names are "<handle>__<8charsGoogleId>" - keep the same suffix so the
        # folder id and its google-account association don't need to be re-resolved.
        suffix = old_folder_name.split("__", 1)[1] if "__" in old_folder_name else ""
        new_folder_name = f"{new_handle}__{suffix}" if suffix else new_handle

        creds = get_credentials(client_secrets_path or "client_secret.json")
        service = drive_client.build_service(creds)
        drive_client.rename_file(service, config.my_drive_folder_id, new_folder_name)
        config.my_folder_name = new_folder_name

    store.save(config)
    return config


def ensure_content_pool_folder(service, config: Config) -> Config:
    if config.content_pool_folder_id:
        return config
    config.content_pool_folder_id = drive_upload.ensure_content_pool_folder(
        service, config.drive_shared_folder_id
    )
    store.save(config)
    return config


def _prepare_content(
    dump: LibraryDump,
    config: Config,
    whitelisted_ids: set[str],
    existing_pool_names: set[str],
    on_progress: ProgressCallback | None = None,
    should_stop: Callable[[], bool] | None = None,
) -> tuple[dict[str, str], dict[str, PreviewInfo], dict[str, Path], int, int, bool]:
    """Resolves every whitelisted raw (Rekordbox-internal) track id to a content id,
    transcoding only content that isn't already in the shared pool or local cache. Two
    raw tracks - whether duplicated within one library or shared across users - that
    resolve to the same content id are transcoded/uploaded at most once, total.

    Returns (raw_track_id -> content_id, content_id -> PreviewInfo, content filename ->
    local path for files that still need uploading, transcoded count, count skipped for
    missing source file, cancelled). If cancelled, the caller must not build/upload a
    manifest from this (incomplete) state - see run_sync.
    """
    tracks_by_id = dump.track_by_id()
    content_id_by_raw_track: dict[str, str] = {}
    previews: dict[str, PreviewInfo] = {}
    new_content_files: dict[str, Path] = {}
    transcoded = 0
    skipped_no_file = 0
    total = len(whitelisted_ids)

    for done, raw_id in enumerate(whitelisted_ids, start=1):
        if should_stop and should_stop():
            return content_id_by_raw_track, previews, new_content_files, transcoded, skipped_no_file, True

        track = tracks_by_id.get(raw_id)
        if track is None:
            if on_progress:
                on_progress(done, total)
            continue

        cid = content_id(track.artist, track.title, track.isrc)
        content_id_by_raw_track[raw_id] = cid

        if cid not in previews:
            pool_filename = f"{cid}.mp3"

            if pool_filename in existing_pool_names:
                # Already published - by us previously, or by a peer - nothing to do.
                previews[cid] = PreviewInfo(format="mp3", bitrate_kbps=PREVIEW_BITRATE_KBPS)
            elif not track.file_path:
                skipped_no_file += 1
            else:
                source_path = paths.resolve_source_audio_path(track.file_path)
                if not source_path.exists():
                    skipped_no_file += 1
                else:
                    local_path = paths.preview_cache_dir() / pool_filename
                    if not local_path.exists():
                        transcode_to_preview_mp3(source_path, local_path, bitrate_kbps=PREVIEW_BITRATE_KBPS)
                        transcoded += 1
                    previews[cid] = PreviewInfo(
                        format="mp3",
                        bitrate_kbps=PREVIEW_BITRATE_KBPS,
                        size_bytes=local_path.stat().st_size,
                        checksum_sha256=_sha256_file(local_path),
                        transcoded_at=datetime.now(timezone.utc).isoformat(),
                    )
                    new_content_files[pool_filename] = local_path

        if on_progress:
            on_progress(done, total)

    return content_id_by_raw_track, previews, new_content_files, transcoded, skipped_no_file, False


def run_sync(
    client_secrets_path: str | Path,
    on_progress: ProgressCallback | None = None,
    should_stop: Callable[[], bool] | None = None,
    on_auth_required: Callable[[], None] | None = None,
) -> SyncResult:
    config = store.load()
    if not config.handle:
        raise RuntimeError(
            "No handle configured. Run `white-soapstone init --handle <name> "
            "--shared-folder-id <id>` first."
        )

    creds = get_credentials(client_secrets_path, on_auth_required=on_auth_required)
    service = drive_client.build_service(creds)
    config = _ensure_user_folder(service, config)
    config = ensure_content_pool_folder(service, config)

    dump = dump_library(db_path=config.rekordbox_db_path, key=config.rekordbox_key)
    whitelisted_playlists = [p for p in dump.playlists if p.id in config.whitelist_playlist_ids]
    whitelisted_track_ids: set[str] = set()
    for playlist in whitelisted_playlists:
        whitelisted_track_ids.update(playlist.track_ids)

    existing_pool_names = {
        f["name"] for f in drive_client.list_children(service, config.content_pool_folder_id)
    }

    # Transcoding and uploading are reported on one combined scale (each phase's own
    # count folded in as an offset) so a caller sees one steadily-advancing progress
    # bar across the whole sync, rather than it resetting or stalling between phases.
    # The upload phase is often much smaller than this (most content tends to already
    # be shared), so the bar may race through the second half - that's expected.
    transcode_total = len(whitelisted_track_ids)

    def transcode_progress(done: int, _total: int) -> None:
        if on_progress:
            on_progress(done, transcode_total * 2)

    content_id_by_raw_track, previews, new_content_files, transcoded, skipped_no_file, cancelled = (
        _prepare_content(
            dump,
            config,
            whitelisted_track_ids,
            existing_pool_names,
            on_progress=transcode_progress,
            should_stop=should_stop,
        )
    )
    if cancelled:
        return SyncResult(
            whitelisted_playlists=len(whitelisted_playlists),
            tracks_published=0,
            tracks_transcoded=transcoded,
            tracks_skipped_no_file=skipped_no_file,
            manifest_path=None,
            cancelled=True,
        )

    manifest = build_manifest(dump, config, content_id_by_raw_track, previews, _app_version())
    local_manifest_path = paths.cache_dir() / "manifest.json"
    write_manifest_atomic(manifest, local_manifest_path)

    def upload_progress(done: int, _total: int) -> None:
        if on_progress:
            on_progress(transcode_total + done, transcode_total * 2)

    # manifest.json is only uploaded *after* every new content file succeeds (below),
    # so a stop/crash mid-upload can never leave a published manifest pointing at
    # content that was never actually written to Drive.
    upload_cancelled = drive_upload.upload_content_pool_files(
        service,
        config.content_pool_folder_id,
        new_content_files,
        on_progress=upload_progress,
        should_stop=should_stop,
    )
    if upload_cancelled:
        return SyncResult(
            whitelisted_playlists=len(whitelisted_playlists),
            tracks_published=0,
            tracks_transcoded=transcoded,
            tracks_skipped_no_file=skipped_no_file,
            manifest_path=local_manifest_path,
            cancelled=True,
        )

    drive_upload.upload_manifest(service, config.my_drive_folder_id, local_manifest_path)

    if on_progress:
        on_progress(transcode_total * 2, transcode_total * 2)

    return SyncResult(
        whitelisted_playlists=len(whitelisted_playlists),
        tracks_published=len(manifest["tracks"]),
        tracks_transcoded=transcoded,
        tracks_skipped_no_file=skipped_no_file,
        manifest_path=local_manifest_path,
    )
