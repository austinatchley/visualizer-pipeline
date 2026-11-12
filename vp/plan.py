"""Pure timeline planning: energy-reactive cuts, windows, and crossfade timing."""

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
) -> tuple[list[Segment], float]:
    times = [b.time for b in boundaries]
    n = len(times) - 1
    if n <= 1:
        return [Segment(0.0, duration, 0)], 0.0

    if xfade <= 0:
        segments = [
            Segment(times[i], times[i + 1] - times[i], i % image_count)
            for i in range(n)
        ]
        return segments, 0.0

    min_seg = min(times[i + 1] - times[i] for i in range(n))
    xfade = min(xfade, min_seg * 0.4)
    if xfade < 0.01:
        return _build_segments(boundaries, 0.0, image_count, duration)

    slack = 0.10
    segments: list[Segment] = []
    for i in range(n):
        offset = 0.0 if i == 0 else times[i] - xfade
        if i == n - 1:
            seg_duration = duration - offset
        else:
            seg_duration = (times[i + 1] - times[i]) + xfade + slack
        segments.append(
            Segment(offset, max(seg_duration, xfade + slack), i % image_count)
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