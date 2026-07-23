"""Wraps the local FastAPI server in a real native OS window via pywebview.

This is what makes the app feel like an actual application instead of "open your
browser to localhost" - the window gets its own taskbar/dock entry, and neither
Electron nor a bundled Chromium is involved (pywebview uses WebView2 on Windows,
WKWebView on Mac).
"""

from __future__ import annotations

import logging
import socket
import sys
import threading
import time
from pathlib import Path

import uvicorn
import webview
from PIL import Image

from ..config.paths import log_file
from ..web.server import app

logger = logging.getLogger(__name__)

WINDOW_TITLE = "white-soapstone"
BACKGROUND_COLOR = "#1e1e1e"  # matches the UI's dark theme, avoids a white flash on load
# Lives in web/static/ so the same file backs both this taskbar/window icon and the
# in-page icon the UI renders (see web/static/index.html). pywebview's Windows
# (WinForms/System.Drawing) backend only accepts a real .ico file, not a raw PNG -
# "Argument 'picture' must be a picture that can be used as a Icon" if given one.
# Other platforms haven't been tested (no Mac available during development).
_STATIC_DIR = Path(__file__).parent.parent / "web" / "static"
ICON_PATH = _STATIC_DIR / "icon.ico" if sys.platform == "win32" else _STATIC_DIR / "icon.png"
# pywebview's automatic backend detection doesn't reliably pick Cocoa on macOS - a
# bare `webview.start()` there silently renders nothing (confirmed via manual testing:
# forcing gui="cocoa" opens a real, working WKWebView window; leaving it on auto-detect
# does not). No such issue observed on Windows/Linux, so only override here.
_WEBVIEW_GUI = "cocoa" if sys.platform == "darwin" else None


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _run_server(port: int) -> None:
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")


def _wait_until_listening(port: int, timeout_sec: float = 10.0) -> None:
    deadline = time.monotonic() + timeout_sec
    while time.monotonic() < deadline:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            if sock.connect_ex(("127.0.0.1", port)) == 0:
                return
        time.sleep(0.05)
    raise RuntimeError(f"Local server didn't start listening on port {port} within {timeout_sec}s")


def launch() -> None:
    """Starts the API server in a background thread and opens the app window.

    Blocks until the window is closed - must be called from the main thread (pywebview
    requirement, especially on macOS).
    """
    logging.basicConfig(
        filename=str(log_file()),
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    port = _find_free_port()
    server_thread = threading.Thread(target=_run_server, args=(port,), daemon=True)
    server_thread.start()
    _wait_until_listening(port)

    window = webview.create_window(
        WINDOW_TITLE,
        f"http://127.0.0.1:{port}",
        width=1100,
        height=700,
        min_size=(700, 450),
        background_color=BACKGROUND_COLOR,
    )

    quitting = False

    # The tray icon is a nice-to-have, not a requirement for the window itself to
    # work - its backend is platform/desktop-environment-specific (e.g. needs
    # AppIndicator3/Ayatana GI typelibs on Linux, which WSL doesn't ship and doesn't
    # really have a tray surface for anyway) and importing pystray can fail entirely
    # in environments that lack it. Never let that take the whole app down.
    try:
        import pystray
    except Exception as exc:  # noqa: BLE001 - tray is optional, any failure just disables it
        pystray = None
        logger.warning("System tray icon unavailable (%s) - continuing without it.", exc)

    def on_closing() -> bool | None:
        # The X button hides to tray instead of exiting - only the tray's "Quit" is
        # allowed to actually end the process (see winforms.py's on_closing: returning
        # False here sets args.Cancel = True on the underlying Form). Without a tray
        # icon there's no way to reopen a hidden window, so let X actually quit instead.
        if quitting or pystray is None:
            return None
        window.hide()
        return False

    window.events.closing += on_closing

    if pystray is not None:
        def on_open(_icon, _item) -> None:
            window.show()

        def on_quit(icon, _item) -> None:
            nonlocal quitting
            quitting = True
            window.destroy()
            icon.stop()

        tray_icon = pystray.Icon(
            WINDOW_TITLE,
            icon=Image.open(ICON_PATH) if ICON_PATH.exists() else None,
            title=WINDOW_TITLE,
            menu=pystray.Menu(
                pystray.MenuItem("Open", on_open, default=True),
                pystray.MenuItem("Quit", on_quit),
            ),
        )
        threading.Thread(target=tray_icon.run, daemon=True).start()

    webview.start(icon=str(ICON_PATH) if ICON_PATH.exists() else None, gui=_WEBVIEW_GUI)
