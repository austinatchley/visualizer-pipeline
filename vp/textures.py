"""Texture library discovery and path resolution."""

from __future__ import annotations

import re
from pathlib import Path

DEFAULT_TEXTURES_DIR = Path("textures")

TEXTURE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"}


def _label(path: Path) -> str:
    parts = re.split(r"[-_/]+", path.with_suffix("").as_posix())
    return " ".join(
        part if part.isupper() or any(ch.isdigit() for ch in part) else part.capitalize()
        for part in parts
        if part
    )


def texture_library(root: str | Path = DEFAULT_TEXTURES_DIR) -> list[dict[str, str]]:
    base = Path(root)
    if not base.is_dir():
        return []
    textures = []
    for path in sorted(base.rglob("*")):
        if path.is_file() and path.suffix.lower() in TEXTURE_EXTS:
            rel = path.relative_to(base)
            textures.append({"path": rel.as_posix(), "label": _label(rel)})
    return textures


def resolve_texture(
    value: object, root: str | Path = DEFAULT_TEXTURES_DIR
) -> Path | None:
    if value is None:
        return None
    raw = str(value).strip()
    if not raw:
        return None

    candidate = Path(raw).expanduser()
    if candidate.is_absolute():
        if candidate.is_file():
            return candidate
        raise FileNotFoundError(f"texture not found: {candidate}")

    base = Path(root).expanduser()
    library_path = base / candidate
    if library_path.is_file():
        return library_path
    if candidate.is_file():
        return candidate

    available = ", ".join(item["path"] for item in texture_library(base)) or "(none)"
    raise FileNotFoundError(
        f"unknown texture {raw!r}; available: {available}"
    )
