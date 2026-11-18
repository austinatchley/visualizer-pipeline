"""Spectrum math for the reactive spectrum layer (issue #81)."""

import numpy as np
import pytest

from vp import spectrum as sp

SR = sp.SAMPLE_RATE
FPS = 30


def _sine(freq: float, seconds: float, amp: float = 0.5) -> np.ndarray:
    t = np.arange(int(seconds * SR)) / SR
    return (amp * np.sin(2 * np.pi * freq * t)).astype(np.float32)


def test_filterbank_rows_are_normalized_and_cover_every_point():
    weights = sp.filterbank()
    assert weights.shape == (sp.POINTS, sp.FFT_SIZE // 2 + 1)
    assert np.allclose(weights.sum(axis=1), 1.0)


@pytest.mark.parametrize("freq", [60.0, 440.0, 3000.0])
def test_a_sine_peaks_at_its_frequency(freq):
    levels = sp.levels_db(_sine(freq, 1.0), FPS, 10, start_offset=0.3)
    peak = sp.point_freqs()[int(np.argmax(levels.mean(axis=0)))]
    assert abs(np.log2(peak / freq)) < 0.1  # within ~1/10 octave


def test_silence_reads_flat_at_the_floor():
    frames = sp.spectrum_frames(np.zeros(SR, np.float32), FPS, 20)
    for curve in (frames.main, frames.lag, frames.hold):
        assert curve.shape == (20, sp.POINTS)
        assert float(curve.max()) == 0.0


def test_loud_material_is_not_clipped_to_the_top():
    frames = sp.spectrum_frames(_sine(100.0, 2.0, amp=0.9), FPS, 40, start_offset=0.5)
    assert 0.8 < float(frames.main.max()) < 1.0


def test_ballistics_attack_fast_and_release_slow():
    step = np.zeros((40, 1), np.float32)
    step[10:20] = 1.0
    out = sp.ballistics(step, FPS)
    assert out[12, 0] > 0.95  # reaches the level within a couple of frames
    assert 0.3 < out[22, 0] < 0.9  # still falling a few frames after release
    assert out[39, 0] < 0.05


def test_peak_hold_stays_above_the_signal_and_decays():
    rng = np.random.default_rng(0)
    levels = rng.random((90, 8)).astype(np.float32)
    held = sp.peak_hold(levels, FPS)
    assert np.all(held >= levels - 1e-6)
    spike = np.zeros((60, 1), np.float32)
    spike[5] = 1.0
    held = sp.peak_hold(spike, FPS)
    assert held[6, 0] > 0.9 and held[59, 0] < held[30, 0] < held[6, 0]


def test_lag_curve_is_the_main_curve_shifted():
    levels = np.arange(30, dtype=np.float32)[:, None]
    shift = round(sp.LAG_S * FPS)
    lag = sp.lagged(levels, FPS)
    assert lag.shape == levels.shape
    assert lag[shift + 5, 0] == levels[5, 0]
    assert lag[0, 0] == levels[0, 0]


def test_spectrum_is_deterministic():
    rng = np.random.default_rng(1)
    noise = rng.normal(0, 0.2, SR * 2).astype(np.float32)
    a = sp.spectrum_frames(noise, FPS, 50)
    b = sp.spectrum_frames(noise, FPS, 50)
    assert np.array_equal(a.main, b.main) and np.array_equal(a.hold, b.hold)
