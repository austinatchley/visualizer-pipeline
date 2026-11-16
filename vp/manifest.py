"""Track folder plumbing: `track.json` IO, track discovery, and image lookup.

The single implementation shared by the loader (`vp.track`), the CLI, and the
studio (issue #49). Nothing here interprets manifest contents beyond the
`images` list; style/hook resolution lives in `vp.config`.
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path

MANIFEST_NAME = "track.json"
IMAGES_DIRNAME = "images"
AUDIO_EXTS = {".wav", ".mp3", ".flac", ".m4a", ".aiff", ".aif", ".ogg", ".opus"}
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"}


def manifest_path(folder: Path) -> Path:
    return folder / MANIFEST_NAME


def read_manifest(folder: Path) -> dict:
    """Return the folder's `track.json` as a dict, or `{}` when there is none."""
    path = manifest_path(folder)
    if not path.is_file():
        return {}
    data = json.loads(path.read_text())
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return data


def write_manifest(folder: Path, manifest: dict) -> None:
    """Write `track.json` atomically: a temp file in the same folder, then
    `os.replace`, so a crash or a concurrent reader never sees a partial file."""
    path = manifest_path(folder)
    # Unique per writer (not mkstemp: that creates 0600 files, ignoring umask).
    tmp = path.with_name(f".{MANIFEST_NAME}.{os.getpid()}.{threading.get_ident()}.tmp")
    try:
        tmp.write_text(json.dumps(manifest, indent=2) + "\n")
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def is_track_folder(path: Path) -> bool:
    """A track folder is a directory holding at least one audio file."""
    return path.is_dir() and any(
        p.is_file() and p.suffix.lower() in AUDIO_EXTS for p in path.iterdir()
    )


def discover_tracks(root: Path) -> list[Path]:
    """Track folders directly under `root`, sorted by name; `[]` if `root` is
    not a directory."""
    if not root.is_dir():
        return []
    return sorted(p for p in root.iterdir() if is_track_folder(p))


def image_dir(folder: Path) -> Path:
    """Where a track's images live: `images/` if present, else the folder itself."""
    d = folder / IMAGES_DIRNAME
    return d if d.is_dir() else folder


def scan_images(folder: Path) -> list[Path]:
    """Image files in the track's image dir, sorted by name (ignores the manifest)."""
    d = image_dir(folder)
    return sorted(p for p in d.iterdir() if p.is_file() and p.suffix.lower() in IMAGE_EXTS)
