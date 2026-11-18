"""Per-frame log-frequency spectrum for the reactive spectrum layer (issue #81).

Pure numpy: given mono samples and a frame rate, produce one smoothed spectrum
curve per video frame, plus two trailing curves (a short lag and a slow peak
hold) that the layer draws behind the main curve. Values are display levels in
[0, 1]; nothing here touches Pillow or ffmpeg, so the math is unit-testable.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

SAMPLE_RATE = 44100
FFT_SIZE = 4096
FREQ_MIN = 40.0
FREQ_MAX = 16000.0
POINTS = 160
# Display tilt in dB per octave above FREQ_MIN. Mixes fall off roughly 3-5 dB/oct;
# a partial lift keeps the natural slope readable without flattening it.
TILT_DB_PER_OCT = 1.5
DYNAMIC_RANGE_DB = 60.0
TOP_PERCENTILE = 99.5
HEADROOM_DB = 4.0  # the loud ceiling sits this far below the top of the plot
# Lowest ceiling allowed: a full-scale sine reads ~55 dB here, so near-silent
# windows stay near the floor instead of being normalized up to full height.
MIN_TOP_DB = 0.0
# Ballistics (seconds): fast attack, slower release, like an analyzer's meter.
ATTACK_S = 0.02
RELEASE_S = 0.12
LAG_S = 0.08  # the first trail is the main curve this many seconds ago
HOLD_RELEASE_S = 0.7  # the second trail holds peaks and decays slowly
FRAME_CHUNK = 256  # frames per FFT batch (bounds memory on long tracks)


@dataclass(frozen=True)
class SpectrumFrames:
    """Display curves, shape (frames, POINTS), values in [0, 1]."""

    freqs: np.ndarray  # (POINTS,) Hz, log-spaced
    main: np.ndarray
    lag: np.ndarray
    hold: np.ndarray


def point_freqs(points: int = POINTS) -> np.ndarray:
    return np.geomspace(FREQ_MIN, FREQ_MAX, points)


def filterbank(sr: int = SAMPLE_RATE, n_fft: int = FFT_SIZE, points: int = POINTS) -> np.ndarray:
    """Triangular filters on a log-frequency axis, shape (points, n_fft // 2 + 1).

    Each filter spans its neighbors' centers, widened to at least ~1.5 FFT bins
    so low bands (narrower than a bin) still interpolate smoothly. Rows sum to 1,
    so a band's value is the weighted mean power under it."""
    centers = point_freqs(points)
    bin_freqs = np.fft.rfftfreq(n_fft, 1.0 / sr)
    bin_width = sr / n_fft
    ratio = centers[1] / centers[0]
    weights = np.zeros((points, len(bin_freqs)))
    for i, center in enumerate(centers):
        half = max(center * (ratio - 1.0), 1.5 * bin_width)
        tri = 1.0 - np.abs(bin_freqs - center) / half
        weights[i] = np.clip(tri, 0.0, None)
        total = weights[i].sum()
        if total > 0:
            weights[i] /= total
    return weights


def _frame_power(samples: np.ndarray, centers: np.ndarray, n_fft: int) -> np.ndarray:
    """Hann-windowed power spectra centered on the given sample indices."""
    half = n_fft // 2
    padded = np.concatenate([np.zeros(half, np.float32), samples, np.zeros(half, np.float32)])
    idx = centers[:, None] + np.arange(n_fft)[None, :]  # centers shifted by `half` padding
    idx = np.clip(idx, 0, len(padded) - 1)
    frames = padded[idx] * np.hanning(n_fft).astype(np.float32)
    return np.abs(np.fft.rfft(frames, axis=1)) ** 2


def levels_db(
    samples: np.ndarray,
    fps: int,
    n_frames: int,
    sr: int = SAMPLE_RATE,
    start_offset: float = 0.0,
    n_fft: int = FFT_SIZE,
    points: int = POINTS,
) -> np.ndarray:
    """Tilted band levels in dB, shape (n_frames, points).

    Frame k is centered at `start_offset + k / fps` seconds into `samples`."""
    weights = filterbank(sr, n_fft, points).T.astype(np.float32)
    tilt = TILT_DB_PER_OCT * np.log2(point_freqs(points) / FREQ_MIN)
    centers = np.round((start_offset + np.arange(n_frames) / fps) * sr).astype(np.int64)
    out = np.empty((n_frames, points), np.float32)
    for lo in range(0, n_frames, FRAME_CHUNK):
        power = _frame_power(samples, centers[lo : lo + FRAME_CHUNK], n_fft)
        out[lo : lo + FRAME_CHUNK] = 10.0 * np.log10(power @ weights + 1e-12) + tilt
    return out


def normalize(db: np.ndarray) -> np.ndarray:
    """Map dB to [0, 1] against the window's loud ceiling and a fixed range."""
    if db.size == 0:
        return db
    top = max(float(np.percentile(db, TOP_PERCENTILE)) + HEADROOM_DB, MIN_TOP_DB)
    return np.clip((db - (top - DYNAMIC_RANGE_DB)) / DYNAMIC_RANGE_DB, 0.0, 1.0)


def _coef(tau: float, fps: int) -> float:
    return 1.0 - math.exp(-1.0 / (fps * tau)) if tau > 0 else 1.0


def ballistics(levels: np.ndarray, fps: int) -> np.ndarray:
    """Per-point attack/release smoothing over frames."""
    attack, release = _coef(ATTACK_S, fps), _coef(RELEASE_S, fps)
    out = np.empty_like(levels)
    if len(levels) == 0:
        return out
    y = levels[0].copy()
    for k, x in enumerate(levels):
        y += np.where(x > y, attack, release) * (x - y)
        out[k] = y
    return out


def peak_hold(levels: np.ndarray, fps: int) -> np.ndarray:
    """Running max that decays toward the signal with HOLD_RELEASE_S."""
    release = _coef(HOLD_RELEASE_S, fps)
    out = np.empty_like(levels)
    if len(levels) == 0:
        return out
    y = levels[0].copy()
    for k, x in enumerate(levels):
        y = np.maximum(x, y - release * (y - x))
        out[k] = y
    return out


def lagged(levels: np.ndarray, fps: int) -> np.ndarray:
    shift = max(1, round(LAG_S * fps))
    if len(levels) == 0:
        return levels.copy()
    return np.concatenate([np.repeat(levels[:1], shift, axis=0), levels[:-shift]])[: len(levels)]


def spectrum_frames(
    samples: np.ndarray,
    fps: int,
    n_frames: int,
    sr: int = SAMPLE_RATE,
    start_offset: float = 0.0,
) -> SpectrumFrames:
    """Main, lag, and hold display curves for `n_frames` frames of `samples`."""
    main = ballistics(normalize(levels_db(samples, fps, n_frames, sr, start_offset)), fps)
    return SpectrumFrames(point_freqs(), main, lagged(main, fps), peak_hold(main, fps))
