"""Data readout strip: beat state, clock, layout, and drawing (#82)."""

import numpy as np
import pytest

from vp import readouts
from vp.config import resolve_style

BEATMAP = {
    "duration": 257.0,
    "tempo": 99.4,
    "beats": [1.0, 1.5, 2.0, 2.5, 3.0, 3.5],
    "downbeats": [1.0, 3.0],
    "key": {"tonic": 9, "mode": "minor", "name": "A minor", "confidence": 0.6},
}


def test_beat_state_counts_the_bar_and_flashes_on_beats():
    times = np.array([0.5, 1.0, 1.6, 2.0, 2.9, 3.0, 3.5])
    pos, flash = readouts.beat_state(BEATMAP["beats"], BEATMAP["downbeats"], times)
    assert pos.tolist() == [-1, 0, 1, 2, 3, 0, 1]
    assert flash[0] == 0 and flash[1] == pytest.approx(1.0) and flash[5] == pytest.approx(1.0)
    assert 0 < flash[2] < flash[1]


def test_beat_state_without_beats():
    pos, flash = readouts.beat_state([], [], np.array([0.0, 1.0]))
    assert pos.tolist() == [-1, -1] and flash.tolist() == [0.0, 0.0]


@pytest.mark.parametrize("seconds,text", [(0, "00:00"), (11.9, "00:11"), (257, "04:17")])
def test_format_clock(seconds, text):
    assert readouts.format_clock(seconds) == text


def test_readout_frames_use_track_time():
    bands = np.zeros((60, 4), np.float32)
    frames = readouts.readout_frames(BEATMAP, (1.0, 3.0), 30, bands)
    assert frames.clock[0] == 1.0 and frames.clock[30] == pytest.approx(2.0)
    assert frames.beat_pos[0] == 0


def test_wants_readouts_follows_the_toggles():
    assert readouts.wants_readouts(resolve_style())  # on by default
    off = {k: False for k in ("show_clock", "show_tempo", "show_beats", "show_meters")}
    assert not readouts.wants_readouts(resolve_style(flags=off))


@pytest.mark.parametrize("size", [(1920, 1080), (1080, 1920), (1080, 1080), (270, 480)])
def test_strip_stays_inside_the_frame(size):
    painter = readouts.ReadoutPainter(size, resolve_style(), BEATMAP)
    r = painter.rect
    assert r.x >= 0 and r.y >= 0 and r.x + r.width <= size[0] and r.y + r.height <= size[1]
    assert r.height < size[1] * 0.3  # a strip, not the frame
    assert painter.frame(10.0, 0, 1.0, np.full(4, 0.5)).size == (r.width, r.height)


def test_static_text_comes_from_the_beatmap():
    painter = readouts.ReadoutPainter((1920, 1080), resolve_style(), BEATMAP)
    assert (painter.bpm_text, painter.key_text, painter.total) == ("99", "A MIN", "04:17")
    blank = readouts.ReadoutPainter((1920, 1080), resolve_style(), {})
    assert (blank.bpm_text, blank.key_text) == ("--", "--")


def test_frames_move_with_clock_beats_and_meters():
    painter = readouts.ReadoutPainter((1920, 1080), resolve_style(), BEATMAP)
    base = np.asarray(painter.frame(10.0, 0, 1.0, np.zeros(4)))
    assert not np.array_equal(base, np.asarray(painter.frame(11.0, 0, 1.0, np.zeros(4))))
    assert not np.array_equal(base, np.asarray(painter.frame(10.0, 2, 1.0, np.zeros(4))))
    assert not np.array_equal(base, np.asarray(painter.frame(10.0, 0, 1.0, np.ones(4))))
    again = np.asarray(painter.frame(10.0, 0, 1.0, np.zeros(4)))
    assert np.array_equal(base, again)  # pure


def test_toggles_shrink_the_strip():
    full = readouts.ReadoutPainter((1920, 1080), resolve_style(), BEATMAP)
    right_only = readouts.ReadoutPainter(
        (1920, 1080), resolve_style(flags={"show_clock": False, "show_beats": False}), BEATMAP
    )
    assert right_only.left is None and right_only.right is not None
    assert right_only.rect.width < full.rect.width


def test_painter_pickles_without_fonts():
    import pickle

    painter = readouts.ReadoutPainter((540, 960), resolve_style(), BEATMAP)
    painter.frame(1.0, 0, 1.0, np.zeros(4))
    clone = pickle.loads(pickle.dumps(painter))
    a = np.asarray(painter.frame(1.0, 1, 0.5, np.full(4, 0.3)))
    assert np.array_equal(a, np.asarray(clone.frame(1.0, 1, 0.5, np.full(4, 0.3))))
