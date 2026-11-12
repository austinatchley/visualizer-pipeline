"""Track discovery: locate audio, title, and images for a beat folder."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

AUDIO_EXTS = {".wav", ".mp3", ".flac", ".m4a", ".aiff", ".aif", ".ogg", ".opus"}
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"}

DEFAULT_STYLE = {
    "beats_per_cut": 8,
    "crossfade": 0.35,
    "zoom": 0.10,
    "fps": 30,
    "title_font": None,
    "waveform": False,
    "fade_in": 0.4,
    "fade_out": 0.8,
}


@dataclass
class Track:
    root: Path
    audio: Path
    title: str
    images: list[Path]
    artist: str | None = None
    style: dict = field(default_factory=dict)

    @property
    def slug(self) -> str:
        raw = self.title or self.root.name
        slug = re.sub(r"[^a-z0-9]+", "-", raw.lower()).strip("-")
        return slug or "track"

    @property
    def cache_dir(self) -> Path:
        return self.root / ".vp"

    @property
    def beatmap_path(self) -> Path:
        return self.cache_dir / "beatmap.json"


def _clean_title(name: str) -> str:
    name = re.sub(r"^\s*\d+[\s._-]+", "", name)
    name = name.replace("_", " ").replace("-", " ")
    return re.sub(r"\s+", " ", name).strip()


def _find_audio(root: Path, manifest: dict) -> Path:
    if manifest.get("audio"):
        p = root / manifest["audio"]
        if not p.is_file():
            raise FileNotFoundError(f"manifest audio not found: {p}")
        return p
    found = sorted(p for p in root.iterdir() if p.is_file() and p.suffix.lower() in AUDIO_EXTS)
    if not found:
        raise FileNotFoundError(f"no audio file found in {root}")
    if len(found) > 1:
        raise ValueError(
            f"multiple audio files in {root}; set \"audio\" in track.json: "
            + ", ".join(p.name for p in found)
        )
    return found[0]


def _find_images(root: Path, manifest: dict) -> list[Path]:
    if manifest.get("images"):
        imgs = [root / p for p in manifest["images"]]
        missing = [str(p) for p in imgs if not p.is_file()]
        if missing:
            raise FileNotFoundError("manifest images not found: " + ", ".join(missing))
        return imgs

    img_dir = root / "images"
    search_dir = img_dir if img_dir.is_dir() else root
    imgs = sorted(
        p for p in search_dir.iterdir() if p.is_file() and p.suffix.lower() in IMAGE_EXTS
    )
    if not imgs:
        raise FileNotFoundError(f"no images found in {search_dir}")
    return imgs


def load_track(root: Path, overrides: dict | None = None) -> Track:
    root = root.resolve()
    if not root.is_dir():
        raise NotADirectoryError(f"not a directory: {root}")

    manifest: dict = {}
    manifest_path = root / "track.json"
    if manifest_path.is_file():
        manifest = json.loads(manifest_path.read_text())

    audio = _find_audio(root, manifest)
    images = _find_images(root, manifest)

    title = manifest.get("title")
    if not title:
        title_file = root / "title.txt"
        if title_file.is_file():
            title = title_file.read_text().strip()
        else:
            title = _clean_title(root.name)
    if not title:
        raise ValueError(f"could not determine a title for {root}")

    style = dict(DEFAULT_STYLE)
    style.update(manifest.get("style", {}))
    if overrides:
        style.update({k: v for k, v in overrides.items() if v is not None})

    return Track(
        root=root,
        audio=audio,
        title=title,
        images=images,
        artist=manifest.get("artist"),
        style=style,
    )