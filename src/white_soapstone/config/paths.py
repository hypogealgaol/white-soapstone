"""Per-OS filesystem locations for this app's own data (not Rekordbox's)."""

from __future__ import annotations

import re
import shutil
import sys
from pathlib import Path

import platformdirs

_WINDOWS_DRIVE_PATH = re.compile(r"^([A-Za-z]):[\\/](.*)$")

APP_NAME = "white-soapstone"
APP_AUTHOR = "white-soapstone"


def config_dir() -> Path:
    path = Path(platformdirs.user_config_dir(APP_NAME, APP_AUTHOR))
    path.mkdir(parents=True, exist_ok=True)
    return path


def config_file() -> Path:
    return config_dir() / "config.json"


def cache_dir() -> Path:
    path = Path(platformdirs.user_cache_dir(APP_NAME, APP_AUTHOR))
    path.mkdir(parents=True, exist_ok=True)
    return path


def cache_db_file() -> Path:
    return cache_dir() / "cache.sqlite3"


def log_file() -> Path:
    return cache_dir() / "app.log"


def preview_cache_dir() -> Path:
    path = cache_dir() / "previews"
    path.mkdir(parents=True, exist_ok=True)
    return path


def resolve_source_audio_path(raw_path: str) -> Path:
    """Resolves a track's source file path as recorded by Rekordbox.

    Rekordbox records paths in whatever OS it was running on. When this app itself
    runs under WSL against a Rekordbox install on the Windows side of the same
    machine, those Windows-style paths (e.g. "C:\\Users\\...") need translating to
    their "/mnt/<drive>/..." WSL mount equivalent before Python can read the file.
    On native Windows/Mac the path is already correct and passes through unchanged.
    """
    if sys.platform.startswith("linux"):
        match = _WINDOWS_DRIVE_PATH.match(raw_path)
        if match:
            drive, rest = match.groups()
            return Path(f"/mnt/{drive.lower()}/{rest.replace('\\', '/')}")
    return Path(raw_path)


def resolve_rekordbox_db_path(configured_path: str | None) -> str | None:
    """Resolves the master.db path to actually open.

    Returns None unchanged (letting pyrekordbox auto-detect) unless a path was
    configured. If that configured path sits on a WSL 9p/drvfs mount (`/mnt/...`),
    SQLite/SQLCipher's locking doesn't work reliably over that filesystem (it
    reliably raises "disk I/O error") - so the db and its -wal/-shm sidecars are
    copied to a native-filesystem cache dir first, and that copy is opened instead.
    Re-copies on every call so a running Rekordbox's latest changes are picked up.
    """
    if not configured_path:
        return None

    original = Path(configured_path)
    if not (sys.platform.startswith("linux") and str(original).startswith("/mnt/")):
        return str(original)

    dest_dir = cache_dir() / "rekordbox_db_copy"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest_path = dest_dir / original.name
    for suffix in ("", "-wal", "-shm"):
        src = original.with_name(original.name + suffix)
        if src.exists():
            shutil.copyfile(src, dest_dir / src.name)
    return str(dest_path)
