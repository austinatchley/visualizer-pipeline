"""Generate prototype fixture tracks (audio + images) for pipeline testing.

Covers: landscape/portrait/square images, mixed image sets, different BPMs and
durations, title.txt vs track.json, and per-track style overrides.

Run:  python scripts/make_fixtures.py
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import soundfile as sf
from PIL import Image, ImageDraw, ImageFilter

SR = 44100
# Fixtures only need enough dynamic range to exercise cut planning; 16-bit mono
# keeps the generated audio files small.
SUB_TYPE = "PCM_16"

# name, bpm, duration (s), title, artist, style override, image specs
# image spec = (filename, width, height, seed, palette)
FIXTURES = [
    {
        "name": "a_landscape",
        "bpm": 120.0,
        "duration": 90.0,
        "title": "Midnight Drive",
        "title_source": "title.txt",
        "images": [
            ("01.jpg", 1600, 1200, 11),
            ("02.jpg", 1600, 1200, 12),
            ("03.jpg", 1600, 1200, 13),
        ],
    },
    {
        "name": "b_portrait",
        "bpm": 84.0,
        "duration": 36.0,
        "title": "Slow Burn",
        "title_source": "title.txt",
        "images": [
            ("01.png", 1080, 1350, 21),
            ("02.png", 1080, 1350, 22),
            ("03.png", 1080, 1350, 23),
        ],
    },
    {
        "name": "c_mixed",
        "bpm": 140.0,
        "duration": 18.0,
        "title": "Golden Hour",
        "title_source": "track.json",
        "artist": "AUSTIN",
        "style": {"beats_per_cut": 4, "zoom": 0.12, "crossfade": 0.4},
        "images": [
            ("01.png", 1080, 1080, 31),
            ("02.jpg", 1600, 900, 32),
            ("03.png", 900, 1600, 33),
        ],
    },
    {
        "name": "d_short",
        "bpm": 100.0,
        "duration": 8.0,
        "title": "Interlude",
        "title_source": "title.txt",
        "images": [
            ("01.jpg", 1200, 1200, 41),
            ("02.jpg", 1200, 1200, 42),
            ("03.jpg", 1200, 1200, 43),
        ],
    },
]


def make_audio(path: Path, bpm: float, duration: float, seed: int) -> None:
    rng = np.random.default_rng(seed)
    n = int(SR * duration)
    out = np.zeros(n)
    beat = 60.0 / bpm

    for b in np.arange(0, duration, beat):
        start = int(b * SR)
        length = int(0.18 * SR)
        if start + length > n:
            continue
        tt = np.arange(length) / SR
        env = np.exp(-tt * 28)
        freq = 120 * np.exp(-tt * 30) + 45
        out[start:start + length] += 0.9 * env * np.sin(2 * np.pi * freq * tt)

    for b in np.arange(beat, duration, beat * 2):
        start = int(b * SR)
        length = int(0.12 * SR)
        if start + length > n:
            continue
        tt = np.arange(length) / SR
        noise = rng.normal(0, 1, length)
        out[start:start + length] += 0.35 * np.exp(-tt * 32) * noise

    for i, b in enumerate(np.arange(0, duration, beat / 2)):
        start = int(b * SR)
        length = int(0.04 * SR)
        if start + length > n:
            continue
        tt = np.arange(length) / SR
        noise = rng.normal(0, 1, length)
        out[start:start + length] += 0.12 * np.exp(-tt * 90) * noise

    notes = [55.0, 65.41, 73.42, 82.41]
    for i, b in enumerate(np.arange(0, duration, beat * 2)):
        start = int(b * SR)
        length = int(beat * 2 * SR)
        if start + length > n:
            continue
        tt = np.arange(length) / SR
        f = notes[i % len(notes)]
        env = np.minimum(1, tt / 0.05) * np.exp(-tt * 1.5)
        out[start:start + length] += 0.22 * env * np.sin(2 * np.pi * f * tt)

    peak = np.max(np.abs(out)) or 1.0
    out = (out / peak) * 0.89
    path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(path, out.astype(np.float32), SR, subtype=SUB_TYPE)


def make_image(path: Path, width: int, height: int, seed: int) -> None:
    rng = np.random.default_rng(seed)
    c0 = rng.integers(25, 180, size=3)
    c1 = rng.integers(120, 255, size=3)

    x = np.linspace(0, 1, width)[None, :, None]
    y = np.linspace(0, 1, height)[:, None, None]
    diag = (x * 0.6 + y * 0.4)
    grad = (c0 * (1 - diag) + c1 * diag).astype(np.uint8)
    grad = np.repeat(grad, 3, axis=2) if grad.shape[2] == 1 else grad

    img = Image.fromarray(grad, "RGB").filter(ImageFilter.GaussianBlur(width / 50))
    draw = ImageDraw.Draw(img, "RGBA")
    for _ in range(12):
        cx, cy = int(rng.integers(0, width)), int(rng.integers(0, height))
        r = int(rng.integers(min(width, height) // 12, min(width, height) // 3))
        color = tuple(int(v) for v in rng.integers(0, 255, 3)) + (int(rng.integers(25, 85)),)
        if rng.random() < 0.5:
            draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=color)
        else:
            draw.rectangle([cx - r, cy - r, cx + r, cy + r], fill=color)
    img = img.filter(ImageFilter.GaussianBlur(2))
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path)


def make_track(root: Path, spec: dict, index: int) -> None:
    folder = root / spec["name"]
    folder.mkdir(parents=True, exist_ok=True)

    make_audio(folder / "audio.wav", spec["bpm"], spec["duration"], seed=index + 1)

    images_dir = folder / "images"
    for filename, w, h, seed in spec["images"]:
        make_image(images_dir / filename, w, h, seed)

    if spec["title_source"] == "title.txt":
        (folder / "title.txt").write_text(spec["title"])
    else:
        manifest = {
            "title": spec["title"],
            "artist": spec.get("artist"),
            "style": spec.get("style", {}),
        }
        (folder / "track.json").write_text(json.dumps(manifest, indent=2) + "\n")


def main() -> None:
    root = Path(__file__).resolve().parent.parent / "fixtures"
    for i, spec in enumerate(FIXTURES):
        make_track(root, spec, i)
    print(f"wrote {len(FIXTURES)} fixture tracks to {root}")


if __name__ == "__main__":
    main()