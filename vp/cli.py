"""Command line interface: vp analyze | inspect | render | batch | init."""

from __future__ import annotations

import argparse
import json
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from .analyze import load_beatmap
from .aspects import ASPECTS
from .plan import plan_timeline
from .render import plan_variants, render
from .track import AUDIO_EXTS, load_track


def _aspects(value: str) -> list[str]:
    if value == "all":
        return ["horizontal", "vertical", "square"]
    if value == "both":
        return ["vertical", "horizontal"]
    if value not in ASPECTS:
        raise ValueError(f"invalid aspect: {value}")
    return [value]


def _add_style_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--preset")
    p.add_argument("--beats-per-cut", type=int, dest="beats_per_cut")
    p.add_argument("--crossfade", type=float)
    p.add_argument("--zoom", type=float)
    p.add_argument("--fps", type=int)
    p.add_argument("--supersample", type=int)
    p.add_argument("--font", dest="title_font")
    p.add_argument("--waveform", action=argparse.BooleanOptionalAction, default=None)
    p.add_argument("--contrast", type=float)
    p.add_argument("--beat-punch", type=float, dest="beat_punch")
    p.add_argument("--vignette", type=float)
    p.add_argument("--grain", type=float)
    p.add_argument("--texture")
    p.add_argument("--texture-blend", dest="texture_blend")


def _add_hook_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--hook", default=None, help="auto | full | START-END (seconds)")
    p.add_argument("--handle")
    p.add_argument("--bpm", type=float)
    p.add_argument("--tempo-multiplier", type=float, dest="tempo_multiplier")


def _add_common_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--aspect", default="all", help="vertical | horizontal | square | all")
    p.add_argument("--out", help="output root directory")
    _add_style_args(p)
    _add_hook_args(p)


def _style_overrides(args: argparse.Namespace) -> dict:
    return {
        "preset": getattr(args, "preset", None),
        "beats_per_cut": getattr(args, "beats_per_cut", None),
        "crossfade": getattr(args, "crossfade", None),
        "zoom": getattr(args, "zoom", None),
        "fps": getattr(args, "fps", None),
        "supersample": getattr(args, "supersample", None),
        "title_font": getattr(args, "title_font", None),
        "waveform": getattr(args, "waveform", None),
        "contrast": getattr(args, "contrast", None),
        "beat_punch": getattr(args, "beat_punch", None),
        "vignette": getattr(args, "vignette", None),
        "grain": getattr(args, "grain", None),
        "texture": getattr(args, "texture", None),
        "texture_blend": getattr(args, "texture_blend", None),
    }


def _hook_flags(value: str | None) -> dict:
    if value is None:
        return {}
    if value in ("auto", "full"):
        return {"mode": value}
    if "-" in value:
        left, right = value.split("-", 1)
        try:
            start, end = float(left), float(right)
        except ValueError:
            raise ValueError(f"invalid --hook: {value}") from None
        if start < 0 or end <= start:
            raise ValueError("--hook must satisfy 0 <= START < END")
        return {"start": start, "end": end, "mode": "manual"}
    raise ValueError(f"invalid --hook: {value}")


def _flags(args: argparse.Namespace) -> dict:
    flags = _style_overrides(args)
    flags.update(_hook_flags(getattr(args, "hook", None)))
    if getattr(args, "bpm", None) is not None:
        flags["bpm"] = args.bpm
    if getattr(args, "tempo_multiplier", None) is not None:
        flags["tempo_multiplier"] = args.tempo_multiplier
    return flags


def _load(folder: Path, args: argparse.Namespace):
    track = load_track(folder, _flags(args))
    if getattr(args, "handle", None):
        track.handle = args.handle
    return track


def _prepare(folder: Path, args: argparse.Namespace):
    track = _load(folder, args)
    beatmap = load_beatmap(track, reanalyze=getattr(args, "reanalyze", False))
    return track, beatmap


def _out_dir(track, args: argparse.Namespace) -> Path:
    base = getattr(args, "out", None)
    return Path(base) / track.slug if base else Path("out") / track.slug


def _input_mtime(track) -> float:
    paths = [track.audio, *track.images]
    for extra in (
        track.beatmap_path,
        track.root / "track.json",
        track.root / "title.txt",
    ):
        if extra.exists():
            paths.append(extra)
    return max(p.stat().st_mtime for p in paths if p.exists())


def _up_to_date(path: Path, mtime: float) -> bool:
    return path.exists() and path.stat().st_mtime >= mtime


def _render_track(track, beatmap, args, aspects, verbose: bool = True):
    out_dir = _out_dir(track, args)
    variants = plan_variants(track, beatmap, out_dir, aspects)
    mtime = _input_mtime(track)
    written: list[Path] = []

    for variant in variants:
        if not args.force and _up_to_date(variant.path, mtime):
            if verbose:
                print(f"  skip {variant.path}")
            continue
        render(track, variant.aspect, beatmap, variant.path, window=variant.window)
        if verbose:
            print(f"  {variant.path}")
        written.append(variant.path)

    return variants, written


def _cmd_analyze(args: argparse.Namespace) -> int:
    track = _load(Path(args.track), args)
    beatmap = load_beatmap(track, reanalyze=True)
    print(
        f"{track.title}: {beatmap['duration']:.1f}s, "
        f"{beatmap['tempo']:.1f} BPM, {len(beatmap['beats'])} beats"
    )
    if not track.overrides.get("bpm"):
        print(f"wrote {track.beatmap_path}")
    return 0


def _cmd_inspect(args: argparse.Namespace) -> int:
    track, beatmap = _prepare(Path(args.track), args)
    out_dir = _out_dir(track, args)
    variants = plan_variants(track, beatmap, out_dir, _aspects(args.aspect))

    print(f"{track.title} ({track.slug})")
    print(
        f"  audio: {track.audio.name}  {beatmap['duration']:.2f}s  "
        f"{beatmap['tempo']:.1f} BPM  {len(beatmap['beats'])} beats"
    )
    print(
        f"  preset: {track.style['preset']}  fps: {track.style['fps']}  "
        f"images: {len(track.images)}"
    )
    print(f"  hook: {track.hook['mode']}  target: {track.hook['target']:.0f}s")

    for variant in variants:
        plan = plan_timeline(
            beatmap, variant.window, len(track.images), track.style, track.overrides
        )
        kind = "hook" if variant.is_hook else "full"
        print(
            f"\n  {variant.aspect.label} ({kind}) "
            f"{variant.window[0]:.2f}-{variant.window[1]:.2f}s "
            f"({plan.duration:.2f}s) -> {variant.path}"
        )
        for boundary in plan.boundaries:
            print(f"    {boundary.time:7.2f}  {boundary.reason}")
    return 0


def _cmd_render(args: argparse.Namespace) -> int:
    track, beatmap = _prepare(Path(args.track), args)
    print(f"rendering {track.title} ({track.slug})")
    _, written = _render_track(track, beatmap, args, _aspects(args.aspect))
    if not written:
        print("  all outputs up to date")
    return 0


def _looks_like_track(path: Path) -> bool:
    return any(p.suffix.lower() in AUDIO_EXTS for p in path.iterdir() if p.is_file())


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

    def work(folder: Path):
        try:
            track, beatmap = _prepare(folder, args)
            _, written = _render_track(track, beatmap, args, aspects, verbose=False)
            return folder, track, written, None
        except Exception as exc:  # noqa: BLE001 - keep the batch going
            return folder, None, [], exc

    results = []
    jobs = max(1, int(args.jobs))
    if jobs == 1:
        for folder in tracks:
            results.append(work(folder))
    else:
        with ThreadPoolExecutor(max_workers=jobs) as pool:
            futures = [pool.submit(work, folder) for folder in tracks]
            for future in as_completed(futures):
                results.append(future.result())

    failures = 0
    for folder, track, written, exc in results:
        if exc is not None:
            failures += 1
            print(f"FAILED {folder.name}: {exc}", file=sys.stderr)
            continue
        print(f"{track.title} ({track.slug}): {len(written)} rendered")
        for path in written:
            print(f"  {path}")

    print(f"done: {len(tracks) - failures}/{len(tracks)} tracks rendered")
    return 1 if failures else 0


def _cmd_init(args: argparse.Namespace) -> int:
    folder = Path(args.track)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "images").mkdir(exist_ok=True)

    manifest = folder / "track.json"
    if not manifest.exists():
        title = folder.name.replace("_", " ").replace("-", " ").strip().title()
        manifest.write_text(
            json.dumps({"title": title, "artist": None, "handle": None}, indent=2)
            + "\n"
        )

    print(f"initialized {folder}")
    print(f"  add audio to {folder}/ and images to {folder}/images/")
    print(f"  then run: vp render {folder}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="vp", description="Beat visualizer pipeline")
    sub = parser.add_subparsers(dest="command", required=True)

    p_analyze = sub.add_parser("analyze", help="analyze a track's audio")
    p_analyze.add_argument("track")
    p_analyze.add_argument("--bpm", type=float)
    p_analyze.add_argument("--tempo-multiplier", type=float, dest="tempo_multiplier")
    p_analyze.set_defaults(func=_cmd_analyze)

    p_inspect = sub.add_parser("inspect", help="print the plan without rendering")
    p_inspect.add_argument("track")
    _add_common_args(p_inspect)
    p_inspect.add_argument("--reanalyze", action="store_true")
    p_inspect.set_defaults(func=_cmd_inspect)

    p_render = sub.add_parser("render", help="render a single track")
    p_render.add_argument("track")
    _add_common_args(p_render)
    p_render.add_argument("--force", action="store_true")
    p_render.add_argument("--reanalyze", action="store_true")
    p_render.set_defaults(func=_cmd_render)

    p_batch = sub.add_parser("batch", help="render every track folder under a directory")
    p_batch.add_argument("root")
    _add_common_args(p_batch)
    p_batch.add_argument("--jobs", type=int, default=1)
    p_batch.add_argument("--force", action="store_true")
    p_batch.add_argument("--reanalyze", action="store_true")
    p_batch.set_defaults(func=_cmd_batch)

    p_init = sub.add_parser("init", help="scaffold a new track folder")
    p_init.add_argument("track")
    p_init.set_defaults(func=_cmd_init)

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