"""Entry point for the packaged windowed (no console) executable.

Kept separate from cli.py's console entry point: a PyInstaller build with a GUI
subsystem (no console window) can't usefully print CLI output, so the console CLI
(`white-soapstone`, everything in cli.py - init, whitelist, sync-once, etc.) and this
windowed app (`white-soapstone-app`, just opens the UI) are packaged as two separate
executables from the same codebase rather than trying to make one binary do both.
"""

from __future__ import annotations


def main() -> None:
    # Absolute import, not relative: PyInstaller freezes this file as the top-level
    # __main__ script (not as part of the white_soapstone package), so a relative
    # import (`from .ui...`) fails with "attempted relative import with no known
    # parent package" in the packaged executable, even though it works fine in dev.
    from white_soapstone.ui.app_window import launch

    launch()


if __name__ == "__main__":
    main()
