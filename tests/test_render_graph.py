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
from vp.reactive import ReactiveClip
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
    # Both looks draw the spectrum by default (#88), so their graphs carry the clip.
    for preset in ("datahud", "vgm"):
        for aspect in ("vertical", "horizontal", "square"):
            yield f"{preset}-{aspect}", {"preset": preset}, aspect, False, {"reactive": True}
    yield "preview-vertical", {}, "vertical", True, {}
    yield (
        "preview-spectrum",
        {"spectrum": True},
        "vertical",
        True,
        {"reactive": True},
    )
    yield (
        "texture",
        {"texture": "grain.png", "texture_blend": "overlay"},
        "vertical",
        False,
        {"texture": True},
    )
    yield (
        "color-punch",
        {"color_punch": 0.4, "saturation": 0.8},
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
    yield (
        "spectrum",
        {"spectrum": True},
        "vertical",
        False,
        {"reactive": True},
    )
    # Readouts on (the default, #82): the spectrum and the readout strip clips.
    yield "readouts", {}, "horizontal", False, {"reactive": True, "readouts": True}
    # Everything the default look draws (#83): linework still + nodes, spectrum, readouts.
    yield (
        "schematic",
        {},
        "horizontal",
        False,
        {"reactive": True, "readouts": True, "schematic": True},
    )


CASES = {name: rest for name, *rest in _cases()}


def _clips(extra: dict, size: tuple[int, int]) -> tuple[ReactiveClip, ...]:
    if not extra.get("reactive"):
        return ()
    clips = [ReactiveClip(Path("/tmp/vp/spectrum.mkv"), 65, 749)]
    if extra.get("readouts"):
        clips.append(ReactiveClip(Path("/tmp/vp/readouts.mkv"), 0, 120))
    if extra.get("schematic"):  # nodes: half-res, scaled up, under the rest
        clips.insert(0, ReactiveClip(Path("/tmp/vp/schematic.mkv"), 0, 0, size))
    return tuple(clips)


def _command(style, aspect_key, preview, extra) -> list[str]:
    track = _track(style)
    aspect = ASPECTS[aspect_key]
    window = (0.0, 60.0) if aspect.kind == "horizontal" else (8.0, 53.0)
    plan = plan_for(track, BEATMAP, window, preview)
    size = output_size(aspect, preview)
    assets = RenderAssets(
        hud=Path("/tmp/vp/hud.png"),
        texture=Path("/tmp/vp/texture.png") if extra.get("texture") else None,
        schematic=Path("/tmp/vp/schematic.png") if extra.get("schematic") else None,
        reactive=_clips(extra, size),
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
