"""Audio decoding shared by the reactive layers."""

from __future__ import annotations

import subprocess
from pathlib import Path

import numpy as np


def decode_mono(path: Path, start: float, end: float, sample_rate: int) -> np.ndarray:
    """Decode a clip window to a mono float32 buffer via ffmpeg."""
    duration = float(end) - float(start)
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-nostdin",
        "-ss",
        f"{start:.3f}",
        "-t",
        f"{duration:.3f}",
        "-i",
        str(path),
        "-ac",
        "1",
        "-ar",
        str(sample_rate),
        "-f",
        "f32le",
        "pipe:1",
    ]
    proc = subprocess.run(cmd, capture_output=True)
    if proc.returncode != 0:
        tail = "\n".join(proc.stderr.decode(errors="replace").strip().splitlines()[-20:])
        raise RuntimeError(f"failed to decode audio: {tail}")
    return np.frombuffer(proc.stdout, dtype="<f4")
