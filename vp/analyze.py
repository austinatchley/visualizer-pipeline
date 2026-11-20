"""Audio analysis: turn a beat into a beatmap (tempo, beats, energy, novelty)."""

from __future__ import annotations

import json
import os
from pathlib import Path

from .config import ANALYSIS_VERSION
from .music import detect_key

KEY_HOP = 2048
OPENING_SECONDS = 4.0  # harmony the key tie-break listens to (see music.detect_key)


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


def audio_source(audio: Path) -> dict:
    """Identity of the analyzed audio file. A cached beatmap is reused only while
    this matches, so replacing the audio invalidates it (issue #43)."""
    stat = Path(audio).stat()
    return {"size": stat.st_size, "mtime_ns": stat.st_mtime_ns}


def analyze_key(y, sr: int) -> dict | None:
    """Musical key from harmonic chroma; the opening seconds of the harmony
    break the tie between a major key and its relative minor."""
    import librosa
    import numpy as np

    if len(y) < sr or not np.any(y):
        return None
    harmonic = librosa.effects.harmonic(y, margin=2.0)
    chroma = librosa.feature.chroma_cqt(y=harmonic, sr=sr, hop_length=KEY_HOP)
    rms = librosa.feature.rms(y=harmonic, hop_length=KEY_HOP)[0]
    loud = rms > 0.25 * float(np.percentile(rms, 95))
    start = int(np.argmax(loud)) if loud.any() else 0
    span = max(1, int(OPENING_SECONDS * sr / KEY_HOP))
    return detect_key(chroma.mean(axis=1), chroma[:, start : start + span].mean(axis=1))


def analyze_audio(audio: Path, sr: int = 22050, bpm: float | None = None) -> dict:
    import librosa
    import numpy as np

    y, sr = librosa.load(str(audio), sr=sr, mono=True)
    duration = float(librosa.get_duration(y=y, sr=sr))

    if bpm:
        tempo, beat_times = librosa.beat.beat_track(y=y, sr=sr, bpm=float(bpm), units="time")
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
            "source": audio_source(audio),
            "bpm_hint": float(bpm) if bpm else None,
            "sr": sr,
            "duration": round(duration, 4),
            "tempo": round(tempo, 3),
            "beats": beats,
            "downbeats": beats[::4],
            "beat_energy": beat_energy,
            "onset_novelty": onset_novelty,
            "key": analyze_key(y, sr),
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
                    new_novelty.append((sample(novelty, i) + sample(novelty, i + 1)) / 2)
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


def beatmap_cache_path(track, bpm: float | None = None) -> Path:
    """`.vp/beatmap.json`, or `.vp/beatmap-bpm<N>.json` for a BPM-hinted analysis."""
    if not bpm:
        return track.beatmap_path
    return track.cache_dir / f"beatmap-bpm{float(bpm):g}.json"


def _cache_is_valid(cached: object, audio: Path, bpm: float | None) -> bool:
    if not isinstance(cached, dict):
        return False
    try:
        source = audio_source(audio)
    except OSError:
        return False
    return (
        cached.get("analysis_version") == ANALYSIS_VERSION
        and cached.get("source") == source
        and cached.get("bpm_hint") == (float(bpm) if bpm else None)
    )


def _read_cache(path: Path) -> object:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def _write_cache(path: Path, beatmap: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(beatmap, indent=2))
    os.replace(tmp, path)


def load_beatmap(track, reanalyze: bool = False) -> dict:
    """Load the cached beatmap, re-analyzing when it is missing, from an older
    analysis version, for different audio, or for a different BPM hint."""
    overrides = getattr(track, "overrides", {}) or {}
    bpm = float(overrides["bpm"]) if overrides.get("bpm") else None
    multiplier = float(overrides.get("tempo_multiplier") or 1.0)

    path = beatmap_cache_path(track, bpm)
    cached = None if reanalyze else _read_cache(path)
    if _cache_is_valid(cached, track.audio, bpm):
        beatmap = cached
    else:
        beatmap = analyze_audio(track.audio, bpm=bpm)
        _write_cache(path, beatmap)

    if multiplier != 1.0:
        beatmap = apply_tempo_multiplier(beatmap, multiplier)
    return beatmap
