"""Pure timeline planning: energy-reactive cuts, hook windows, crossfade timing."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class Boundary:
    time: float
    reason: str


@dataclass(frozen=True)
class Segment:
    offset: float
    duration: float
    image_index: int
    transition: str = "fade"


@dataclass(frozen=True)
class Plan:
    window: tuple[float, float]
    duration: float
    xfade: float
    boundaries: list[Boundary]
    segments: list[Segment]

    @property
    def cut_times(self) -> list[float]:
        return [b.time for b in self.boundaries]


def full_window(duration: float) -> tuple[float, float]:
    return (0.0, float(duration))


def _slice_window(beatmap: dict, window: tuple[float, float]):
    start, end = window
    beats = beatmap.get("beats") or []
    energy = beatmap.get("beat_energy") or []
    rel_beats: list[float] = []
    rel_energy: list[float] = []
    for i, b in enumerate(beats):
        if b < start or b > end:
            continue
        rel_beats.append(float(b) - start)
        rel_energy.append(float(energy[i]) if i < len(energy) else 0.5)
    return rel_beats, rel_energy


def _downbeats(beatmap: dict, window: tuple[float, float], rel_beats: list[float]) -> list[float]:
    start, end = window
    raw = beatmap.get("downbeats") or []
    if raw:
        return [float(b) - start for b in raw if start <= b <= end]
    return rel_beats[::4]


def _section_cuts(
    rel_beats: list[float], rel_energy: list[float], threshold: float = 0.2
) -> list[tuple[float, str]]:
    n = len(rel_beats)
    if n < 8:
        return []

    bars = [
        sum(rel_energy[i : i + 4]) / len(rel_energy[i : i + 4])
        for i in range(0, n - 3, 4)
    ]
    cuts: list[tuple[float, str]] = []
    for bar in range(1, len(bars)):
        delta = bars[bar] - bars[bar - 1]
        if abs(delta) >= threshold:
            reason = "drop" if delta > 0 else "section"
            cuts.append((rel_beats[bar * 4], reason))
    return cuts


def _adaptive_cuts(
    rel_beats: list[float], rel_energy: list[float], base: int
) -> list[tuple[float, str]]:
    n = len(rel_beats)
    if n < 2:
        return []

    cuts: list[tuple[float, str]] = []
    current = 0
    while current < n - 1:
        energy = rel_energy[current] if current < len(rel_energy) else 0.5
        if energy >= 0.70:
            step, reason = max(2, base // 2), "energy"
        elif energy <= 0.35:
            step, reason = base * 2, "hold"
        else:
            step, reason = base, "interval"
        nxt = current + step
        if nxt >= n:
            break
        cuts.append((rel_beats[nxt], reason))
        current = nxt
    return cuts


def _snap(time: float, candidates: list[float], tolerance: float) -> float:
    if not candidates:
        return time
    nearest = min(candidates, key=lambda c: abs(c - time))
    return nearest if abs(nearest - time) <= tolerance else time


def _finalize(
    candidates: list[tuple[float, int, str]],
    duration: float,
    min_first: float = 1.2,
    min_last: float = 1.5,
    min_gap: float = 1.0,
) -> list[Boundary]:
    accepted: list[tuple[float, str]] = []
    for time, _, reason in sorted(candidates):
        if time <= 0 or time >= duration:
            continue
        if not accepted and time < min_first:
            continue
        if time > duration - min_last:
            continue
        if accepted and time - accepted[-1][0] < min_gap:
            continue
        accepted.append((time, reason))

    return (
        [Boundary(0.0, "start")]
        + [Boundary(time, reason) for time, reason in accepted]
        + [Boundary(duration, "end")]
    )


def _build_segments(
    boundaries: list[Boundary],
    xfade: float,
    image_count: int,
    duration: float,
    image_offset: int = 0,
) -> tuple[list[Segment], float]:
    times = [b.time for b in boundaries]
    n = len(times) - 1
    if n <= 1:
        return [Segment(0.0, duration, image_offset % image_count)], 0.0

    if xfade <= 0:
        segments = [
            Segment(times[i], times[i + 1] - times[i], (i + image_offset) % image_count)
            for i in range(n)
        ]
        return segments, 0.0

    min_seg = min(times[i + 1] - times[i] for i in range(n))
    xfade = min(xfade, min_seg * 0.4)
    if xfade < 0.01:
        return _build_segments(boundaries, 0.0, image_count, duration, image_offset)

    slack = 0.10
    segments: list[Segment] = []
    for i in range(n):
        offset = 0.0 if i == 0 else times[i] - xfade
        if i == n - 1:
            seg_duration = duration - offset
        else:
            seg_duration = (times[i + 1] - times[i]) + xfade + slack
        segments.append(
            Segment(
                offset,
                max(seg_duration, xfade + slack),
                (i + image_offset) % image_count,
            )
        )
    return segments, xfade


def plan_timeline(
    beatmap: dict,
    window: tuple[float, float],
    image_count: int,
    style: dict,
    overrides: dict | None = None,
) -> Plan:
    start, end = window
    duration = float(end) - float(start)
    if duration <= 0:
        raise ValueError(f"invalid window: {window}")
    if image_count < 1:
        raise ValueError("image_count must be >= 1")

    base = int(style.get("beats_per_cut", 8))
    overrides = overrides or {}
    explicit = overrides.get("cuts")

    if explicit:
        times = sorted(float(t) - start for t in explicit if start < float(t) < end)
        candidates = [(t, 0, "override") for t in times]
        boundaries = _finalize(
            candidates, duration, min_first=0.0, min_last=0.0, min_gap=0.1
        )
    else:
        rel_beats, rel_energy = _slice_window(beatmap, window)
        if len(rel_beats) >= 2:
            downbeats = _downbeats(beatmap, window, rel_beats)
            intervals = np.diff(rel_beats)
            tolerance = 0.25 * float(np.median(intervals))
            candidates = [
                (time, 0, reason)
                for time, reason in _section_cuts(rel_beats, rel_energy)
            ]
            candidates += [
                (_snap(time, downbeats, tolerance), 1, reason)
                for time, reason in _adaptive_cuts(rel_beats, rel_energy, base)
            ]
        else:
            tempo = float(beatmap.get("tempo") or 0.0)
            interval = base * 60.0 / tempo if tempo > 0 else 4.0
            candidates = [
                (float(t), 1, "interval")
                for t in np.arange(interval, duration, interval)
            ]
        boundaries = _finalize(candidates, duration)

    segments, xfade = _build_segments(
        boundaries, float(style.get("crossfade", 0.35)), image_count, duration
    )
    return Plan(
        window=(float(start), float(end)),
        duration=duration,
        xfade=xfade,
        boundaries=boundaries,
        segments=segments,
    )


def _mean_in(values: list, times: list, start: float, end: float) -> float:
    samples = [float(v) for t, v in zip(times, values) if start <= t < end]
    return sum(samples) / len(samples) if samples else 0.0


def _index_of(values: list[float], target: float) -> int | None:
    for i, value in enumerate(values):
        if abs(value - target) < 1e-6:
            return i
    return None


def select_hook(beatmap: dict, hook: dict) -> tuple[float, float]:
    """Choose the short-form window for vertical/square exports."""
    duration = float(beatmap.get("duration") or 0.0)
    mode = hook.get("mode", "auto")

    if mode == "full":
        return (0.0, duration)

    if mode == "manual":
        start = float(hook["start"])
        end = float(hook["end"])
        if start < 0 or end <= start:
            raise ValueError("hook must satisfy 0 <= start < end")
        if end > duration + 1e-6:
            raise ValueError(
                f"hook end {end:.2f}s is past the track ({duration:.2f}s)"
            )
        return (start, end)

    target = min(float(hook.get("target", 45.0)), duration)
    if duration <= 0 or target >= duration - 1e-6:
        return (0.0, duration)

    beats = [float(b) for b in beatmap.get("beats") or []]
    if not beats:
        return (0.0, target)

    downbeats = [float(b) for b in beatmap.get("downbeats") or beats[::4]]
    energy = list(beatmap.get("beat_energy") or [])
    novelty = list(beatmap.get("onset_novelty") or [])
    bar_energy = list(beatmap.get("bar_energy") or [])

    starts = [d for d in downbeats if d + target <= duration + 1e-6]
    if not starts:
        starts = [0.0]

    max_energy_time = None
    if energy:
        limit = min(len(energy), len(beats))
        index = max(range(limit), key=lambda i: energy[i])
        max_energy_time = beats[index]

    best: tuple[float, float] | None = None
    for start in starts:
        end = start + target
        score = _mean_in(energy, beats, start, end)
        score += 0.5 * _mean_in(novelty, beats, start, end)

        bar_index = _index_of(downbeats, start)
        if bar_index is not None:
            if 0 < bar_index < len(bar_energy):
                score += 0.5 * max(0.0, bar_energy[bar_index] - bar_energy[bar_index - 1])
            if bar_index % 4 == 0:
                score += 0.1

        if max_energy_time is not None and start <= max_energy_time < end:
            score += 0.3
        if start < 0.08 * duration:
            score -= 0.15

        if best is None or score > best[0] + 1e-12:
            best = (score, start)

    start = float(best[1])
    ideal_end = start + target
    end = min(duration, ideal_end)

    intervals = np.diff(beats)
    tolerance = 2.0 * float(np.median(intervals)) if len(intervals) else 1.0
    near = min(downbeats, key=lambda d: abs(d - ideal_end))
    if (
        abs(near - ideal_end) <= tolerance
        and 30.0 - 1e-6 <= near - start <= 60.0 + 1e-6
    ):
        end = min(duration, near)

    return (start, float(end))


PREVIEW_LEAD = 1.5


def _segment_index_at(plan: Plan, rel_time: float) -> int:
    for i in range(len(plan.boundaries) - 1):
        if plan.boundaries[i].time <= rel_time < plan.boundaries[i + 1].time:
            return i
    return max(0, len(plan.boundaries) - 2)


def slice_plan(
    plan: Plan, start: float, end: float, image_count: int
) -> Plan:
    """Cut a sub-window out of an existing plan without re-planning cuts."""
    start = max(plan.window[0], float(start))
    end = min(plan.window[1], float(end))
    if end <= start:
        raise ValueError(f"invalid slice: {(start, end)}")
    if image_count < 1:
        raise ValueError("image_count must be >= 1")

    rel_start = start - plan.window[0]
    rel_end = end - plan.window[0]
    duration = end - start

    image_offset = _segment_index_at(plan, rel_start)
    inside = [
        boundary
        for boundary in plan.boundaries
        if rel_start < boundary.time < rel_end
    ]
    boundaries = (
        [Boundary(0.0, "start")]
        + [Boundary(b.time - rel_start, b.reason) for b in inside]
        + [Boundary(duration, "end")]
    )
    segments, xfade = _build_segments(
        boundaries, plan.xfade, image_count, duration, image_offset
    )
    return Plan(
        window=(start, end),
        duration=duration,
        xfade=xfade,
        boundaries=boundaries,
        segments=segments,
    )


def preview_slice(
    plan: Plan,
    image_count: int,
    seconds: float = 4.0,
    lead: float = PREVIEW_LEAD,
) -> Plan:
    """Pick a short slice that contains at least one cut when possible.

    Starts at the beginning if the first cut is already inside the preview;
    otherwise starts shortly before the first cut so a transition is visible.
    """
    window_start, window_end = plan.window
    total = window_end - window_start
    cuts = [b.time for b in plan.boundaries[1:-1]]

    if not cuts:
        start_rel = 0.0
    else:
        first_cut = cuts[0]
        if first_cut <= seconds - 0.5:
            start_rel = 0.0
        else:
            start_rel = max(0.0, first_cut - min(lead, seconds / 2))
        start_rel = max(0.0, min(start_rel, max(0.0, total - seconds)))

    end_rel = min(total, start_rel + seconds)
    return slice_plan(
        plan,
        window_start + start_rel,
        window_start + end_rel,
        image_count,
    )