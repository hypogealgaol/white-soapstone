"""Transcodes source audio to a compressed preview via ffmpeg.

The actual speed here comes entirely from ffmpeg's own compiled, multithreaded
encoder - there's no faster path regardless of what language calls it, so we just
shell out to it directly rather than going through a wrapper library.
imageio-ffmpeg bundles a per-platform static ffmpeg binary so users don't need
ffmpeg on PATH.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import imageio_ffmpeg

# Without this, each ffmpeg invocation pops up a new console window when running from
# a windowed (--windowed/no-console) PyInstaller build on Windows, since the child
# process has no parent console to attach to.
_CREATIONFLAGS = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0

DEFAULT_BITRATE_KBPS = 128


class TranscodeError(Exception):
    """ffmpeg exited non-zero while transcoding a preview."""


def ffmpeg_path() -> str:
    return imageio_ffmpeg.get_ffmpeg_exe()


def transcode_to_preview_mp3(
    source_path: str | Path,
    dest_path: str | Path,
    bitrate_kbps: int = DEFAULT_BITRATE_KBPS,
) -> Path:
    """Re-encode `source_path` to a full-length MP3 preview at `dest_path`.

    Returns dest_path on success. Raises TranscodeError with ffmpeg's stderr on failure
    (e.g. source file missing/corrupt, unsupported codec).
    """
    dest_path = Path(dest_path)
    dest_path.parent.mkdir(parents=True, exist_ok=True)

    cmd = [
        ffmpeg_path(),
        "-y",
        "-i", str(source_path),
        "-vn",  # drop any embedded cover-art video stream
        "-map", "0:a:0",
        "-codec:a", "libmp3lame",
        "-b:a", f"{bitrate_kbps}k",
        str(dest_path),
    ]
    # ffmpeg echoes the input path (and any embedded metadata) back into stderr, which
    # can contain non-ASCII characters (e.g. accented filenames) - decode as UTF-8
    # explicitly rather than the OS locale's default codepage, which has crashed here
    # on Windows systems where that default isn't UTF-8.
    result = subprocess.run(
        cmd, capture_output=True, encoding="utf-8", errors="replace", creationflags=_CREATIONFLAGS
    )
    if result.returncode != 0:
        raise TranscodeError(
            f"ffmpeg failed transcoding '{source_path}' (exit {result.returncode}): "
            f"{result.stderr.strip()[-2000:]}"
        )
    return dest_path
