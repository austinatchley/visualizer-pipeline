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


# --- band meters (#82) ------------------------------------------------------------

BANDS = (("SUB", 40.0, 60.0), ("LOW", 60.0, 250.0), ("MID", 250.0, 2000.0), ("HI", 2000.0, 16000.0))


def band_levels(main: np.ndarray, freqs: np.ndarray) -> np.ndarray:
    """Mean display level per band, shape (frames, len(BANDS)), in [0, 1].

    Averages the main curve (already normalized and smoothed) over each band's
    points, then stretches each band against its own window range so every
    meter uses its full travel."""
    if len(main) == 0:
        return np.zeros((0, len(BANDS)), np.float32)
    cols = []
    for _, lo, hi in BANDS:
        mask = (freqs >= lo) & (freqs < hi)
        cols.append(main[:, mask].mean(axis=1) if mask.any() else np.zeros(len(main)))
    levels = np.stack(cols, axis=1)
    floor = np.percentile(levels, 5, axis=0)
    ceil = np.percentile(levels, 99.5, axis=0)
    span = np.maximum(ceil - floor, 0.15)
    return np.clip((levels - floor) / span, 0.0, 1.0).astype(np.float32)


# --- peak notes (#82) -------------------------------------------------------------

NOTE_FFT = 16384  # 2.7 Hz bins at 44.1 kHz: enough to name bass notes
NOTE_MIN_HZ = 40.0
NOTE_MAX_HZ = 800.0
NOTE_SLOTS = 2  # callouts on screen at once
NOTE_FLOOR_DB = 24.0  # a peak must sit within this of the window's loudest peak
NOTE_MIN_GAP = 5.0  # semitones between two callouts
NOTE_TRACK = 1.0  # semitones a tracked peak may drift per frame and keep its slot
NOTE_HOLD_S = 0.3  # a new callout stays at least this long
NOTE_FADE_S = 0.15  # fade in/out
NOTE_RENAME = 0.75  # semitones from the labeled note before the label changes


@dataclass(frozen=True)
class NotePeaks:
    """Tracked peaks, shape (frames, NOTE_SLOTS): frequency in Hz (NaN = empty
    slot), the labeled MIDI note (-1 = empty), and callout opacity in [0, 1]."""

    hz: np.ndarray
    midi: np.ndarray
    alpha: np.ndarray


def _peak_candidates(mag_db: np.ndarray, bin_hz: float, lo: int, hi: int) -> list[tuple]:
    """Local maxima in bins [lo, hi) as (dB, Hz), loudest first, refined by
    parabolic interpolation on the dB curve."""
    seg = mag_db[lo - 1 : hi + 1]
    mid = seg[1:-1]
    is_peak = (mid > seg[:-2]) & (mid >= seg[2:])
    out = []
    for i in np.flatnonzero(is_peak):
        a, b, c = seg[i], seg[i + 1], seg[i + 2]
        denom = a - 2 * b + c
        shift = 0.5 * (a - c) / denom if denom != 0 else 0.0
        out.append((float(b - 0.25 * (a - c) * shift), (lo + i + shift) * bin_hz))
    out.sort(reverse=True)
    return out


def _semitones(a: float, b: float) -> float:
    return abs(12.0 * math.log2(a / b))


def _midi(hz: float) -> float:
    return 69.0 + 12.0 * math.log2(hz / 440.0)


def note_peaks(
    samples: np.ndarray,
    fps: int,
    n_frames: int,
    sr: int = SAMPLE_RATE,
    start_offset: float = 0.0,
) -> NotePeaks:
    """The strongest low spectral peaks per frame, tracked into stable slots.

    A slot keeps following its peak while one stays within NOTE_TRACK semitones
    and above the floor, so labels don't hop between near-equal peaks; a freed
    slot fades out, and new peaks only claim a slot after the old one is gone."""
    bin_hz = sr / NOTE_FFT
    lo = max(2, int(NOTE_MIN_HZ / bin_hz))
    hi = int(NOTE_MAX_HZ / bin_hz)
    centers = np.round((start_offset + np.arange(n_frames) / fps) * sr).astype(np.int64)
    candidates: list[list[tuple]] = []
    for c0 in range(0, n_frames, FRAME_CHUNK // 4):
        power = _frame_power(samples, centers[c0 : c0 + FRAME_CHUNK // 4], NOTE_FFT)
        mag_db = 10.0 * np.log10(power[:, : hi + 2] + 1e-12)
        candidates += [_peak_candidates(row, bin_hz, lo, hi) for row in mag_db]
    loudest = [c[0][0] for c in candidates if c]
    floor = (float(np.percentile(loudest, 95)) if loudest else 0.0) - NOTE_FLOOR_DB

    hz = np.full((n_frames, NOTE_SLOTS), np.nan, np.float32)
    midi = np.full((n_frames, NOTE_SLOTS), -1, np.int16)
    alpha = np.zeros((n_frames, NOTE_SLOTS), np.float32)
    fade = 1.0 / max(1, round(NOTE_FADE_S * fps))
    hold = max(1, round(NOTE_HOLD_S * fps))
    slot_hz = [math.nan] * NOTE_SLOTS
    slot_alpha = [0.0] * NOTE_SLOTS
    slot_age = [0] * NOTE_SLOTS
    slot_live = [False] * NOTE_SLOTS
    slot_note = [-1] * NOTE_SLOTS
    for k, cands in enumerate(candidates):
        cands = [c for c in cands if c[0] >= floor]
        taken: list[float] = []
        for s in range(NOTE_SLOTS):
            if math.isnan(slot_hz[s]):
                continue
            match = next((f for _, f in cands if _semitones(f, slot_hz[s]) <= NOTE_TRACK), None)
            if match is not None and match not in taken:
                slot_hz[s], slot_live[s] = match, True
                taken.append(match)
            elif slot_age[s] < hold:
                slot_live[s] = True  # hold a fresh callout through a brief dropout
            else:
                slot_live[s] = False
            slot_age[s] += 1
        for s in range(NOTE_SLOTS):
            if not math.isnan(slot_hz[s]):
                continue
            pick = next(
                (
                    f
                    for _, f in cands
                    if f not in taken
                    and all(
                        math.isnan(other) or _semitones(f, other) >= NOTE_MIN_GAP
                        for other in slot_hz
                    )
                ),
                None,
            )
            if pick is not None:
                slot_hz[s], slot_live[s], slot_age[s], slot_alpha[s] = pick, True, 0, 0.0
                slot_note[s] = round(_midi(pick))
                taken.append(pick)
        for s in range(NOTE_SLOTS):
            if math.isnan(slot_hz[s]):
                continue
            target = 1.0 if slot_live[s] else 0.0
            slot_alpha[s] = min(1.0, slot_alpha[s] + fade) if target else slot_alpha[s] - fade
            if slot_alpha[s] <= 0.0:
                slot_hz[s], slot_alpha[s], slot_note[s] = math.nan, 0.0, -1
                continue
            if abs(_midi(slot_hz[s]) - slot_note[s]) > NOTE_RENAME:
                slot_note[s] = round(_midi(slot_hz[s]))
            hz[k, s], midi[k, s], alpha[k, s] = slot_hz[s], slot_note[s], slot_alpha[s]
    return NotePeaks(hz, midi, alpha)
