"""FFmpeg renderer: beat-synced Ken Burns slideshow for vertical + horizontal."""

from __future__ import annotations

import math
import shutil
import subprocess
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from PIL import Image

from .aspects import ASPECTS, Aspect, HORIZONTAL, SQUARE, VERTICAL
from .fonts import resolve_face
from .hud import VgmConfig, build_hud, build_vgm_overlay
from .plan import full_window, plan_timeline, preview_slice, select_hook
from .status import invalidate, record, render_fingerprint
from .textures import DEFAULT_TEXTURES_DIR, resolve_texture
from .track import Track
from .vibe import finalize_auto_style
from .waveform import build_strip

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


def _zoom_expr(
    i: int, fps: int, frames: int, max_zoom: float, punch: float
) -> str:
    rate = (max_zoom - 1.0) / max(frames - 1, 1)
    if i % 2 == 1:
        return f"max({max_zoom:.5f}-{rate:.6f}*on,1)"
    if punch:
        return (
            f"min(1+{rate:.6f}*on+{punch:.4f}*exp(-on/{fps}*6.0),{max_zoom:.5f})"
        )
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


def _saturation_expr(base: float, punch: float, fps: int) -> str:
    """Per-segment saturation that starts at ``base + punch`` and decays to
    ``base`` over roughly 0.3 s, mirroring the `beat_punch` zoom profile.

    Each segment is its own input stream, so `n` (the eq filter's frame counter)
    resets to 0 at the cut and the spike lands on the downbeat."""
    return (
        f"eq=saturation='{base:.4f}+{punch:.4f}*exp(-n/{fps}*6.0)':eval=frame"
    )


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
    finalize_auto_style(track, beatmap)
    style = track.style
    fps = int(style.get("fps", 30))
    width, height = aspect.width, aspect.height
    if window is None:
        window = window_for(track, beatmap, aspect)
    fingerprint = render_fingerprint(
        track, aspect, beatmap, window, preview=preview, textures_dir=textures_dir
    )
    if preview:
        width, height = max(1, width // PREVIEW_SCALE), max(1, height // PREVIEW_SCALE)
        aspect = Aspect(aspect.key, width, height, aspect.label, aspect.kind)
        full_plan = plan_timeline(
            beatmap, window, len(track.images), style, track.overrides
        )
        plan = preview_slice(full_plan, len(track.images), PREVIEW_SECONDS)
    else:
        plan = plan_timeline(
            beatmap, window, len(track.images), style, track.overrides
        )
    window_start, window_end = plan.window
    clip_duration = plan.duration
    segments, xfade = plan.segments, plan.xfade
    n = len(segments)

    font_face = resolve_face(
        style.get("font") or "clean", style.get("title_font")
    )
    meta_name = style.get("meta_font") or "auto"
    meta_face = (
        font_face
        if meta_name == "auto"
        else resolve_face(meta_name, style.get("title_font"))
    )
    max_zoom = 1.0 + float(style.get("zoom", 0.10))
    punch = float(style.get("beat_punch", 0.0))
    color_punch = float(style.get("color_punch", 0.0))
    base_saturation = float(style.get("saturation", 1.0))
    supersample = int(style.get("supersample", 2))

    tmp = Path(tempfile.mkdtemp(prefix="vp-"))
    try:
        hud = tmp / "hud.png"
        vgm_cfg = VgmConfig.from_style(style)
        vgm_img = (
            build_vgm_overlay((aspect.width, aspect.height), vgm_cfg)
            if vgm_cfg.active
            else None
        )
        build_hud(
            hud,
            aspect,
            track.title,
            track.artist,
            track.handle,
            font_face=font_face,
            meta_font_face=meta_face,
            text_case=str(style.get("text_case", "preserve")),
            tracking=float(style.get("tracking", 0.0)),
            line_spacing=float(style.get("line_spacing", 1.12)),
            show_title=bool(style.get("show_title", True)),
            overlay=vgm_img,
        )

        texture_path = resolve_texture(style.get("texture"), textures_dir)

        # ---- inputs: one image per segment, then audio, then HUD overlay ----
        # Track file indices via an explicit counter (every input adds ONE "-i").
        inputs: list[str] = []
        file_count = 0
        for seg in segments:
            inputs += ["-i", str(track.images[seg.image_index])]
            file_count += 1
        audio_idx = None
        if not preview:
            audio_idx = file_count
            inputs += ["-i", str(track.audio)]
            file_count += 1
        hud_idx = file_count
        inputs += [
            "-loop", "1", "-framerate", str(fps), "-t", f"{clip_duration:.3f}",
            "-i", str(hud),
        ]
        file_count += 1
        tex_idx = None
        if texture_path:
            tex_idx = file_count
            file_count += 1
            tex = tmp / "texture.png"
            _cover_image(texture_path, width, height, tex)
            inputs += [
                "-loop", "1", "-framerate", str(fps), "-t",
                f"{clip_duration:.3f}", "-i", str(tex),
            ]

        wf_idx = None
        strip_img = None
        if style.get("waveform"):
            wf_idx = file_count
            file_count += 1
            strip_h = max(64, int(height * 0.07))
            strip_img = build_strip(
                track.audio, window, width * 2, strip_h, beatmap
            )
            wf = tmp / "waveform.png"
            strip_img.save(wf)
            inputs += [
                "-loop", "1", "-framerate", str(fps), "-t",
                f"{clip_duration:.3f}", "-i", str(wf),
            ]

        parts: list[str] = []
        prev = "v0"
        for i, seg in enumerate(segments):
            frames = max(2, math.ceil(seg.duration * fps) + 2)
            zexpr = _zoom_expr(i, fps, frames, max_zoom, punch)
            chain = [
                f"scale={width * supersample}:{height * supersample}:"
                f"force_original_aspect_ratio=increase",
                f"crop={width * supersample}:{height * supersample}",
                "setsar=1",
                f"zoompan=z='{zexpr}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
                f"d={frames}:s={width}x{height}:fps={fps}",
            ]
            if color_punch > 0:
                # Baseline follows the segment's energy; the punch decays from
                # the cut. `n` is this segment's own frame counter.
                abs_start = window_start + seg.offset
                base = _energy_saturation(
                    _mean_beat_energy(beatmap, abs_start, abs_start + seg.duration),
                    base_saturation,
                )
                chain.append(_saturation_expr(base, color_punch, fps))
            chain += ["settb=AVTB", "setpts=PTS-STARTPTS"]
            parts.append(f"[{i}:v]" + ",".join(chain) + f"[v{i}]")

        for i in range(1, n):
            label = f"x{i}"
            parts.append(
                f"[{prev}][v{i}]xfade=transition=fade:duration={xfade:.3f}:"
                f"offset={segments[i].offset:.3f}[{label}]"
            )
            prev = label

        audio_src = f"[{audio_idx}:a]" if audio_idx is not None else None

        if texture_path:
            parts.append(
                f"[{tex_idx}:v]format=rgb24,settb=AVTB,setpts=PTS-STARTPTS[tex]"
            )
            parts.append(f"[{prev}]format=rgb24[base]")
            blend_mode = style.get("texture_blend", "multiply")
            parts.append(f"[tex][base]blend=all_mode={blend_mode}[comp]")
            prev = "comp"

        parts.append(
            f"[{hud_idx}:v]format=rgba,"
            f"fade=t=in:st={HUD_FADE_IN}:d={HUD_FADE_DUR}:alpha=1,"
            f"settb=AVTB,setpts=PTS-STARTPTS[hud]"
        )
        parts.append(f"[{prev}][hud]overlay=0:0:format=auto[vg]")
        prev = "vg"

        if strip_img is not None:
            strip_h = strip_img.height
            margin = max(24, int(height * 0.02))
            y = height - strip_h - margin
            parts.append(
                f"[{wf_idx}:v]format=rgba,"
                f"fade=t=in:st={HUD_FADE_IN}:d={HUD_FADE_DUR}:alpha=1,"
                f"settb=AVTB,setpts=PTS-STARTPTS[wf]"
            )
            xexpr = f"-(t/{clip_duration:.3f})*{width}"
            parts.append(
                f"[{prev}][wf]overlay=x='{xexpr}':y={y}:format=auto[wv]"
            )
            prev = "wv"

        finish: list[str] = []
        grade = GRADES.get(style.get("grade", "none"))
        if grade:
            finish.append(grade)
        saturation = float(style.get("saturation", 1.0))
        if color_punch <= 0 and saturation != 1.0:
            # When color_punch is active the per-segment eq already carries the
            # base saturation, so only apply a static grade when it is off.
            # 0.0 must reproduce a true monochrome phase: eq=saturation=0 yields
            # R=G=B per-frame (the reference's hard grayscale cut).
            finish.append(f"eq=saturation={saturation:.3f}")
        vignette = float(style.get("vignette", 0.0))
        if vignette > 0:
            angle = f"(PI/5)*(1+{vignette:.3f}*1.6)"
            finish.append(f"vignette=angle={angle}")
        grain = float(style.get("grain", 0.0))
        if grain > 0:
            strength = int(grain * 25)
            finish.append(f"noise=alls={strength}:allf=t+u:all_seed={GRAIN_SEED}")
        contrast = float(style.get("contrast", 1.0))
        if contrast != 1.0:
            finish.append(f"eq=contrast={contrast:.3f}")

        fade_in = float(style.get("fade_in", 0.4))
        fade_out = float(style.get("fade_out", 0.8))
        if not preview:
            finish += [
                f"fade=t=in:st=0:d={fade_in:.3f}",
                f"fade=t=out:st={clip_duration - fade_out:.3f}:d={fade_out:.3f}",
            ]
        finish.append("scale=out_range=tv,format=yuv420p")
        parts.append(f"[{prev}]format=rgb24," + ",".join(finish) + "[vout]")
        if audio_src is not None:
            parts.append(
                f"{audio_src}atrim={window_start:.3f}:{window_end:.3f},asetpts=PTS-STARTPTS,"
                f"loudnorm=I=-14:TP=-1.5:LRA=11,aresample=48000,"
                f"aformat=channel_layouts=stereo,"
                f"afade=t=in:st=0:d={fade_in:.3f},"
                f"afade=t=out:st={clip_duration - fade_out:.3f}:d={fade_out:.3f}[aout]"
            )

        out_path.parent.mkdir(parents=True, exist_ok=True)
        invalidate(out_path)
        maps = ["-map", "[vout]"] + ([] if audio_src is None else ["-map", "[aout]"])
        enc = [
            "-c:v", "libx264",
            "-preset", "veryfast" if preview else "medium",
            "-crf", "23" if preview else "18",
        ]
        audio_enc = [] if audio_src is None else ["-c:a", "aac", "-b:a", "192k"]
        cmd = [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
            *inputs,
            "-filter_complex", ";".join(parts),
            *maps,
            *enc,
            "-pix_fmt", "yuv420p", "-r", str(fps),
            *audio_enc,
            "-movflags", "+faststart",
            "-t", f"{clip_duration:.3f}",
            str(out_path),
        ]
        def _run_with_progress() -> tuple[int, str]:
            if cancel is not None and cancel():
                raise RenderCancelled("render cancelled")
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
                    pct = _progress_percent(line, clip_duration)
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

        if progress is None and cancel is None:
            proc = subprocess.run(cmd, capture_output=True, text=True)
            returncode, stderr_text = proc.returncode, proc.stderr or ""
        else:
            if progress is not None:
                progress(0.0, "rendering")
            returncode, stderr_text = _run_with_progress()
            if returncode == 0 and progress is not None:
                progress(100.0, "done")

        if returncode != 0:
            tail = "\n".join(stderr_text.strip().splitlines()[-40:])
            raise RuntimeError(f"ffmpeg failed (exit {returncode}):\n{tail}")
        record(out_path, fingerprint)
        return out_path
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def plan_variants(
    track: Track, beatmap: dict, out_dir: Path, aspects: list[str]
) -> list[Variant]:
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
                path=out_dir / f"{track.slug}_{label}.mp4",
            )
        )
    return variants


def render_aspects(
    track: Track, beatmap: dict, out_dir: Path, aspects: list[str]
) -> list[Path]:
    outputs = []
    for variant in plan_variants(track, beatmap, out_dir, aspects):
        render(
            track, variant.aspect, beatmap, variant.path, window=variant.window
        )
        outputs.append(variant.path)
    return outputs