# Builds standalone Windows executables via PyInstaller: no Python/uv/git needed on
# the machine that runs them - just this repo checked out (or copied) somewhere with
# `uv sync` already run once, then this script.
#
# Produces two executables in dist/, not one, because a GUI-subsystem build (no
# console window) can't usefully show CLI output on Windows:
#   - white-soapstone.exe      console CLI - init, whitelist, sync-once, pull, gc, etc.
#   - white-soapstone-app.exe  windowed app - just opens the UI (serve-ui equivalent)
#
# Both need client_secret.json (see docs/OAUTH_SETUP.md) copied next to them before
# they'll be able to reach Google Drive - it isn't bundled into the executable itself.

$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")

$dataArgs = @(
    "--add-data", "src/white_soapstone/cache/migrations/001_init.sql;white_soapstone/cache/migrations",
    "--add-data", "src/white_soapstone/schema/manifest.schema.json;white_soapstone/schema",
    "--add-data", "src/white_soapstone/web/static;white_soapstone/web/static"
)

Write-Host "Building white-soapstone.exe (console CLI)..."
uv run pyinstaller --noconfirm --name white-soapstone --onefile --console `
    @dataArgs `
    src/white_soapstone/cli_main.py

Write-Host "Building white-soapstone-app.exe (windowed UI)..."
uv run pyinstaller --noconfirm --name white-soapstone-app --onefile --windowed `
    --icon src/white_soapstone/web/static/icon.ico `
    @dataArgs `
    src/white_soapstone/gui_main.py

Write-Host ""
Write-Host "Done. dist/white-soapstone.exe and dist/white-soapstone-app.exe are ready."
Write-Host "Copy client_secret.json into dist/ alongside them before running either."
