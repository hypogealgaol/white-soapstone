# Architecture

See the repo root's plan doc for full context. Summary: this app reads a user's local
Rekordbox library, lets them whitelist entire playlists (not individual tracks) to
share, transcodes whitelisted tracks to 128kbps MP3 previews, and publishes a
`manifest.json` + `previews/*.mp3` into that user's own subfolder of a shared Google
Drive folder. Other users' apps read the same shared folder to browse everyone's
whitelisted playlists and preview-play their tracks. There's no hosted backend - the
shared Drive folder is the database.

## Confirmed against a real Rekordbox library (Phase 1)

- `pyrekordbox`'s automatic SQLCipher key extraction and playlist/track reading work
  correctly against a live, real Rekordbox 6/7 `master.db` on Windows.
- Rekordbox's `BPM` column is stored as `real_bpm * 100` (confirmed: a track showing
  `128.06` BPM decoded correctly through that scaling).
- `ffmpeg` (via `imageio-ffmpeg`'s bundled per-platform binary) transcodes to
  128kbps MP3 correctly and at the expected file size.
- The whitelist-then-regenerate-manifest design correctly drops all of a playlist's
  tracks from the manifest the moment it's un-whitelisted (verified via
  `build_manifest` before/after `unwhitelist_playlist`).

## Known risks / open verification items

- **macOS process name for Rekordbox is unverified** - no Mac available during Phase 1
  development. `pyrekordbox.utils.get_rekordbox_pid()` / `get_rekordbox_agent_pid()`
  already wrap this cross-platform (pyrekordbox's own maintainers keep them in sync
  with Rekordbox releases) - the daemon watcher (Phase 4) should use these directly
  instead of hand-rolling process-name matching.
- **PyInstaller + `sqlcipher3-wheels`**: not yet verified that PyInstaller can bundle
  pyrekordbox's SQLCipher native dependency into a standalone executable. Since this
  is now a single Python app (no Electron/sidecar split), there's no "sign a nested
  binary separately" problem, but the native dependency itself still needs testing
  under a real PyInstaller build before Phase 4.
- **`pystray` + `pywebview` main-thread coexistence** on macOS is a known tricky
  interaction - verify the standard workaround (one on the main thread, the other on a
  background thread) under a packaged build, not just in dev, before relying on it.
- **WebView2 Runtime** (Windows, for `pywebview`) is preinstalled on Windows 10 21H2+
  and Windows 11; only a concern on older/unpatched systems.
- Google's 100-test-user cap and manual "add as test user" step (see
  `docs/OAUTH_SETUP.md`) is an ongoing operational task, not a one-time setup step, for
  as long as the OAuth consent screen stays in Testing status.
- `manifest.json` is plaintext and readable by anyone with access to the shared Drive
  folder - don't add fields to it beyond what's needed for display/fetch (no raw
  filesystem paths, no Rekordbox-internal ids, no email addresses).
- The Drive upload/download path (`drive/auth.py`, `drive/client.py`, `drive/upload.py`)
  is implemented but not yet exercised against a real Google Cloud OAuth client or a
  real shared folder - that requires the OAuth setup in `docs/OAUTH_SETUP.md` to be
  completed first with real credentials, which weren't available during initial
  development.

## What's implemented (Phase 1 - headless CLI pipeline)

- `rekordbox/extractor.py` - full library dump via pyrekordbox.
- `config/store.py` / `config/paths.py` - local JSON config (handle, shared folder id,
  playlist whitelist) and per-OS app data paths.
- `transcode/ffmpeg.py` - subprocess-based MP3 transcoding.
- `manifest/builder.py` / `manifest/writer.py` - manifest construction (namespaced,
  collision-safe ids) and atomic local writes.
- `drive/auth.py` / `drive/client.py` / `drive/upload.py` - OAuth, a thin Drive v3
  wrapper, and the find-or-create-folder + upsert + prune upload flow.
- `sync/sync_service.py` + `cli.py` - ties it all together behind
  `white-soapstone init`, `list-playlists`, `whitelist add/remove`, and `sync-once`.

## Not yet implemented (Phases 2-4)

- Reading other users' manifests and the local SQLite cache (`drive/download.py`,
  `sync/pull_service.py`, `cache/db.py`, `manifest/reader.py`).
- The `pywebview`-wrapped minimal UI (`web/server.py`, `web/static/`, `ui/app_window.py`).
- The background daemon/tray, auto-launch-on-login, and PyInstaller packaging
  (`daemon/watcher.py`, `daemon/tray.py`, `scripts/build_executable.*`).
