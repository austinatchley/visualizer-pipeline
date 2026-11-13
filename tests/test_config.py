import pytest

from vp.config import (
    resolve_hook,
    resolve_overrides,
    resolve_style,
)


def test_clean_preset_defaults():
    style = resolve_style()
    assert style["preset"] == "clean"
    assert style["beats_per_cut"] == 8
    assert style["fps"] == 30
    assert style["supersample"] == 2


def test_track_style_overrides_preset():
    style = resolve_style({"beats_per_cut": 4, "zoom": 0.2})
    assert style["beats_per_cut"] == 4
    assert style["zoom"] == 0.2


def test_flags_win_over_track_style():
    style = resolve_style(
        {"beats_per_cut": 4}, flags={"beats_per_cut": 2, "preset": "clean"}
    )
    assert style["beats_per_cut"] == 2


def test_punchy_preset_overrides():
    style = resolve_style(preset="punchy")
    assert style["preset"] == "punchy"
    assert style["beats_per_cut"] == 4
    assert style["crossfade"] == 0.25
    assert style["contrast"] == 1.15
    assert style["beat_punch"] == 0.06


def test_balanced_preset_combines():
    style = resolve_style(preset="balanced")
    assert style["preset"] == "balanced"
    assert style["beats_per_cut"] == 6
    assert style["contrast"] == 1.08


def test_flags_pick_preset():
    style = resolve_style(flags={"preset": "punchy"})
    assert style["preset"] == "punchy"


def test_unknown_preset_raises():
    with pytest.raises(ValueError, match="unknown preset"):
        resolve_style(preset="nope")


def test_contrast_range_raises():
    with pytest.raises(ValueError, match="contrast"):
        resolve_style(flags={"contrast": 0.1})
    with pytest.raises(ValueError, match="contrast"):
        resolve_style(flags={"contrast": 3.0})


def test_beat_punch_range_raises():
    with pytest.raises(ValueError, match="beat_punch"):
        resolve_style(flags={"beat_punch": -0.1})
    with pytest.raises(ValueError, match="beat_punch"):
        resolve_style(flags={"beat_punch": 0.5})


def test_vignette_and_grain_validation():
    with pytest.raises(ValueError, match="vignette"):
        resolve_style(flags={"vignette": 1.5})
    with pytest.raises(ValueError, match="grain"):
        resolve_style(flags={"grain": -0.2})


def test_texture_blend_validation():
    with pytest.raises(ValueError, match="texture_blend"):
        resolve_style(flags={"texture_blend": "burn"})
    assert resolve_style(flags={"texture_blend": "overlay"})["texture_blend"] == "overlay"


def test_texture_path_stringified():
    assert resolve_style(flags={"texture": "/tmp/grain.png"})["texture"] == "/tmp/grain.png"


def test_numeric_choices_accept_string_payloads():
    assert resolve_style({"supersample": "2"})["supersample"] == 2
    assert resolve_style({"fps": "30"})["fps"] == 30
    with pytest.raises(ValueError, match="supersample"):
        resolve_style({"supersample": "9"})


def test_invalid_fps_raises():
    with pytest.raises(ValueError, match="fps"):
        resolve_style(flags={"fps": 25})


def test_unknown_track_style_key_raises():
    with pytest.raises(ValueError, match="unknown style keys"):
        resolve_style({"nope": 1})


def test_hook_defaults_to_auto():
    hook = resolve_hook()
    assert hook["mode"] == "auto"
    assert hook["target"] == 45.0


def test_hook_full_mode():
    assert resolve_hook({"mode": "full"})["mode"] == "full"


def test_hook_manual_mode():
    hook = resolve_hook({"start": 10, "end": 50})
    assert hook["mode"] == "manual"
    assert (hook["start"], hook["end"]) == (10.0, 50.0)


def test_hook_partial_raises():
    with pytest.raises(ValueError, match="together"):
        resolve_hook({"start": 10})


def test_hook_target_clamped():
    assert resolve_hook({"target": 999})["target"] == 60.0
    assert resolve_hook({"target": 1})["target"] == 30.0


def test_hook_target_limited_by_duration():
    assert resolve_hook(duration=20)["target"] == 20.0


def test_overrides_bpm_validation():
    with pytest.raises(ValueError, match="bpm"):
        resolve_overrides({"bpm": 0})


def test_overrides_cuts_sorted():
    overrides = resolve_overrides({"cuts": [10, 4, 7]})
    assert overrides["cuts"] == [4.0, 7.0, 10.0]


def test_overrides_tempo_multiplier_validation():
    with pytest.raises(ValueError, match="tempo_multiplier"):
        resolve_overrides({"tempo_multiplier": 3})