"""FFmpeg renderer: beat-synced Ken Burns slideshow for vertical + horizontal."""

from __future__ import annotations

import math
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from .plan import full_window, plan_timeline
from .track import Track

FONT_CANDIDATES = [
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/System/Library/Fonts/Supplemental/Impact.ttf",
    "/System/Library/Fonts/Supplemental/Arial Black.ttf",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
]


@dataclass(frozen=True)
class Aspect:
    key: str
    width: int
    height: int
    label: str


VERTICAL = Aspect("vertical", 1080, 1920, "9x16")
HORIZONTAL = Aspect("horizontal", 1920, 1080, "16x9")
ASPECTS = {"vertical": VERTICAL, "horizontal": HORIZONTAL}

HUD_FADE_IN = 0.3
HUD_FADE_DUR = 0.8


def _find_font(override: str | None) -> str:
    if override:
        if Path(override).is_file():
            return override
        raise FileNotFoundError(f"title_font not found: {override}")
    for candidate in FONT_CANDIDATES:
        if Path(candidate).is_file():
            return candidate
    raise FileNotFoundError("no usable system font found; set style.title_font")


def _wrap_to_width(text: str, font: ImageFont.FreeTypeFont, max_width: int) -> list[str]:
    lines: list[str] = []
    current = ""
    for word in text.split():
        trial = f"{current} {word}".strip()
        if not current or font.getlength(trial) <= max_width:
            current = trial
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def _draw_text_block(
    draw: ImageDraw.ImageDraw,
    lines: list[str],
    font: ImageFont.FreeTypeFont,
    x: int,
    top: int,
    fill: tuple,
    shadow: tuple,
    align: str,
    shadow_offset: int,
) -> int:
    ascent, descent = font.getmetrics()
    line_h = int((ascent + descent) * 1.12)
    y = top
    for line in lines:
        w = font.getlength(line)
        tx = x if align == "left" else x - w / 2
        draw.text((tx + shadow_offset, y + shadow_offset), line, font=font, fill=shadow)
        draw.text((tx, y), line, font=font, fill=fill)
        y += line_h
    return y


def _make_overlay(
    path: Path,
    width: int,
    height: int,
    title: str,
    artist: str | None,
    font_path: str,
    aspect: Aspect,
) -> None:
    canvas = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(canvas)

    start = int(height * 0.52)
    ramp = np.linspace(0.0, 1.0, height - start) ** 1.7
    alpha_vals = (ramp * 190).astype(np.uint8)
    arr = np.array(canvas)
    arr[start:, :, 3] = np.maximum(arr[start:, :, 3], alpha_vals[:, None])
    canvas = Image.fromarray(arr, "RGBA")
    draw = ImageDraw.Draw(canvas)

    if aspect is VERTICAL:
        fontsize = int(width * 0.085)
        max_width = int(width * 0.86)
        block_bottom = int(height * 0.80)
        align = "center"
        x = width // 2
    else:
        fontsize = int(height * 0.075)
        max_width = int(width * 0.55)
        block_bottom = int(height * 0.87)
        align = "left"
        x = int(width * 0.06)

    font = ImageFont.truetype(font_path, fontsize)
    lines = _wrap_to_width(title, font, max_width)

    ascent, descent = font.getmetrics()
    line_h = int((ascent + descent) * 1.12)
    title_h = line_h * len(lines)
    title_top = block_bottom - title_h

    shadow_offset = max(2, fontsize // 40)
    _draw_text_block(
        draw, lines, font, x, title_top,
        fill=(255, 255, 255, 255),
        shadow=(0, 0, 0, 150),
        align=align,
        shadow_offset=shadow_offset,
    )

    if artist:
        afontsize = int(fontsize * 0.42)
        afont = ImageFont.truetype(font_path, afontsize)
        ay = title_top - int(fontsize * 0.55)
        _draw_text_block(
            draw, [artist], afont, x, ay,
            fill=(255, 255, 255, 200),
            shadow=(0, 0, 0, 120),
            align=align,
            shadow_offset=max(1, afontsize // 40),
        )

    canvas.save(path)


def render(track: Track, aspect: Aspect, beatmap: dict, out_path: Path) -> Path:
    style = track.style
    fps = int(style.get("fps", 30))
    width, height = aspect.width, aspect.height
    duration = float(beatmap["duration"])

    plan = plan_timeline(
        beatmap, full_window(duration), len(track.images), style, track.overrides
    )
    segments, xfade = plan.segments, plan.xfade
    n = len(segments)

    font = _find_font(style.get("title_font"))
    max_zoom = 1.0 + float(style.get("zoom", 0.10))

    tmp = Path(tempfile.mkdtemp(prefix="vp-"))
    try:
        hud = tmp / "hud.png"
        _make_overlay(hud, width, height, track.title, track.artist, font, aspect)

        # ---- inputs: one image per segment, then audio, then HUD overlay ----
        inputs: list[str] = []
        for seg in segments:
            inputs += ["-i", str(track.images[seg.image_index])]
        audio_idx = n
        inputs += ["-i", str(track.audio)]
        hud_idx = n + 1
        inputs += [
            "-loop", "1", "-framerate", str(fps), "-t", f"{duration:.3f}",
            "-i", str(hud),
        ]

        parts: list[str] = []
        prev = "v0"
        for i, seg in enumerate(segments):
            frames = max(2, math.ceil(seg.duration * fps) + 2)
            rate = (max_zoom - 1.0) / max(frames - 1, 1)
            if i % 2 == 0:
                zexpr = f"min(1+{rate:.6f}*on,{max_zoom:.5f})"
            else:
                zexpr = f"max({max_zoom:.5f}-{rate:.6f}*on,1)"
            parts.append(
                f"[{i}:v]scale={width * 2}:{height * 2}:force_original_aspect_ratio=increase,"
                f"crop={width * 2}:{height * 2},setsar=1,"
                f"zoompan=z='{zexpr}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
                f"d={frames}:s={width}x{height}:fps={fps},"
                f"settb=AVTB,setpts=PTS-STARTPTS[v{i}]"
            )

        for i in range(1, n):
            label = f"x{i}"
            parts.append(
                f"[{prev}][v{i}]xfade=transition=fade:duration={xfade:.3f}:"
                f"offset={segments[i].offset:.3f}[{label}]"
            )
            prev = label

        waveform = bool(style.get("waveform", False))
        if waveform:
            wave_h = int(height * 0.09)
            wave_h -= wave_h % 2
            parts.append(f"[{audio_idx}:a]asplit=2[aw][am]")
            parts.append(
                f"[aw]showwaves=s={width}x{wave_h}:mode=cline:rate={fps}:colors=white,"
                f"fps={fps},format=yuva420p,colorchannelmixer=aa=0.5,"
                f"settb=AVTB,setpts=PTS-STARTPTS[wave]"
            )
            parts.append(f"[{prev}][wave]overlay=0:{height - wave_h}:format=auto[vw]")
            prev = "vw"
            audio_src = "[am]"
        else:
            audio_src = f"[{audio_idx}:a]"

        parts.append(
            f"[{hud_idx}:v]format=rgba,"
            f"fade=t=in:st={HUD_FADE_IN}:d={HUD_FADE_DUR}:alpha=1,"
            f"settb=AVTB,setpts=PTS-STARTPTS[hud]"
        )
        parts.append(f"[{prev}][hud]overlay=0:0:format=auto[vg]")
        prev = "vg"

        fade_in = float(style.get("fade_in", 0.4))
        fade_out = float(style.get("fade_out", 0.8))
        parts.append(
            f"[{prev}]fade=t=in:st=0:d={fade_in:.3f},"
            f"fade=t=out:st={duration - fade_out:.3f}:d={fade_out:.3f},"
            f"format=yuv420p[vout]"
        )
        parts.append(
            f"{audio_src}atrim=0:{duration:.3f},asetpts=PTS-STARTPTS,"
            f"loudnorm=I=-14:TP=-1.5:LRA=11,"
            f"afade=t=in:st=0:d={fade_in:.3f},"
            f"afade=t=out:st={duration - fade_out:.3f}:d={fade_out:.3f}[aout]"
        )

        out_path.parent.mkdir(parents=True, exist_ok=True)
        cmd = [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
            *inputs,
            "-filter_complex", ";".join(parts),
            "-map", "[vout]", "-map", "[aout]",
            "-c:v", "libx264", "-preset", "medium", "-crf", "18",
            "-pix_fmt", "yuv420p", "-r", str(fps),
            "-c:a", "aac", "-b:a", "192k",
            "-movflags", "+faststart",
            "-t", f"{duration:.3f}",
            str(out_path),
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode != 0:
            tail = "\n".join(proc.stderr.strip().splitlines()[-25:])
            raise RuntimeError(f"ffmpeg failed:\n{tail}")
        return out_path
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def render_aspects(
    track: Track, beatmap: dict, out_dir: Path, aspects: list[str]
) -> list[Path]:
    outputs = []
    for key in aspects:
        aspect = ASPECTS[key]
        out = out_dir / f"{track.slug}_{aspect.label}.mp4"
        render(track, aspect, beatmap, out)
        outputs.append(out)
    return outputs