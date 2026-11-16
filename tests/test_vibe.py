"""Deterministic vibe-aware preset selection tests (issue #19)."""

from types import SimpleNamespace

import pytest

from vp.config import DEFAULT_STYLE, resolve_style
from vp.vibe import choose_preset, features, finalize_auto_style, score


def _beatmap(energy, novelty, tempo):
    return {"tempo": tempo, "beat_energy": energy, "onset_novelty": novelty}


def _track(style):
    return SimpleNamespace(style=style)


def test_features_and_score_are_bounded():
    bm = _beatmap([0.2, 0.8, 0.4], [0.1, 0.9, 0.3], 120)
    f = features(bm)
    assert f["mean_energy"] == pytest.approx(0.4667, abs=1e-3)
    assert 0.0 <= score(bm) <= 1.0


def test_high_energy_maps_to_punchy_and_quiet_to_clean():
    loud = _beatmap([0.9, 0.85, 0.95, 0.8], [0.8, 0.9, 0.7, 0.85], 128)
    quiet = _beatmap([0.2, 0.2, 0.15, 0.2], [0.1, 0.1, 0.1, 0.1], 80)
    assert choose_preset(loud) == "punchy"
    assert choose_preset(quiet) == "clean"


def test_choose_preset_is_deterministic():
    bm = _beatmap([0.5] * 8, [0.5] * 8, 110)
    assert choose_preset(bm) == choose_preset(dict(bm))
    assert choose_preset(bm) in {"clean", "punchy"}


def test_resolve_auto_is_neutral_until_finalized():
    style = resolve_style({"preset": "auto"})
    assert style["preset"] == "auto"
    assert style["beats_per_cut"] == DEFAULT_STYLE["beats_per_cut"]


def test_finalize_auto_style_picks_and_rewrites():
    loud = _beatmap([0.9, 0.85, 0.95, 0.8], [0.8, 0.9, 0.7, 0.85], 128)
    track = _track(resolve_style({"preset": "auto"}))
    assert finalize_auto_style(track, loud) == "punchy"
    assert track.style["preset"] == "punchy"
    # The chosen preset actually took effect.
    assert track.style["beats_per_cut"] == 4


def test_explicit_preset_wins_over_auto():
    loud = _beatmap([0.9, 0.85, 0.95, 0.8], [0.8, 0.9, 0.7, 0.85], 128)
    track = _track(resolve_style({"preset": "clean"}))
    assert finalize_auto_style(track, loud) == "clean"
    assert track.style["preset"] == "clean"


def test_finalize_auto_preserves_track_overrides():
    loud = _beatmap([0.9, 0.85, 0.95, 0.8], [0.8, 0.9, 0.7, 0.85], 128)
    track = _track(resolve_style({"preset": "auto", "zoom": 0.25}))
    finalize_auto_style(track, loud)
    assert track.style["preset"] == "punchy"
    assert track.style["zoom"] == 0.25
