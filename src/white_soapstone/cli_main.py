"""PyInstaller entry point for the console CLI executable.

Not the same file as cli.py itself: PyInstaller freezes whatever script it's pointed
at as the top-level __main__, and cli.py relies on relative imports (`from .cache
import ...`) that only work when it's imported as part of the white_soapstone package
- see gui_main.py for the same issue on the windowed executable's side.
"""

from __future__ import annotations

from white_soapstone.cli import app

if __name__ == "__main__":
    app()
