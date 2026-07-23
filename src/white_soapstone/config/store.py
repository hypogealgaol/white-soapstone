"""Local app config: identity, Drive folder ids, and the playlist whitelist.

Whitelisting is playlist-granularity only (see manifest/builder.py) - this file stores
a flat list of Rekordbox playlist ids, never track ids.
"""

from __future__ import annotations

import getpass
import json
import os
import uuid
from dataclasses import asdict, dataclass, field

from .paths import config_file


def _default_handle() -> str | None:
    """Falls back to the OS account name so a brand-new config isn't just blank -
    still just a starting suggestion, editable (via `set-handle`/the UI) like any
    other handle."""
    try:
        return getpass.getuser() or None
    except OSError:
        return None


# The group's shared Drive folder - fixed and not expected to change, so every new
# install defaults straight to it instead of requiring anyone to go look it up and
# type/paste an id in before the app is usable.
DEFAULT_SHARED_FOLDER_ID = "1vSEjPt47GvKcp9fhv0w49dHG7PHvrkyC"


@dataclass
class Config:
    user_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    handle: str | None = field(default_factory=_default_handle)
    drive_shared_folder_id: str | None = DEFAULT_SHARED_FOLDER_ID
    my_drive_folder_id: str | None = None
    my_folder_name: str | None = None
    # The shared "_content" pool folder (direct child of drive_shared_folder_id, not
    # per-user) that deduplicated preview files live in - see drive/upload.py.
    content_pool_folder_id: str | None = None
    whitelist_playlist_ids: list[str] = field(default_factory=list)
    # Overrides for when pyrekordbox's own OS-based auto-detection can't find things -
    # e.g. running under WSL against a Rekordbox install on the Windows side, where
    # auto-detection is unsupported (pyrekordbox only knows Windows/Mac paths).
    rekordbox_db_path: str | None = None
    rekordbox_key: str | None = None

    @classmethod
    def from_dict(cls, data: dict) -> Config:
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in data.items() if k in known})


def load() -> Config:
    path = config_file()
    if not path.exists():
        return Config()
    with path.open("r", encoding="utf-8") as fh:
        data = json.load(fh)
    return Config.from_dict(data)


def save(config: Config) -> None:
    """Atomic write: temp file in the same directory, then rename over the target."""
    path = config_file()
    tmp_path = path.with_suffix(".tmp")
    with tmp_path.open("w", encoding="utf-8") as fh:
        json.dump(asdict(config), fh, indent=2)
        fh.write("\n")
    os.replace(tmp_path, path)


def whitelist_playlist(playlist_id: str) -> Config:
    config = load()
    if playlist_id not in config.whitelist_playlist_ids:
        config.whitelist_playlist_ids.append(playlist_id)
        save(config)
    return config


def unwhitelist_playlist(playlist_id: str) -> Config:
    config = load()
    if playlist_id in config.whitelist_playlist_ids:
        config.whitelist_playlist_ids.remove(playlist_id)
        save(config)
    return config
