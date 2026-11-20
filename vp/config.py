"""Configuration defaults, presets, and option resolution."""

from __future__ import annotations

import json
import warnings
from pathlib import Path

from .fonts import FONT_CHOICES

ANALYSIS_VERSION = 4  # 4: musical key (#82)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PRESETS_DIR = PROJECT_ROOT / "presets"

# The default look is the data-HUD (#88): a dimmed, graded base under crisp,
# music-reactive overlays. It lives in DEFAULT_STYLE itself, so every preset
# file is a set of overrides on top of it.
DEFAULT_PRESET = "datahud"

# Presets that were removed. A manifest that names one renders the default
# look with a warning instead of failing (#88).
RETIRED_PRESETS = {"clean", "punchy", "balanced", "auto"}

DEFAULT_STYLE = {
    "preset": DEFAULT_PRESET,
    "beats_per_cut": 8,
    "crossfade": 0.35,
    "fade_in": 0.4,
    "fade_out": 0.8,
    "zoom": 0.10,
    "fps": 30,
    "title_font": None,
    "font": "clean",
    "meta_font": "auto",
    "text_case": "preserve",
    "tracking": 0.0,
    "line_spacing": 1.12,
    "supersample": 2,
    "contrast": 1.0,
    "saturation": 1.0,
    "beat_punch": 0.06,
    "color_punch": 0.2,
    "dim": 0.15,
    "vignette": 0.25,
    "grain": 0.15,
    "texture": None,
    "texture_blend": "multiply",
    "grade": "none",
    "show_title": True,
    "watermark": False,
    "watermark_text": "postbag",
    "watermark_font": "anton",
    "watermark_opacity": 0.15,
    "watermark_width": 0.95,
    "watermark_stroke": 0.06,
    "watermark_x": 0.0,
    "watermark_y": 0.0,
    "spectrum": True,
    "spectrum_height": 0.22,
    "spectrum_y": 0.5,
    "backplate": 0.6,
    "accent_color": "#d9708f",
    "show_clock": True,
    "show_tempo": True,
    "show_beats": True,
    "show_meters": True,
    "show_notes": True,
    "schematic": True,
    "schematic_density": 0.5,
    "schematic_opacity": 0.24,
}

# Style keys that were removed. Older track.json files may still set them: they
# are dropped with a warning instead of failing the load.
RETIRED_STYLE_KEYS = {
    "waveform": "the scrolling waveform was removed; the spectrum layer replaces it (#87)",
}

TEXTURE_BLENDS = {"multiply", "overlay", "screen", "softlight"}

FPS_CHOICES = {24, 30, 60}

# Declarative style surface: validation and the UI controls both read this.
# `type` is one of "preset" | "int" | "float" | "bool" | "choice" | "path" | "texture".
# Numeric keys use `min`/`max` (+ optional `exclusive_min`); `choice` uses `choices`.
STYLE_FIELDS: dict[str, dict] = {
    "preset": {"type": "preset", "help": "Named look; presets live in presets/*.json"},
    "beats_per_cut": {
        "type": "int",
        "min": 1,
        "max": 32,
        "step": 1,
        "help": "Base cut interval in beats",
    },
    "crossfade": {
        "type": "float",
        "min": 0.0,
        "max": 2.0,
        "exclusive_min": True,
        "step": 0.05,
        "help": "Crossfade length in seconds",
    },
    "fade_in": {
        "type": "float",
        "min": 0.0,
        "max": 3.0,
        "step": 0.05,
        "help": "Fade from black (and audio fade-in) at the start, in seconds; 0 = off",
    },
    "fade_out": {
        "type": "float",
        "min": 0.0,
        "max": 3.0,
        "step": 0.05,
        "help": "Fade to black (and audio fade-out) at the end, in seconds; 0 = off",
    },
    "zoom": {
        "type": "float",
        "min": 0.0,
        "max": 0.5,
        "step": 0.01,
        "help": "Ken Burns amount (zooms 1.0 to 1+zoom)",
    },
    "fps": {"type": "choice", "choices": sorted(FPS_CHOICES), "help": "Output framerate"},
    "title_font": {"type": "path", "help": "Path to a .ttf (null = use font choice)"},
    "font": {
        "type": "choice",
        "choices": FONT_CHOICES,
        "help": "Title typeface; bundled OFL fonts plus system clean",
    },
    "meta_font": {
        "type": "choice",
        "choices": ["auto"] + FONT_CHOICES,
        "help": "Artist/handle typeface (auto = same as title)",
    },
    "text_case": {
        "type": "choice",
        "choices": ["preserve", "upper", "lower"],
        "help": "Title letter case",
    },
    "tracking": {
        "type": "float",
        "min": -0.05,
        "max": 0.25,
        "step": 0.01,
        "help": "Letter spacing in em",
    },
    "line_spacing": {
        "type": "float",
        "min": 1.0,
        "max": 1.5,
        "step": 0.01,
        "help": "Title line-height multiplier",
    },
    "supersample": {
        "type": "choice",
        "choices": [1, 2, 3, 4],
        "help": "Pre-zoom upscale factor",
    },
    "contrast": {
        "type": "float",
        "min": 0.5,
        "max": 2.0,
        "step": 0.05,
        "help": "Luma contrast",
    },
    "saturation": {
        "type": "float",
        "min": 0.0,
        "max": 2.0,
        "step": 0.05,
        "help": "Color saturation multiplier (0.0 = monochrome); 1.0 = natural",
    },
    "beat_punch": {
        "type": "float",
        "min": 0.0,
        "max": 0.3,
        "step": 0.01,
        "help": "Downbeat zoom punch; 0 = off",
    },
    "color_punch": {
        "type": "float",
        "min": 0.0,
        "max": 1.0,
        "step": 0.01,
        "help": "Saturation boost at each cut that decays (~0.3s); 0 = off",
    },
    "dim": {
        "type": "float",
        "min": 0.0,
        "max": 0.6,
        "step": 0.01,
        "help": "Darken the footage under the overlays (0 = off)",
    },
    "vignette": {
        "type": "float",
        "min": 0.0,
        "max": 1.0,
        "step": 0.05,
        "help": "Edge darkening strength; 0 = off",
    },
    "grain": {
        "type": "float",
        "min": 0.0,
        "max": 1.0,
        "step": 0.05,
        "help": "Film grain amount; 0 = off",
    },
    "texture": {
        "type": "texture",
        "help": "Texture from the textures/ library (null = off)",
    },
    "texture_blend": {
        "type": "choice",
        "choices": sorted(TEXTURE_BLENDS),
        "help": "Texture blend mode",
    },
    "grade": {
        "type": "choice",
        "choices": ["none", "sunset", "night", "fog", "analog"],
        "help": "Film grade applied to the footage",
    },
    "show_title": {"type": "bool", "help": "Show the title/artist/handle HUD"},
    "watermark": {
        "type": "bool",
        "help": "Giant translucent watermark overlay (on/off)",
    },
    "watermark_text": {
        "type": "text",
        "help": "Watermark text (default: postbag; toggle with watermark)",
    },
    "watermark_font": {
        "type": "choice",
        "choices": FONT_CHOICES,
        "help": "Watermark typeface (default: anton)",
    },
    "watermark_opacity": {
        "type": "float",
        "min": 0.0,
        "max": 0.5,
        "step": 0.01,
        "help": "Watermark opacity; 0 = invisible",
    },
    "watermark_width": {
        "type": "float",
        "min": 0.2,
        "max": 1.0,
        "step": 0.01,
        "help": "Watermark width as a fraction of frame width",
    },
    "watermark_stroke": {
        "type": "float",
        "min": 0.0,
        "max": 0.25,
        "step": 0.005,
        "help": "Watermark outline thickness as a fraction of font size",
    },
    "watermark_x": {
        "type": "float",
        "min": -1.0,
        "max": 1.0,
        "step": 0.01,
        "help": "Watermark horizontal offset (-1..1; 0 = center, magnitude bleeds)",
    },
    "watermark_y": {
        "type": "float",
        "min": -1.0,
        "max": 1.0,
        "step": 0.01,
        "help": "Watermark vertical offset (-1..1; 0 = center)",
    },
    "spectrum": {"type": "bool", "help": "Music-reactive spectrum analyzer layer (on/off)"},
    "spectrum_height": {
        "type": "float",
        "min": 0.05,
        "max": 0.6,
        "step": 0.01,
        "help": "Spectrum plot height as a fraction of frame height",
    },
    "spectrum_y": {
        "type": "float",
        "min": 0.0,
        "max": 1.0,
        "step": 0.01,
        "help": "Spectrum vertical center as a fraction of frame height",
    },
    "backplate": {
        "type": "float",
        "min": 0.0,
        "max": 1.0,
        "step": 0.05,
        "help": "Soft dark plate behind the reactive layers so they read on bright images; 0 = off",
    },
    "accent_color": {
        "type": "color",
        "help": "Accent color for reactive layers (#rrggbb)",
    },
    "show_clock": {"type": "bool", "help": "Readout: elapsed / total track time"},
    "show_tempo": {"type": "bool", "help": "Readout: BPM and detected musical key"},
    "show_beats": {"type": "bool", "help": "Readout: beat-in-bar indicator, downbeat in accent"},
    "show_meters": {"type": "bool", "help": "Readout: sub / low / mid / high band meters"},
    "show_notes": {
        "type": "bool",
        "help": "Note callouts on the spectrum's strongest low peaks (needs spectrum)",
    },
    "schematic": {
        "type": "bool",
        "help": "Circuit-trace background; its nodes pulse with the bands and downbeats",
    },
    "schematic_density": {
        "type": "float",
        "min": 0.1,
        "max": 1.0,
        "step": 0.05,
        "help": "How many traces the schematic background draws",
    },
    "schematic_opacity": {
        "type": "float",
        "min": 0.0,
        "max": 0.6,
        "step": 0.01,
        "help": "Schematic linework opacity; 0 hides the traces (nodes still pulse)",
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


def preset_names() -> list[str]:
    """Selectable presets: the built-in default, then every presets/*.json."""
    return [DEFAULT_PRESET] + sorted(
        p.stem for p in PRESETS_DIR.glob("*.json") if p.stem != DEFAULT_PRESET
    )


def load_preset(name: str) -> dict:
    if name == DEFAULT_PRESET:
        return {}  # the default look is DEFAULT_STYLE itself
    path = PRESETS_DIR / f"{name}.json"
    if not path.is_file():
        available = preset_names()
        raise ValueError(f"unknown preset {name!r}; available: {', '.join(available) or '(none)'}")
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


def parse_color(value: str) -> tuple[int, int, int]:
    """`#rrggbb` (or `rrggbb`) to an RGB tuple; raises ValueError otherwise."""
    text = str(value).strip().removeprefix("#")
    if len(text) != 6:
        raise ValueError(f"color must be #rrggbb, got {value!r}")
    try:
        return int(text[0:2], 16), int(text[2:4], 16), int(text[4:6], 16)
    except ValueError:
        raise ValueError(f"color must be #rrggbb, got {value!r}") from None


def _normalize_color(value) -> str:
    text = str(value).strip().lower()
    if not text.startswith("#"):
        text = "#" + text
    if len(text) != 7 or any(c not in "0123456789abcdef" for c in text[1:]):
        raise ValueError(f"color must be #rrggbb, got {value!r}")
    return text


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
                if "max" in spec and style[key] > spec["max"]:
                    raise ValueError(f"{key} must be <= {spec['max']}")
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
            if kind == "color":
                style[key] = _normalize_color(value)
                continue
            if kind in ("path", "texture", "text"):
                style[key] = None if value is None else str(value)
                continue
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid style value: {exc}") from None
    return style


def _drop_retired(track_style: dict | None) -> dict:
    style = dict(track_style or {})
    for key, reason in RETIRED_STYLE_KEYS.items():
        if key in style:
            del style[key]
            warnings.warn(f"ignoring style.{key}: {reason}", UserWarning, stacklevel=3)
    return style


def resolve_style(
    track_style: dict | None = None,
    preset: str | None = None,
    flags: dict | None = None,
) -> dict:
    track_style = _drop_retired(track_style)
    flags = dict(flags or {})

    unknown = set(track_style) - _STYLE_KEYS
    if unknown:
        raise ValueError(f"unknown style keys in track.json: {sorted(unknown)}")

    name = preset or flags.get("preset") or track_style.get("preset") or DEFAULT_STYLE["preset"]
    if name in RETIRED_PRESETS:
        warnings.warn(
            f"preset {name!r} was retired; using the default {DEFAULT_PRESET!r} look",
            UserWarning,
            stacklevel=2,
        )
        name = DEFAULT_PRESET
    preset_data = load_preset(name)
    style_flags = {
        k: v for k, v in flags.items() if k in _STYLE_KEYS and k != "preset" and v is not None
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
