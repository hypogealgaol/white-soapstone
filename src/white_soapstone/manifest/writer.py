"""Atomic local write of a manifest dict to disk, before it's uploaded to Drive.

Writing to a temp file then renaming avoids ever leaving a half-written manifest.json
on disk if the process is interrupted mid-write.
"""

from __future__ import annotations

import json
import os
from pathlib import Path


def write_manifest_atomic(manifest: dict, dest_path: str | Path) -> Path:
    dest_path = Path(dest_path)
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = dest_path.with_suffix(dest_path.suffix + ".tmp")
    with tmp_path.open("w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2)
        fh.write("\n")
    os.replace(tmp_path, dest_path)
    return dest_path
