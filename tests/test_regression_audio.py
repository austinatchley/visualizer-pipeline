"""Regression checks on arranged, genre-shaped beats with known BPMs (issue #54).

The tracks come from scripts/make_regression_tracks.py: layered drums, bass,
pads, and an intro/verse/drop/breakdown/drop/outro arrangement, synthesized
on the fly (a few seconds total). The assertions pin properties, not golden
numbers, so planner tuning only fails here when it gets something wrong:

- detected tempo matches the true BPM within 3%, allowing a 2x/0.5x reading
- the auto hook is 30-60 s, contains a drop entry, and is mostly drop
- every drop entry gets a cut within one beat
- average segment length stays in a sane range
- analysis is deterministic

Known gap (for #11): the intro -> verse change (drums enter) usually gets no
cut nearby; only drops are asserted.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

from vp.analyze import analyze_audio
from vp.config import resolve_hook, resolve_style
from vp.plan import full_window, plan_timeline, select_hook

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "make_regression_tracks.py"


def _load_generator():
    spec = importlib.util.spec_from_file_location("make_regression_tracks", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses resolve their module by name
    spec.loader.exec_module(module)
    return module


gen = _load_generator()
SPECS = {spec.name: spec for spec in gen.SPECS}


@pytest.fixture(scope="module")
def analyzed(tmp_path_factory):
    """name -> (spec, audio path, beatmap), analyzed once per module."""
    root = tmp_path_factory.mktemp("regression")
    out = {}
    for name, spec in SPECS.items():
        audio = gen.write_track(spec, root / name, image=False)
        out[name] = (spec, audio, analyze_audio(audio))
    return out


def _drops(spec) -> list[tuple[float, float]]:
    return [(start, end) for name, start, end in spec.sections() if name in gen.HIGH_ENERGY]


@pytest.mark.parametrize("name", SPECS)
def test_tempo_within_tolerance_allowing_octaves(analyzed, name):
    spec, _, beatmap = analyzed[name]
    detected = float(beatmap["tempo"])
    ratios = [detected / (spec.bpm * k) for k in (0.5, 1.0, 2.0)]
    assert any(abs(r - 1.0) <= 0.03 for r in ratios), (
        f"{name}: detected {detected} BPM, true {spec.bpm}"
    )


@pytest.mark.parametrize("name", SPECS)
def test_auto_hook_lands_on_the_drops(analyzed, name):
    spec, _, beatmap = analyzed[name]
    start, end = select_hook(beatmap, resolve_hook(None, None))
    assert 30.0 - 1e-6 <= end - start <= 60.0 + 1e-6

    drops = _drops(spec)
    assert any(start - 0.5 <= d_start < end for d_start, _ in drops), (
        f"{name}: hook {start:.1f}-{end:.1f}s contains no drop entry {drops}"
    )
    in_drops = sum(max(0.0, min(end, d1) - max(start, d0)) for d0, d1 in drops)
    assert in_drops / (end - start) >= 0.5, f"{name}: hook is mostly not drop"


@pytest.mark.parametrize("name", SPECS)
def test_every_drop_entry_gets_a_cut(analyzed, name):
    spec, _, beatmap = analyzed[name]
    plan = plan_timeline(beatmap, full_window(beatmap["duration"]), 3, resolve_style())
    cuts = plan.cut_times[1:-1]
    beat = 60.0 / spec.bpm
    for drop_start, _ in _drops(spec):
        nearest = min(abs(c - drop_start) for c in cuts)
        assert nearest <= beat, f"{name}: no cut within a beat of the drop at {drop_start:.2f}s"


@pytest.mark.parametrize("name", SPECS)
def test_cut_density_is_sane(analyzed, name):
    _, _, beatmap = analyzed[name]
    duration = float(beatmap["duration"])
    plan = plan_timeline(beatmap, full_window(duration), 3, resolve_style())
    mean_segment = duration / len(plan.segments)
    assert 1.5 <= mean_segment <= 10.0, f"{name}: mean segment {mean_segment:.2f}s"


def test_analysis_is_deterministic(analyzed):
    _, audio, beatmap = analyzed["house_124"]
    again = analyze_audio(audio)
    assert again["tempo"] == beatmap["tempo"]
    assert again["beats"] == beatmap["beats"]
