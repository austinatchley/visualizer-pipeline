"""FFmpeg renderer: beat-synced Ken Burns slideshow for vertical + horizontal."""

from __future__ import annotations

import math
import shutil
import subprocess
import tempfile
from pathlib import Path

from .aspects import ASPECTS, Aspect, HORIZONTAL, SQUARE, VERTICAL
from .hud import build_hud, find_font
from .plan import full_window, plan_timeline
from .track import Track

HUD_FADE_IN = 0.3
HUD_FADE_DUR = 0.8


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

    font = find_font(style.get("title_font"))
    max_zoom = 1.0 + float(style.get("zoom", 0.10))

    tmp = Path(tempfile.mkdtemp(prefix="vp-"))
    try:
        hud = tmp / "hud.png"
        build_hud(hud, aspect, track.title, track.artist, track.handle, font)

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