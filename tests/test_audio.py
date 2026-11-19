import shutil
import wave
from pathlib import Path

import numpy as np
import pytest

from vp.audio import decode_mono

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")


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


def test_decode_mono_sample_count(tmp_path):
    audio = _write_wav(tmp_path / "a.wav", duration=2.0)
    samples = decode_mono(audio, 0.0, 1.0, sample_rate=8000)
    assert samples.dtype == np.float32
    assert abs(len(samples) - 8000) <= 80
    assert 0.6 < float(np.max(np.abs(samples))) <= 1.0
