"""Schematic background: seeded linework and pulsing nodes (#83)."""

import pickle

import numpy as np
import pytest

from vp import schematic
from vp.config import resolve_style

SIZE = (1920, 1080)


def test_same_seed_same_board_and_different_seeds_differ():
    a = schematic.generate(SIZE, seed=11, density=0.5)
    assert a == schematic.generate(SIZE, seed=11, density=0.5)
    assert a.traces != schematic.generate(SIZE, seed=12, density=0.5).traces
    assert a.traces and a.nodes and len(a.nodes) == 2 * len(a.traces)


def test_seed_for_is_stable_across_processes():
    # crc32, not Python's per-process salted hash()
    assert schematic.seed_for("the-tip") == 3584313585


def test_preview_size_draws_the_same_board_scaled():
    full = schematic.generate(SIZE, seed=5, density=0.5)
    quarter = schematic.generate((480, 270), seed=5, density=0.5)
    assert len(full.traces) == len(quarter.traces)
    for big, small in zip(full.traces, quarter.traces, strict=True):
        assert np.allclose(np.array(big) / 4, np.array(small))


def test_density_scales_the_trace_count():
    counts = [len(schematic.generate(SIZE, 3, d).traces) for d in (0.2, 0.5, 1.0)]
    assert counts[0] < counts[1] < counts[2]


def test_traces_thin_out_toward_the_middle():
    def along(trace):  # points every few pixels along each segment
        return np.concatenate(
            [np.linspace(a, b, 8) for a, b in zip(trace, trace[1:], strict=False)]
        )

    pts = np.concatenate(
        [along(t) for seed in range(8) for t in schematic.generate(SIZE, seed, 0.5).traces]
    )
    d = np.hypot(pts[:, 0] / SIZE[0] - 0.5, pts[:, 1] / SIZE[1] - 0.5) / 0.5

    def share(lo, hi):  # fraction of trace length per unit of area in a ring
        return ((d >= lo) & (d < hi)).mean() / (hi**2 - lo**2)

    assert share(0.0, 0.3) < 0.1 * share(0.6, 0.9)  # the middle stays clear
    assert share(0.3, 0.6) > 0.2 * share(0.6, 0.9)  # a gradient, not a hard hole


@pytest.mark.parametrize("size", [(1920, 1080), (1080, 1920), (270, 480)])
def test_linework_is_full_frame_at_the_requested_opacity(size):
    board = schematic.generate(size, 1, 0.5)
    alpha = np.asarray(schematic.draw_linework(board, 0.24))[..., 3]
    assert alpha.shape == (size[1], size[0])
    assert 0 < alpha.max() <= round(255 * 0.24)
    assert np.asarray(schematic.draw_linework(board, 0.0))[..., 3].max() == 0


def _painter():
    return schematic.NodePainter(schematic.generate(SIZE, 2, 0.5), (217, 112, 143))


def test_a_downbeat_frame_is_brighter_than_between_beats():
    painter = _painter()
    bands = np.full(4, 0.4)
    between = np.asarray(painter.frame(bands, 0.05, 2))[..., 3].astype(int).sum()
    downbeat = np.asarray(painter.frame(bands, 1.0, 0))[..., 3].astype(int).sum()
    assert downbeat > 1.5 * between
    assert painter.size == (960, 540)


def test_band_motion_is_capped_at_half_strength():
    painter = _painter()
    levels = painter.levels(np.ones(4), flash=1.0, downbeat=False)
    assert levels.max() == pytest.approx(schematic.BAND_STRENGTH)
    on_beat = painter.levels(np.zeros(4), flash=1.0, downbeat=True)
    assert set(np.unique(on_beat)) == {0.0, 1.0}  # only the downbeat nodes, at full


def test_node_frames_are_pure_and_pickle():
    painter = _painter()
    a = np.asarray(painter.frame(np.full(4, 0.7), 0.5, 1))
    clone = pickle.loads(pickle.dumps(painter))
    assert np.array_equal(a, np.asarray(clone.frame(np.full(4, 0.7), 0.5, 1)))


def test_style_keys_default_on():
    style = resolve_style()
    assert style["schematic"] is True
    assert (style["schematic_density"], style["schematic_opacity"]) == (0.5, 0.24)
    with pytest.raises(ValueError, match="schematic_opacity"):
        resolve_style(flags={"schematic_opacity": 0.9})
