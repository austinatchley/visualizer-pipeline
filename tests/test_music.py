"""Note naming and key detection helpers (#82)."""

import numpy as np
import pytest

from vp import music


@pytest.mark.parametrize(
    "hz,name",
    [(440.0, "A4"), (55.0, "A1"), (261.63, "C4"), (41.2, "E1"), (277.18, "C#4"), (450.0, "A4")],
)
def test_note_name(hz, name):
    assert music.note_name(hz) == name


def _chroma(notes: dict[str, float]) -> np.ndarray:
    v = np.full(12, 0.05)
    for note, weight in notes.items():
        v[music.NOTE_NAMES.index(note)] += weight
    return v


AM_F_C_G = _chroma({"A": 2, "C": 3, "E": 2, "F": 1, "G": 2, "B": 1, "D": 1})


def test_relative_minor_needs_the_opening_chord():
    assert music.detect_key(AM_F_C_G)["name"] == "C major"
    opening_am = _chroma({"A": 1, "C": 1, "E": 1})
    key = music.detect_key(AM_F_C_G, opening_am)
    assert (key["name"], key["tonic"], key["mode"]) == ("A minor", 9, "minor")
    opening_c = _chroma({"C": 1, "E": 1, "G": 1})
    assert music.detect_key(AM_F_C_G, opening_c)["name"] == "C major"


def test_detect_key_transposes():
    shifted = np.roll(AM_F_C_G, 2)  # up a whole step: B minor / D major
    opening = np.roll(_chroma({"A": 1, "C": 1, "E": 1}), 2)
    assert music.detect_key(shifted, opening)["name"] == "B minor"


def test_detect_key_silence_is_none():
    assert music.detect_key(np.zeros(12)) is None
    assert music.detect_key(np.full(12, np.nan)) is None


def test_key_spelling_and_short_form():
    assert music.key_name(10, "major") == "Bb major"
    assert music.key_name(1, "minor") == "C# minor"
    assert music.short_key({"tonic": 9, "mode": "minor"}) == "A MIN"
    assert music.short_key({"tonic": 3, "mode": "major"}) == "Eb MAJ"
    assert music.short_key(None) == "--"
