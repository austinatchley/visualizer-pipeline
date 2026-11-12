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