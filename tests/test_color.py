"""Deterministic color-grade and beat-reactive saturation tests (#20/#21)."""

import math

import pytest

from vp.config import resolve_style
from vp.render import _energy_saturation, _mean_beat_energy, _saturation_expr


def test_saturation_default_and_grade_choices():
    assert resolve_style()["saturation"] == 1.0
    assert "analog" in resolve_style()["grade"] or resolve_style()["grade"] == "none"
    # `analog` is a valid, selectable grade.
    assert resolve_style({"grade": "analog"})["grade"] == "analog"


def test_color_punch_default_off():
    assert resolve_style()["color_punch"] == 0.0


def test_color_punch_range_raises():
    with pytest.raises(ValueError, match="color_punch"):
        resolve_style(flags={"color_punch": -0.1})
    with pytest.raises(ValueError, match="color_punch"):
        resolve_style(flags={"color_punch": 1.5})


def test_energy_saturation_tracks_energy():
    quiet = _energy_saturation(0.0, 1.0)
    loud = _energy_saturation(1.0, 1.0)
    assert quiet == pytest.approx(0.85)
    assert loud == pytest.approx(1.2)
    assert loud > quiet
    # Mono stays mono regardless of energy.
    assert _energy_saturation(1.0, 0.0) == 0.0


def test_mean_beat_energy_window():
    beatmap = {
        "beats": [0.0, 1.0, 2.0, 3.0],
        "beat_energy": [0.1, 0.9, 0.5, 0.5],
    }
    assert _mean_beat_energy(beatmap, 0.0, 2.0) == pytest.approx(0.5)
    assert _mean_beat_energy(beatmap, 1.0, 2.0) == pytest.approx(0.9)
    assert _mean_beat_energy(beatmap, 10.0, 12.0) == 0.5  # empty window fallback


def test_saturation_expr_decays_to_base():
    expr = _saturation_expr(1.0, 0.3, 30)
    assert "eval=frame" in expr
    assert "1.0000+0.3000*exp(-n/30*6.0)" in expr

    def value(on: int) -> float:
        return 1.0 + 0.3 * math.exp(-on / 30 * 6.0)

    # Spike at the cut, gone within ~0.3 s (9 frames at 30 fps).
    assert value(0) == pytest.approx(1.3)
    assert value(9) < value(0)
    assert value(30 * 5) == pytest.approx(1.0, abs=0.01)
