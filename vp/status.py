"""Idempotency helpers shared by the CLI and the web UI."""

from __future__ import annotations

from pathlib import Path


def input_mtime(track) -> float:
    """Latest mtime among the files a render depends on."""
    paths = [track.audio, *track.images]
    for extra in (
        track.beatmap_path,
        track.root / "track.json",
        track.root / "title.txt",
    ):
        if extra.exists():
            paths.append(extra)
    return max(p.stat().st_mtime for p in paths if p.exists())


def is_up_to_date(path: Path, mtime: float) -> bool:
    """True when a change to `path` would produce an up-to-date output."""
    return path.exists() and path.stat().st_mtime >= mtime