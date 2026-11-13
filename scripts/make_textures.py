"""Generate the starter texture library under textures/."""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

ROOT = Path(__file__).resolve().parent.parent
TEXTURE_NAMES = (
    "grain-35mm.png",
    "dust-scratches.png",
    "halftone.png",
    "paper.png",
    "concrete.png",
)


def _grain(size: int) -> Image.Image:
    rng = np.random.default_rng(35)
    y, x = np.mgrid[0:size, 0:size]
    noise = rng.normal(0.0, 17.0, (size, size))
    banding = 3.0 * np.sin(y * 0.035) + 2.0 * np.sin(x * 0.011)
    arr = np.clip(190.0 + noise + banding, 0, 255).astype(np.uint8)
    return Image.fromarray(arr, mode="L").filter(ImageFilter.GaussianBlur(0.35))


def _dust_scratches(size: int) -> Image.Image:
    rng = np.random.default_rng(7)
    base = np.clip(250 + rng.normal(0, 2, (size, size)), 0, 255).astype(np.uint8)
    img = Image.fromarray(base, mode="L")
    draw = ImageDraw.Draw(img)

    for _ in range(70):
        x, y = rng.integers(0, size, 2)
        radius = int(rng.integers(1, 4))
        shade = int(rng.integers(60, 170))
        draw.ellipse((x - radius, y - radius, x + radius, y + radius), fill=shade)

    for _ in range(18):
        x = int(rng.integers(0, size))
        y0 = int(rng.integers(0, size // 2))
        y1 = y0 + int(rng.integers(size // 8, size // 2))
        shade = int(rng.integers(110, 200))
        draw.line((x, y0, x + int(rng.integers(-3, 4)), y1), fill=shade, width=1)

    for _ in range(24):
        y = int(rng.integers(0, size))
        x0 = int(rng.integers(0, size // 2))
        x1 = x0 + int(rng.integers(size // 10, size // 2))
        shade = int(rng.integers(130, 210))
        draw.line((x0, y, x1, y + int(rng.integers(-2, 3))), fill=shade, width=1)

    return img.filter(ImageFilter.GaussianBlur(0.25))


def _halftone(size: int) -> Image.Image:
    img = Image.new("L", (size, size), 255)
    draw = ImageDraw.Draw(img)
    step = max(8, size // 64)
    radius = step * 0.28
    for y in range(step // 2, size, step):
        for x in range(step // 2, size, step):
            wave = 0.55 + 0.45 * np.sin((x + y) / (size / 6))
            r = max(0.8, radius * float(wave))
            draw.ellipse((x - r, y - r, x + r, y + r), fill=95)
    return img


def _resized_noise(
    rng: np.random.Generator, shape: tuple[int, int], size: int
) -> np.ndarray:
    small = rng.normal(0.0, 1.0, shape)
    img = Image.fromarray(
        ((small - small.min()) / (np.ptp(small) or 1) * 255).astype(np.uint8)
    )
    return np.asarray(img.resize((size, size), Image.Resampling.BICUBIC), dtype=np.float32)


def _paper(size: int) -> Image.Image:
    rng = np.random.default_rng(19)
    coarse = _resized_noise(rng, (48, 48), size)
    fine = _resized_noise(rng, (256, 256), size)
    arr = np.clip(228 + (coarse - 128) * 0.20 + (fine - 128) * 0.06, 0, 255)
    img = Image.fromarray(arr.astype(np.uint8), mode="L").filter(
        ImageFilter.GaussianBlur(0.6)
    )
    draw = ImageDraw.Draw(img)
    for _ in range(180):
        x, y = rng.integers(0, size, 2)
        length = int(rng.integers(3, 16))
        angle = rng.integers(-1, 2)
        draw.line((x, y, x + length, y + int(angle)), fill=210, width=1)
    return img


def _concrete(size: int) -> Image.Image:
    rng = np.random.default_rng(23)
    coarse = _resized_noise(rng, (32, 32), size)
    mid = _resized_noise(rng, (128, 128), size)
    arr = np.clip(196 + (coarse - 128) * 0.24 + (mid - 128) * 0.10, 0, 255)
    img = Image.fromarray(arr.astype(np.uint8), mode="L").filter(
        ImageFilter.GaussianBlur(0.8)
    )
    draw = ImageDraw.Draw(img)
    for _ in range(420):
        x, y = rng.integers(0, size, 2)
        r = int(rng.integers(1, 3))
        shade = int(rng.integers(120, 190))
        draw.ellipse((x - r, y - r, x + r, y + r), fill=shade)
    return img


GENERATORS = {
    "grain-35mm.png": _grain,
    "dust-scratches.png": _dust_scratches,
    "halftone.png": _halftone,
    "paper.png": _paper,
    "concrete.png": _concrete,
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default=str(ROOT / "textures"))
    parser.add_argument("--size", type=int, default=768)
    args = parser.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    for name in TEXTURE_NAMES:
        path = out / name
        GENERATORS[name](args.size).save(path, optimize=True)
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
