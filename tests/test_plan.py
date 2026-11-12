import pytest

from vp.plan import full_window, plan_timeline

STYLE = {"beats_per_cut": 8, "crossfade": 0.35}


def make_beatmap(duration: float = 60.0, bpm: float = 120.0, energy=None):
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
    }


def test_full_plan_invariants():
    plan = plan_timeline(make_beatmap(), full_window(60.0), 3, STYLE)

    assert plan.duration == pytest.approx(60.0)
    times = [b.time for b in plan.boundaries]
    assert times[0] == 0.0
    assert times[-1] == pytest.approx(60.0)
    assert times == sorted(times)
    assert len(times) == len(set(times))

    offsets = [s.offset for s in plan.segments]
    assert offsets[0] == 0.0
    assert offsets == sorted(offsets)
    assert plan.segments[-1].offset + plan.segments[-1].duration == pytest.approx(60.0)

    min_seg = min(times[i + 1] - times[i] for i in range(len(times) - 1))
    assert plan.xfade <= min_seg * 0.4 + 1e-9


def test_image_indices_cycle():
    plan = plan_timeline(make_beatmap(), full_window(60.0), 3, STYLE)
    assert len(plan.segments) > 3
    assert [s.image_index for s in plan.segments] == [
        i % 3 for i in range(len(plan.segments))
    ]


def test_explicit_cuts_are_used():
    plan = plan_timeline(
        make_beatmap(), full_window(60.0), 3, STYLE, {"cuts": [15, 5, 10]}
    )
    assert plan.cut_times == pytest.approx([0.0, 5.0, 10.0, 15.0, 60.0])
    assert [b.reason for b in plan.boundaries] == [
        "start",
        "override",
        "override",
        "override",
        "end",
    ]


def test_high_energy_cuts_more_than_low():
    high = make_beatmap(energy=[1.0] * 120)
    low = make_beatmap(energy=[0.0] * 120)
    high_plan = plan_timeline(high, full_window(60.0), 3, STYLE)
    low_plan = plan_timeline(low, full_window(60.0), 3, STYLE)
    assert len(high_plan.segments) > len(low_plan.segments)


def test_section_boundary_detected():
    beatmap = make_beatmap(energy=[0.2] * 60 + [0.9] * 60)
    plan = plan_timeline(beatmap, full_window(60.0), 3, STYLE)
    near = [b for b in plan.boundaries if abs(b.time - 30.0) < 1.0]
    assert near, [b.time for b in plan.boundaries]
    assert near[0].reason in {"drop", "section"}


def test_short_window_single_segment():
    plan = plan_timeline(make_beatmap(), (10.0, 14.0), 3, STYLE)
    assert len(plan.segments) == 1
    assert plan.duration == pytest.approx(4.0)
    assert plan.boundaries[0].time == 0.0


def test_crossfade_clamped_to_shortest_segment():
    style = {"beats_per_cut": 2, "crossfade": 5.0}
    plan = plan_timeline(make_beatmap(energy=[1.0] * 120), full_window(60.0), 3, style)
    times = [b.time for b in plan.boundaries]
    min_seg = min(times[i + 1] - times[i] for i in range(len(times) - 1))
    assert plan.xfade == pytest.approx(min(5.0, min_seg * 0.4))


def test_zero_crossfade_gives_hard_cuts():
    style = {"beats_per_cut": 2, "crossfade": 0.0}
    plan = plan_timeline(make_beatmap(energy=[1.0] * 120), full_window(60.0), 3, style)
    assert plan.xfade == 0.0
    for current, following in zip(plan.segments, plan.segments[1:]):
        assert current.offset + current.duration == pytest.approx(following.offset)


def test_window_rebases_times():
    plan = plan_timeline(make_beatmap(), (10.0, 20.0), 3, STYLE)
    assert plan.boundaries[0].time == 0.0
    assert plan.boundaries[-1].time == pytest.approx(10.0)
    assert plan.duration == pytest.approx(10.0)