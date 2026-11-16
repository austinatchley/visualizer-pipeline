from vp.analyze import apply_tempo_multiplier


def base_beatmap():
    beats = [i * 0.5 for i in range(40)]
    return {
        "duration": 20.0,
        "tempo": 120.0,
        "beats": beats,
        "downbeats": beats[::4],
        "beat_energy": [0.5] * 40,
        "onset_novelty": [0.4] * 40,
        "bar_energy": [],
        "phrase_energy": [],
    }


def test_multiplier_one_is_identity():
    beatmap = base_beatmap()
    assert apply_tempo_multiplier(beatmap, 1.0) is beatmap


def test_multiplier_double_inserts_beats():
    out = apply_tempo_multiplier(base_beatmap(), 2.0)
    assert out["tempo"] == 240.0
    assert len(out["beats"]) == 79
    assert out["beats"][1] == 0.25


def test_multiplier_half_decimates():
    out = apply_tempo_multiplier(base_beatmap(), 0.5)
    assert out["tempo"] == 60.0
    assert len(out["beats"]) == 20
    assert out["beats"][1] == 1.0
    assert out["downbeats"] == out["beats"][::4]


def test_multiplier_recomputes_aggregates():
    out = apply_tempo_multiplier(base_beatmap(), 0.5)
    assert len(out["bar_energy"]) == 5

# ---- beatmap cache (issue #43) ----

import json  # noqa: E402
import os  # noqa: E402
from types import SimpleNamespace  # noqa: E402

import pytest  # noqa: E402

from vp import analyze  # noqa: E402
from vp.config import ANALYSIS_VERSION  # noqa: E402


@pytest.fixture
def cache_track(tmp_path, monkeypatch):
    audio = tmp_path / "audio.wav"
    audio.write_bytes(b"RIFF-one")
    calls = []

    def fake_analyze(path, sr=22050, bpm=None):
        calls.append(bpm)
        return {
            "analysis_version": ANALYSIS_VERSION,
            "source": analyze.audio_source(path),
            "bpm_hint": float(bpm) if bpm else None,
            "duration": 20.0,
            "tempo": float(bpm or 92.3),
            "beats": [0.5, 1.0],
            "beat_energy": [0.5, 0.5],
            "onset_novelty": [0.5, 0.5],
        }

    monkeypatch.setattr(analyze, "analyze_audio", fake_analyze)
    track = SimpleNamespace(
        audio=audio,
        cache_dir=tmp_path / ".vp",
        beatmap_path=tmp_path / ".vp" / "beatmap.json",
        overrides={"bpm": None, "tempo_multiplier": 1.0},
    )
    return track, calls


def test_cache_is_reused(cache_track):
    track, calls = cache_track
    analyze.load_beatmap(track)
    analyze.load_beatmap(track)
    assert calls == [None]
    assert "audio" not in json.loads(track.beatmap_path.read_text())  # no abs path


def test_replacing_audio_invalidates_cache(cache_track):
    track, calls = cache_track
    analyze.load_beatmap(track)
    st = track.audio.stat()
    track.audio.write_bytes(b"RIFF-two-longer")
    os.utime(track.audio, ns=(st.st_atime_ns, st.st_mtime_ns))  # same mtime, new size
    analyze.load_beatmap(track)
    assert calls == [None, None]

    os.utime(track.audio, ns=(st.st_atime_ns, st.st_mtime_ns + 10**9))  # touched
    analyze.load_beatmap(track)
    assert calls == [None, None, None]


def test_bpm_hint_is_cached_separately(cache_track):
    track, calls = cache_track
    analyze.load_beatmap(track)
    track.overrides["bpm"] = 140.0
    assert analyze.load_beatmap(track)["tempo"] == 140.0
    assert analyze.load_beatmap(track)["tempo"] == 140.0  # cached, no re-analysis
    assert calls == [None, 140.0]
    assert (track.cache_dir / "beatmap-bpm140.json").is_file()

    track.overrides["bpm"] = None
    assert analyze.load_beatmap(track)["tempo"] == 92.3  # plain cache untouched
    track.overrides["bpm"] = 150.0
    analyze.load_beatmap(track)
    assert calls == [None, 140.0, 150.0]


@pytest.mark.parametrize(
    "content",
    ["{not json", "[1, 2]", json.dumps({"analysis_version": ANALYSIS_VERSION - 1})],
    ids=["corrupt", "not-a-dict", "old-version"],
)
def test_bad_cache_is_reanalyzed(cache_track, content):
    track, calls = cache_track
    track.cache_dir.mkdir()
    track.beatmap_path.write_text(content)
    analyze.load_beatmap(track)
    assert calls == [None]
    assert json.loads(track.beatmap_path.read_text())["analysis_version"] == ANALYSIS_VERSION


def test_reanalyze_flag_bypasses_cache(cache_track):
    track, calls = cache_track
    analyze.load_beatmap(track)
    analyze.load_beatmap(track, reanalyze=True)
    assert calls == [None, None]


def test_tempo_multiplier_applies_to_cached_beatmap(cache_track):
    track, calls = cache_track
    analyze.load_beatmap(track)
    track.overrides["tempo_multiplier"] = 2.0
    assert analyze.load_beatmap(track)["tempo"] == pytest.approx(184.6)
    assert calls == [None]
