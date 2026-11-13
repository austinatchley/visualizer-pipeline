import shutil
import wave
from pathlib import Path

import numpy as np
import pytest

from vp.waveform import (
    SAMPLE_RATE,
    amplitude_envelopes,
    bar_count,
    build_strip,
    decode_mono,
    energy_curve,
    peak_envelopes,
)

pytestmark = pytest.mark.skipif(
    shutil.which("ffmpeg") is None, reason="ffmpeg not installed"
)


def _write_wav(path: Path, duration: float = 1.0, sr: int = 8000) -> Path:
    n = int(sr * duration)
    t = np.arange(n) / sr
    data = (0.8 * np.sin(2 * np.pi * 440 * t) * 32767).astype(np.int16)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(data.tobytes())
    return path


def test_peak_envelopes_normalized():
    peaks = peak_envelopes(np.array([1.0, -2.0, 3.0, -1.0, 0.5]), num_bars=5)
    assert peaks[2] == 1.0
    assert 0 <= peaks.max() <= 1.0
    assert peaks.min() >= 0.0


def test_peak_envelopes_empty():
    peaks = peak_envelopes(np.array([]), num_bars=8)
    assert list(peaks) == [0.0] * 8


def test_bar_count_targets_four_pixels_and_caps():
    assert bar_count(3840) == 960
    assert bar_count(256) == 64
    assert bar_count(48) == 48
    assert bar_count(100_000) == 1600


def test_amplitude_envelopes_contrast_and_range():
    samples = np.concatenate(
        [np.full(4000, 0.01, dtype=np.float32), np.full(4000, 0.5, dtype=np.float32)]
    )
    amplitudes = amplitude_envelopes(samples, 20)
    assert amplitudes[-1] > amplitudes[0] * 4
    assert amplitudes.max() <= 1.0
    assert amplitudes.min() >= 0.0
    assert np.all(amplitudes > 0)


def test_amplitude_envelopes_silence_is_zero():
    amplitudes = amplitude_envelopes(np.zeros(1000, dtype=np.float32), 4)
    assert list(amplitudes) == [0.0] * 4


def test_energy_curve_tracks_beats():
    beatmap = {
        "beats": [0.0, 0.25, 0.5, 0.75, 1.0],
        "beat_energy": [0.0, 0.0, 1.0, 1.0, 0.0],
    }
    curve = energy_curve(beatmap, (0.0, 1.0), num_bars=2)
    assert curve[0] == pytest.approx(0.0)
    assert curve[1] == pytest.approx(1.0)


def test_energy_curve_interpolates_between_beats():
    beatmap = {"beats": [0.0, 0.4], "beat_energy": [0.0, 1.0]}
    curve = energy_curve(beatmap, (0.0, 1.0), num_bars=5)
    assert curve[0] == pytest.approx(0.25)
    assert curve[1] == pytest.approx(0.75)
    assert curve[2] == pytest.approx(1.0)


def test_energy_curve_defaults_when_no_beats():
    curve = energy_curve({"beats": [], "beat_energy": []}, (0.0, 2.0), num_bars=4)
    assert list(curve) == [0.5] * 4


def test_decode_mono_sample_count(tmp_path):
    audio = _write_wav(tmp_path / "a.wav", duration=1.0)
    samples = decode_mono(audio, 0.0, 1.0, sample_rate=SAMPLE_RATE)
    assert len(samples) == pytest.approx(SAMPLE_RATE, abs=2)
    assert np.abs(samples).max() > 0.5


def test_build_strip_dimensions_and_bars(tmp_path):
    audio = _write_wav(tmp_path / "a.wav", duration=1.0)
    beatmap = {"beats": [0.0], "beat_energy": [1.0]}
    img = build_strip(audio, (0.0, 1.0), width=256, height=64, beatmap=beatmap)
    assert img.size == (256, 64)
    assert img.mode == "RGBA"
    alpha = np.asarray(img.getchannel("A"))
    assert alpha.max() > 0
    assert np.count_nonzero(alpha > 0) > 20


def test_build_strip_is_deterministic(tmp_path):
    audio = _write_wav(tmp_path / "a.wav", duration=1.0)
    beatmap = {"beats": [0.0, 0.5], "beat_energy": [0.2, 1.0]}
    first = build_strip(audio, (0.0, 1.0), width=512, height=80, beatmap=beatmap)
    second = build_strip(audio, (0.0, 1.0), width=512, height=80, beatmap=beatmap)
    assert first.tobytes() == second.tobytes()
