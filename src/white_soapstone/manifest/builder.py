"""Builds the manifest.json dict for a user's whitelisted playlists.

The manifest is always regenerated from scratch from a fresh Rekordbox read plus the
current whitelist - see writer.py for why that makes un-whitelisting and pruning trivial.

Tracks are identified by *content* (see `content_id`), not by a per-user id, so the
same song whitelisted by two different people resolves to the same id and the same
shared preview file in Drive's `_content/` pool - see drive/upload.py.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone

from ..config.store import Config
from ..rekordbox.extractor import LibraryDump

SCHEMA_VERSION = 2


@dataclass
class PreviewInfo:
    format: str
    bitrate_kbps: int
    # None when this sync is only *referencing* a preview that already existed in the
    # shared content pool (uploaded by this user or a peer previously) rather than
    # having transcoded it locally itself - see sync/sync_service.py.
    size_bytes: int | None = None
    checksum_sha256: str | None = None
    transcoded_at: str | None = None


def namespaced_id(user_id: str, raw_id: str) -> str:
    """Namespaces a Rekordbox-internal id by the local user id.

    Two different users' Rekordbox databases can reuse the same internal playlist ids,
    so playlist ids published in the manifest are hashed together with the user id to
    avoid collisions once multiple users' data is merged into one local cache (see
    cache/db.py). Playlists are inherently personal (never shared across users the way
    tracks are), so unlike tracks they keep a per-user identity - see content_id below.
    """
    digest = hashlib.sha256(f"{user_id}:{raw_id}".encode("utf-8")).hexdigest()
    return digest[:16]


def derive_user_id(google_permission_id: str) -> str:
    """Derives a stable identity from the signed-in Google account itself, rather than
    a randomly generated per-install id.

    This is what makes the *same* real person resolve to the same identity regardless
    of which machine or environment (e.g. a native install vs. a WSL dev environment)
    they happen to run the app from and sign in with the same Google account - without
    it, each separate install mints its own random user_id, and the same person's own
    published playlists end up looking like a different person's to themselves.
    """
    digest = hashlib.sha256(f"googleid:{google_permission_id}".encode("utf-8")).hexdigest()
    return digest[:16]


def _normalize_text(value: str | None) -> str:
    text = (value or "").strip().lower()
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = re.sub(r"[^\w\s]", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def content_id(artist: str | None, title: str | None, isrc: str | None = None) -> str:
    """Identifies a track by what it actually *is*, shared across every user's library -
    this is what lets the same song end up as one preview file in Drive instead of one
    per person who whitelists it.

    ISRC (a true globally-unique recording id, populated for roughly 1 in 10 tracks in
    practice - mostly label/store-distributed releases, rarely for direct downloads
    like Bandcamp) takes priority when present. Otherwise falls back to normalized
    artist+title text, which is inherently fuzzy: won't catch a remix tagged
    differently, a typo, or "feat." ordering differences, and could in principle
    conflate two different tracks that happen to normalize to the same text. There's no
    audio fingerprinting here - this is a best-effort match, not a guarantee.
    """
    if isrc and isrc.strip():
        raw = f"isrc:{isrc.strip().upper()}"
    else:
        raw = f"at:{_normalize_text(artist)}|{_normalize_text(title)}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def build_manifest(
    dump: LibraryDump,
    config: Config,
    content_id_by_raw_track: dict[str, str],
    previews: dict[str, PreviewInfo],
    app_version: str,
) -> dict:
    """`content_id_by_raw_track` maps every whitelisted raw (Rekordbox-internal) track
    id to its content id. `previews` maps content id -> PreviewInfo for every piece of
    content that has a usable preview available (whether just transcoded now, or
    already sitting in the shared pool from a prior sync). A track whose content id
    has no entry in `previews` is dropped from the manifest entirely - nothing to
    play back."""

    if not config.handle or not config.my_folder_name:
        raise ValueError("Config must have handle and my_folder_name set before building a manifest")

    whitelisted = {p.id: p for p in dump.playlists if p.id in config.whitelist_playlist_ids}
    # Folders themselves are never whitelisted (only real playlists are), so the parent
    # folder a whitelisted playlist sits in is almost never itself part of `whitelisted`.
    # Resolve its display name from the full dump anyway, purely for context - it's just
    # a label, not a reference a peer needs to dereference (see parent_id below).
    all_playlists_by_id = {p.id: p for p in dump.playlists}
    tracks_by_id = dump.track_by_id()

    playlists_out = []
    included_content_ids: set[str] = set()
    content_id_to_sample_raw: dict[str, str] = {}

    for playlist in whitelisted.values():
        parent_id = (
            namespaced_id(config.user_id, playlist.parent_id)
            if playlist.parent_id and playlist.parent_id in whitelisted
            else None
        )
        parent = all_playlists_by_id.get(playlist.parent_id) if playlist.parent_id else None

        track_content_ids = []
        seen_content_ids: set[str] = set()
        for raw_id in playlist.track_ids:
            cid = content_id_by_raw_track.get(raw_id)
            if cid is None or cid not in previews:
                continue
            # Two distinct raw tracks in the same playlist (e.g. a duplicate file, or a
            # differently-tagged copy) can resolve to the same shared content id - listing
            # it twice for one playlist would violate playlist_tracks' unique constraint
            # on ingest (see cache/db.py), so only its first occurrence is kept.
            if cid in seen_content_ids:
                continue
            seen_content_ids.add(cid)
            track_content_ids.append(cid)
            included_content_ids.add(cid)
            content_id_to_sample_raw.setdefault(cid, raw_id)

        playlists_out.append(
            {
                "id": namespaced_id(config.user_id, playlist.id),
                "name": playlist.name,
                "parent_id": parent_id,
                "parent_name": parent.name if parent else None,
                "position": playlist.position,
                "track_ids": track_content_ids,
            }
        )

    tracks_out = []
    for cid in included_content_ids:
        track = tracks_by_id[content_id_to_sample_raw[cid]]
        preview = previews[cid]
        preview_out = {"format": preview.format, "bitrate_kbps": preview.bitrate_kbps}
        if preview.size_bytes is not None:
            preview_out["size_bytes"] = preview.size_bytes
        if preview.checksum_sha256 is not None:
            preview_out["checksum_sha256"] = preview.checksum_sha256
        if preview.transcoded_at is not None:
            preview_out["transcoded_at"] = preview.transcoded_at

        tracks_out.append(
            {
                "id": cid,
                "title": track.title,
                "artist": track.artist,
                "album": track.album,
                "genre": track.genre,
                "bpm": track.bpm,
                "key": track.key,
                "duration_sec": track.duration_sec,
                "year": track.year,
                "rating": track.rating,
                "comment": track.comment,
                "preview": preview_out,
            }
        )

    return {
        "schema_version": SCHEMA_VERSION,
        "user": {
            "id": config.user_id,
            "handle": config.handle,
            "folder_name": config.my_folder_name,
            "app_version": app_version,
            "generated_at": datetime.now(timezone.utc).isoformat(),
        },
        "playlists": playlists_out,
        "tracks": tracks_out,
    }
