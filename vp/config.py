"""Configuration defaults, presets, and option resolution."""

from __future__ import annotations

import json
from pathlib import Path

ANALYSIS_VERSION = 2

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PRESETS_DIR = PROJECT_ROOT / "presets"

DEFAULT_STYLE = {
    "preset": "clean",
    "beats_per_cut": 8,
    "crossfade": 0.35,
    "zoom": 0.10,
    "fps": 30,
    "waveform": False,
    "title_font": None,
    "supersample": 2,
}

DEFAULT_HOOK = {
    "mode": "auto",
    "target": 45.0,
    "start": None,
    "end": None,
}

DEFAULT_OVERRIDES = {
    "bpm": None,
    "tempo_multiplier": 1.0,
    "cuts": None,
    "hook": None,
}

FPS_CHOICES = {24, 30, 60}
HOOK_MIN = 30.0
HOOK_MAX = 60.0

_STYLE_KEYS = set(DEFAULT_STYLE)
_HOOK_KEYS = set(DEFAULT_HOOK)
_OVERRIDE_KEYS = set(DEFAULT_OVERRIDES)


def load_preset(name: str) -> dict:
    path = PRESETS_DIR / f"{name}.json"
    if not path.is_file():
        available = sorted(p.stem for p in PRESETS_DIR.glob("*.json"))
        raise ValueError(
            f"unknown preset {name!r}; available: {', '.join(available) or '(none)'}"
        )
    data = json.loads(path.read_text())
    if not isinstance(data, dict):
        raise ValueError(f"preset {name!r} must be a JSON object")
    unknown = set(data) - _STYLE_KEYS
    if unknown:
        raise ValueError(f"preset {name!r} has unknown keys: {sorted(unknown)}")
    return data


def _validate_style(style: dict) -> dict:
    try:
        style["beats_per_cut"] = int(style["beats_per_cut"])
        style["fps"] = int(style["fps"])
        style["supersample"] = int(style["supersample"])
        style["crossfade"] = float(style["crossfade"])
        style["zoom"] = float(style["zoom"])
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid style value: {exc}") from None

    if style["beats_per_cut"] < 1:
        raise ValueError("beats_per_cut must be >= 1")
    if style["fps"] not in FPS_CHOICES:
        raise ValueError(f"fps must be one of {sorted(FPS_CHOICES)}")
    if not 0 < style["crossfade"] <= 2:
        raise ValueError("crossfade must be between 0 and 2 seconds")
    if not 0 <= style["zoom"] <= 0.5:
        raise ValueError("zoom must be between 0 and 0.5")
    if style["supersample"] not in (1, 2, 3, 4):
        raise ValueError("supersample must be 1, 2, 3, or 4")
    style["waveform"] = bool(style["waveform"])
    if style["title_font"] is not None:
        style["title_font"] = str(style["title_font"])
    return style


def resolve_style(
    track_style: dict | None = None,
    preset: str | None = None,
    flags: dict | None = None,
) -> dict:
    track_style = dict(track_style or {})
    flags = dict(flags or {})

    unknown = set(track_style) - _STYLE_KEYS
    if unknown:
        raise ValueError(f"unknown style keys in track.json: {sorted(unknown)}")

    name = preset or flags.get("preset") or track_style.get("preset") or "clean"
    preset_data = load_preset(name)
    style_flags = {
        k: v
        for k, v in flags.items()
        if k in _STYLE_KEYS and k != "preset" and v is not None
    }

    style = dict(DEFAULT_STYLE)
    style.update(preset_data)
    style.update({k: v for k, v in track_style.items() if v is not None})
    style.update(style_flags)
    style["preset"] = name
    return _validate_style(style)


def resolve_hook(
    track_hook: dict | None = None,
    overrides: dict | None = None,
    flags: dict | None = None,
    duration: float | None = None,
) -> dict:
    flags = dict(flags or {})
    track_hook = dict(track_hook or {})

    unknown = set(track_hook) - _HOOK_KEYS
    if unknown:
        raise ValueError(f"unknown hook keys in track.json: {sorted(unknown)}")

    hook = dict(DEFAULT_HOOK)
    hook.update({k: v for k, v in track_hook.items() if v is not None})

    forced = (overrides or {}).get("hook")
    if forced:
        hook.update({k: v for k, v in forced.items() if v is not None})

    for key in _HOOK_KEYS:
        if key in flags and flags[key] is not None:
            hook[key] = flags[key]

    start, end = hook.get("start"), hook.get("end")
    if (start is None) != (end is None):
        raise ValueError("hook start and end must be provided together")
    if start is not None and end is not None:
        start, end = float(start), float(end)
        if start < 0 or end <= start:
            raise ValueError("hook must satisfy 0 <= start < end")
        hook["mode"] = "manual"
    elif hook.get("mode") == "full":
        hook["mode"] = "full"
    else:
        hook["mode"] = "auto"

    target = float(hook["target"])
    target = max(HOOK_MIN, min(HOOK_MAX, target))
    if duration is not None:
        target = min(target, float(duration))

    return {
        "mode": hook["mode"],
        "target": target,
        "start": start,
        "end": end,
    }


def resolve_overrides(manifest: dict | None, flags: dict | None = None) -> dict:
    merged = dict(DEFAULT_OVERRIDES)
    merged.update(
        {k: v for k, v in (manifest or {}).items() if k in _OVERRIDE_KEYS and v is not None}
    )
    for key in ("bpm", "tempo_multiplier", "cuts"):
        if key in (flags or {}) and flags[key] is not None:
            merged[key] = flags[key]

    if merged["bpm"] is not None:
        bpm = float(merged["bpm"])
        if bpm <= 0:
            raise ValueError("bpm override must be > 0")
        merged["bpm"] = bpm

    multiplier = float(merged["tempo_multiplier"])
    if multiplier not in (0.5, 1.0, 2.0):
        raise ValueError("tempo_multiplier must be 0.5, 1, or 2")
    merged["tempo_multiplier"] = multiplier

    if merged["cuts"] is not None:
        try:
            merged["cuts"] = sorted(float(c) for c in merged["cuts"])
        except (TypeError, ValueError) as exc:
            raise ValueError(f"invalid cuts override: {exc}") from None

    return merged