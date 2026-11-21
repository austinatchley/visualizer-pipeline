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


def test_color_punch_default_is_subtle():
    assert 0.0 < resolve_style()["color_punch"] <= 0.3


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


def test_zoom_is_still_without_ken_burns_or_hits():
    from vp.render import _zoom_expr

    assert _zoom_expr(0, 30, 90, 1.0, 0.04) == "1"
    assert _zoom_expr(1, 30, 90, 1.0, 0.04, []) == "1"


def test_zoom_punches_only_at_hit_frames():
    from vp.render import _zoom_expr

    expr = _zoom_expr(0, 30, 90, 1.0, 0.04, [12, 60])
    assert expr.startswith("1+0.0400*(")
    assert "if(lt(on,12),0," in expr and "if(lt(on,60),0," in expr
    assert _zoom_expr(0, 30, 90, 1.0, 0.0, [12]) == "1"  # beat_punch 0 = off


def _envelope(f, a, fps, on):
    """Evaluate the punch envelope in Python, mirroring the ffmpeg expression."""
    from vp.render import PUNCH_DECAY

    if on < f:
        return 0.0
    if on < f + a:
        t = (on - f) / a
        return t * t * (3 - 2 * t)
    return math.exp(-(on - (f + a)) / fps * PUNCH_DECAY)


def test_punch_eases_in_without_a_jump_and_settles():
    from vp.render import PUNCH_ATTACK, _punch_term

    fps = 30
    a = round(PUNCH_ATTACK * fps)
    term = _punch_term(10, a, fps)
    assert term.startswith("if(lt(on,10),0,") and f"if(lt(on,{10 + a})," in term
    env = [_envelope(10, a, fps, on) for on in range(0, 90)]
    steps = [abs(y - x) for x, y in zip(env, env[1:], strict=False)]
    assert max(steps) < 0.35  # no one-frame jump to full strength
    assert env[10 + a] == 1.0 and env[89] < 0.05  # peaks after the attack, then settles


def test_punch_from_before_the_cut_continues_into_the_segment():
    from vp.render import _punch_term

    assert _punch_term(-6, 5, 30).startswith("if(lt(on,-6),0,if(lt(on,-1),")


def test_ken_burns_still_alternates_when_enabled():
    from vp.render import _zoom_expr

    assert _zoom_expr(0, 30, 90, 1.1, 0.0).startswith("min(1+")
    assert _zoom_expr(1, 30, 90, 1.1, 0.0).startswith("max(1.10000-")
