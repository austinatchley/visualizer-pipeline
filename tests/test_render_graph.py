"""Snapshot tests for the pure ffmpeg graph builder (issue #47). No ffmpeg needed.

A change to the filtergraph shows up as a readable diff of
tests/snapshots/render_graphs.json. If the change is intended, regenerate with:

    UPDATE_SNAPSHOTS=1 pytest tests/test_render_graph.py

and bump `status.RENDER_VERSION` so existing outputs re-render.
"""

import json
import os
import re
from pathlib import Path

import pytest

from vp.aspects import ASPECTS
from vp.config import resolve_style
from vp.render import RenderAssets, build_command, build_graph, output_size, plan_for
from vp.track import Track

SNAPSHOTS = Path(__file__).parent / "snapshots" / "render_graphs.json"

BEATS = [round(i * 0.5, 4) for i in range(120)]  # 60 s at 120 BPM
BEATMAP = {
    "duration": 60.0,
    "tempo": 120.0,
    "beats": BEATS,
    "downbeats": BEATS[::4],
    # Quiet intro, a drop at 16 s, a breakdown at 40 s: exercises every cut reason.
    "beat_energy": [0.3 if t < 16 else (0.9 if t < 40 else 0.5) for t in BEATS],
    "onset_novelty": [0.5] * len(BEATS),
}


def _track(style: dict) -> Track:
    return Track(
        root=Path("/tracks/demo"),
        audio=Path("/tracks/demo/audio.wav"),
        title="Demo",
        images=[Path(f"/tracks/demo/images/{i:02d}.jpg") for i in range(1, 5)],
        style=resolve_style(style),
        hook={"mode": "auto", "target": 45.0, "start": None, "end": None},
        overrides={"cuts": None},
    )


def _cases():
    for preset in ("clean", "punchy", "balanced", "vgm"):
        for aspect in ("vertical", "horizontal", "square"):
            yield f"{preset}-{aspect}", {"preset": preset}, aspect, False, {}
    yield "preview-vertical", {}, "vertical", True, {}
    yield "preview-waveform", {"waveform": True}, "vertical", True, {"waveform": True}
    yield "waveform", {"waveform": True}, "horizontal", False, {"waveform": True}
    yield (
        "texture",
        {"texture": "grain.png", "texture_blend": "overlay"},
        "vertical",
        False,
        {"texture": True},
    )
    yield (
        "color-punch",
        {"preset": "punchy", "color_punch": 0.4, "saturation": 0.8},
        "vertical",
        False,
        {},
    )
    yield (
        "mono-graded",
        {"saturation": 0.0, "grade": "analog", "vignette": 0.5, "grain": 0.3},
        "square",
        False,
        {},
    )
    yield "no-fades", {"fade_in": 0, "fade_out": 0}, "vertical", False, {}


CASES = {name: rest for name, *rest in _cases()}


def _command(style, aspect_key, preview, extra) -> list[str]:
    track = _track(style)
    aspect = ASPECTS[aspect_key]
    window = (0.0, 60.0) if aspect.kind == "horizontal" else (8.0, 53.0)
    plan = plan_for(track, BEATMAP, window, preview)
    size = output_size(aspect, preview)
    assets = RenderAssets(
        hud=Path("/tmp/vp/hud.png"),
        texture=Path("/tmp/vp/texture.png") if extra.get("texture") else None,
        waveform=Path("/tmp/vp/waveform.png") if extra.get("waveform") else None,
        waveform_height=75 if extra.get("waveform") else 0,
    )
    graph = build_graph(track, plan, BEATMAP, size, assets, preview)
    return build_command(graph, int(track.style["fps"]), plan.duration, Path("/out/x.mp4"), preview)


def _load() -> dict:
    return json.loads(SNAPSHOTS.read_text()) if SNAPSHOTS.is_file() else {}


@pytest.fixture(scope="module")
def snapshots():
    data = _load()
    yield data
    if os.environ.get("UPDATE_SNAPSHOTS"):
        SNAPSHOTS.write_text(json.dumps(data, indent=1, sort_keys=True) + "\n")


@pytest.mark.parametrize("name", sorted(CASES))
def test_graph_matches_snapshot(name, snapshots):
    cmd = _command(*CASES[name])
    # One filter per line keeps snapshot diffs readable.
    graph = cmd[cmd.index("-filter_complex") + 1].split(";")
    actual = {
        "args": [a for a in cmd if a != cmd[cmd.index("-filter_complex") + 1]],
        "graph": graph,
    }
    if os.environ.get("UPDATE_SNAPSHOTS"):
        snapshots[name] = actual
        return
    assert name in snapshots, f"no snapshot for {name}; run with UPDATE_SNAPSHOTS=1"
    assert actual == snapshots[name]


def test_snapshot_file_has_no_stale_cases():
    if os.environ.get("UPDATE_SNAPSHOTS"):
        pytest.skip("regenerating")
    assert set(_load()) == set(CASES)


@pytest.mark.parametrize("name", sorted(CASES))
def test_graph_is_well_formed(name):
    """Structural checks that hold for any valid graph, independent of snapshots."""
    style, aspect_key, preview, extra = CASES[name]
    cmd = _command(style, aspect_key, preview, extra)
    n_inputs = cmd.count("-i")
    graph = cmd[cmd.index("-filter_complex") + 1]
    referenced = {int(i) for i in re.findall(r"\[(\d+):[va]\]", graph)}
    assert referenced == set(range(n_inputs))  # every input used, none missing
    assert "[vout]" in graph
    has_audio = "[aout]" in graph
    assert has_audio is (not preview)
    assert ("-c:a" in cmd) is has_audio


def test_preview_waveform_covers_the_preview_slice(tmp_path, monkeypatch):
    """Issue #68: the strip is decoded for the previewed slice, not the whole hook."""
    from PIL import Image

    import vp.render as render_mod

    windows = []

    def fake_strip(audio, window, width, height, beatmap, *args, **kwargs):
        windows.append(tuple(window))
        return Image.new("RGBA", (width, height))

    monkeypatch.setattr(render_mod, "build_strip", fake_strip)
    monkeypatch.setattr(render_mod, "run_ffmpeg", lambda *args, **kwargs: None)
    track = _track({"waveform": True})
    hook = (8.0, 53.0)
    expected = plan_for(track, BEATMAP, hook, preview=True).window
    assert expected != hook  # the preview really is a slice

    render_mod.render(
        track, ASPECTS["vertical"], BEATMAP, tmp_path / "p.mp4", window=hook, preview=True
    )
    render_mod.render(track, ASPECTS["vertical"], BEATMAP, tmp_path / "e.mp4", window=hook)
    assert windows == [expected, hook]  # preview: the slice; export: the full window
