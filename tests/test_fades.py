"""Start/end fades as real style keys (issue #45)."""

import re
import shutil
import subprocess
from pathlib import Path

import pytest
from PIL import Image, ImageStat

from vp.config import DEFAULT_STYLE, STYLE_FIELDS, resolve_style
from vp.render import _fade_filters, fade_times

ROOT = Path(__file__).resolve().parent.parent
SHORT = ROOT / "fixtures" / "d_short"


pytestmark = pytest.mark.usefixtures("small_exports")


def test_fade_keys_are_in_the_schema_with_previous_defaults():
    assert DEFAULT_STYLE["fade_in"] == 0.4
    assert DEFAULT_STYLE["fade_out"] == 0.8
    assert STYLE_FIELDS["fade_in"]["type"] == "float"
    assert resolve_style({"fade_in": 0, "fade_out": 2.5})["fade_out"] == 2.5


@pytest.mark.parametrize("key", ["fade_in", "fade_out"])
def test_fade_keys_are_validated(key):
    with pytest.raises(ValueError, match=key):
        resolve_style({key: 3.5})
    with pytest.raises(ValueError, match=key):
        resolve_style({key: -0.1})


def test_style_reads_stay_in_schema():
    """Every style key read anywhere in vp/ must exist in DEFAULT_STYLE, so no
    phantom knobs can appear outside the one style schema."""
    pattern = re.compile(r"""style(?:\.get\(|\[)\s*["']([a-z_]+)["']""")
    read = set()
    for path in (ROOT / "vp").rglob("*.py"):
        read |= set(pattern.findall(path.read_text()))
    assert read, "pattern found no style reads; update the test"
    assert read - set(DEFAULT_STYLE) == set()


def test_fade_times_pass_through_when_they_fit():
    assert fade_times(0.4, 0.8, 30.0) == (0.4, 0.8)


def test_fade_times_shrink_proportionally_on_short_clips():
    fade_in, fade_out = fade_times(1.0, 3.0, 2.0)
    assert fade_in == pytest.approx(0.5)
    assert fade_out == pytest.approx(1.5)
    assert 2.0 - fade_out >= 0  # fade-out never starts before the clip


def test_fade_times_clamp_negatives_and_zero_duration():
    assert fade_times(-1.0, 0.5, 10.0) == (0.0, 0.5)
    assert fade_times(0.4, 0.8, 0.0) == (0.0, 0.0)


def test_zero_fades_emit_no_filters():
    assert _fade_filters("fade", 0.0, 0.0, 10.0) == []
    assert _fade_filters("afade", 0.4, 0.0, 10.0) == ["afade=t=in:st=0:d=0.400"]
    assert _fade_filters("fade", 0.0, 0.8, 10.0) == ["fade=t=out:st=9.200:d=0.800"]


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
@pytest.mark.parametrize("fade_in, black", [(0.0, False), (0.4, True)])
def test_fade_in_controls_first_frame(tmp_path, fade_in, black):
    if not SHORT.is_dir():
        pytest.skip("fixtures missing; run scripts/make_fixtures.py")
    from vp.analyze import load_beatmap
    from vp.aspects import VERTICAL
    from vp.render import render
    from vp.track import load_track

    track = load_track(SHORT, overrides={"fade_in": fade_in, "show_title": False})
    beatmap = load_beatmap(track)
    out = render(track, VERTICAL, beatmap, tmp_path / "v.mp4", window=(0.0, 2.0))

    frame = tmp_path / "first.png"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-i", str(out), "-frames:v", "1", str(frame)],
        check=True,
    )
    mean = ImageStat.Stat(Image.open(frame).convert("L")).mean[0]
    assert (mean < 20) is black, mean


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
def test_long_fades_on_tiny_clip_render(tmp_path):
    if not SHORT.is_dir():
        pytest.skip("fixtures missing; run scripts/make_fixtures.py")
    from vp.analyze import load_beatmap
    from vp.aspects import VERTICAL
    from vp.render import render
    from vp.track import load_track

    track = load_track(SHORT, overrides={"fade_in": 3.0, "fade_out": 3.0})
    beatmap = load_beatmap(track)
    out = render(track, VERTICAL, beatmap, tmp_path / "v.mp4", window=(0.0, 0.6))
    assert out.stat().st_size > 0
