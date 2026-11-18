"""Reactive overlay pass: geometry, colors, and the encoded clip (issue #81)."""

import shutil
import subprocess

import numpy as np
import pytest
import soundfile as sf

from vp import reactive
from vp.config import resolve_style

needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not on PATH")


def _style(**kw):
    return resolve_style(flags={"spectrum": True, **kw})


def test_wants_reactive_only_when_a_layer_is_on():
    assert not reactive.wants_reactive(resolve_style())
    assert reactive.wants_reactive(_style())


@pytest.mark.parametrize(
    "value,expected", [("#d9708f", (217, 112, 143)), ("FFFFFF", (255, 255, 255))]
)
def test_parse_color(value, expected):
    assert reactive.parse_color(value) == expected


def test_accent_color_is_validated_and_normalized():
    assert resolve_style(flags={"accent_color": "FFAA00"})["accent_color"] == "#ffaa00"
    with pytest.raises(ValueError, match="accent_color|color"):
        resolve_style(flags={"accent_color": "pink"})


@pytest.mark.parametrize("size", [(1080, 1920), (1920, 1080), (1080, 1080), (270, 480)])
@pytest.mark.parametrize("y", [0.0, 0.5, 1.0])
def test_spectrum_box_stays_inside_the_frame(size, y):
    box = reactive.spectrum_box(size, _style(spectrum_y=y, spectrum_height=0.6))
    width, height = size
    assert 0 <= box.x and box.x + box.width <= width
    assert 0 <= box.y and box.y + box.height <= height


def _audio(tmp_path, seconds=9.0):
    sr = 44100
    t = np.arange(int(seconds * sr)) / sr
    # A pulse every half second so frames differ.
    signal = 0.4 * np.sin(2 * np.pi * 110 * t) * (np.sin(2 * np.pi * 2 * t) > 0)
    path = tmp_path / "a.wav"
    sf.write(path, signal.astype(np.float32), sr)
    return path


def _framemd5(path):
    out = subprocess.run(
        ["ffmpeg", "-loglevel", "error", "-i", str(path), "-f", "framemd5", "-"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return [line.rsplit(",", 1)[-1].strip() for line in out.splitlines() if line[:1].isdigit()]


@needs_ffmpeg
def test_parallel_drawing_matches_serial_bytes(tmp_path):
    audio, style, size = _audio(tmp_path), _style(), (270, 480)
    serial = reactive.render_reactive(audio, (0.5, 8.5), size, style, tmp_path / "s.mkv", workers=1)
    parallel = reactive.render_reactive(
        audio, (0.5, 8.5), size, style, tmp_path / "p.mkv", workers=2
    )
    a, b = _framemd5(serial.path), _framemd5(parallel.path)
    assert len(a) == 8 * 30 >= reactive.PARALLEL_MIN_FRAMES
    assert a == b
    assert len(set(a)) > 10  # the layer actually moves
    box = reactive.spectrum_box(size, style)
    assert (serial.x, serial.y) == (box.x, box.y)


@needs_ffmpeg
def test_cancel_stops_the_overlay_pass(tmp_path):
    with pytest.raises(reactive.ReactiveCancelled):
        reactive.render_reactive(
            _audio(tmp_path),
            (0.0, 8.0),
            (270, 480),
            _style(),
            tmp_path / "c.mkv",
            cancel=lambda: True,
            workers=1,
        )
