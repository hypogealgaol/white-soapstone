import pytest

from white_soapstone.rekordbox import extractor
from white_soapstone.rekordbox.errors import DbLocked, DbNotFound, KeyExtractionFailed


def test_open_database_maps_file_not_found(monkeypatch):
    def raise_it(*args, **kwargs):
        raise FileNotFoundError("no db here")

    monkeypatch.setattr(extractor, "Rekordbox6Database", raise_it)
    with pytest.raises(DbNotFound):
        extractor._open_database()


def test_open_database_maps_bad_key_value_error(monkeypatch):
    def raise_it(*args, **kwargs):
        raise ValueError("The provided database key doesn't look valid!")

    monkeypatch.setattr(extractor, "Rekordbox6Database", raise_it)
    with pytest.raises(KeyExtractionFailed):
        extractor._open_database()


def test_open_database_maps_locked_message(monkeypatch):
    def raise_it(*args, **kwargs):
        raise RuntimeError("database is locked")

    monkeypatch.setattr(extractor, "Rekordbox6Database", raise_it)
    with pytest.raises(DbLocked):
        extractor._open_database()


def test_open_database_maps_cipher_failure_message(monkeypatch):
    def raise_it(*args, **kwargs):
        raise RuntimeError("file is not a database")

    monkeypatch.setattr(extractor, "Rekordbox6Database", raise_it)
    with pytest.raises(KeyExtractionFailed):
        extractor._open_database()


def test_track_from_content_scales_bpm_and_pulls_related_names():
    class FakeRelated:
        def __init__(self, name):
            self.Name = name

    class FakeKey:
        ScaleName = "Am"

    class FakeContent:
        ID = 42
        Title = "Track"
        Artist = FakeRelated("Some Artist")
        Album = None
        Genre = FakeRelated("Techno")
        BPM = 12806
        Key = FakeKey()
        Length = 352.0
        FolderPath = "/music/track.mp3"
        ReleaseYear = 2021
        Rating = 3
        Commnt = "hi"
        ISRC = ""

    track = extractor._track_from_content(FakeContent())

    assert track.id == "42"
    assert track.artist == "Some Artist"
    assert track.album is None
    assert track.bpm == pytest.approx(128.06)
    assert track.key == "Am"
    assert track.file_path == "/music/track.mp3"
    assert track.isrc is None
