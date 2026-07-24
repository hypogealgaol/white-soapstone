"""CLI entrypoints: headless sync (Phase 1/2) plus launching the minimal UI (Phase 3)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import click

from .cache import db as cache_db
from .config import store
from .drive.auth import sign_out
from .rekordbox.extractor import dump_library
from .sync.gc_service import run_gc
from .sync.pull_service import pull_all
from .sync.sync_service import run_sync, update_handle


@click.group()
def app() -> None:
    """white-soapstone: share Rekordbox playlist previews over a shared Google Drive folder."""


@app.command()
@click.option("--handle", required=True, help="Display name shown to other users")
@click.option("--shared-folder-id", required=True, help="Google Drive folder id shared with the group")
@click.option(
    "--db-path",
    default=None,
    help=(
        "Path to Rekordbox's master.db, only needed if auto-detection can't find it "
        "(e.g. running under WSL against a Rekordbox install on the Windows side - "
        "use the /mnt/c/... mount path there)."
    ),
)
@click.option(
    "--key",
    "db_key",
    default=None,
    help="Manual SQLCipher key override, only needed if automatic key extraction fails.",
)
def init(handle: str, shared_folder_id: str, db_path: str | None, db_key: str | None) -> None:
    """Set your handle and the shared Drive folder id (one-time setup)."""
    config = store.load()
    config.handle = handle
    config.drive_shared_folder_id = shared_folder_id
    if db_path:
        config.rekordbox_db_path = db_path
    if db_key:
        config.rekordbox_key = db_key
    store.save(config)
    click.echo(f"Saved. handle={handle!r} shared_folder_id={shared_folder_id!r}")


@app.command("set-handle")
@click.argument("new_handle")
@click.option(
    "--client-secrets",
    default="client_secret.json",
    show_default=True,
    help="Path to the Google OAuth client secrets JSON (see docs/OAUTH_SETUP.md)",
)
def set_handle(new_handle: str, client_secrets: str) -> None:
    """Change your display handle.

    If you've already published under the old handle, your Drive folder is renamed in
    place rather than abandoned, so existing shared content carries over.
    """
    try:
        config = update_handle(new_handle, client_secrets)
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(f"Handle is now {config.handle!r}.")


@app.command("list-playlists")
def list_playlists() -> None:
    """Print your Rekordbox playlists and ids, marking which are whitelisted."""
    config = store.load()
    dump = dump_library(db_path=config.rekordbox_db_path, key=config.rekordbox_key)
    for playlist in dump.playlists:
        marker = "[x]" if playlist.id in config.whitelist_playlist_ids else "[ ]"
        kind = " (folder)" if playlist.is_folder else ""
        click.echo(f"{marker} {playlist.id}\t{playlist.name}{kind}\t({len(playlist.track_ids)} tracks)")


@app.group()
def whitelist() -> None:
    """Manage which playlists are shared. Whitelisting is playlist-only. Every track
    currently in a whitelisted playlist is included, and tracks added to it later are
    picked up automatically on the next sync."""


@whitelist.command("add")
@click.argument("playlist_id")
def whitelist_add(playlist_id: str) -> None:
    store.whitelist_playlist(playlist_id)
    click.echo(f"Whitelisted: {playlist_id}")


@whitelist.command("remove")
@click.argument("playlist_id")
def whitelist_remove(playlist_id: str) -> None:
    store.unwhitelist_playlist(playlist_id)
    click.echo(f"Removed from whitelist: {playlist_id}")


@app.command("sync-once")
@click.option(
    "--client-secrets",
    default="client_secret.json",
    show_default=True,
    help="Path to the Google OAuth client secrets JSON (see docs/OAUTH_SETUP.md)",
)
def sync_once(client_secrets: str) -> None:
    """Run one full sync: extract whitelisted playlists, transcode, upload to Drive."""
    result = run_sync(client_secrets)
    click.echo(
        f"Synced {result.tracks_published} tracks across {result.whitelisted_playlists} "
        f"playlists ({result.tracks_transcoded} newly transcoded, "
        f"{result.tracks_skipped_no_file} skipped - source file missing)."
    )
    click.echo(f"Manifest written to: {result.manifest_path}")


@app.command("pull")
@click.option(
    "--client-secrets",
    default="client_secret.json",
    show_default=True,
    help="Path to the Google OAuth client secrets JSON (see docs/OAUTH_SETUP.md)",
)
def pull(client_secrets: str) -> None:
    """Download every user's manifest.json into the local cache.

    Skips any whose manifest hasn't changed since the last pull.
    """
    result = pull_all(client_secrets)
    click.echo(
        f"Found {result.users_found} user folder(s): {result.users_updated} updated, "
        f"{result.users_unchanged} unchanged, {result.users_pruned} pruned.",
    )
    if result.failed_folders:
        click.echo(f"Failed to ingest: {', '.join(result.failed_folders)}")


@app.command("browse")
def browse() -> None:
    """Print everyone currently in the local cache and their playlists (post-`pull`)."""
    conn = cache_db.connect()
    for user in cache_db.list_users(conn):
        marker = " (you)" if user["is_self"] else ""
        click.echo(f"{user['handle']}{marker}  [{user['id']}]")
        for playlist in cache_db.list_playlists(conn, user["id"]):
            tracks = cache_db.list_tracks_for_playlist(conn, user["id"], playlist["id"])
            folder = f"{playlist['parent_name']} / " if playlist["parent_name"] else ""
            click.echo(f"  {folder}{playlist['name']}\t({len(tracks)} tracks)")


@app.command("gc")
@click.option(
    "--client-secrets",
    default="client_secret.json",
    show_default=True,
    help="Path to the Google OAuth client secrets JSON (see docs/OAUTH_SETUP.md)",
)
def gc(client_secrets: str) -> None:
    """Delete shared preview files nobody's current manifest references anymore.

    Safe by design: checks every user's live manifest.json first and aborts entirely
    (deleting nothing) if any of them can't be read, rather than risk deleting content
    someone else still needs based on incomplete information. Not run automatically as
    part of sync-once - run this occasionally by hand instead.
    """
    result = run_gc(client_secrets)
    click.echo(
        f"Checked {result.users_checked} user(s) and {result.content_files_checked} "
        f"shared content file(s): deleted {result.orphans_deleted} orphan(s)."
    )


@app.command("logout")
def logout() -> None:
    """Forget the cached Google refresh token.

    Only clears the Google sign-in - your handle, whitelist, and Drive folder ids stay
    put. The next action that needs Drive (sync/pull/preview) will go through the full
    interactive sign-in again.
    """
    sign_out()
    click.echo("Signed out of Google. The next sync/pull will prompt you to sign in again.")


@app.command("serve-ui")
def serve_ui() -> None:
    """Open the minimal app window (backed by a local FastAPI server)."""
    from .ui.app_window import launch

    launch()


_REPO_ROOT = Path(__file__).resolve().parents[2]
# PyInstaller's --add-data separator is platform-specific (";" on Windows, ":" on
# everything else) - not the same as os.sep, so computed here explicitly.
_ADD_DATA_SEP = ";" if sys.platform == "win32" else ":"
_BUNDLED_DATA = [
    f"src/white_soapstone/cache/migrations/001_init.sql{_ADD_DATA_SEP}white_soapstone/cache/migrations",
    f"src/white_soapstone/schema/manifest.schema.json{_ADD_DATA_SEP}white_soapstone/schema",
    f"src/white_soapstone/web/static{_ADD_DATA_SEP}white_soapstone/web/static",
]


def _run_pyinstaller(name: str, entry: str, *, windowed: bool, icon: str | None) -> None:
    cmd = ["uv", "run", "pyinstaller", "--noconfirm", "--name", name, "--onefile"]
    cmd.append("--windowed" if windowed else "--console")
    for data_arg in _BUNDLED_DATA:
        cmd += ["--add-data", data_arg]
    if icon:
        cmd += ["--icon", icon]
    cmd.append(entry)
    click.echo(f"Building {name}...")
    try:
        subprocess.run(cmd, cwd=_REPO_ROOT, check=True)
    except subprocess.CalledProcessError as exc:
        raise click.ClickException(f"pyinstaller failed building {name} (exit {exc.returncode})") from exc
    except FileNotFoundError as exc:
        raise click.ClickException("uv not found on PATH - can't invoke pyinstaller.") from exc


@app.command("build")
@click.option("--skip-cli", is_flag=True, help="Only build the windowed app, skip the console CLI executable.")
@click.option("--skip-app", is_flag=True, help="Only build the console CLI executable, skip the windowed app.")
def build(skip_cli: bool, skip_app: bool) -> None:
    """Package this app into standalone .exe files via PyInstaller, into dist/.

    Windows-only for now - packaging on macOS is unsupported. Reimplements
    scripts/build_executable.ps1 in pure Python so it doesn't depend on PowerShell.
    Needs client_secret.json copied next to the output manually afterward - it's never
    bundled into the executable itself.
    """
    if sys.platform != "win32":
        raise click.ClickException(
            "`build` only works from a native Windows shell - PyInstaller builds for whatever "
            "platform it runs on (no cross-compiling), so running this from WSL or macOS would "
            "silently produce a Linux/Mac binary, not a .exe. Unsupported for now."
        )

    icon = "src/white_soapstone/web/static/icon.ico"

    if not skip_cli:
        _run_pyinstaller("white-soapstone", "src/white_soapstone/cli_main.py", windowed=False, icon=None)
    if not skip_app:
        _run_pyinstaller("white-soapstone-app", "src/white_soapstone/gui_main.py", windowed=True, icon=icon)

    click.echo("")
    click.echo(f"Done. Output in {_REPO_ROOT / 'dist'}")
    click.echo("Copy client_secret.json next to the output before running either.")


if __name__ == "__main__":
    app()
