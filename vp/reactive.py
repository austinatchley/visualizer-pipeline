"""Music-reactive overlay layers, drawn per frame (issue #81, epic #80).

The still HUD is one PNG; reactive layers need a fresh image every video frame.
`render_reactive` draws them with Pillow, streams raw RGBA into an ffmpeg
encoder (lossless FFV1 with alpha), and returns the clip plus where to overlay
it. `build_graph` then treats the clip like any other overlay input, so the
render graph stays pure and snapshot-testable.

Only the layers' bounding box is encoded, not the full frame, which keeps the
intermediate small and the encode fast.
"""

from __future__ import annotations

import math
import multiprocessing
import os
import subprocess
from collections.abc import Callable, Iterator
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

from .audio import decode_mono
from .fonts import load_font, style_faces
from .spectrum import SAMPLE_RATE, SpectrumFrames, spectrum_frames

SUPERSAMPLE = 2  # layer-local supersampling for anti-aliased lines
AXIS_TICKS = (50, 100, 200, 500, 1000, 2000, 5000, 10000)
CHUNK = 24  # frames drawn per worker task; also the cancel/progress cadence
MAX_WORKERS = 8
PARALLEL_MIN_FRAMES = 240  # below this a process pool costs more than it saves


class ReactiveCancelled(RuntimeError):
    """Raised when the caller's cancel hook fires during the overlay pass."""


@dataclass(frozen=True)
class ReactiveClip:
    """An encoded overlay clip and its top-left position in the output frame."""

    path: Path
    x: int
    y: int


def wants_reactive(style: dict) -> bool:
    return bool(style["spectrum"])


def parse_color(value: str) -> tuple[int, int, int]:
    """`#rrggbb` (or `rrggbb`) to an RGB tuple; raises ValueError otherwise."""
    text = str(value).strip().removeprefix("#")
    if len(text) != 6:
        raise ValueError(f"color must be #rrggbb, got {value!r}")
    try:
        return int(text[0:2], 16), int(text[2:4], 16), int(text[4:6], 16)
    except ValueError:
        raise ValueError(f"color must be #rrggbb, got {value!r}") from None


@dataclass(frozen=True)
class SpectrumBox:
    """Layer geometry in output pixels: plot area plus a label row below it."""

    x: int
    y: int
    width: int
    plot_height: int
    label_height: int

    @property
    def height(self) -> int:
        return self.plot_height + self.label_height


def spectrum_box(size: tuple[int, int], style: dict) -> SpectrumBox:
    """Centered horizontally, 88% wide; `spectrum_y` is the plot's vertical center."""
    width, height = size
    box_w = int(width * 0.88)
    plot_h = max(8, int(height * float(style["spectrum_height"])))
    label_h = max(6, int(min(width, height) * 0.04))
    center = float(style["spectrum_y"]) * height
    y = int(round(center - plot_h / 2))
    y = max(0, min(height - plot_h - label_h, y))
    return SpectrumBox((width - box_w) // 2, y, box_w, plot_h, label_h)


@dataclass(frozen=True)
class ClipRect:
    """The encoded clip in output pixels: the layer box plus the backplate margin."""

    x: int
    y: int
    width: int
    height: int


PLATE_ALPHA = 0.75  # plate opacity at backplate = 1.0
PLATE_RGB = (10, 9, 12)


def clip_rect(size: tuple[int, int], box: SpectrumBox, style: dict) -> ClipRect:
    """Grow the box by the backplate's feather margin, clamped to the frame."""
    width, height = size
    pad = int(min(width, height) * 0.05) if float(style["backplate"]) > 0 else 0
    x0, y0 = max(0, box.x - pad), max(0, box.y - pad)
    x1 = min(width, box.x + box.width + pad)
    y1 = min(height, box.y + box.height + pad)
    return ClipRect(x0, y0, x1 - x0, y1 - y0)


def _x_of(freqs: np.ndarray, width: float) -> np.ndarray:
    lo, hi = math.log(freqs[0]), math.log(freqs[-1])
    return (np.log(freqs) - lo) / (hi - lo) * (width - 1)


class SpectrumPainter:
    """Draws one spectrum frame at `SUPERSAMPLE`x, then downsamples to the box."""

    def __init__(
        self, box: SpectrumBox, freqs: np.ndarray, style: dict, clip: ClipRect | None = None
    ):
        self.box = box
        self.clip = clip or ClipRect(box.x, box.y, box.width, box.height)
        self.ox, self.oy = box.x - self.clip.x, box.y - self.clip.y
        self.ss = SUPERSAMPLE
        self.w = box.width * self.ss
        self.h = box.plot_height * self.ss
        self.xs = _x_of(freqs, self.w)
        accent = parse_color(style["accent_color"])
        self.main_rgba = (245, 240, 236, 255)
        self.lag_rgba = (*accent, 230)
        self.hold_rgba = (150, 140, 140, 150)
        self.fill_rgba = (*accent, 46)
        self.line_w = max(2, round(self.h / 140))
        self.base = self._base(freqs, style)

    def _plate(self, strength: float) -> Image.Image:
        """A soft dark plate under the layer so it reads on bright footage (#88)."""
        clip, box = self.clip, self.box
        plate = Image.new("RGBA", (clip.width, clip.height), (*PLATE_RGB, 0))
        if strength <= 0:
            return plate
        feather = max(1, min(clip.width - box.width, clip.height - box.height) // 2)
        mask = Image.new("L", (clip.width, clip.height), 0)
        ImageDraw.Draw(mask).rounded_rectangle(
            (self.ox, self.oy, self.ox + box.width - 1, self.oy + box.height - 1),
            radius=feather,
            fill=round(255 * min(1.0, strength) * PLATE_ALPHA),
        )
        plate.putalpha(mask.filter(ImageFilter.GaussianBlur(feather * 0.6)))
        return plate

    def _base(self, freqs: np.ndarray, style: dict) -> Image.Image:
        """Static parts: backplate, baseline and frequency labels (full clip, 1x)."""
        img = self._plate(float(style["backplate"]))
        layer = Image.new("RGBA", (self.box.width, self.box.height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(layer)
        _, meta_face = style_faces(style)
        font = load_font(meta_face, max(6, int(self.box.label_height * 0.78)))
        lo, hi = math.log(freqs[0]), math.log(freqs[-1])
        draw.line(
            [(0, self.box.plot_height - 1), (self.box.width - 1, self.box.plot_height - 1)],
            fill=(220, 210, 210, 120),
            width=1,
        )
        for hz in AXIS_TICKS:
            if not freqs[0] <= hz <= freqs[-1]:
                continue
            x = (math.log(hz) - lo) / (hi - lo) * (self.box.width - 1)
            label = f"{hz / 1000:g}k" if hz >= 1000 else str(hz)
            text_w = draw.textlength(label, font=font)
            tx = min(max(0, x - text_w / 2), self.box.width - text_w)
            draw.text((tx, self.box.plot_height + 2), label, font=font, fill=(236, 230, 226, 225))
        img.alpha_composite(layer, dest=(self.ox, self.oy))
        return img

    def _points(self, levels: np.ndarray) -> list[tuple[float, float]]:
        ys = (1.0 - levels) * (self.h - self.line_w) + self.line_w / 2
        return list(zip(self.xs.tolist(), ys.tolist(), strict=True))

    def frame(self, main: np.ndarray, lag: np.ndarray, hold: np.ndarray) -> Image.Image:
        plot = Image.new("RGBA", (self.w, self.h), (0, 0, 0, 0))
        draw = ImageDraw.Draw(plot)
        main_pts = self._points(main)
        draw.polygon([(0, self.h), *main_pts, (self.w - 1, self.h)], fill=self.fill_rgba)
        draw.line(self._points(hold), fill=self.hold_rgba, width=self.line_w, joint="curve")
        draw.line(self._points(lag), fill=self.lag_rgba, width=self.line_w, joint="curve")
        draw.line(main_pts, fill=self.main_rgba, width=self.line_w + 1, joint="curve")
        plot = plot.resize((self.box.width, self.box.plot_height), Image.Resampling.BOX)
        out = self.base.copy()
        out.alpha_composite(plot, dest=(self.ox, self.oy))
        return out


def encode_command(path: Path, size: tuple[int, int], fps: int) -> list[str]:
    width, height = size
    return [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-nostdin",
        "-y",
        "-f",
        "rawvideo",
        "-pix_fmt",
        "rgba",
        "-s",
        f"{width}x{height}",
        "-r",
        str(fps),
        "-i",
        "pipe:0",
        "-c:v",
        "ffv1",
        "-level",
        "3",
        "-slices",
        "16",
        "-pix_fmt",
        "bgra",
        str(path),
    ]


def _frames_for(duration: float, fps: int) -> int:
    return max(1, math.ceil(duration * fps))


def compute_spectrum(audio: Path, window: tuple[float, float], fps: int) -> SpectrumFrames:
    """Decode the window (with half an FFT of context each side) and analyze it."""
    start, end = float(window[0]), float(window[1])
    pad = 0.1
    lead = min(pad, start)
    samples = decode_mono(audio, start - lead, end + pad, sample_rate=SAMPLE_RATE)
    return spectrum_frames(samples, fps, _frames_for(end - start, fps), SAMPLE_RATE, lead)


# Worker state for the process pool: set once per worker by `_init_worker`.
_WORKER: tuple[SpectrumPainter, SpectrumFrames] | None = None


def _init_worker(painter: SpectrumPainter, data: SpectrumFrames) -> None:
    global _WORKER
    _WORKER = (painter, data)


def _draw_chunk(painter: SpectrumPainter, data: SpectrumFrames, lo: int, hi: int) -> bytes:
    return b"".join(
        painter.frame(data.main[k], data.lag[k], data.hold[k]).tobytes() for k in range(lo, hi)
    )


def _worker_chunk(bounds: tuple[int, int]) -> bytes:
    assert _WORKER is not None
    return _draw_chunk(*_WORKER, *bounds)


def _chunks(painter: SpectrumPainter, data: SpectrumFrames, workers: int) -> Iterator[bytes]:
    """Frame bytes in order, `CHUNK` frames at a time. Drawing is a pure function
    of the frame index, so the parallel path yields exactly the serial bytes."""
    n = len(data.main)
    bounds = [(lo, min(n, lo + CHUNK)) for lo in range(0, n, CHUNK)]
    if workers <= 1 or n < PARALLEL_MIN_FRAMES:
        for lo, hi in bounds:
            yield _draw_chunk(painter, data, lo, hi)
        return
    # Not "fork": the studio renders from worker threads, and forking a threaded
    # process can deadlock. forkserver/spawn start clean interpreters instead.
    methods = multiprocessing.get_all_start_methods()
    context = multiprocessing.get_context("forkserver" if "forkserver" in methods else "spawn")
    with ProcessPoolExecutor(
        workers, mp_context=context, initializer=_init_worker, initargs=(painter, data)
    ) as pool:
        try:
            yield from pool.map(_worker_chunk, bounds)
        except GeneratorExit:
            pool.shutdown(wait=False, cancel_futures=True)
            raise


def default_workers() -> int:
    return max(1, min(MAX_WORKERS, (os.cpu_count() or 2) - 1))


def render_reactive(
    audio: Path,
    window: tuple[float, float],
    size: tuple[int, int],
    style: dict,
    out: Path,
    progress: Callable[[float], None] | None = None,
    cancel: Callable[[], bool] | None = None,
    workers: int | None = None,
) -> ReactiveClip:
    """Draw and encode the reactive layers for `window` into `out` (.mkv)."""
    fps = int(style["fps"])
    data = compute_spectrum(audio, window, fps)
    box = spectrum_box(size, style)
    clip = clip_rect(size, box, style)
    painter = SpectrumPainter(box, data.freqs, style, clip)
    n = len(data.main)
    workers = default_workers() if workers is None else workers

    proc = subprocess.Popen(
        encode_command(out, (clip.width, clip.height), fps),
        stdin=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    assert proc.stdin is not None
    chunks = _chunks(painter, data, workers)
    try:
        done = 0
        for chunk in chunks:
            if cancel is not None and cancel():
                raise ReactiveCancelled("render cancelled")
            proc.stdin.write(chunk)
            done = min(n, done + CHUNK)
            if progress is not None:
                progress(100.0 * done / n)
        proc.stdin.close()
        err = proc.stderr.read().decode(errors="replace") if proc.stderr else ""
        if proc.wait() != 0:
            raise RuntimeError(f"overlay encode failed:\n{err.strip()[-2000:]}")
    except BaseException:
        chunks.close()
        proc.kill()
        proc.wait()
        raise
    return ReactiveClip(out, clip.x, clip.y)
