from pathlib import Path

from vp.aspects import HORIZONTAL, SQUARE, VERTICAL
from vp.render import window_for
from vp.track import Track


def make_beatmap(duration=90.0, bpm=120.0, energy=None):
    beat = 60.0 / bpm
    count = int(duration / beat)
    beats = [round(i * beat, 4) for i in range(count)]
    if energy is None:
        energy = [0.5] * count
    return {
        "duration": duration,
        "tempo": bpm,
        "beats": beats,
        "downbeats": beats[::4],
        "beat_energy": energy,
        "onset_novelty": [0.5] * count,
        "bar_energy": [],
        "phrase_energy": [],
    }


def make_track(hook):
    return Track(
        root=Path("."),
        audio=Path("a.wav"),
        title="t",
        images=[Path("1.jpg")],
        hook=hook,
    )


def auto_hook():
    return {"mode": "auto", "target": 45.0, "start": None, "end": None}


def test_vertical_and_square_share_hook():
    beatmap = make_beatmap()
    track = make_track(auto_hook())
    assert window_for(track, beatmap, VERTICAL) == window_for(track, beatmap, SQUARE)
    assert window_for(track, beatmap, VERTICAL) != (0.0, 90.0)


def test_horizontal_is_always_full():
    beatmap = make_beatmap()
    track = make_track(auto_hook())
    assert window_for(track, beatmap, HORIZONTAL) == (0.0, 90.0)


def test_manual_hook_applies_to_vertical_not_horizontal():
    beatmap = make_beatmap()
    track = make_track(
        {"mode": "manual", "target": 45.0, "start": 10.0, "end": 55.0}
    )
    assert window_for(track, beatmap, VERTICAL) == (10.0, 55.0)
    assert window_for(track, beatmap, HORIZONTAL) == (0.0, 90.0)