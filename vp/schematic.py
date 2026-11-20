"""Schematic background: seeded circuit-trace linework with pulsing nodes (#83).

Two parts, split by cost:

- **Linework** (traces, chips, pads) never moves, so it is drawn once into a
  full-frame transparent PNG and composited like the texture.
- **Nodes** pulse with the music. They are redrawn per frame, but only as small
  glow sprites on a reduced-resolution clip (`NODE_SCALE`) that ffmpeg scales
  back up: glows are soft, so the lost resolution doesn't show.

Everything is a pure function of (size, seed, density) plus per-frame audio
state, so renders stay deterministic. The grid is defined in cells (GRID_CELLS
across the short side), not pixels, so a quarter-res preview draws the same
board as the full render.
"""

from __future__ import annotations

import zlib
from dataclasses import dataclass

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

GRID_CELLS = 40  # grid cells across the frame's short side
NODE_SCALE = 0.5  # node clip resolution relative to the output
GLOW_LEVELS = 16  # brightness quantization for cached glow sprites
NODE_OPACITY = 0.9  # glow strength at full brightness
BAND_STRENGTH = 0.5  # nodes follow their band meter at up to this brightness
# Traces thin out toward the middle (where the spectrum sits): a cell at
# normalized ellipse distance d from the center is blocked with probability
# clip((CENTER_CLEAR - d) / CENTER_SOFT, 0, CENTER_MAX).
CENTER_CLEAR = 0.6
CENTER_SOFT = 0.35
CENTER_MAX = 0.85
DIRS = ((1, 0), (0, 1), (-1, 0), (0, -1))


@dataclass(frozen=True)
class Node:
    x: float  # output pixels (cell center)
    y: float
    band: int  # which band meter drives it (0..3)
    downbeat: bool  # also flashes on downbeats


@dataclass(frozen=True)
class Schematic:
    size: tuple[int, int]
    cell: float  # grid cell size in output pixels
    traces: tuple[tuple[tuple[float, float], ...], ...]  # polylines, output pixels
    chips: tuple[tuple[float, float, float, float], ...]  # x0, y0, x1, y1
    nodes: tuple[Node, ...]


def seed_for(track_id: str) -> int:
    """Stable per-track seed (not Python's salted hash): a track keeps its board."""
    return zlib.crc32(track_id.encode("utf-8"))


def generate(size: tuple[int, int], seed: int = 0, density: float = 0.5) -> Schematic:
    """Orthogonal traces as self-avoiding walks on a grid, sprouting from a few
    chips and from free cells. Same (size, seed, density) -> same linework."""
    width, height = size
    rng = np.random.default_rng(seed)
    cell = min(width, height) / GRID_CELLS
    cols = int(round(width / cell, 6))
    rows = int(round(height / cell, 6))
    ox, oy = (width - cols * cell) / 2, (height - rows * cell) / 2
    cy, cx = np.mgrid[0:rows, 0:cols]
    d = np.hypot((cx + 0.5) / cols - 0.5, (cy + 0.5) / rows - 0.5) / 0.5
    p_block = np.clip((CENTER_CLEAR - d) / CENTER_SOFT, 0.0, CENTER_MAX)
    used = rng.random((rows, cols)) < p_block

    def center(c: int, r: int) -> tuple[float, float]:
        return ox + (c + 0.5) * cell, oy + (r + 0.5) * cell

    chips: list[tuple[float, float, float, float]] = []
    starts: list[tuple[int, int, int]] = []  # (col, row, direction)
    for _ in range(max(1, round(density * cols * rows / 260))):
        w, h = int(rng.integers(3, 7)), int(rng.integers(2, 4))
        c0, r0 = (
            int(rng.integers(1, max(2, cols - w - 1))),
            int(rng.integers(1, max(2, rows - h - 1))),
        )
        if used[max(0, r0 - 1) : r0 + h + 1, max(0, c0 - 1) : c0 + w + 1].any():
            continue
        used[r0 : r0 + h, c0 : c0 + w] = True
        x0, y0 = center(c0, r0)
        x1, y1 = center(c0 + w - 1, r0 + h - 1)
        chips.append((x0 - cell * 0.3, y0 - cell * 0.3, x1 + cell * 0.3, y1 + cell * 0.3))
        for c in range(c0, c0 + w):  # pins on the top and bottom edges
            starts += [(c, r0 - 1, 3), (c, r0 + h, 1)]
        for r in range(r0, r0 + h):
            starts += [(c0 - 1, r, 2), (c0 + w, r, 0)]
    rng.shuffle(starts)
    starts = starts[: len(starts) // 2]

    n_free = round(density * cols * rows / 22)
    for _ in range(n_free):
        starts.append((int(rng.integers(cols)), int(rng.integers(rows)), int(rng.integers(4))))

    traces: list[tuple[tuple[float, float], ...]] = []
    nodes: list[Node] = []
    for c, r, d in starts:
        if not (0 <= c < cols and 0 <= r < rows) or used[r, c]:
            continue
        path = [(c, r)]
        used[r, c] = True
        target = int(rng.integers(4, 26))
        while len(path) < target:
            options = [d]
            if rng.random() < 0.2:
                options = [(d + 1) % 4, (d + 3) % 4]
                rng.shuffle(options)
                options.append(d)
            for nd in options:
                nc, nr = c + DIRS[nd][0], r + DIRS[nd][1]
                if 0 <= nc < cols and 0 <= nr < rows and not used[nr, nc]:
                    c, r, d = nc, nr, nd
                    used[r, c] = True
                    path.append((c, r))
                    break
            else:
                break
        if len(path) < 3:
            continue
        traces.append(_chamfer([center(pc, pr) for pc, pr in path], cell * 0.4))
        for pc, pr in (path[0], path[-1]):
            x, y = center(pc, pr)
            nodes.append(Node(x, y, int(rng.integers(4)), bool(rng.random() < 0.3)))
    return Schematic(size, cell, tuple(traces), tuple(chips), tuple(nodes))


def _chamfer(points: list[tuple[float, float]], cut: float) -> tuple[tuple[float, float], ...]:
    """Drop collinear points and cut each 90-degree corner at 45 degrees."""
    corners = [points[0]]
    for a, b, c in zip(points, points[1:], points[2:], strict=False):
        if (b[0] - a[0], b[1] - a[1]) != (c[0] - b[0], c[1] - b[1]):
            corners.append(b)
    corners.append(points[-1])
    out = [corners[0]]
    for a, b, c in zip(corners, corners[1:], corners[2:], strict=False):
        ux, uy = np.sign(b[0] - a[0]), np.sign(b[1] - a[1])
        vx, vy = np.sign(c[0] - b[0]), np.sign(c[1] - b[1])
        out += [(b[0] - ux * cut, b[1] - uy * cut), (b[0] + vx * cut, b[1] + vy * cut)]
    out.append(corners[-1])
    return tuple(out)


def draw_linework(s: Schematic, opacity: float, rgb=(225, 220, 215)) -> Image.Image:
    """The static layer: traces, chip outlines, and dim pads (full frame RGBA)."""
    ss = 2  # supersample for clean diagonals
    img = Image.new("RGBA", (s.size[0] * ss, s.size[1] * ss), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    a = round(255 * opacity)
    lw = max(1, round(s.cell * 0.07 * ss))
    for trace in s.traces:
        draw.line([(x * ss, y * ss) for x, y in trace], fill=(*rgb, a), width=lw, joint="curve")
    for x0, y0, x1, y1 in s.chips:
        draw.rectangle((x0 * ss, y0 * ss, x1 * ss, y1 * ss), outline=(*rgb, a), width=lw)
    pad = s.cell * 0.22 * ss
    for n in s.nodes:
        x, y = n.x * ss, n.y * ss
        draw.rectangle((x - pad, y - pad, x + pad, y + pad), outline=(*rgb, a), width=lw)
    return img.resize(s.size, Image.Resampling.BOX)  # BOX never overshoots the alpha


class NodePainter:
    """Per-frame node glows at NODE_SCALE resolution."""

    def __init__(self, s: Schematic, accent: tuple[int, int, int], opacity: float = NODE_OPACITY):
        self.size = (max(1, round(s.size[0] * NODE_SCALE)), max(1, round(s.size[1] * NODE_SCALE)))
        self.nodes = s.nodes
        self.band = np.array([n.band for n in s.nodes], int)
        self.downbeat = np.array([n.downbeat for n in s.nodes], bool)
        self.xy = [(n.x * NODE_SCALE, n.y * NODE_SCALE) for n in s.nodes]
        r = max(2, round(s.cell * NODE_SCALE * 0.9))
        self.sprites = [
            [self._sprite(r, color, opacity * (k + 1) / GLOW_LEVELS) for k in range(GLOW_LEVELS)]
            for color in ((245, 240, 236), accent)
        ]
        self.r = r

    @staticmethod
    def _sprite(r: int, rgb, strength: float) -> Image.Image:
        size = 4 * r
        glow = Image.new("L", (size, size), 0)
        d = ImageDraw.Draw(glow)
        core = r * 0.22
        d.rectangle(
            (2 * r - core * 2, 2 * r - core * 2, 2 * r + core * 2, 2 * r + core * 2), fill=200
        )
        glow = glow.filter(ImageFilter.GaussianBlur(r * 0.6))
        d = ImageDraw.Draw(glow)
        d.rectangle((2 * r - core, 2 * r - core, 2 * r + core, 2 * r + core), fill=255)
        alpha = glow.point(lambda v: round(v * strength))
        sprite = Image.new("RGBA", (size, size), (*rgb, 0))
        sprite.putalpha(alpha)
        return sprite

    def levels(self, bands: np.ndarray, flash: float, downbeat: bool) -> np.ndarray:
        """Node brightness in [0, 1]: its band's meter level at half strength, so
        nodes keep moving all the time; downbeat nodes flash to full on downbeats."""
        level = BAND_STRENGTH * np.asarray(bands, float)[self.band] ** 1.5
        if downbeat:
            level = np.maximum(level, self.downbeat * float(flash))
        return level

    def frame(self, bands: np.ndarray, flash: float, beat_pos: int) -> Image.Image:
        m = 2 * self.r  # margin so edge sprites never get a negative offset
        img = Image.new("RGBA", (self.size[0] + 2 * m, self.size[1] + 2 * m), (0, 0, 0, 0))
        on_downbeat = beat_pos == 0
        for (x, y), level, hot in zip(
            self.xy, self.levels(bands, flash, on_downbeat), self.downbeat, strict=True
        ):
            k = int(level * GLOW_LEVELS) - 1
            if k < 0:
                continue
            sprite = self.sprites[1 if (hot and on_downbeat) else 0][min(k, GLOW_LEVELS - 1)]
            img.alpha_composite(sprite, dest=(round(x), round(y)))
        return img.crop((m, m, m + self.size[0], m + self.size[1]))
