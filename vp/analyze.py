"""Audio analysis: turn a beat into a beatmap (tempo, beats, energy, novelty)."""

from __future__ import annotations

import json
from pathlib import Path

from .config import ANALYSIS_VERSION


def _group_mean(values: list[float], size: int) -> list[float]:
    return [
        round(sum(values[i : i + size]) / len(values[i : i + size]), 4)
        for i in range(0, len(values), size)
        if values[i : i + size]
    ]


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

    onset_env = librosa.onset.onset_strength(y=y, sr=sr)
    onset_times = librosa.frames_to_time(np.arange(len(onset_env)), sr=sr)

    if beats:
        raw_energy = np.interp(beats, rms_times, rms)
        peak = float(raw_energy.max()) or 1.0
        beat_energy = [round(float(e / peak), 4) for e in raw_energy]

        raw_novelty = np.interp(beats, onset_times, onset_env)
        npeak = float(raw_novelty.max()) or 1.0
        onset_novelty = [round(float(v / npeak), 4) for v in raw_novelty]
    else:
        beat_energy = []
        onset_novelty = []

    return {
        "analysis_version": ANALYSIS_VERSION,
        "audio": str(audio),
        "sr": sr,
        "duration": round(duration, 4),
        "tempo": round(tempo, 3),
        "beats": beats,
        "downbeats": beats[::4],
        "beat_energy": beat_energy,
        "bar_energy": _group_mean(beat_energy, 4),
        "phrase_energy": _group_mean(beat_energy, 16),
        "onset_novelty": onset_novelty,
    }


def load_beatmap(track, reanalyze: bool = False) -> dict:
    if track.beatmap_path.is_file() and not reanalyze:
        cached = json.loads(track.beatmap_path.read_text())
        if cached.get("analysis_version") == ANALYSIS_VERSION:
            return cached

    beatmap = analyze_audio(track.audio)
    track.cache_dir.mkdir(parents=True, exist_ok=True)
    track.beatmap_path.write_text(json.dumps(beatmap, indent=2))
    return beatmap