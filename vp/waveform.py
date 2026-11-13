"""Deterministic Pillow scrolling waveform strip, reactive to beat energy."""

from __future__ import annotations

import subprocess
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

SAMPLE_RATE = 8000
MIN_BARS = 48
MAX_BARS = 1600
PIXELS_PER_BAR = 4
NOISE_FLOOR = 0.04
ENERGY_MIX = 0.12
RMS_WEIGHT = 0.65
PEAK_WEIGHT = 0.35
DYNAMICS_CURVE = 0.7
DYNAMICS_PERCENTILE = 95


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


def bar_count(width: int) -> int:
    """Number of waveform bars for a strip width, capped for performance."""
    return max(MIN_BARS, min(MAX_BARS, width // PIXELS_PER_BAR))


def peak_envelopes(samples: np.ndarray, num_bars: int) -> np.ndarray:
    """Per-bar peak amplitude, normalized to the global maximum."""
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


def amplitude_envelopes(samples: np.ndarray, num_bars: int) -> np.ndarray:
    """Per-bar RMS/peak blend with percentile normalization and a soft curve."""
    if len(samples) == 0:
        return np.zeros(num_bars, dtype=float)
    if len(samples) < num_bars:
        samples = np.pad(samples, (0, num_bars - len(samples)))

    edges = np.linspace(0, len(samples), num_bars + 1, dtype=int)
    values = np.empty(num_bars, dtype=float)
    for i in range(num_bars):
        chunk = samples[edges[i] : edges[i + 1]]
        if len(chunk) == 0:
            values[i] = 0.0
            continue
        magnitude = np.abs(chunk)
        rms = float(np.sqrt(np.mean(chunk * chunk)))
        peak = float(magnitude.max())
        values[i] = RMS_WEIGHT * rms + PEAK_WEIGHT * peak

    scale = float(np.percentile(values, DYNAMICS_PERCENTILE))
    if scale <= 0:
        return np.zeros(num_bars, dtype=float)

    shaped = np.clip(values / scale, 0.0, 1.0) ** DYNAMICS_CURVE
    return np.where(
        values > 0, NOISE_FLOOR + (1.0 - NOISE_FLOOR) * shaped, 0.0
    )


def energy_curve(
    beatmap: dict, window: tuple[float, float], num_bars: int
) -> np.ndarray:
    """Interpolated beat energy at each bar center, mapped to 0..1."""
    start, end = window
    beats = beatmap.get("beats") or []
    energy = beatmap.get("beat_energy") or []
    values: list[tuple[float, float]] = []
    for i, b in enumerate(beats):
        if start <= b < end:
            values.append((float(b) - start, float(energy[i]) if i < len(energy) else 0.5))

    if not values:
        return np.full(num_bars, 0.5, dtype=float)

    times = np.array([v[0] for v in values])
    energies = np.array([v[1] for v in values])
    order = np.argsort(times)
    bar_edges = np.linspace(0.0, float(end) - float(start), num_bars + 1)
    centers = (bar_edges[:-1] + bar_edges[1:]) / 2.0
    curve = np.interp(
        centers,
        times[order],
        energies[order],
        left=float(energies[order][0]),
        right=float(energies[order][-1]),
    )
    return np.clip(curve, 0.0, 1.0)


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
    gap = max(1, int(round(bar_w * 0.5)))
    for i in range(n):
        energy = float(energies[i])
        amp = float(amplitudes[i]) * (1.0 - ENERGY_MIX + 2.0 * ENERGY_MIX * energy)
        amp = min(1.0, max(0.0, amp))
        bar_h = max(1, int(round(height * amp)))
        x0 = int(i * bar_w)
        x1 = int((i + 1) * bar_w) - gap
        if x1 <= x0:
            x1 = x0 + 1
        radius = min(2, max(1, (x1 - x0) // 2))
        alpha = int(80 + 120 * energy)
        draw.rounded_rectangle(
            [x0, height - bar_h, x1, height],
            radius=radius,
            fill=(*color, alpha),
        )
    glow = img.filter(ImageFilter.GaussianBlur(radius=max(2, height // 14)))
    glow.putalpha(glow.getchannel("A").point(lambda alpha: alpha * 3 // 5))
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
    num_bars = bar_count(width)
    amplitudes = amplitude_envelopes(samples, num_bars)
    energies = energy_curve(beatmap, window, num_bars)
    return _draw_strip(width, height, amplitudes, energies, color)
