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
    "contrast": 1.0,
    "beat_punch": 0.0,
    "vignette": 0.0,
    "grain": 0.0,
    "texture": None,
    "texture_blend": "multiply",
}

TEXTURE_BLENDS = {"multiply", "overlay", "screen", "softlight"}

FPS_CHOICES = {24, 30, 60}

# Declarative style surface: validation and the UI controls both read this.
# `type` is one of "preset" | "int" | "float" | "bool" | "choice" | "path".
# Numeric keys use `min`/`max` (+ optional `exclusive_min`); `choice` uses `choices`.
STYLE_FIELDS: dict[str, dict] = {
    "preset": {"type": "preset", "help": "Named look; presets live in presets/*.json"},
    "beats_per_cut": {"type": "int", "min": 1, "help": "Base cut interval in beats"},
    "crossfade": {
        "type": "float", "min": 0.0, "max": 2.0, "exclusive_min": True,
        "step": 0.05, "help": "Crossfade length in seconds",
    },
    "zoom": {
        "type": "float", "min": 0.0, "max": 0.5, "step": 0.01,
        "help": "Ken Burns amount (zooms 1.0 to 1+zoom)",
    },
    "fps": {"type": "choice", "choices": sorted(FPS_CHOICES), "help": "Output framerate"},
    "waveform": {"type": "bool", "help": "Scrolling waveform strip at the bottom"},
    "title_font": {"type": "path", "help": "Path to a .ttf (null = system default)"},
    "supersample": {
        "type": "choice", "choices": [1, 2, 3, 4], "help": "Pre-zoom upscale factor",
    },
    "contrast": {
        "type": "float", "min": 0.5, "max": 2.0, "step": 0.05, "help": "Luma contrast",
    },
    "beat_punch": {
        "type": "float", "min": 0.0, "max": 0.3, "step": 0.01,
        "help": "Downbeat zoom punch; 0 = off",
    },
    "vignette": {
        "type": "float", "min": 0.0, "max": 1.0, "step": 0.05,
        "help": "Edge darkening strength; 0 = off",
    },
    "grain": {
        "type": "float", "min": 0.0, "max": 1.0, "step": 0.05,
        "help": "Film grain amount; 0 = off",
    },
    "texture": {"type": "path", "help": "Overlay image path (null = off)"},
    "texture_blend": {
        "type": "choice", "choices": sorted(TEXTURE_BLENDS), "help": "Texture blend mode",
    },
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


def _coerce_choice(value, choices) -> object:
    """Coerce string payloads to the numeric type of the choices (e.g. "30" -> 30),
    mirroring JSON/UI form inputs while keeping strict membership checks."""
    if all(isinstance(c, int) for c in choices):
        try:
            return int(value)
        except (TypeError, ValueError):
            return value
    if all(isinstance(c, float) for c in choices):
        try:
            return float(value)
        except (TypeError, ValueError):
            return value
    return value


def _validate_style(style: dict) -> dict:
    try:
        for key, spec in STYLE_FIELDS.items():
            if key not in style:
                continue
            kind = spec["type"]
            value = style[key]

            if kind == "preset":
                continue
            if kind == "int":
                style[key] = int(value)
                if "min" in spec and style[key] < spec["min"]:
                    raise ValueError(f"{key} must be >= {spec['min']}")
                continue
            if kind == "float":
                style[key] = float(value)
                lo, hi = spec.get("min"), spec.get("max")
                if lo is not None:
                    if spec.get("exclusive_min"):
                        if style[key] <= lo:
                            raise ValueError(f"{key} must be > {lo} (exclusive)")
                    elif style[key] < lo:
                        raise ValueError(f"{key} must be >= {lo}")
                if hi is not None and style[key] > hi:
                    raise ValueError(f"{key} must be <= {hi}")
                continue
            if kind == "bool":
                style[key] = bool(value)
                continue
            if kind == "choice":
                style[key] = _coerce_choice(value, spec["choices"])
                if style[key] not in spec["choices"]:
                    raise ValueError(f"{key} must be one of {spec['choices']}")
                continue
            if kind == "path":
                style[key] = None if value is None else str(value)
                continue
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid style value: {exc}") from None
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