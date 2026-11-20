"""Pitch and key helpers for the data readouts (issue #82).

Pure numpy, no librosa: note naming for the spectrum's peak callouts, and the
key decision that `analyze.py` feeds with chroma vectors.
"""

from __future__ import annotations

import math

import numpy as np

A4_HZ = 440.0
A4_MIDI = 69
NOTE_NAMES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")
# Key names follow common spelling (Bb major, not A# major; C# minor, not Db minor).
MAJOR_KEY_NAMES = ("C", "Db", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B")
MINOR_KEY_NAMES = ("C", "C#", "D", "Eb", "E", "F", "F#", "G", "G#", "A", "Bb", "B")

# Krumhansl-Kessler key profiles, indexed from the tonic.
MAJOR_PROFILE = np.array([6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88])
MINOR_PROFILE = np.array([6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17])
# Weight of the opening-chord evidence when choosing between a major key and its
# relative minor. Their scales are the same notes, so a profile match alone can't
# separate them (Am-F-C-G reads as C major); the chord a track opens on can.
OPENING_WEIGHT = 3.0


def hz_to_midi(hz: float) -> float:
    return A4_MIDI + 12.0 * math.log2(hz / A4_HZ)


def note_name(hz: float) -> str:
    """Nearest equal-tempered note with octave, e.g. 55 Hz -> `A1`."""
    midi = round(hz_to_midi(hz))
    return f"{NOTE_NAMES[midi % 12]}{midi // 12 - 1}"


def key_name(tonic: int, mode: str) -> str:
    names = MAJOR_KEY_NAMES if mode == "major" else MINOR_KEY_NAMES
    return f"{names[tonic % 12]} {mode}"


def _triad(root: int, minor: bool) -> np.ndarray:
    v = np.zeros(12)
    v[[root % 12, (root + (3 if minor else 4)) % 12, (root + 7) % 12]] = 1.0
    return v


def _corr(a: np.ndarray, b: np.ndarray) -> float:
    a, b = a - a.mean(), b - b.mean()
    denom = float(np.sqrt((a * a).sum() * (b * b).sum()))
    return float((a * b).sum() / denom) if denom > 0 else 0.0


def detect_key(chroma: np.ndarray, opening: np.ndarray | None = None) -> dict | None:
    """Key from a 12-bin chroma profile (C first), or None for silence.

    The best of the 24 Krumhansl correlations picks the scale. Between that
    scale's major key and its relative minor, the opening chroma (a few seconds
    where the harmony starts) adds evidence for whichever tonic triad it fits.
    Confidence is the chosen key's profile correlation (0..1)."""
    chroma = np.asarray(chroma, dtype=float)
    if chroma.shape != (12,) or not np.isfinite(chroma).all() or chroma.max() <= 0:
        return None
    major = np.array([_corr(chroma, np.roll(MAJOR_PROFILE, t)) for t in range(12)])
    minor = np.array([_corr(chroma, np.roll(MINOR_PROFILE, t)) for t in range(12)])
    if major.max() >= minor.max():
        major_tonic = int(major.argmax())
    else:
        major_tonic = (int(minor.argmax()) + 3) % 12
    minor_tonic = (major_tonic + 9) % 12
    major_score, minor_score = float(major[major_tonic]), float(minor[minor_tonic])

    lean = 0.0  # > 0 favors the minor reading
    if opening is not None:
        opening = np.asarray(opening, dtype=float)
        fit_major = float(opening @ _triad(major_tonic, minor=False))
        fit_minor = float(opening @ _triad(minor_tonic, minor=True))
        if fit_major + fit_minor > 0:
            lean = fit_minor / (fit_major + fit_minor) - 0.5

    if minor_score + OPENING_WEIGHT * lean > major_score:
        tonic, mode, score = minor_tonic, "minor", minor_score
    else:
        tonic, mode, score = major_tonic, "major", major_score
    return {
        "tonic": tonic,
        "mode": mode,
        "name": key_name(tonic, mode),
        "confidence": round(max(0.0, score), 4),
    }


def short_key(key: dict | None) -> str:
    """Readout form: `A MIN`, `Bb MAJ`; `--` when unknown."""
    if not key:
        return "--"
    names = MAJOR_KEY_NAMES if key["mode"] == "major" else MINOR_KEY_NAMES
    return f"{names[int(key['tonic']) % 12]} {'MAJ' if key['mode'] == 'major' else 'MIN'}"
