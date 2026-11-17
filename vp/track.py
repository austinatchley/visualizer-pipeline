"""Track discovery: locate audio, title, images, and resolved options."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from .config import explicit_style, resolve_hook, resolve_overrides, resolve_style
from .manifest import AUDIO_EXTS, image_dir, read_manifest, scan_images


@dataclass
class Track:
    root: Path
    audio: Path
    title: str
    images: list[Path]
    artist: str | None = None
    handle: str | None = None
    style: dict = field(default_factory=dict)
    hook: dict = field(default_factory=dict)
    overrides: dict = field(default_factory=dict)
    # Explicitly set style values (track.json + flags). `None` means unknown, e.g.
    # the studio replaced `style` with a full model; see vibe.finalize_auto_style.
    style_explicit: dict | None = None

    @property
    def id(self) -> str:
        """Stable track identity: the folder name (issue #42).

        Used for the output directory, output filenames, studio routes, and jobs.
        Unlike the title it never changes on rename and is unique per tracks root.
        """
        return self.root.name

    @property
    def cache_dir(self) -> Path:
        return self.root / ".vp"

    @property
    def beatmap_path(self) -> Path:
        return self.cache_dir / "beatmap.json"


def slugify(text: str | None, default: str = "untitled") -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return slug or default


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
            f'multiple audio files in {root}; set "audio" in track.json: '
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

    imgs = scan_images(root)
    if not imgs:
        raise FileNotFoundError(f"no images found in {image_dir(root)}")
    return imgs


def load_track(root: Path, overrides: dict | None = None) -> Track:
    root = root.resolve()
    if not root.is_dir():
        raise NotADirectoryError(f"not a directory: {root}")

    manifest = read_manifest(root)

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

    flags = overrides or {}
    style = resolve_style(manifest.get("style"), preset=flags.get("preset"), flags=flags)
    hook = resolve_hook(manifest.get("hook"), manifest.get("overrides"), flags=flags)
    resolved_overrides = resolve_overrides(manifest.get("overrides"), flags)

    return Track(
        root=root,
        audio=audio,
        title=title,
        images=images,
        artist=manifest.get("artist"),
        handle=manifest.get("handle"),
        style=style,
        hook=hook,
        overrides=resolved_overrides,
        style_explicit=explicit_style(manifest.get("style"), flags),
    )
