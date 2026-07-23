"""Minimal local API + static file server backing the Phase 3 UI.

Binds to 127.0.0.1 only; ui/app_window.py wraps it in a native pywebview window rather
than exposing it as a normal browser-facing site.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import threading
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from ..cache import db as cache_db
from ..config import paths, store
from ..drive import client as drive_client
from ..drive import download as drive_download
from ..drive.auth import get_credentials, sign_out
from ..rekordbox.errors import RekordboxAccessError
from ..rekordbox.extractor import dump_library
from ..sync.pull_service import pull_all
from ..sync.sync_service import ensure_content_pool_folder, run_sync, update_handle

STATIC_DIR = Path(__file__).parent / "static"
CLIENT_SECRETS = "client_secret.json"

logger = logging.getLogger(__name__)

app = FastAPI(title="white-soapstone")


@app.exception_handler(Exception)
async def log_unhandled_exception(request: Request, exc: Exception) -> JSONResponse:
    # Catches anything not already handled as an HTTPException (those keep FastAPI's
    # normal handling - this only sees genuinely unexpected failures), so every "see
    # logs"-style error the UI shows actually has a traceback waiting in app.log.
    logger.error("Unhandled error handling %s %s", request.method, request.url.path, exc_info=exc)
    return JSONResponse(status_code=500, content={"detail": str(exc)})

# Single-user local app, so one shared in-memory progress record is enough - no need
# for per-session tracking. The UI polls /api/sync/status while a POST /api/sync (or
# an auto-triggered one) is in flight to show real done/total progress.
_sync_state_lock = threading.Lock()
_sync_state: dict = {"running": False, "done": 0, "total": 0, "error": None, "waiting_for_auth": False}
_stop_requested = threading.Event()


class WhitelistUpdate(BaseModel):
    whitelisted: bool


class HandleUpdate(BaseModel):
    handle: str


class RekordboxPathUpdate(BaseModel):
    db_path: str | None = None


@app.post("/api/logs/open")
def api_open_logs() -> dict:
    """Opens app.log in the OS's default text viewer, so "see logs"-style errors have
    a concrete place to look instead of a dead end."""
    log_path = paths.log_file()
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.touch(exist_ok=True)
    if sys.platform == "win32":
        os.startfile(str(log_path))
    elif sys.platform == "darwin":
        subprocess.run(["open", str(log_path)])
    else:
        subprocess.run(["xdg-open", str(log_path)])
    return {"ok": True}


@app.get("/api/settings")
def api_get_settings() -> dict:
    config = store.load()
    return {"handle": config.handle, "rekordbox_db_path": config.rekordbox_db_path}


@app.post("/api/settings/rekordbox-path")
def api_set_rekordbox_path(body: RekordboxPathUpdate) -> dict:
    config = store.load()
    config.rekordbox_db_path = body.db_path or None
    store.save(config)
    return {"rekordbox_db_path": config.rekordbox_db_path}


@app.post("/api/settings/browse-rekordbox-db")
def api_browse_rekordbox_db() -> dict:
    """Opens a native file-picker so the user can point at master.db directly, for
    when pyrekordbox's own auto-detection can't find it. Only meaningful when running
    inside the packaged windowed app (needs a live pywebview window to anchor the
    dialog to) - not available when just hitting this API directly in dev."""
    import webview

    if not webview.windows:
        raise HTTPException(503, "No app window available to anchor a file picker to.")

    result = webview.windows[0].create_file_dialog(
        webview.OPEN_DIALOG,
        file_types=("Rekordbox database (master.db)", "*.db"),
    )
    if not result:
        return {"rekordbox_db_path": None}

    config = store.load()
    config.rekordbox_db_path = result[0]
    store.save(config)
    return {"rekordbox_db_path": config.rekordbox_db_path}


@app.post("/api/settings/handle")
def api_set_handle(body: HandleUpdate) -> dict:
    try:
        config = update_handle(body.handle, CLIENT_SECRETS)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"handle": config.handle}


@app.post("/api/settings/logout")
def api_logout() -> dict:
    """Clears only the cached Google sign-in - handle/whitelist/Drive folder ids are
    untouched. The next sync/pull will go through the interactive sign-in again."""
    sign_out()
    return {"ok": True}


@app.get("/api/users")
def api_list_users() -> list[dict]:
    conn = cache_db.connect()
    return [
        {"id": u["id"], "handle": u["handle"], "is_self": bool(u["is_self"])}
        for u in cache_db.list_users(conn)
    ]


@app.get("/api/users/{user_id}/playlists")
def api_list_playlists(user_id: str) -> list[dict]:
    conn = cache_db.connect()
    return [
        {
            "id": p["id"],
            "name": p["name"],
            "parent_name": p["parent_name"],
            "track_count": len(cache_db.list_tracks_for_playlist(conn, user_id, p["id"])),
        }
        for p in cache_db.list_playlists(conn, user_id)
    ]


@app.get("/api/users/{user_id}/playlists/{playlist_id}/tracks")
def api_list_tracks(user_id: str, playlist_id: str) -> list[dict]:
    conn = cache_db.connect()
    return [
        {
            "id": t["id"],
            "title": t["title"],
            "artist": t["artist"],
            "album": t["album"],
            "genre": t["genre"],
            "bpm": t["bpm"],
            "key": t["key"],
            "duration_sec": t["duration_sec"],
            "year": t["year"],
            "rating": t["rating"],
        }
        for t in cache_db.list_tracks_for_playlist(conn, user_id, playlist_id)
    ]


@app.get("/api/preview/{user_id}/{track_id}")
def api_preview(user_id: str, track_id: str) -> FileResponse:
    # track_id is a content id (see manifest/builder.py:content_id), shared across
    # every user's manifest for the same song - the preview always lives in the
    # shared _content/ pool, never in a per-user folder, regardless of whose track
    # list this was reached through.
    local_path = paths.preview_cache_dir() / f"{track_id}.mp3"
    if not local_path.exists():
        conn = cache_db.connect()
        track = cache_db.get_track(conn, user_id, track_id)
        if not track:
            raise HTTPException(404, "track not found")

        config = store.load()
        creds = get_credentials(CLIENT_SECRETS)
        service = drive_client.build_service(creds)
        config = ensure_content_pool_folder(service, config)

        downloaded = drive_download.download_content_file(
            service, config.content_pool_folder_id, f"{track_id}.mp3", local_path
        )
        if downloaded is None:
            raise HTTPException(404, "preview file missing from Drive")

    return FileResponse(local_path, media_type="audio/mpeg")


@app.get("/api/my-playlists")
def api_my_playlists() -> list[dict]:
    config = store.load()
    try:
        dump = dump_library(db_path=config.rekordbox_db_path, key=config.rekordbox_key)
    except RekordboxAccessError as exc:
        # Distinguishable from a generic 500 so the UI can offer a concrete recovery
        # action (browse for master.db) instead of just showing an error message.
        raise HTTPException(503, {"error_code": type(exc).__name__, "message": str(exc)}) from exc
    return [
        {
            "id": p.id,
            "name": p.name,
            "track_count": len(p.track_ids),
            "whitelisted": p.id in config.whitelist_playlist_ids,
        }
        for p in dump.playlists
        if not p.is_folder
    ]


@app.post("/api/whitelist/{playlist_id}")
def api_set_whitelist(playlist_id: str, body: WhitelistUpdate) -> dict:
    if body.whitelisted:
        store.whitelist_playlist(playlist_id)
    else:
        store.unwhitelist_playlist(playlist_id)
    return {"ok": True}


@app.get("/api/sync/status")
def api_sync_status() -> dict:
    with _sync_state_lock:
        return dict(_sync_state)


@app.post("/api/sync/stop")
def api_sync_stop() -> dict:
    _stop_requested.set()
    return {"ok": True}


@app.post("/api/sync")
def api_sync_now() -> dict:
    with _sync_state_lock:
        if _sync_state["running"]:
            raise HTTPException(409, "a sync is already running")
        _sync_state.update(running=True, done=0, total=0, error=None, waiting_for_auth=False)
    _stop_requested.clear()

    def on_progress(done: int, total: int) -> None:
        with _sync_state_lock:
            _sync_state.update(done=done, total=total, waiting_for_auth=False)

    def on_auth_required() -> None:
        with _sync_state_lock:
            _sync_state["waiting_for_auth"] = True

    try:
        result = run_sync(
            CLIENT_SECRETS,
            on_progress=on_progress,
            should_stop=_stop_requested.is_set,
            on_auth_required=on_auth_required,
        )
    except Exception as exc:
        with _sync_state_lock:
            _sync_state["error"] = str(exc)
        raise
    finally:
        with _sync_state_lock:
            _sync_state["running"] = False

    return {
        "tracks_published": result.tracks_published,
        "whitelisted_playlists": result.whitelisted_playlists,
        "cancelled": result.cancelled,
    }


@app.post("/api/pull")
def api_pull_now() -> dict:
    result = pull_all(CLIENT_SECRETS)
    return {
        "users_found": result.users_found,
        "users_updated": result.users_updated,
        "users_unchanged": result.users_unchanged,
    }


# Mounted last: the API routes above take precedence, everything else falls through
# to serving the static single-page UI.
app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
