"""Command line interface: vp analyze | render | batch."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .analyze import load_beatmap
from .render import ASPECTS, render_aspects
from .track import AUDIO_EXTS, load_track


def _add_style_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--beats-per-cut", type=int, dest="beats_per_cut")
    p.add_argument("--crossfade", type=float)
    p.add_argument("--zoom", type=float)
    p.add_argument("--fps", type=int)
    p.add_argument("--font", dest="title_font")
    p.add_argument("--waveform", action=argparse.BooleanOptionalAction, default=None)


def _style_overrides(args: argparse.Namespace) -> dict:
    return {
        "beats_per_cut": getattr(args, "beats_per_cut", None),
        "crossfade": getattr(args, "crossfade", None),
        "zoom": getattr(args, "zoom", None),
        "fps": getattr(args, "fps", None),
        "title_font": getattr(args, "title_font", None),
        "waveform": getattr(args, "waveform", None),
    }


def _aspects(value: str) -> list[str]:
    if value == "both":
        return ["vertical", "horizontal"]
    if value not in ASPECTS:
        raise argparse.ArgumentTypeError(f"invalid aspect: {value}")
    return [value]


def _cmd_analyze(args: argparse.Namespace) -> int:
    track = load_track(Path(args.track))
    beatmap = load_beatmap(track, reanalyze=True)
    print(
        f"{track.title}: {beatmap['duration']:.1f}s, "
        f"{beatmap['tempo']:.1f} BPM, {len(beatmap['beats'])} beats"
    )
    print(f"wrote {track.beatmap_path}")
    return 0


def _cmd_render(args: argparse.Namespace) -> int:
    track = load_track(Path(args.track), _style_overrides(args))
    beatmap = load_beatmap(track, reanalyze=args.reanalyze)
    out_dir = Path(args.out) if args.out else Path("out") / track.slug
    print(f"rendering {track.title} ({track.slug})")
    for out in render_aspects(track, beatmap, out_dir, _aspects(args.aspect)):
        print(f"  {out}")
    return 0


def _looks_like_track(path: Path) -> bool:
    if any(p.suffix.lower() in AUDIO_EXTS for p in path.iterdir() if p.is_file()):
        return True
    return False


def _cmd_batch(args: argparse.Namespace) -> int:
    root = Path(args.root)
    if not root.is_dir():
        print(f"not a directory: {root}", file=sys.stderr)
        return 2

    tracks = sorted(p for p in root.iterdir() if p.is_dir() and _looks_like_track(p))
    if not tracks:
        print(f"no track folders found under {root}", file=sys.stderr)
        return 2

    aspects = _aspects(args.aspect)
    failures = 0
    for folder in tracks:
        try:
            track = load_track(folder, _style_overrides(args))
            beatmap = load_beatmap(track, reanalyze=args.reanalyze)
            out_dir = Path(args.out) / track.slug if args.out else Path("out") / track.slug
            print(f"rendering {track.title} ({track.slug})")
            for out in render_aspects(track, beatmap, out_dir, aspects):
                print(f"  {out}")
        except Exception as exc:  # noqa: BLE001 - keep batch going
            failures += 1
            print(f"  FAILED {folder.name}: {exc}", file=sys.stderr)

    print(f"done: {len(tracks) - failures}/{len(tracks)} tracks rendered")
    return 1 if failures else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="vp", description="Beat visualizer pipeline")
    sub = parser.add_subparsers(dest="command", required=True)

    p_analyze = sub.add_parser("analyze", help="analyze a track's audio")
    p_analyze.add_argument("track", help="track folder")
    p_analyze.set_defaults(func=_cmd_analyze)

    p_render = sub.add_parser("render", help="render a single track")
    p_render.add_argument("track", help="track folder")
    p_render.add_argument("--aspect", default="both", help="vertical | horizontal | both")
    p_render.add_argument("--out", help="output directory")
    p_render.add_argument("--reanalyze", action="store_true", help="ignore cached beatmap")
    _add_style_args(p_render)
    p_render.set_defaults(func=_cmd_render)

    p_batch = sub.add_parser("batch", help="render every track folder under a directory")
    p_batch.add_argument("root", help="directory containing track folders")
    p_batch.add_argument("--aspect", default="both", help="vertical | horizontal | both")
    p_batch.add_argument("--out", help="output root directory")
    p_batch.add_argument("--reanalyze", action="store_true", help="ignore cached beatmaps")
    _add_style_args(p_batch)
    p_batch.set_defaults(func=_cmd_batch)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except (FileNotFoundError, NotADirectoryError, ValueError, RuntimeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())