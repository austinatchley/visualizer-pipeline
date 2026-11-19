import json
import shutil
import subprocess
from fractions import Fraction
from pathlib import Path

import pytest

from vp.analyze import load_beatmap
from vp.aspects import HORIZONTAL, VERTICAL
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
    variants = plan_variants(track, beatmap, out_dir, ["vertical", "square", "horizontal"])
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


@pytest.fixture(scope="module")
def styled_renders(tmp_path_factory):
    _require(SHORT)
    out_dir = tmp_path_factory.mktemp("styled")
    track = load_track(SHORT)
    beatmap = load_beatmap(track)

    punchy = load_track(SHORT, overrides={"beat_punch": 0.1})
    punchy_path = out_dir / "punchy_9x16.mp4"
    render(punchy, VERTICAL, beatmap, punchy_path, window=(0.0, beatmap["duration"]))

    overlays = load_track(
        SHORT,
        overrides={
            "spectrum": True,
            "vignette": 0.5,
            "grain": 0.4,
        },
    )
    overlays_path = out_dir / "overlays_16x9.mp4"
    render(overlays, HORIZONTAL, beatmap, overlays_path, window=(0.0, beatmap["duration"]))

    vgm = load_track(SHORT, overrides={"preset": "vgm"})
    vgm_path = out_dir / "vgm_9x16.mp4"
    render(vgm, VERTICAL, beatmap, vgm_path, window=(0.0, beatmap["duration"]))

    analog = load_track(SHORT, overrides={"grade": "analog"})
    analog_path = out_dir / "analog_9x16.mp4"
    render(analog, VERTICAL, beatmap, analog_path, window=(0.0, beatmap["duration"]))

    return punchy_path, overlays_path, vgm_path, analog_path


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


def test_punchy_preset_renders(styled_renders):
    punchy, *_ = styled_renders
    video = _video(_probe(punchy))
    assert (video["width"], video["height"]) == (1080, 1920)
    assert video["pix_fmt"] == "yuv420p"


def test_spectrum_overlay_renders(styled_renders):
    _, overlays, *_ = styled_renders
    video = _video(_probe(overlays))
    assert (video["width"], video["height"]) == (1920, 1080)
    assert float(_probe(overlays)["format"]["duration"]) <= 12.0 + 0.15


def test_vgm_preset_renders(styled_renders):
    _, _, vgm, *_ = styled_renders
    video = _video(_probe(vgm))
    assert (video["width"], video["height"]) == (1080, 1920)
    assert video["pix_fmt"] == "yuv420p"
    assert Fraction(_video(_probe(vgm))["r_frame_rate"]) == 30


def test_analog_grade_renders(styled_renders):
    *_, analog = styled_renders
    video = _video(_probe(analog))
    assert (video["width"], video["height"]) == (1080, 1920)
    assert video["pix_fmt"] == "yuv420p"
    assert Fraction(_video(_probe(analog))["r_frame_rate"]) == 30


def _extract_frame(path: Path, out: Path) -> None:
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-i", str(path), "-frames:v", "1", str(out)],
        check=True,
        capture_output=True,
    )


def test_saturation_zero_is_monochrome(tmp_path):
    """`saturation: 0` must render true grayscale (R=G=B) across sampled frames."""
    _require(SHORT)
    # The default color_punch must not add color back; the spectrum's accent is
    # colored by design and drawn above the grade, so it is off here.
    track = load_track(SHORT, overrides={"saturation": 0.0, "spectrum": False})
    beatmap = load_beatmap(track)
    out = tmp_path / "mono.mp4"
    render(
        track,
        VERTICAL,
        beatmap,
        out,
        window=(0.0, beatmap["duration"]),
        preview=True,
    )
    frame = tmp_path / "f.ppm"
    _extract_frame(out, frame)
    from PIL import Image

    img = Image.open(frame).convert("RGB")
    width, height = img.size
    px = img.load()
    for y in range(0, height, 7):
        for x in range(0, width, 7):
            r, g, b = px[x, y]
            assert max(r, g, b) - min(r, g, b) <= 8, (x, y, (r, g, b))


def test_color_punch_renders(tmp_path):
    """`color_punch` emits a per-frame eq expression; it must not crash ffmpeg."""
    _require(SHORT)
    track = load_track(SHORT, overrides={"color_punch": 0.3})
    beatmap = load_beatmap(track)
    out = tmp_path / "punch.mp4"
    render(
        track,
        VERTICAL,
        beatmap,
        out,
        window=(0.0, beatmap["duration"]),
        preview=True,
    )
    video = _video(_probe(out))
    assert video["pix_fmt"] == "yuv420p"
    assert (video["width"], video["height"]) == (270, 480)


def test_spectrum_layer_renders_with_two_pass_progress(tmp_path):
    """Issue #81: the reactive pass feeds the main encode; progress spans both."""
    _require(SHORT)
    track = load_track(SHORT, overrides={"spectrum": True})
    beatmap = load_beatmap(track)
    out = tmp_path / "spectrum.mp4"
    seen: list[tuple[float, str]] = []
    render(
        track,
        VERTICAL,
        beatmap,
        out,
        window=(0.0, beatmap["duration"]),
        preview=True,
        progress=lambda pct, stage: seen.append((pct, stage)),
    )
    video = _video(_probe(out))
    assert (video["width"], video["height"]) == (270, 480)
    stages = [stage for _, stage in seen]
    assert stages[0] == "drawing" and stages[-1] == "done"
    pcts = [pct for pct, _ in seen]
    assert pcts == sorted(pcts) and pcts[-1] == 100.0


def test_spectrum_render_can_be_cancelled_in_the_overlay_pass(tmp_path):
    from vp.render import RenderCancelled

    _require(SHORT)
    track = load_track(SHORT, overrides={"spectrum": True})
    beatmap = load_beatmap(track)
    with pytest.raises(RenderCancelled):
        render(
            track,
            VERTICAL,
            beatmap,
            tmp_path / "x.mp4",
            window=(0.0, beatmap["duration"]),
            preview=True,
            cancel=lambda: True,
        )
