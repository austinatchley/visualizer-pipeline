"""Deterministic Pillow scrolling waveform strip, reactive to beat energy."""

from __future__ import annotations

import subprocess
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

SAMPLE_RATE = 8000


def decode_mono(
    path: Path, start: float, end: float, sample_rate: int = SAMPLE_RATE
) -> np.ndarray:
    """Decode a clip window to a mono float32 buffer via ffmpeg."""
    duration = float(end) - float(start)
    cmd = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin",
        "-ss", f"{start:.3f}", "-t", f"{duration:.3f}",
        "-i", str(path),
        "-ac", "1", "-ar", str(sample_rate),
        "-f", "f32le", "pipe:1",
    ]
    proc = subprocess.run(cmd, capture_output=True)
    if proc.returncode != 0:
        tail = "\n".join(proc.stderr.decode(errors="replace").strip().splitlines()[-20:])
        raise RuntimeError(f"failed to decode audio for waveform: {tail}")
    return np.frombuffer(proc.stdout, dtype="<f4")


def peak_envelopes(samples: np.ndarray, num_bars: int) -> np.ndarray:
    """Per-bar peak amplitude, normalized to 0..1."""
    if len(samples) == 0:
        return np.zeros(num_bars, dtype=float)
    if len(samples) < num_bars:
        samples = np.pad(samples, (0, num_bars - len(samples)))
    edges = np.linspace(0, len(samples), num_bars + 1, dtype=int)
    peaks = np.array(
        [float(np.max(np.abs(samples[edges[i] : edges[i + 1]]))) for i in range(num_bars)]
    )
    top = float(peaks.max()) if len(peaks) else 0.0
    if top <= 0:
        return np.zeros(num_bars, dtype=float)
    return peaks / top


def energy_curve(
    beatmap: dict, window: tuple[float, float], num_bars: int
) -> np.ndarray:
    """Mean beat energy per bar window, mapped to 0..1."""
    start, end = window
    beats = beatmap.get("beats") or []
    energy = beatmap.get("beat_energy") or []
    values: list[tuple[float, float]] = []
    for i, b in enumerate(beats):
        if start <= b < end:
            values.append((float(b) - start, float(energy[i]) if i < len(energy) else 0.5))

    curve = np.full(num_bars, 0.5, dtype=float)
    if not values:
        return curve

    times = np.array([v[0] for v in values])
    energies = np.array([v[1] for v in values])
    bar_edges = np.linspace(0.0, float(end) - float(start), num_bars + 1)
    for i in range(num_bars):
        mask = (times >= bar_edges[i]) & (times < bar_edges[i + 1])
        if mask.any():
            curve[i] = float(np.mean(energies[mask]))
    return curve


def _draw_strip(
    width: int,
    height: int,
    amplitudes: np.ndarray,
    energies: np.ndarray,
    color: tuple[int, int, int],
) -> Image.Image:
    img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    n = len(amplitudes)
    bar_w = width / n
    gap = max(1, int(bar_w * 0.25))
    for i in range(n):
        amp = amplitudes[i] * (0.55 + 0.45 * energies[i])
        bar_h = max(2, int(height * 0.5 * amp))
        x = int(i * bar_w) + gap
        w = max(1, int(bar_w) - 2 * gap)
        alpha = int(70 + 130 * energies[i])
        draw.rectangle([x, height - bar_h, x + w, height], fill=(*color, alpha))
    glow = img.filter(ImageFilter.GaussianBlur(radius=max(2, height // 8)))
    return Image.alpha_composite(glow, img)


def build_strip(
    audio: Path,
    window: tuple[float, float],
    width: int,
    height: int,
    beatmap: dict,
    color: tuple[int, int, int] = (255, 255, 255),
) -> Image.Image:
    """Render a transparent scrolling waveform strip for the given clip window."""
    samples = decode_mono(audio, window[0], window[1])
    num_bars = max(48, width // 8)
    peaks = peak_envelopes(samples, num_bars)
    energies = energy_curve(beatmap, window, num_bars)
    return _draw_strip(width, height, peaks, energies, color)
