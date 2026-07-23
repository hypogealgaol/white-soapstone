"""Local SQLite cache of everyone's manifests (including our own).

Rebuilt with a replace-per-user transaction on every ingest: the row for a given
user_id is deleted (cascading to their playlists/tracks/playlist_tracks) and
reinserted fresh. That makes ingestion idempotent and correctly handles a peer
removing tracks or playlists between syncs - there's never a stale leftover row.
"""

from __future__ import annotations

import sqlite3
from importlib import resources
from pathlib import Path

from ..config.paths import cache_db_file


def connect(db_path: str | Path | None = None) -> sqlite3.Connection:
    path = Path(db_path) if db_path else cache_db_file()
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.row_factory = sqlite3.Row
    _ensure_schema(conn)
    return conn


def _ensure_schema(conn: sqlite3.Connection) -> None:
    exists = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='users'"
    ).fetchone()
    if exists:
        return
    migration_sql = (
        resources.files("white_soapstone.cache").joinpath("migrations", "001_init.sql").read_text(encoding="utf-8")
    )
    conn.executescript(migration_sql)
    conn.commit()


def get_manifest_hash_by_folder(conn: sqlite3.Connection, folder_name: str) -> str | None:
    row = conn.execute("SELECT manifest_hash FROM users WHERE folder_name = ?", (folder_name,)).fetchone()
    return row["manifest_hash"] if row else None


def ingest_manifest(
    conn: sqlite3.Connection,
    manifest: dict,
    manifest_hash: str,
    drive_folder_id: str | None = None,
    is_self: bool = False,
) -> None:
    user = manifest["user"]
    user_id = user["id"]

    with conn:
        # A Drive subfolder should only ever map to one cached row. If the publisher's
        # identity scheme changed since the last ingest (e.g. a local migration to a
        # different derivation of user_id - see sync_service.py:derive_user_id) without
        # this ever having actually diverged into two real people, the *old* id would
        # otherwise linger as an orphaned duplicate forever, since folder_name (not id)
        # is what actually identifies "the same publishing slot" here.
        conn.execute(
            "DELETE FROM users WHERE folder_name = ? AND id != ?", (user["folder_name"], user_id)
        )
        conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
        conn.execute(
            """INSERT INTO users (id, handle, folder_name, drive_folder_id, manifest_hash,
                                   schema_version, last_synced_at, is_self)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                user_id,
                user["handle"],
                user["folder_name"],
                drive_folder_id,
                manifest_hash,
                manifest["schema_version"],
                user["generated_at"],
                1 if is_self else 0,
            ),
        )
        for playlist in manifest["playlists"]:
            conn.execute(
                """INSERT INTO playlists (user_id, id, name, parent_id, parent_name, position)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    user_id,
                    playlist["id"],
                    playlist["name"],
                    playlist.get("parent_id"),
                    playlist.get("parent_name"),
                    playlist["position"],
                ),
            )
        # tracks must exist before playlist_tracks, which has a foreign key to both
        for track in manifest["tracks"]:
            preview = track["preview"]
            conn.execute(
                """INSERT INTO tracks (
                       user_id, id, title, artist, album, genre, bpm, key, duration_sec,
                       year, rating, comment, preview_format,
                       preview_bitrate_kbps, preview_size_bytes, preview_checksum, transcoded_at
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    user_id,
                    track["id"],
                    track.get("title"),
                    track.get("artist"),
                    track.get("album"),
                    track.get("genre"),
                    track.get("bpm"),
                    track.get("key"),
                    track.get("duration_sec"),
                    track.get("year"),
                    track.get("rating"),
                    track.get("comment"),
                    preview["format"],
                    preview["bitrate_kbps"],
                    preview.get("size_bytes"),
                    preview.get("checksum_sha256"),
                    preview.get("transcoded_at"),
                ),
            )
        for playlist in manifest["playlists"]:
            for position, track_id in enumerate(playlist["track_ids"]):
                conn.execute(
                    """INSERT INTO playlist_tracks (user_id, playlist_id, track_id, position)
                       VALUES (?, ?, ?, ?)""",
                    (user_id, playlist["id"], track_id, position),
                )


def prune_missing_users(conn: sqlite3.Connection, current_folder_names: set[str]) -> int:
    """Removes any cached user (and their playlists/tracks, via cascade) whose Drive
    subfolder no longer exists - e.g. they left the group or renamed their folder."""
    rows = conn.execute("SELECT id, folder_name FROM users").fetchall()
    stale_ids = [row["id"] for row in rows if row["folder_name"] not in current_folder_names]
    with conn:
        for user_id in stale_ids:
            conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
    return len(stale_ids)


def list_users(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM users ORDER BY handle").fetchall()


def get_user(conn: sqlite3.Connection, user_id: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()


def get_track(conn: sqlite3.Connection, user_id: str, track_id: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM tracks WHERE user_id = ? AND id = ?", (user_id, track_id)
    ).fetchone()


def list_playlists(conn: sqlite3.Connection, user_id: str) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM playlists WHERE user_id = ? ORDER BY position", (user_id,)
    ).fetchall()


def list_tracks_for_playlist(conn: sqlite3.Connection, user_id: str, playlist_id: str) -> list[sqlite3.Row]:
    return conn.execute(
        """SELECT tracks.* FROM playlist_tracks
           JOIN tracks ON tracks.user_id = playlist_tracks.user_id AND tracks.id = playlist_tracks.track_id
           WHERE playlist_tracks.user_id = ? AND playlist_tracks.playlist_id = ?
           ORDER BY playlist_tracks.position""",
        (user_id, playlist_id),
    ).fetchall()
