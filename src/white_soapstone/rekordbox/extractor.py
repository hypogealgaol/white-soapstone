"""Reads the local Rekordbox 6/7 library via pyrekordbox.

pyrekordbox handles locating and SQLCipher-decrypting master.db; this module just
converts its ORM objects into plain dataclasses so the rest of the app never touches
pyrekordbox or SQLAlchemy types directly.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from pyrekordbox import Rekordbox6Database
from pyrekordbox.db6.tables import DjmdContent

from ..config.paths import resolve_rekordbox_db_path
from .errors import DbLocked, DbNotFound, KeyExtractionFailed


@dataclass
class RawTrack:
    id: str
    title: str | None
    artist: str | None
    album: str | None
    genre: str | None
    bpm: float | None
    key: str | None
    duration_sec: float | None
    file_path: str | None
    year: int | None
    rating: int | None
    comment: str | None
    isrc: str | None


@dataclass
class RawPlaylist:
    id: str
    name: str
    parent_id: str | None
    is_folder: bool
    position: int
    track_ids: list[str] = field(default_factory=list)


@dataclass
class LibraryDump:
    playlists: list[RawPlaylist]
    tracks: list[RawTrack]

    def track_by_id(self) -> dict[str, RawTrack]:
        return {t.id: t for t in self.tracks}


def _open_database(db_path: str | None = None, key: str | None = None) -> Rekordbox6Database:
    db_path = resolve_rekordbox_db_path(db_path)
    try:
        return Rekordbox6Database(path=db_path or None, key=key or "")
    except FileNotFoundError as exc:
        raise DbNotFound(str(exc)) from exc
    except ImportError as exc:
        # sqlcipher3 binding missing from the environment/bundle
        raise KeyExtractionFailed(str(exc)) from exc
    except ValueError as exc:
        # pyrekordbox raises ValueError when a manually supplied key is malformed,
        # and the same underlying failure mode (can't decrypt) shows up as garbled
        # SQLAlchemy/sqlite errors below when key extraction silently produced a bad key
        raise KeyExtractionFailed(str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 - classify by message, see errors.py docstring
        message = str(exc).lower()
        if "lock" in message:
            raise DbLocked(str(exc)) from exc
        if "file is not a database" in message or "not a database" in message or "cipher" in message:
            raise KeyExtractionFailed(str(exc)) from exc
        raise


def _track_from_content(content: DjmdContent) -> RawTrack:
    return RawTrack(
        id=str(content.ID),
        title=content.Title,
        artist=content.Artist.Name if content.Artist else None,
        album=content.Album.Name if content.Album else None,
        genre=content.Genre.Name if content.Genre else None,
        # Rekordbox stores BPM * 100 (e.g. 12800 -> 128.00 BPM)
        bpm=(content.BPM / 100) if content.BPM else None,
        key=content.Key.ScaleName if content.Key else None,
        duration_sec=float(content.Length) if content.Length is not None else None,
        file_path=content.FolderPath,
        year=content.ReleaseYear,
        rating=content.Rating,
        comment=content.Commnt,
        # Populated for maybe ~10% of tracks (mostly label/store-distributed releases,
        # rarely for bandcamp downloads) - a true globally unique recording id when
        # present, so it's preferred over fuzzy artist/title matching for content dedup.
        isrc=content.ISRC or None,
    )


def dump_library(db_path: str | None = None, key: str | None = None) -> LibraryDump:
    """Read the entire local Rekordbox library: every playlist (incl. folders) and track.

    This is the raw, unfiltered dump - whitelist filtering happens later in
    manifest/builder.py, so the whitelist CLI/UI has the complete playlist tree to
    choose from.
    """
    db = _open_database(db_path, key)
    try:
        playlists: list[RawPlaylist] = []
        tracks_by_id: dict[str, RawTrack] = {}

        for plist in db.get_playlist().all():
            track_ids: list[str] = []
            if plist.is_playlist or plist.is_smart_playlist:
                for content in db.get_playlist_contents(plist).all():
                    track_id = str(content.ID)
                    track_ids.append(track_id)
                    if track_id not in tracks_by_id:
                        tracks_by_id[track_id] = _track_from_content(content)

            parent_id = str(plist.ParentID) if plist.ParentID and plist.ParentID != "root" else None
            playlists.append(
                RawPlaylist(
                    id=str(plist.ID),
                    name=plist.Name or "",
                    parent_id=parent_id,
                    is_folder=plist.is_folder,
                    position=plist.Seq or 0,
                    track_ids=track_ids,
                )
            )

        return LibraryDump(playlists=playlists, tracks=list(tracks_by_id.values()))
    finally:
        db.close()
