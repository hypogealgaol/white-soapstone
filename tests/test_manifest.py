from white_soapstone.config.store import Config
from white_soapstone.manifest.builder import PreviewInfo, build_manifest, content_id, namespaced_id
from white_soapstone.rekordbox.extractor import LibraryDump, RawPlaylist, RawTrack


def _track(id, title, artist=None, file_path="a.mp3", isrc=None):
    return RawTrack(
        id=id,
        title=title,
        artist=artist,
        album=None,
        genre=None,
        bpm=None,
        key=None,
        duration_sec=None,
        file_path=file_path,
        year=None,
        rating=None,
        comment=None,
        isrc=isrc,
    )


def _preview() -> PreviewInfo:
    return PreviewInfo(
        format="mp3",
        bitrate_kbps=128,
        size_bytes=1234,
        checksum_sha256="deadbeef",
        transcoded_at="2026-01-01T00:00:00+00:00",
    )


def test_namespaced_id_is_deterministic_and_user_scoped():
    assert namespaced_id("user-a", "123") == namespaced_id("user-a", "123")
    assert namespaced_id("user-a", "123") != namespaced_id("user-b", "123")


def test_content_id_prefers_isrc_over_text():
    same_isrc_different_text = content_id("Artist A", "Title A", isrc="US1234567890")
    assert same_isrc_different_text == content_id("Someone Else Entirely", "Different Title", isrc="US1234567890")


def test_content_id_falls_back_to_normalized_artist_title():
    assert content_id("Artist", "Track Name") == content_id("  ARTIST  ", "track   name")
    assert content_id("Artist", "Café Song") == content_id("artist", "cafe song")
    assert content_id("Artist A", "Track") != content_id("Artist B", "Track")


def test_build_manifest_only_includes_whitelisted_playlists_and_their_tracks():
    dump = LibraryDump(
        playlists=[
            RawPlaylist(id="pl-1", name="Shared", parent_id=None, is_folder=False, position=0, track_ids=["t1", "t2"]),
            RawPlaylist(id="pl-2", name="Private", parent_id=None, is_folder=False, position=1, track_ids=["t3"]),
        ],
        tracks=[
            _track("t1", "A", artist="Artist A"),
            _track("t2", "B", artist="Artist B"),
            _track("t3", "C", artist="Artist C"),
        ],
    )
    config = Config(user_id="user-a", handle="dj", my_folder_name="dj__abcd1234", whitelist_playlist_ids=["pl-1"])
    content_id_by_raw_track = {
        "t1": content_id("Artist A", "A"),
        "t2": content_id("Artist B", "B"),
        "t3": content_id("Artist C", "C"),
    }
    previews = {cid: _preview() for cid in content_id_by_raw_track.values()}

    manifest = build_manifest(dump, config, content_id_by_raw_track, previews, "0.1.0")

    assert [p["name"] for p in manifest["playlists"]] == ["Shared"]
    assert {t["title"] for t in manifest["tracks"]} == {"A", "B"}


def test_build_manifest_drops_tracks_with_no_preview():
    dump = LibraryDump(
        playlists=[RawPlaylist(id="pl-1", name="Shared", parent_id=None, is_folder=False, position=0, track_ids=["t1", "t2"])],
        tracks=[_track("t1", "A", artist="Artist A"), _track("t2", "B", artist="Artist B", file_path=None)],
    )
    config = Config(user_id="user-a", handle="dj", my_folder_name="dj__abcd1234", whitelist_playlist_ids=["pl-1"])
    cid_a = content_id("Artist A", "A")
    cid_b = content_id("Artist B", "B")
    content_id_by_raw_track = {"t1": cid_a, "t2": cid_b}
    # only t1 got transcoded (t2 had no source file, so sync_service never produces a preview for it)
    previews = {cid_a: _preview()}

    manifest = build_manifest(dump, config, content_id_by_raw_track, previews, "0.1.0")

    assert [t["title"] for t in manifest["tracks"]] == ["A"]
    assert manifest["playlists"][0]["track_ids"] == [cid_a]


def test_build_manifest_dedupes_two_raw_tracks_with_same_content_id():
    # Same song imported into Rekordbox twice under different internal track ids -
    # both should collapse to one entry in "tracks", referenced by both playlists.
    dump = LibraryDump(
        playlists=[
            RawPlaylist(id="pl-1", name="Playlist One", parent_id=None, is_folder=False, position=0, track_ids=["t1"]),
            RawPlaylist(id="pl-2", name="Playlist Two", parent_id=None, is_folder=False, position=1, track_ids=["t2"]),
        ],
        tracks=[
            _track("t1", "Same Song", artist="Same Artist"),
            _track("t2", "Same Song", artist="Same Artist"),
        ],
    )
    config = Config(
        user_id="user-a", handle="dj", my_folder_name="dj__abcd1234", whitelist_playlist_ids=["pl-1", "pl-2"]
    )
    cid = content_id("Same Artist", "Same Song")
    content_id_by_raw_track = {"t1": cid, "t2": cid}
    previews = {cid: _preview()}

    manifest = build_manifest(dump, config, content_id_by_raw_track, previews, "0.1.0")

    assert len(manifest["tracks"]) == 1
    assert manifest["playlists"][0]["track_ids"] == [cid]
    assert manifest["playlists"][1]["track_ids"] == [cid]


def test_build_manifest_omits_optional_preview_fields_when_referencing_shared_content():
    # sync_service leaves size/checksum/transcoded_at unset when a track's content id
    # was already present in the shared Drive pool - nothing was transcoded locally.
    dump = LibraryDump(
        playlists=[RawPlaylist(id="pl-1", name="Shared", parent_id=None, is_folder=False, position=0, track_ids=["t1"])],
        tracks=[_track("t1", "A", artist="Artist A")],
    )
    config = Config(user_id="user-a", handle="dj", my_folder_name="dj__abcd1234", whitelist_playlist_ids=["pl-1"])
    cid = content_id("Artist A", "A")
    previews = {cid: PreviewInfo(format="mp3", bitrate_kbps=128)}

    manifest = build_manifest(dump, config, {"t1": cid}, previews, "0.1.0")

    preview_out = manifest["tracks"][0]["preview"]
    assert preview_out == {"format": "mp3", "bitrate_kbps": 128}


def test_build_manifest_resolves_parent_folder_name_even_when_folder_itself_is_not_whitelisted():
    # Folders are never whitelisted directly (only real playlists are), so the parent
    # folder's *id* is correctly left out of parent_id (a peer has no way to resolve a
    # folder it never received) - but its name should still surface for display context.
    dump = LibraryDump(
        playlists=[
            RawPlaylist(id="folder-1", name="My Folder", parent_id=None, is_folder=True, position=0, track_ids=[]),
            RawPlaylist(id="pl-1", name="Nested", parent_id="folder-1", is_folder=False, position=0, track_ids=["t1"]),
        ],
        tracks=[_track("t1", "A", artist="Artist A")],
    )
    config = Config(user_id="user-a", handle="dj", my_folder_name="dj__abcd1234", whitelist_playlist_ids=["pl-1"])
    cid = content_id("Artist A", "A")
    manifest = build_manifest(dump, config, {"t1": cid}, {cid: _preview()}, "0.1.0")

    playlist = manifest["playlists"][0]
    assert playlist["parent_id"] is None
    assert playlist["parent_name"] == "My Folder"


def test_build_manifest_empty_whitelist_produces_empty_manifest():
    dump = LibraryDump(
        playlists=[RawPlaylist(id="pl-1", name="Shared", parent_id=None, is_folder=False, position=0, track_ids=["t1"])],
        tracks=[_track("t1", "A", artist="Artist A")],
    )
    config = Config(user_id="user-a", handle="dj", my_folder_name="dj__abcd1234", whitelist_playlist_ids=[])
    cid = content_id("Artist A", "A")
    manifest = build_manifest(dump, config, {"t1": cid}, {cid: _preview()}, "0.1.0")

    assert manifest["playlists"] == []
    assert manifest["tracks"] == []
