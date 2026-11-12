"""Generate a synthetic demo track (audio + 3 images) for smoke-testing."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import soundfile as sf
from PIL import Image, ImageDraw, ImageFilter

SR = 44100
BPM = 120
DURATION = 24.0


def make_audio(path: Path) -> None:
    n = int(SR * DURATION)
    t = np.arange(n) / SR
    out = np.zeros(n)

    beat = 60.0 / BPM
    kick_positions = np.arange(0, DURATION, beat)
    for b in kick_positions:
        start = int(b * SR)
        length = int(0.18 * SR)
        if start + length > n:
            continue
        tt = np.arange(length) / SR
        env = np.exp(-tt * 28)
        freq = 120 * np.exp(-tt * 30) + 45
        out[start:start + length] += 0.9 * env * np.sin(2 * np.pi * freq * tt)

    snare_positions = np.arange(beat, DURATION, beat * 2)
    for b in snare_positions:
        start = int(b * SR)
        length = int(0.12 * SR)
        if start + length > n:
            continue
        tt = np.arange(length) / SR
        noise = np.random.default_rng(int(b * 1000)).normal(0, 1, length)
        env = np.exp(-tt * 32)
        out[start:start + length] += 0.35 * env * noise

    hat_positions = np.arange(0, DURATION, beat / 2)
    for i, b in enumerate(hat_positions):
        start = int(b * SR)
        length = int(0.04 * SR)
        if start + length > n:
            continue
        tt = np.arange(length) / SR
        noise = np.random.default_rng(i).normal(0, 1, length)
        env = np.exp(-tt * 90)
        out[start:start + length] += 0.12 * env * noise

    notes = [55.0, 65.41, 73.42, 82.41]
    for i, b in enumerate(np.arange(0, DURATION, beat * 2)):
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
    sf.write(path, out.astype(np.float32), SR)


def make_image(path: Path, seed: int) -> None:
    rng = np.random.default_rng(seed)
    w, h = 1400, 1050
    base = rng.integers(40, 220, size=3)
    grad = np.zeros((h, w, 3), dtype=np.uint8)
    for y in range(h):
        mix = y / h
        for c in range(3):
            grad[y, :, c] = np.linspace(base[c], 255 - base[c], w) * (0.4 + 0.6 * mix)
    img = Image.fromarray(grad, "RGB").filter(ImageFilter.GaussianBlur(28))
    draw = ImageDraw.Draw(img, "RGBA")
    for _ in range(9):
        x, y = rng.integers(0, w), rng.integers(0, h)
        r = int(rng.integers(80, 380))
        color = tuple(int(v) for v in rng.integers(0, 255, 3)) + (int(rng.integers(30, 90)),)
        draw.ellipse([x - r, y - r, x + r, y + r], fill=color)
    img = img.filter(ImageFilter.GaussianBlur(3))
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path)


def main() -> None:
    root = Path(__file__).resolve().parent.parent / "examples" / "sample"
    root.mkdir(parents=True, exist_ok=True)
    make_audio(root / "audio.wav")
    (root / "title.txt").write_text("Late Night")
    (root / "track.json").write_text('{\n  "artist": "AUSTIN"\n}\n')
    for i in range(1, 4):
        make_image(root / "images" / f"{i:02d}.png", seed=i)
    print(f"wrote demo track to {root}")


if __name__ == "__main__":
    main()