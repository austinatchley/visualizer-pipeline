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


def _aggregate(beatmap: dict) -> dict:
    energy = list(beatmap.get("beat_energy") or [])
    beatmap["bar_energy"] = _group_mean(energy, 4)
    beatmap["phrase_energy"] = _group_mean(energy, 16)
    return beatmap


def analyze_audio(audio: Path, sr: int = 22050, bpm: float | None = None) -> dict:
    import librosa
    import numpy as np

    y, sr = librosa.load(str(audio), sr=sr, mono=True)
    duration = float(librosa.get_duration(y=y, sr=sr))

    if bpm:
        tempo, beat_times = librosa.beat.beat_track(
            y=y, sr=sr, bpm=float(bpm), units="time"
        )
    else:
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

    return _aggregate(
        {
            "analysis_version": ANALYSIS_VERSION,
            "audio": str(audio),
            "sr": sr,
            "duration": round(duration, 4),
            "tempo": round(tempo, 3),
            "beats": beats,
            "downbeats": beats[::4],
            "beat_energy": beat_energy,
            "onset_novelty": onset_novelty,
        }
    )


def apply_tempo_multiplier(beatmap: dict, multiplier: float) -> dict:
    multiplier = float(multiplier)
    if multiplier == 1.0:
        return beatmap

    data = dict(beatmap)
    beats = [float(b) for b in data.get("beats") or []]
    energy = list(data.get("beat_energy") or [])
    novelty = list(data.get("onset_novelty") or [])
    duration = float(data.get("duration") or 0.0)

    def sample(values: list, index: int) -> float:
        return float(values[index]) if index < len(values) else 0.5

    if multiplier == 2.0 and beats:
        new_beats: list[float] = []
        new_energy: list[float] = []
        new_novelty: list[float] = []
        for i, beat in enumerate(beats):
            new_beats.append(round(beat, 4))
            new_energy.append(sample(energy, i))
            new_novelty.append(sample(novelty, i))
            if i + 1 < len(beats):
                mid = (beat + beats[i + 1]) / 2.0
                if mid < duration:
                    new_beats.append(round(mid, 4))
                    new_energy.append((sample(energy, i) + sample(energy, i + 1)) / 2)
                    new_novelty.append(
                        (sample(novelty, i) + sample(novelty, i + 1)) / 2
                    )
        beats, energy, novelty = new_beats, new_energy, new_novelty
    elif multiplier == 0.5 and beats:
        beats = beats[::2]
        energy = energy[::2]
        novelty = novelty[::2]

    data["beats"] = beats
    data["beat_energy"] = energy
    data["onset_novelty"] = novelty
    data["downbeats"] = beats[::4]
    data["tempo"] = round(float(data.get("tempo") or 0.0) * multiplier, 3)
    return _aggregate(data)


def _analyze_and_cache(track) -> dict:
    beatmap = analyze_audio(track.audio)
    track.cache_dir.mkdir(parents=True, exist_ok=True)
    track.beatmap_path.write_text(json.dumps(beatmap, indent=2))
    return beatmap


def load_beatmap(track, reanalyze: bool = False) -> dict:
    overrides = getattr(track, "overrides", {}) or {}
    bpm = overrides.get("bpm")
    multiplier = float(overrides.get("tempo_multiplier") or 1.0)

    if bpm:
        beatmap = analyze_audio(track.audio, bpm=float(bpm))
    elif track.beatmap_path.is_file() and not reanalyze:
        cached = json.loads(track.beatmap_path.read_text())
        beatmap = (
            cached
            if cached.get("analysis_version") == ANALYSIS_VERSION
            else _analyze_and_cache(track)
        )
    else:
        beatmap = _analyze_and_cache(track)

    if multiplier != 1.0:
        beatmap = apply_tempo_multiplier(beatmap, multiplier)
    return beatmap