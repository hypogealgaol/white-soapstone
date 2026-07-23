import json

import pytest

from white_soapstone.cache import db as cache_db
from white_soapstone.manifest.reader import ManifestValidationError, parse_manifest


def _manifest(user_id: str, handle: str, playlist_name: str = "Shared") -> dict:
    return {
        "schema_version": 2,
        "user": {
            "id": user_id,
            "handle": handle,
            "folder_name": f"{handle}__abcd1234",
            "app_version": "0.1.0",
            "generated_at": "2026-01-01T00:00:00+00:00",
        },
        "playlists": [
            {
                "id": "pl-1",
                "name": playlist_name,
                "parent_id": None,
                "parent_name": None,
                "position": 0,
                "track_ids": ["t1"],
            }
        ],
        "tracks": [
            {
                "id": "t1",
                "title": "Track",
                "artist": "Artist",
                "album": None,
                "genre": None,
                "bpm": 128.0,
                "key": "Am",
                "duration_sec": 200.0,
                "year": 2024,
                "rating": 0,
                "comment": None,
                "preview": {
                    "format": "mp3",
                    "bitrate_kbps": 128,
                    "size_bytes": 100,
                    "checksum_sha256": "deadbeef",
                    "transcoded_at": "2026-01-01T00:00:00+00:00",
                },
            }
        ],
    }


@pytest.fixture
def conn():
    return cache_db.connect(":memory:")


def test_ingest_and_query_round_trips(conn):
    manifest = _manifest("user-a", "alice")
    cache_db.ingest_manifest(conn, manifest, manifest_hash="hash-1")

    users = cache_db.list_users(conn)
    assert [u["handle"] for u in users] == ["alice"]

    playlists = cache_db.list_playlists(conn, "user-a")
    assert [p["name"] for p in playlists] == ["Shared"]

    tracks = cache_db.list_tracks_for_playlist(conn, "user-a", "pl-1")
    assert [t["title"] for t in tracks] == ["Track"]


def test_ingest_replaces_prior_data_for_same_user(conn):
    cache_db.ingest_manifest(conn, _manifest("user-a", "alice", "Old"), manifest_hash="hash-1")
    cache_db.ingest_manifest(conn, _manifest("user-a", "alice", "New"), manifest_hash="hash-2")

    playlists = cache_db.list_playlists(conn, "user-a")
    assert [p["name"] for p in playlists] == ["New"]
    assert cache_db.get_manifest_hash_by_folder(conn, "alice__abcd1234") == "hash-2"


def test_ingest_replaces_stale_row_when_same_folder_reports_a_new_id(conn):
    # e.g. a local user_id derivation migration (see sync_service.py:derive_user_id) -
    # same real publishing slot (folder_name), different id than last ingested. The old
    # id must not linger as an orphaned duplicate of the same person.
    cache_db.ingest_manifest(conn, _manifest("old-id", "alice"), manifest_hash="hash-1")
    cache_db.ingest_manifest(conn, _manifest("new-id", "alice"), manifest_hash="hash-2")

    users = cache_db.list_users(conn)
    assert [u["id"] for u in users] == ["new-id"]
    assert cache_db.list_playlists(conn, "old-id") == []


def test_prune_missing_users_removes_stale_and_cascades(conn):
    cache_db.ingest_manifest(conn, _manifest("user-a", "alice"), manifest_hash="hash-1")
    cache_db.ingest_manifest(conn, _manifest("user-b", "bob"), manifest_hash="hash-1")

    pruned = cache_db.prune_missing_users(conn, current_folder_names={"alice__abcd1234"})

    assert pruned == 1
    assert [u["handle"] for u in cache_db.list_users(conn)] == ["alice"]
    assert cache_db.list_playlists(conn, "user-b") == []


def test_parse_manifest_accepts_valid_data():
    manifest = parse_manifest(json.dumps(_manifest("user-a", "alice")).encode("utf-8"))
    assert manifest["user"]["handle"] == "alice"


def test_parse_manifest_rejects_missing_required_field():
    broken = _manifest("user-a", "alice")
    del broken["user"]["handle"]
    with pytest.raises(ManifestValidationError):
        parse_manifest(json.dumps(broken).encode("utf-8"))


def test_parse_manifest_rejects_unsupported_schema_version():
    broken = _manifest("user-a", "alice")
    broken["schema_version"] = 99
    with pytest.raises(ManifestValidationError):
        parse_manifest(json.dumps(broken).encode("utf-8"))


def test_parse_manifest_rejects_invalid_json():
    with pytest.raises(ManifestValidationError):
        parse_manifest(b"{not valid json")
