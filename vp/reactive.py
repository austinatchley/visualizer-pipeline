"""Music-reactive overlay layers, drawn per frame (issue #81, epic #80).

The still HUD is one PNG; reactive layers need a fresh image every video frame.
`render_reactive` draws them with Pillow, streams raw RGBA into one ffmpeg
encoder per layer (lossless FFV1 with alpha), and returns the clips plus where
to overlay them. `build_graph` then treats each clip like any other overlay
input, so the render graph stays pure and snapshot-testable.

Each layer encodes only its own box (the spectrum in the middle, the readout
strip at the top), not the full frame, which keeps the intermediates small and
the encode fast.
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
from typing import Protocol

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

from .audio import decode_mono
from .config import parse_color
from .fonts import READOUT_FONT, load_font, resolve_face
from .music import NOTE_NAMES
from .readouts import ReadoutFrames, ReadoutPainter, readout_frames, wants_readouts
from .schematic import NodePainter, generate
from .spectrum import (
    SAMPLE_RATE,
    NotePeaks,
    SpectrumFrames,
    band_levels,
    note_peaks,
    spectrum_frames,
)

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
    scale_to: tuple[int, int] | None = None  # clips drawn at reduced resolution


def wants_reactive(style: dict) -> bool:
    return bool(style["spectrum"]) or bool(style["schematic"]) or wants_readouts(style)


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
        self.accent = accent
        self.log_lo, self.log_hi = math.log(freqs[0]), math.log(freqs[-1])
        self.base = self._base(freqs, style)

    # The callout font is loaded lazily and not pickled (see ReadoutPainter).
    def _note_font(self):
        font = getattr(self, "_font_cache", None)
        if font is None:
            size = max(6, round(self.box.label_height * 0.72))
            font = self._font_cache = load_font(resolve_face(READOUT_FONT), size)
        return font

    def __getstate__(self) -> dict:
        state = dict(self.__dict__)
        state.pop("_font_cache", None)
        return state

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
        # Axis labels share the readouts' monospace face so the HUD reads as one
        # instrument (#82).
        font = load_font(resolve_face(READOUT_FONT), max(6, int(self.box.label_height * 0.7)))
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

    def frame(
        self,
        main: np.ndarray,
        lag: np.ndarray,
        hold: np.ndarray,
        notes: list[tuple[float, int, float]] | None = None,
    ) -> Image.Image:
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
        if notes:
            self._callouts(out, main, notes)
        return out

    def _callouts(
        self, img: Image.Image, main: np.ndarray, notes: list[tuple[float, int, float]]
    ) -> None:
        """Label boxes (note + Hz) above the tracked peaks, with a leader line
        down to a ring on the curve. Overlapping labels stack upward."""
        font = self._note_font()
        box = self.box
        layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(layer)
        pad = max(2, round(font.size * 0.35))
        ring = max(2, round(box.plot_height * 0.022))
        lift = max(ring * 3, round(box.plot_height * 0.16))
        line_w = max(1, round(font.size / 14))
        placed: list[tuple[float, float, float, float]] = []
        n = len(main)
        for hz, midi, alpha in sorted(notes):
            frac = (math.log(hz) - self.log_lo) / (self.log_hi - self.log_lo)
            px = self.ox + frac * (box.width - 1)
            level = float(np.interp(frac * (n - 1), np.arange(n), main))
            py = self.oy + (1.0 - level) * (box.plot_height - 1)
            name = f"{NOTE_NAMES[midi % 12]}{midi // 12 - 1}"
            unit = f"{hz:.0f}Hz"
            gap = font.getlength(" ")
            w = font.getlength(name) + gap + font.getlength(unit) + 2 * pad
            h = font.size + 2 * pad
            x0 = min(max(0.0, px - w / 2), img.width - w)
            y0 = max(0.0, py - lift - h)
            for ox0, oy0, ox1, oy1 in placed:
                if not (x0 < ox1 and x0 + w > ox0 and y0 < oy1 and y0 + h > oy0):
                    continue
                if oy0 - h - pad >= 0:  # room above: stack
                    y0 = oy0 - h - pad
                elif ox1 + pad + w <= img.width:  # else slide right, then left
                    x0 = ox1 + pad
                else:
                    x0 = max(0.0, ox0 - pad - w)
            placed.append((x0, y0, x0 + w, y0 + h))
            a = round(255 * alpha)
            anchor_x = min(max(px, x0 + line_w), x0 + w - line_w)
            draw.line([(px, py - ring), (anchor_x, y0 + h)], fill=(*self.accent, a), width=line_w)
            draw.ellipse(
                (px - ring, py - ring, px + ring, py + ring),
                outline=(*self.main_rgba[:3], a),
                width=line_w,
            )
            draw.rectangle(
                (x0, y0, x0 + w, y0 + h),
                fill=(10, 9, 12, round(190 * alpha)),
                outline=(*self.accent, a),
                width=line_w,
            )
            draw.text((x0 + pad, y0 + pad), name, font=font, fill=(245, 240, 236, a), anchor="la")
            ux = x0 + pad + font.getlength(name) + gap
            draw.text(
                (ux, y0 + pad),
                unit,
                font=font,
                fill=(236, 230, 226, round(200 * alpha)),
                anchor="la",
            )
        img.alpha_composite(layer)


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


def _decode_window(audio: Path, window: tuple[float, float]) -> tuple[np.ndarray, float]:
    """The window's samples plus a little context each side, and the lead-in."""
    start, end = float(window[0]), float(window[1])
    pad = 0.1
    lead = min(pad, start)
    return decode_mono(audio, start - lead, end + pad, sample_rate=SAMPLE_RATE), lead


def compute_spectrum(audio: Path, window: tuple[float, float], fps: int) -> SpectrumFrames:
    """Decode the window (with half an FFT of context each side) and analyze it."""
    samples, lead = _decode_window(audio, window)
    n = _frames_for(float(window[1]) - float(window[0]), fps)
    return spectrum_frames(samples, fps, n, SAMPLE_RATE, lead)


class Layer(Protocol):
    """One encoded overlay clip: a fixed box and a frame-index -> image function."""

    @property
    def name(self) -> str: ...

    @property
    def box(self) -> ClipRect: ...

    def frame(self, k: int) -> Image.Image: ...

    @property
    def scale_to(self) -> tuple[int, int] | None: ...


@dataclass
class SchematicLayer:
    """Pulsing schematic nodes, drawn at NODE_SCALE and scaled up by ffmpeg."""

    painter: NodePainter
    data: ReadoutFrames
    out_size: tuple[int, int]
    name: str = "schematic"

    @property
    def box(self) -> ClipRect:
        return ClipRect(0, 0, *self.painter.size)

    @property
    def scale_to(self) -> tuple[int, int] | None:
        return self.out_size

    def frame(self, k: int) -> Image.Image:
        d = self.data
        return self.painter.frame(d.bands[k], float(d.flash[k]), int(d.beat_pos[k]))


@dataclass
class SpectrumLayer:
    painter: SpectrumPainter
    data: SpectrumFrames
    notes: NotePeaks | None = None
    name: str = "spectrum"
    scale_to: tuple[int, int] | None = None

    @property
    def box(self) -> ClipRect:
        return self.painter.clip

    def frame(self, k: int) -> Image.Image:
        notes = None
        if self.notes is not None:
            notes = [
                (
                    float(self.notes.hz[k, s]),
                    int(self.notes.midi[k, s]),
                    float(self.notes.alpha[k, s]),
                )
                for s in range(self.notes.hz.shape[1])
                if self.notes.alpha[k, s] > 0
            ]
        return self.painter.frame(self.data.main[k], self.data.lag[k], self.data.hold[k], notes)


@dataclass
class ReadoutLayer:
    painter: ReadoutPainter
    data: ReadoutFrames
    name: str = "readouts"
    scale_to: tuple[int, int] | None = None

    @property
    def box(self) -> ClipRect:
        r = self.painter.rect
        return ClipRect(r.x, r.y, r.width, r.height)

    def frame(self, k: int) -> Image.Image:
        d = self.data
        return self.painter.frame(
            float(d.clock[k]), int(d.beat_pos[k]), float(d.flash[k]), d.bands[k]
        )


def build_layers(
    audio: Path,
    window: tuple[float, float],
    size: tuple[int, int],
    style: dict,
    beatmap: dict | None = None,
    seed: int = 0,
) -> tuple[list[Layer], int]:
    """Analyze the window once and build every enabled layer, bottom first;
    returns (layers, frames). `seed` picks the schematic board."""
    fps = int(style["fps"])
    samples, lead = _decode_window(audio, window)
    n = _frames_for(float(window[1]) - float(window[0]), fps)
    data = spectrum_frames(samples, fps, n, SAMPLE_RATE, lead)
    beatmap = beatmap or {}
    frames = None
    if style["schematic"] or wants_readouts(style):
        frames = readout_frames(beatmap, window, fps, band_levels(data.main, data.freqs))
    layers: list[Layer] = []
    if style["schematic"]:
        board = generate(size, seed, float(style["schematic_density"]))
        painter = NodePainter(board, parse_color(style["accent_color"]))
        layers.append(SchematicLayer(painter, frames, size))
    if style["spectrum"]:
        box = spectrum_box(size, style)
        painter = SpectrumPainter(box, data.freqs, style, clip_rect(size, box, style))
        notes = note_peaks(samples, fps, n, SAMPLE_RATE, lead) if style["show_notes"] else None
        layers.append(SpectrumLayer(painter, data, notes))
    if wants_readouts(style):
        layers.append(ReadoutLayer(ReadoutPainter(size, style, beatmap), frames))
    return layers, n


# Worker state for the process pool: set once per worker by `_init_worker`.
_WORKER: list[Layer] | None = None


def _init_worker(layers: list[Layer]) -> None:
    global _WORKER
    _WORKER = layers


def _draw_chunk(layers: list[Layer], lo: int, hi: int) -> tuple[bytes, ...]:
    """One byte string per layer for frames [lo, hi)."""
    return tuple(b"".join(layer.frame(k).tobytes() for k in range(lo, hi)) for layer in layers)


def _worker_chunk(bounds: tuple[int, int]) -> tuple[bytes, ...]:
    assert _WORKER is not None
    return _draw_chunk(_WORKER, *bounds)


def _chunks(layers: list[Layer], n: int, workers: int) -> Iterator[tuple[bytes, ...]]:
    """Frame bytes in order, `CHUNK` frames at a time. Drawing is a pure function
    of the frame index, so the parallel path yields exactly the serial bytes."""
    bounds = [(lo, min(n, lo + CHUNK)) for lo in range(0, n, CHUNK)]
    if workers <= 1 or n < PARALLEL_MIN_FRAMES:
        for lo, hi in bounds:
            yield _draw_chunk(layers, lo, hi)
        return
    # Not "fork": the studio renders from worker threads, and forking a threaded
    # process can deadlock. forkserver/spawn start clean interpreters instead.
    methods = multiprocessing.get_all_start_methods()
    context = multiprocessing.get_context("forkserver" if "forkserver" in methods else "spawn")
    with ProcessPoolExecutor(
        workers, mp_context=context, initializer=_init_worker, initargs=(layers,)
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
    out_dir: Path,
    beatmap: dict | None = None,
    progress: Callable[[float], None] | None = None,
    cancel: Callable[[], bool] | None = None,
    workers: int | None = None,
    seed: int = 0,
) -> list[ReactiveClip]:
    """Draw and encode every enabled reactive layer for `window` into
    `out_dir/<layer>.mkv`; returns the clips in overlay order."""
    fps = int(style["fps"])
    layers, n = build_layers(audio, window, size, style, beatmap, seed)
    if not layers:
        return []
    workers = default_workers() if workers is None else workers
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = [out_dir / f"{layer.name}.mkv" for layer in layers]

    procs: list[subprocess.Popen] = []
    chunks = _chunks(layers, n, workers)
    try:
        for layer, path in zip(layers, paths, strict=True):
            procs.append(
                subprocess.Popen(
                    encode_command(path, (layer.box.width, layer.box.height), fps),
                    stdin=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )
            )
        done = 0
        for chunk in chunks:
            if cancel is not None and cancel():
                raise ReactiveCancelled("render cancelled")
            for proc, data in zip(procs, chunk, strict=True):
                assert proc.stdin is not None
                proc.stdin.write(data)
            done = min(n, done + CHUNK)
            if progress is not None:
                progress(100.0 * done / n)
        for proc in procs:
            assert proc.stdin is not None
            proc.stdin.close()
        for proc in procs:
            err = proc.stderr.read().decode(errors="replace") if proc.stderr else ""
            if proc.wait() != 0:
                raise RuntimeError(f"overlay encode failed:\n{err.strip()[-2000:]}")
    except BaseException:
        chunks.close()
        for proc in procs:
            proc.kill()
            proc.wait()
        raise
    return [
        ReactiveClip(path, layer.box.x, layer.box.y, layer.scale_to)
        for layer, path in zip(layers, paths, strict=True)
    ]
