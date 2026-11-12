"""Audio analysis: turn a beat into a beatmap (tempo, beats, energy)."""

from __future__ import annotations

import json
from pathlib import Path


def analyze_audio(audio: Path, sr: int = 22050) -> dict:
    import librosa
    import numpy as np

    y, sr = librosa.load(str(audio), sr=sr, mono=True)
    duration = float(librosa.get_duration(y=y, sr=sr))

    tempo, beat_times = librosa.beat.beat_track(y=y, sr=sr, units="time")
    tempo = float(np.atleast_1d(tempo)[0])
    beats = [round(float(b), 4) for b in beat_times]

    rms = librosa.feature.rms(y=y, frame_length=2048, hop_length=512)[0]
    rms_times = librosa.frames_to_time(np.arange(len(rms)), sr=sr, hop_length=512)
    if beats:
        energy = np.interp(beats, rms_times, rms)
        peak = float(energy.max()) or 1.0
        beat_energy = [round(float(e / peak), 4) for e in energy]
    else:
        beat_energy = []

    return {
        "audio": str(audio),
        "sr": sr,
        "duration": round(duration, 4),
        "tempo": round(tempo, 3),
        "beats": beats,
        "downbeats": beats[::4],
        "beat_energy": beat_energy,
    }


def load_beatmap(track, reanalyze: bool = False) -> dict:
    if track.beatmap_path.is_file() and not reanalyze:
        return json.loads(track.beatmap_path.read_text())

    beatmap = analyze_audio(track.audio)
    track.cache_dir.mkdir(parents=True, exist_ok=True)
    track.beatmap_path.write_text(json.dumps(beatmap, indent=2))
    return beatmap