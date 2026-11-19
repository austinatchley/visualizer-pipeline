"""Overlays composite above the grade (issue #86).

The finish chain (grade, saturation, vignette, grain, contrast) treats the base
footage only; the HUD, watermark and reactive layers land crisp on top of it.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from vp.aspects import ASPECTS
from vp.config import resolve_style
from vp.reactive import ReactiveClip
from vp.render import RenderAssets, build_command, build_graph, output_size, plan_for, run_ffmpeg
from vp.track import Track

BEATS = [round(i * 0.5, 4) for i in range(40)]  # 20 s at 120 BPM
BEATMAP = {
    "duration": 20.0,
    "tempo": 120.0,
    "beats": BEATS,
    "downbeats": BEATS[::4],
    "beat_energy": [0.6] * len(BEATS),
    "onset_novelty": [0.5] * len(BEATS),
}

# Every footage treatment at full strength, so a graded overlay could not hide.
HEAVY = {
    "grade": "sunset",
    "saturation": 0.0,
    "vignette": 1.0,
    "grain": 1.0,
    "contrast": 1.5,
    "fade_in": 0.0,
    "fade_out": 0.0,
}
GRADE_TOKENS = ("eq=", "vignette=", "noise=", "colorbalance", "curves", "colortemperature")


def _track(root: Path, images: list[Path], style: dict) -> Track:
    return Track(
        root=root,
        audio=root / "audio.wav",
        title="Demo",
        images=images,
        style=resolve_style(style),
        hook={"mode": "full", "target": 45.0, "start": None, "end": None},
        overrides={"cuts": None},
    )


def test_grade_runs_before_every_overlay():
    root = Path("/tracks/demo")
    track = _track(root, [root / "images/01.jpg"], {**HEAVY, "grade": "night"})
    size = output_size(ASPECTS["vertical"])
    clip = ReactiveClip(path=root / "re.mkv", x=0, y=0)
    assets = RenderAssets(hud=root / "hud.png", reactive=clip)
    plan = plan_for(track, BEATMAP, (0.0, 20.0))
    chains = build_graph(track, plan, BEATMAP, size, assets).filter_complex.split(";")

    first_overlay = next(i for i, c in enumerate(chains) if "overlay=" in c)
    graded = [i for i, c in enumerate(chains) if any(t in c for t in GRADE_TOKENS)]
    assert graded and max(graded) < first_overlay
    final = next(c for c in chains if c.endswith("[vout]"))
    assert not any(t in final for t in GRADE_TOKENS)


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
def test_hud_pixels_survive_a_heavy_grade(tmp_path):
    hud_color = (200, 60, 120)
    Image.new("RGB", (64, 64), (40, 120, 200)).save(tmp_path / "01.png")

    track = _track(tmp_path, [tmp_path / "01.png"], HEAVY)
    plan = plan_for(track, BEATMAP, (0.0, 20.0), preview=True)
    width, height = size = output_size(ASPECTS["vertical"], preview=True)

    hud = Image.new("RGBA", size, (0, 0, 0, 0))
    box = (width // 2 - 40, height // 2 - 40, width // 2 + 40, height // 2 + 40)
    hud.paste((*hud_color, 255), box)
    hud.save(tmp_path / "hud.png")

    graph = build_graph(track, plan, BEATMAP, size, RenderAssets(hud=tmp_path / "hud.png"), True)
    out = tmp_path / "out.mp4"
    run_ffmpeg(build_command(graph, 30, plan.duration, out, preview=True), plan.duration)

    frame = tmp_path / "frame.png"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-ss", "2.0", "-i", str(out), "-frames:v", "1",
         "-pix_fmt", "rgb24", str(frame)],
        check=True,
    )  # fmt: skip
    pixels = np.asarray(Image.open(frame).convert("RGB"), dtype=float)

    cx, cy = width // 2, height // 2
    hud_patch = pixels[cy - 20 : cy + 20, cx - 20 : cx + 20].reshape(-1, 3).mean(axis=0)
    assert np.allclose(hud_patch, hud_color, atol=12), hud_patch

    # The footage around it really was graded: saturation 0 leaves it gray.
    base = pixels[10:40, 10:40].reshape(-1, 3).mean(axis=0)
    assert base.max() - base.min() < 12, base
