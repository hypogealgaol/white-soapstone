CREATE TABLE users (
  id             TEXT PRIMARY KEY,
  handle         TEXT NOT NULL,
  folder_name    TEXT NOT NULL,
  drive_folder_id TEXT,
  manifest_hash  TEXT,
  schema_version INTEGER NOT NULL,
  last_synced_at TEXT,
  is_self        INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE playlists (
  user_id   TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  id        TEXT NOT NULL,
  name      TEXT NOT NULL,
  parent_id TEXT,
  parent_name TEXT,
  position  INTEGER NOT NULL,
  PRIMARY KEY (user_id, id)
);

-- `id` is a content id (see manifest/builder.py:content_id), shared across every user
-- who has the same song - not a per-user id. The shared preview file for a track
-- lives at SharedFolder/_content/<id>.mp3, so no separate path column is needed.
CREATE TABLE tracks (
  user_id            TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  id                 TEXT NOT NULL,
  title              TEXT,
  artist             TEXT,
  album              TEXT,
  genre              TEXT,
  bpm                REAL,
  key                TEXT,
  duration_sec       REAL,
  year               INTEGER,
  rating             INTEGER,
  comment            TEXT,
  preview_format     TEXT,
  preview_bitrate_kbps INTEGER,
  preview_size_bytes INTEGER,
  preview_checksum   TEXT,
  transcoded_at      TEXT,
  PRIMARY KEY (user_id, id)
);

CREATE TABLE playlist_tracks (
  user_id     TEXT NOT NULL,
  playlist_id TEXT NOT NULL,
  track_id    TEXT NOT NULL,
  position    INTEGER NOT NULL,
  PRIMARY KEY (user_id, playlist_id, track_id),
  FOREIGN KEY (user_id, playlist_id) REFERENCES playlists(user_id, id) ON DELETE CASCADE,
  FOREIGN KEY (user_id, track_id) REFERENCES tracks(user_id, id) ON DELETE CASCADE
);

CREATE INDEX idx_tracks_user ON tracks(user_id);
CREATE INDEX idx_playlist_tracks_playlist ON playlist_tracks(user_id, playlist_id);
