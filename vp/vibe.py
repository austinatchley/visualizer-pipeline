"""Vibe-aware preset selection from beatmap features (issue #19).

Pure and deterministic: the same beatmap always maps to the same preset, so a
render is reproducible. An explicit preset always wins; `auto` only applies when
no preset was chosen by the manifest or a `--preset` flag.
"""

from __future__ import annotations

from .config import DEFAULT_STYLE, resolve_style

# Tracks above this score read as "punchy"; below it, "clean".
PUNCHY_THRESHOLD = 0.5


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _variance(values: list[float]) -> float:
    if not values:
        return 0.0
    mean = _mean(values)
    return sum((v - mean) ** 2 for v in values) / len(values)


def features(beatmap: dict) -> dict:
    """Summarize a beatmap into a few deterministic vibe features."""
    energy = [float(e) for e in beatmap.get("beat_energy") or []]
    novelty = [float(n) for n in beatmap.get("onset_novelty") or []]
    mean_energy = _mean(energy)
    mean_novelty = _mean(novelty)
    # Low/high balance proxy: transient (high) content relative to sustained
    # (low) loudness. The beatmap carries no spectral bands, so this stands in
    # for a bass/treble split and is clamped so one wild bar cannot dominate.
    punch = min(2.0, mean_novelty / (mean_energy + 1e-6))
    return {
        "tempo": float(beatmap.get("tempo") or 0.0),
        "mean_energy": mean_energy,
        "energy_var": _variance(energy),
        "mean_novelty": mean_novelty,
        "punch": punch,
    }


def score(beatmap: dict) -> float:
    """Deterministic 0-1 "how punchy does this feel" score."""
    f = features(beatmap)
    tempo = f["tempo"]
    tempo_score = 1.0 if tempo >= 120 else (0.5 if tempo >= 95 else 0.0)
    dynamic_score = min(1.0, f["energy_var"] / 0.02)  # variance ~0.02 is lively
    punch_score = min(1.0, f["punch"])
    return (
        0.20 * tempo_score
        + 0.35 * min(1.0, f["mean_energy"])
        + 0.30 * dynamic_score
        + 0.15 * punch_score
    )


def choose_preset(beatmap: dict) -> str:
    """Pick `clean` or `punchy` from the audio's vibe. Always deterministic."""
    return "punchy" if score(beatmap) >= PUNCHY_THRESHOLD else "clean"


def finalize_auto_style(track, beatmap: dict) -> str:
    """Resolve a track's `auto` preset to a concrete one in place.

    Returns the concrete preset name (or the already-explicit one). The style is
    re-resolved from the chosen preset plus the track's own non-default values, so
    per-track overrides survive the switch.
    """
    style = getattr(track, "style", None) or {}
    requested = style.get("preset")
    if requested != "auto":
        return requested

    chosen = choose_preset(beatmap)
    overrides = {
        key: value
        for key, value in style.items()
        if key != "preset" and key in DEFAULT_STYLE and value != DEFAULT_STYLE[key]
    }
    track.style = resolve_style(overrides, preset=chosen)
    return chosen


__all__ = [
    "PUNCHY_THRESHOLD",
    "choose_preset",
    "features",
    "finalize_auto_style",
    "score",
]
