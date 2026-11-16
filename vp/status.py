"""Idempotency helpers shared by the CLI and the web UI (issue #40).

A render is skipped only when its *fingerprint* matches the one recorded in the
sidecar written next to the output after the last successful render. The
fingerprint covers everything that determines the output bytes: the resolved
style (after preset and `auto` resolution), HUD text, aspect, window, preview
flag, cut overrides, the beatmap, the identity of every input file (audio,
images, texture, fonts), and the renderer version.

Bump `RENDER_VERSION` whenever a code change alters rendered output for the same
inputs (filter graph, encoder settings, HUD layout, planner behavior) so stale
outputs are re-rendered.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from . import __version__
from .fonts import resolve_face
from .textures import DEFAULT_TEXTURES_DIR, resolve_texture
from .vibe import finalize_auto_style

RENDER_VERSION = 1
SIDECAR_SUFFIX = ".vp.json"


def sidecar_path(output: Path) -> Path:
    output = Path(output)
    return output.with_name(output.name + SIDECAR_SUFFIX)


def _file_identity(path: Path | None) -> list | None:
    """Cheap identity for an input file: resolved path, size, and mtime (ns)."""
    if path is None:
        return None
    path = Path(path)
    try:
        stat = path.stat()
    except FileNotFoundError:
        return [str(path), None, None]
    return [str(path.resolve()), stat.st_size, stat.st_mtime_ns]


def _font_files(style: dict) -> list[Path | None]:
    title_font = style.get("title_font")
    title = resolve_face(style.get("font") or "clean", title_font)
    meta_name = style.get("meta_font") or "auto"
    meta = title if meta_name == "auto" else resolve_face(meta_name, title_font)
    faces = [title.path, meta.path]
    if style.get("watermark"):
        faces.append(resolve_face(style.get("watermark_font") or "anton").path)
    return faces


def render_fingerprint(
    track,
    aspect,
    beatmap: dict,
    window: tuple[float, float],
    preview: bool = False,
    textures_dir: str | Path = DEFAULT_TEXTURES_DIR,
) -> str:
    """Stable sha256 of everything that determines a render's output."""
    finalize_auto_style(track, beatmap)
    style = track.style
    payload = {
        "render_version": RENDER_VERSION,
        "vp_version": __version__,
        "aspect": [aspect.key, aspect.width, aspect.height],
        "window": [round(float(window[0]), 4), round(float(window[1]), 4)],
        "preview": bool(preview),
        "style": style,
        "hud": [track.title, track.artist, track.handle],
        "cuts": (track.overrides or {}).get("cuts"),
        "beatmap": beatmap,
        "inputs": {
            "audio": _file_identity(track.audio),
            "images": [_file_identity(p) for p in track.images],
            "texture": _file_identity(
                resolve_texture(style.get("texture"), textures_dir)
            ),
            "fonts": [_file_identity(p) for p in _font_files(style)],
        },
    }
    blob = json.dumps(payload, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(blob.encode()).hexdigest()


def is_current(output: Path, fingerprint: str) -> bool:
    """True when `output` exists and was rendered with exactly `fingerprint`."""
    output = Path(output)
    sidecar = sidecar_path(output)
    if not output.is_file() or not sidecar.is_file():
        return False
    try:
        recorded = json.loads(sidecar.read_text()).get("fingerprint")
    except (OSError, ValueError, AttributeError):
        return False
    return recorded == fingerprint


def invalidate(output: Path) -> None:
    """Drop the sidecar before (re)rendering so a failed or partial render is
    never mistaken for a current one."""
    sidecar_path(Path(output)).unlink(missing_ok=True)


def record(output: Path, fingerprint: str) -> None:
    """Write the sidecar atomically after a successful render."""
    sidecar = sidecar_path(Path(output))
    tmp = sidecar.with_name(sidecar.name + ".tmp")
    tmp.write_text(
        json.dumps(
            {"fingerprint": fingerprint, "render_version": RENDER_VERSION}, indent=2
        )
        + "\n"
    )
    os.replace(tmp, sidecar)
