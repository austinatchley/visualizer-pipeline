"""FFmpeg renderer: beat-synced Ken Burns slideshow for vertical + horizontal."""

from __future__ import annotations

import math
import shutil
import subprocess
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path

from PIL import Image

from .aspects import ASPECTS, Aspect
from .fonts import style_faces
from .hud import VgmConfig, build_hud, build_vgm_overlay
from .plan import Plan, full_window, plan_timeline, preview_slice, select_hook
from .reactive import (
    ReactiveCancelled,
    ReactiveClip,
    render_reactive,
    wants_reactive,
)
from .readouts import ReadoutPainter, wants_readouts
from .schematic import draw_linework, seed_for
from .schematic import generate as generate_schematic
from .status import invalidate, record, render_fingerprint
from .textures import DEFAULT_TEXTURES_DIR, resolve_texture
from .track import Track

HUD_FADE_IN = 0.3
HUD_FADE_DUR = 0.8
GRAIN_SEED = 2024
PREVIEW_SECONDS = 4.0
PREVIEW_SCALE = 4

# Film grades for the `grade` style key (issue #37 + #20). Applied as an `eq`/
# `colorbalance` filter in the finish chain, before vignette/grain. `analog` is
# the warm poison grade reference (crushed-green shadows, warm highlights); its
# saturation lives in the distinct `saturation` key so the two compose freely.
GRADES: dict[str, str] = {
    "sunset": "eq=contrast=1.08:saturation=1.15:gamma_r=1.04:gamma_b=0.96",
    "night": "eq=contrast=1.10:saturation=0.80:brightness=-0.03:gamma_b=1.06",
    "fog": "eq=contrast=0.90:saturation=0.40:brightness=0.02",
    "analog": (
        "colorbalance=rs=0.20:gs=-0.45:bs=0.10:"
        "rm=0.10:gm=-0.02:bm=-0.06:"
        "rh=0.12:gh=0.03:bh=-0.08:pl=1"
    ),
}


def fade_times(fade_in: float, fade_out: float, duration: float) -> tuple[float, float]:
    """Clamp the start/end fades to the clip (issue #45).

    Negative values become 0. If the two fades together are longer than the clip,
    both shrink proportionally so they meet without overlapping, which keeps the
    fade-out start time non-negative on very short clips."""
    fade_in, fade_out = max(0.0, float(fade_in)), max(0.0, float(fade_out))
    duration = max(0.0, float(duration))
    total = fade_in + fade_out
    if total > duration and total > 0:
        scale = duration / total
        fade_in, fade_out = fade_in * scale, fade_out * scale
    return fade_in, fade_out


def _fade_filters(kind: str, fade_in: float, fade_out: float, duration: float) -> list[str]:
    """`fade`/`afade` filters for the clamped fades; a zero-length fade is omitted."""
    filters = []
    if fade_in >= 0.001:
        filters.append(f"{kind}=t=in:st=0:d={fade_in:.3f}")
    if fade_out >= 0.001:
        filters.append(f"{kind}=t=out:st={duration - fade_out:.3f}:d={fade_out:.3f}")
    return filters


def _progress_percent(line: str, duration: float) -> float | None:
    """Parse one ffmpeg `-progress` line into a 0-100 percentage.

    ffmpeg emits `out_time_us=N/A` on its final update; ignore empty, N/A, and
    malformed values instead of failing the render.
    """
    if not line.startswith("out_time_us=") or duration <= 0:
        return None
    raw = line.partition("=")[2].strip()
    if raw in ("", "N/A"):
        return None
    try:
        microseconds = float(raw)
    except ValueError:
        return None
    return min(100.0, max(0.0, microseconds / 1e6 / duration * 100.0))


class RenderCancelled(RuntimeError):
    """Raised by render() when the optional cancel hook reports cancellation."""


def _cover_image(src: Path, width: int, height: int, out: Path) -> Path:
    img = Image.open(src).convert("RGB")
    scale = max(width / img.width, height / img.height)
    img = img.resize(
        (math.ceil(img.width * scale), math.ceil(img.height * scale)),
        Image.Resampling.LANCZOS,
    )
    left = (img.width - width) // 2
    top = (img.height - height) // 2
    img.crop((left, top, left + width, top + height)).save(out)
    return out


def _zoom_expr(i: int, fps: int, frames: int, max_zoom: float, punch: float) -> str:
    rate = (max_zoom - 1.0) / max(frames - 1, 1)
    if i % 2 == 1:
        return f"max({max_zoom:.5f}-{rate:.6f}*on,1)"
    if punch:
        return f"min(1+{rate:.6f}*on+{punch:.4f}*exp(-on/{fps}*6.0),{max_zoom:.5f})"
    return f"min(1+{rate:.6f}*on,{max_zoom:.5f})"


def _mean_beat_energy(beatmap: dict, start: float, end: float) -> float:
    """Mean normalized beat energy in the half-open absolute window [start, end)."""
    beats = beatmap.get("beats") or []
    energy = beatmap.get("beat_energy") or []
    values = [
        float(energy[i])
        for i, beat in enumerate(beats)
        if i < len(energy) and start <= float(beat) < end
    ]
    return sum(values) / len(values) if values else 0.5


def _energy_saturation(mean_energy: float, base: float) -> float:
    """Map a segment's mean energy in [0,1] to a saturation multiplier in
    [0.85, 1.2] (quiet sections less color, drops more), scaled by `base`."""
    multiplier = 0.85 + 0.35 * max(0.0, min(1.0, mean_energy))
    return max(0.0, min(2.0, base * multiplier))


def _color_punch(style: dict) -> float:
    """Effective cut punch: scaled by saturation, so `saturation: 0` stays true mono.

    Zero means the per-segment eq is skipped and the grade applies a static
    saturation instead; both call sites must agree on it."""
    return float(style["color_punch"]) * min(1.0, float(style["saturation"]))


def _saturation_expr(base: float, punch: float, fps: int) -> str:
    """Per-segment saturation that starts at ``base + punch`` and decays to
    ``base`` over roughly 0.3 s, mirroring the `beat_punch` zoom profile.

    Each segment is its own input stream, so `n` (the eq filter's frame counter)
    resets to 0 at the cut and the spike lands on the downbeat."""
    return f"eq=saturation='{base:.4f}+{punch:.4f}*exp(-n/{fps}*6.0)':eval=frame"


@dataclass(frozen=True)
class Variant:
    aspect: Aspect
    window: tuple[float, float]
    is_hook: bool
    path: Path


def window_for(track: Track, beatmap: dict, aspect: Aspect) -> tuple[float, float]:
    duration = float(beatmap["duration"])
    if aspect.kind == "horizontal":
        return full_window(duration)
    return select_hook(beatmap, track.hook)


@dataclass(frozen=True)
class RenderAssets:
    """Prepared overlay PNGs for one render (see `prepare_assets`)."""

    hud: Path
    texture: Path | None = None  # already cover-cropped to the output size
    schematic: Path | None = None  # static circuit linework, full frame (#83)
    reactive: tuple[ReactiveClip, ...] = ()  # per-frame layers, bottom first (#81, #82)


@dataclass(frozen=True)
class RenderGraph:
    """Everything ffmpeg needs except encoder settings (see `build_command`)."""

    inputs: list[str]
    filter_complex: str
    maps: list[str]
    has_audio: bool


def _still_input(path: Path, fps: int, duration: float) -> list[str]:
    return ["-loop", "1", "-framerate", str(fps), "-t", f"{duration:.3f}", "-i", str(path)]


def plan_for(
    track: Track, beatmap: dict, window: tuple[float, float], preview: bool = False
) -> Plan:
    """The timeline to render: the full plan, or a short slice of it for previews."""
    plan = plan_timeline(beatmap, window, len(track.images), track.style, track.overrides)
    if preview:
        return preview_slice(plan, len(track.images), PREVIEW_SECONDS)
    return plan


def output_size(aspect: Aspect, preview: bool = False) -> tuple[int, int]:
    if preview:
        return max(1, aspect.width // PREVIEW_SCALE), max(1, aspect.height // PREVIEW_SCALE)
    return aspect.width, aspect.height


TITLE_GAP = 0.035  # of the short side: space between the readout strip and the title


def title_top(style: dict, size: tuple[int, int]) -> int | None:
    """The title starts just below the readout strip (#98); None (the layout's
    default, the strip's top) when the readouts are off."""
    if not wants_readouts(style):
        return None
    return ReadoutPainter(size, style, {}).bottom + round(min(size) * TITLE_GAP)


def prepare_assets(
    track: Track,
    size: tuple[int, int],
    aspect: Aspect,
    tmp: Path,
    textures_dir: str | Path = DEFAULT_TEXTURES_DIR,
) -> RenderAssets:
    """Write the HUD (with VGM overlay) and texture PNGs into `tmp`."""
    style = track.style
    width, height = size
    font_face, meta_face = style_faces(style)
    hud_aspect = Aspect(aspect.key, width, height, aspect.label, aspect.kind)
    vgm_cfg = VgmConfig.from_style(style)
    vgm_img = build_vgm_overlay((width, height), vgm_cfg) if vgm_cfg.active else None
    hud = tmp / "hud.png"
    build_hud(
        hud,
        hud_aspect,
        track.title,
        track.artist,
        track.handle,
        font_face=font_face,
        meta_font_face=meta_face,
        text_case=str(style["text_case"]),
        tracking=float(style["tracking"]),
        line_spacing=float(style["line_spacing"]),
        show_title=bool(style["show_title"]),
        overlay=vgm_img,
        top=title_top(style, size),
    )

    texture = None
    texture_path = resolve_texture(style["texture"], textures_dir)
    if texture_path:
        texture = _cover_image(texture_path, width, height, tmp / "texture.png")
    schematic = None
    if style["schematic"] and float(style["schematic_opacity"]) > 0:
        board = generate_schematic(size, seed_for(track.id), float(style["schematic_density"]))
        schematic = tmp / "schematic.png"
        draw_linework(board, float(style["schematic_opacity"])).save(schematic)
    return RenderAssets(hud, texture, schematic)


def _segment_chain(
    seg, i: int, style: dict, size: tuple[int, int], beatmap: dict, window_start: float
) -> str:
    width, height = size
    fps = int(style["fps"])
    supersample = int(style["supersample"])
    color_punch = _color_punch(style)
    frames = max(2, math.ceil(seg.duration * fps) + 2)
    zexpr = _zoom_expr(i, fps, frames, 1.0 + float(style["zoom"]), float(style["beat_punch"]))
    chain = [
        f"scale={width * supersample}:{height * supersample}:force_original_aspect_ratio=increase",
        f"crop={width * supersample}:{height * supersample}",
        "setsar=1",
        f"zoompan=z='{zexpr}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
        f"d={frames}:s={width}x{height}:fps={fps}",
    ]
    if color_punch > 0:
        # Baseline follows the segment's energy; the punch decays from the cut.
        # `n` is this segment's own frame counter.
        abs_start = window_start + seg.offset
        base = _energy_saturation(
            _mean_beat_energy(beatmap, abs_start, abs_start + seg.duration),
            float(style["saturation"]),
        )
        chain.append(_saturation_expr(base, color_punch, fps))
    chain += ["settb=AVTB", "setpts=PTS-STARTPTS"]
    return f"[{i}:v]" + ",".join(chain) + f"[v{i}]"


def _grade_filters(style: dict) -> list[str]:
    """Grade, saturation, vignette, grain, contrast: the footage-only finish.

    Runs on the base footage before any overlay, so the HUD, watermark and
    reactive layers stay crisp on top of the grade (issue #86)."""
    finish: list[str] = []
    grade = GRADES.get(style["grade"])
    if grade:
        finish.append(grade)
    saturation = float(style["saturation"])
    if _color_punch(style) <= 0 and saturation != 1.0:
        # When color_punch is active the per-segment eq already carries the base
        # saturation, so only apply a static one when it is off. 0.0 must give a
        # true monochrome phase: eq=saturation=0 yields R=G=B per frame.
        finish.append(f"eq=saturation={saturation:.3f}")
    dim = float(style["dim"])
    if dim > 0:
        # Scale toward black (not a brightness offset): blacks stay black and
        # bright skies drop the most, so overlays read on any image (#88).
        gain = 1.0 - dim
        finish.append(f"colorchannelmixer=rr={gain:.3f}:gg={gain:.3f}:bb={gain:.3f}")
    vignette = float(style["vignette"])
    if vignette > 0:
        finish.append(f"vignette=angle=(PI/5)*(1+{vignette:.3f}*1.6)")
    grain = float(style["grain"])
    if grain > 0:
        # Luma-only grain: noise on the Y plane, so it never adds chroma speckle
        # (and `saturation: 0` stays true mono).
        finish.append(
            f"format=yuv444p,noise=c0s={int(grain * 25)}:c0f=t+u:c0_seed={GRAIN_SEED},format=rgb24"
        )
    contrast = float(style["contrast"])
    if contrast != 1.0:
        finish.append(f"eq=contrast={contrast:.3f}")
    return finish


def build_graph(
    track: Track,
    plan: Plan,
    beatmap: dict,
    size: tuple[int, int],
    assets: RenderAssets,
    preview: bool = False,
) -> RenderGraph:
    """Pure: inputs + filtergraph for a plan. No file I/O and no subprocesses, so
    the graph can be snapshot-tested without ffmpeg (issue #47)."""
    style = track.style
    fps = int(style["fps"])
    window_start, window_end = plan.window
    duration = plan.duration
    segments = plan.segments

    # Inputs: one image per segment, then audio, HUD, texture, schematic linework,
    # reactive clips. Every
    # input adds exactly one "-i", so indices follow an explicit counter.
    inputs: list[str] = []
    for seg in segments:
        inputs += ["-i", str(track.images[seg.image_index])]
    index = len(segments)
    audio_idx = None
    if not preview:
        audio_idx, index = index, index + 1
        inputs += ["-i", str(track.audio)]
    hud_idx, index = index, index + 1
    inputs += _still_input(assets.hud, fps, duration)
    tex_idx = None
    if assets.texture:
        tex_idx, index = index, index + 1
        inputs += _still_input(assets.texture, fps, duration)
    sch_idx = None
    if assets.schematic:
        sch_idx, index = index, index + 1
        inputs += _still_input(assets.schematic, fps, duration)
    re_idx: list[int] = []
    for clip in assets.reactive:
        re_idx.append(index)
        index += 1
        inputs += ["-i", str(clip.path)]

    parts = [
        _segment_chain(seg, i, style, size, beatmap, window_start) for i, seg in enumerate(segments)
    ]
    prev = "v0"
    for i in range(1, len(segments)):
        parts.append(
            f"[{prev}][v{i}]xfade=transition=fade:duration={plan.xfade:.3f}:"
            f"offset={segments[i].offset:.3f}[x{i}]"
        )
        prev = f"x{i}"

    if tex_idx is not None:
        parts.append(f"[{tex_idx}:v]format=rgb24,settb=AVTB,setpts=PTS-STARTPTS[tex]")
        parts.append(f"[{prev}]format=rgb24[base]")
        parts.append(f"[tex][base]blend=all_mode={style['texture_blend']}[comp]")
        prev = "comp"

    grade = _grade_filters(style)
    if grade:
        parts.append(f"[{prev}]format=rgb24," + ",".join(grade) + "[graded]")
        prev = "graded"

    if sch_idx is not None:
        parts.append(
            f"[{sch_idx}:v]format=rgba,"
            f"fade=t=in:st={HUD_FADE_IN}:d={HUD_FADE_DUR}:alpha=1,"
            f"settb=AVTB,setpts=PTS-STARTPTS[sch]"
        )
        parts.append(f"[{prev}][sch]overlay=0:0:format=auto[vs]")
        prev = "vs"

    for n, (idx, clip) in enumerate(zip(re_idx, assets.reactive, strict=True)):
        scale = f"scale={clip.scale_to[0]}:{clip.scale_to[1]}," if clip.scale_to else ""
        parts.append(
            f"[{idx}:v]format=rgba,{scale}"
            f"fade=t=in:st={HUD_FADE_IN}:d={HUD_FADE_DUR}:alpha=1,"
            f"settb=AVTB,setpts=PTS-STARTPTS[re{n}]"
        )
        parts.append(f"[{prev}][re{n}]overlay={clip.x}:{clip.y}:format=auto[vr{n}]")
        prev = f"vr{n}"

    parts.append(
        f"[{hud_idx}:v]format=rgba,"
        f"fade=t=in:st={HUD_FADE_IN}:d={HUD_FADE_DUR}:alpha=1,"
        f"settb=AVTB,setpts=PTS-STARTPTS[hud]"
    )
    parts.append(f"[{prev}][hud]overlay=0:0:format=auto[vg]")
    prev = "vg"

    fades = fade_times(style["fade_in"], style["fade_out"], duration)
    # Fades cover the whole frame (overlays included); then the output range.
    finish = [] if preview else _fade_filters("fade", *fades, duration)
    finish.append("scale=out_range=tv,format=yuv420p")
    parts.append(f"[{prev}]format=rgb24," + ",".join(finish) + "[vout]")

    maps = ["-map", "[vout]"]
    if audio_idx is not None:
        audio_chain = [
            f"atrim={window_start:.3f}:{window_end:.3f}",
            "asetpts=PTS-STARTPTS",
            "loudnorm=I=-14:TP=-1.5:LRA=11",
            "aresample=48000",
            "aformat=channel_layouts=stereo",
            *_fade_filters("afade", *fades, duration),
        ]
        parts.append(f"[{audio_idx}:a]" + ",".join(audio_chain) + "[aout]")
        maps += ["-map", "[aout]"]

    return RenderGraph(inputs, ";".join(parts), maps, audio_idx is not None)


def build_command(
    graph: RenderGraph, fps: int, duration: float, out_path: Path, preview: bool = False
) -> list[str]:
    """Pure: the full ffmpeg argument list for a graph."""
    enc = [
        "-c:v",
        "libx264",
        "-preset",
        "veryfast" if preview else "medium",
        "-crf",
        "23" if preview else "18",
    ]
    audio_enc = ["-c:a", "aac", "-b:a", "192k"] if graph.has_audio else []
    return [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-nostdin",
        "-y",
        *graph.inputs,
        "-filter_complex",
        graph.filter_complex,
        *graph.maps,
        *enc,
        "-pix_fmt",
        "yuv420p",
        "-r",
        str(fps),
        *audio_enc,
        "-movflags",
        "+faststart",
        "-t",
        f"{duration:.3f}",
        str(out_path),
    ]


def run_ffmpeg(
    cmd: list[str],
    duration: float,
    progress: Callable[[float, str], None] | None = None,
    cancel: Callable[[], bool] | None = None,
) -> None:
    """Run an ffmpeg command, streaming progress and honoring cancellation.

    Raises `RenderCancelled` when `cancel()` turns true, and `RuntimeError` with
    the stderr tail when ffmpeg fails."""
    if progress is None and cancel is None:
        proc = subprocess.run(cmd, capture_output=True, text=True)
        returncode, stderr_text = proc.returncode, proc.stderr or ""
    else:
        if cancel is not None and cancel():
            raise RenderCancelled("render cancelled")
        if progress is not None:
            progress(0.0, "rendering")
        returncode, stderr_text = _stream(cmd, duration, progress, cancel)
        if returncode == 0 and progress is not None:
            progress(100.0, "done")
    if returncode != 0:
        tail = "\n".join(stderr_text.strip().splitlines()[-40:])
        raise RuntimeError(f"ffmpeg failed (exit {returncode}):\n{tail}")


def _stream(cmd, duration, progress, cancel) -> tuple[int, str]:
    proc = subprocess.Popen(
        [*cmd, "-nostats", "-progress", "pipe:1"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        errors="replace",
    )
    assert proc.stdout is not None
    try:
        for line in proc.stdout:
            if cancel is not None and cancel():
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()
                raise RenderCancelled("render cancelled")
            pct = _progress_percent(line, duration)
            if pct is not None:
                if progress is not None:
                    progress(pct, "rendering")
            elif line.strip() == "progress=end" and progress is not None:
                progress(100.0, "done")
    finally:
        if proc.poll() is None:
            proc.terminate()
    err = proc.stderr.read() if proc.stderr else ""
    return proc.wait(), err


def render(
    track: Track,
    aspect: Aspect,
    beatmap: dict,
    out_path: Path,
    window: tuple[float, float] | None = None,
    preview: bool = False,
    progress: Callable[[float, str], None] | None = None,
    cancel: Callable[[], bool] | None = None,
    textures_dir: str | Path = DEFAULT_TEXTURES_DIR,
) -> Path:
    """Render an mp4 variant. `preview=True` renders a short, quarter-resolution,
    audio-less clip (no audio stream) for fast UI iteration.

    `progress`, when given, is called with ``(percent 0-100, stage)`` as encoding
    progresses; `cancel`, when given, is polled and raises `RenderCancelled` early.
    Both are optional and change nothing when omitted."""
    if window is None:
        window = window_for(track, beatmap, aspect)
    fingerprint = render_fingerprint(
        track, aspect, beatmap, window, preview=preview, textures_dir=textures_dir
    )
    plan = plan_for(track, beatmap, window, preview)
    size = output_size(aspect, preview)

    tmp = Path(tempfile.mkdtemp(prefix="vp-"))
    try:
        assets = prepare_assets(track, size, aspect, tmp, textures_dir)
        encode_progress = progress
        if wants_reactive(track.style):
            # Two passes: draw the reactive layers (first 40% of progress), then
            # the main encode (the rest).
            def overlay_progress(pct: float) -> None:
                if progress is not None:
                    progress(0.4 * pct, "drawing")

            overlay_progress(0.0)  # report the start before the first chunk lands
            try:
                clips = render_reactive(
                    track.audio,
                    plan.window,
                    size,
                    track.style,
                    tmp / "reactive",
                    beatmap,
                    overlay_progress,
                    cancel,
                    seed=seed_for(track.id),
                )
            except ReactiveCancelled:
                raise RenderCancelled("render cancelled") from None
            assets = replace(assets, reactive=tuple(clips))
            if progress is not None:
                encode_progress = lambda pct, stage: progress(40.0 + 0.6 * pct, stage)  # noqa: E731
        graph = build_graph(track, plan, beatmap, size, assets, preview)
        cmd = build_command(graph, int(track.style["fps"]), plan.duration, out_path, preview)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        invalidate(out_path)
        run_ffmpeg(cmd, plan.duration, encode_progress, cancel)
        record(out_path, fingerprint)
        return out_path
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def plan_variants(track: Track, beatmap: dict, out_dir: Path, aspects: list[str]) -> list[Variant]:
    duration = float(beatmap["duration"])
    variants: list[Variant] = []
    for key in aspects:
        aspect = ASPECTS[key]
        window = window_for(track, beatmap, aspect)
        is_hook = abs(window[0]) > 1e-6 or abs(window[1] - duration) > 1e-6
        label = f"{aspect.label}_hook" if is_hook else aspect.label
        variants.append(
            Variant(
                aspect=aspect,
                window=window,
                is_hook=is_hook,
                path=out_dir / f"{track.id}_{label}.mp4",
            )
        )
    return variants


def render_aspects(track: Track, beatmap: dict, out_dir: Path, aspects: list[str]) -> list[Path]:
    outputs = []
    for variant in plan_variants(track, beatmap, out_dir, aspects):
        render(track, variant.aspect, beatmap, variant.path, window=variant.window)
        outputs.append(variant.path)
    return outputs
