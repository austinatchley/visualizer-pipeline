import json
import shutil
import subprocess
from fractions import Fraction
from pathlib import Path

import pytest

from vp.analyze import load_beatmap
from vp.render import plan_variants, render
from vp.track import load_track

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "fixtures"
SHORT = FIXTURES / "d_short"
LONG = FIXTURES / "a_landscape"

pytestmark = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg/ffprobe not installed",
)


def _require(path: Path) -> None:
    if not path.is_dir():
        pytest.skip("fixtures missing; run scripts/make_fixtures.py")


def _probe(path: Path) -> dict:
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)],
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(result.stdout)


def _video(data: dict) -> dict:
    return next(stream for stream in data["streams"] if stream["codec_type"] == "video")


def _audio(data: dict) -> dict:
    return next(stream for stream in data["streams"] if stream["codec_type"] == "audio")


@pytest.fixture(scope="module")
def short_renders(tmp_path_factory):
    _require(SHORT)
    out_dir = tmp_path_factory.mktemp("short")
    track = load_track(SHORT)
    beatmap = load_beatmap(track)
    variants = plan_variants(
        track, beatmap, out_dir, ["vertical", "square", "horizontal"]
    )
    for variant in variants:
        render(track, variant.aspect, beatmap, variant.path, window=variant.window)
    return {variant.aspect.key: variant for variant in variants}


@pytest.fixture(scope="module")
def hook_render(tmp_path_factory):
    _require(LONG)
    out_dir = tmp_path_factory.mktemp("hook")
    track = load_track(LONG)
    beatmap = load_beatmap(track)
    variant = plan_variants(track, beatmap, out_dir, ["vertical"])[0]
    render(track, variant.aspect, beatmap, variant.path, window=variant.window)
    return variant


def test_resolutions(short_renders):
    expected = {
        "vertical": (1080, 1920),
        "square": (1080, 1080),
        "horizontal": (1920, 1080),
    }
    for key, size in expected.items():
        video = _video(_probe(short_renders[key].path))
        assert (video["width"], video["height"]) == size


def test_fps_and_duration(short_renders):
    for variant in short_renders.values():
        data = _probe(variant.path)
        assert Fraction(_video(data)["r_frame_rate"]) == 30
        expected = variant.window[1] - variant.window[0]
        assert abs(float(data["format"]["duration"]) - expected) <= 0.15


def test_audio_stream(short_renders):
    for variant in short_renders.values():
        audio = _audio(_probe(variant.path))
        assert audio["codec_name"] == "aac"
        assert int(audio["sample_rate"]) == 48000


def test_faststart_and_pixel_format(short_renders):
    for variant in short_renders.values():
        video = _video(_probe(variant.path))
        assert video["pix_fmt"] == "yuv420p"
        raw = variant.path.read_bytes()
        assert raw.find(b"moov") < raw.find(b"mdat")


def test_hook_window_and_alignment(hook_render):
    data = _probe(hook_render.path)
    expected = hook_render.window[1] - hook_render.window[0]

    assert 30 <= expected <= 60
    assert abs(float(data["format"]["duration"]) - expected) <= 0.15
    assert abs(float(_audio(data)["duration"]) - expected) <= 0.15