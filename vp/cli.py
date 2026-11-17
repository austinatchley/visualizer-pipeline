"""Command line interface: vp analyze | inspect | render | batch | init."""

from __future__ import annotations

import argparse
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from .analyze import beatmap_cache_path, load_beatmap
from .aspects import ASPECTS
from .config import PRESETS_DIR, STYLE_FIELDS
from .manifest import IMAGES_DIRNAME, discover_tracks, manifest_path, write_manifest
from .plan import plan_timeline
from .render import plan_variants, render
from .status import is_current, render_fingerprint
from .track import load_track
from .vibe import finalize_auto_style


def _aspects(value: str) -> list[str]:
    if value == "all":
        return ["horizontal", "vertical", "square"]
    if value == "both":
        return ["vertical", "horizontal"]
    if value not in ASPECTS:
        raise ValueError(f"invalid aspect: {value}")
    return [value]


def _style_flag(key: str) -> str:
    return "--" + key.replace("_", "-")


def _add_style_args(p: argparse.ArgumentParser) -> None:
    """One flag per `config.STYLE_FIELDS` key, generated so the CLI can't drift
    from the schema (issue #48). Values are validated by `resolve_style`;
    `choices` also gives early errors and shows options in --help."""
    group = p.add_argument_group("style", "override any style key for this run")
    for key, spec in STYLE_FIELDS.items():
        kind = spec["type"]
        help_text = spec.get("help", "").replace("%", "%%")
        kwargs: dict = {"dest": key, "default": None}
        if kind == "bool":
            kwargs["action"] = argparse.BooleanOptionalAction
        elif kind == "int":
            kwargs["type"] = int
            kwargs["metavar"] = "N"
            help_text += f" [{spec['min']}, {spec['max']}]"
        elif kind == "float":
            kwargs["type"] = float
            kwargs["metavar"] = "X"
            help_text += f" [{spec['min']}, {spec['max']}]"
        elif kind == "choice":
            choices = spec["choices"]
            if all(isinstance(c, int) for c in choices):
                kwargs["type"] = int
            kwargs["choices"] = choices
        elif kind == "preset":
            kwargs["choices"] = _preset_choices()
        elif kind == "path":
            kwargs["metavar"] = "PATH"
        elif kind == "texture":
            kwargs["metavar"] = "NAME|PATH"
        else:  # text
            kwargs["metavar"] = "TEXT"
        group.add_argument(_style_flag(key), help=help_text, **kwargs)


def _preset_choices() -> list[str]:
    return sorted(p.stem for p in PRESETS_DIR.glob("*.json")) + ["auto"]


def _add_hook_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--hook", default=None, help="auto | full | START-END (seconds)")
    p.add_argument("--bpm", type=float)
    p.add_argument("--tempo-multiplier", type=float, dest="tempo_multiplier")


def _add_common_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--aspect", default="all", help="vertical | horizontal | square | all")
    p.add_argument("--out", help="output root directory")
    p.add_argument("--handle", help="social handle shown after the artist")
    _add_hook_args(p)
    _add_style_args(p)


def _style_overrides(args: argparse.Namespace) -> dict:
    return {key: getattr(args, key, None) for key in STYLE_FIELDS}


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
    return Path(base) / track.id if base else Path("out") / track.id


def _render_track(track, beatmap, args, aspects, verbose: bool = True):
    out_dir = _out_dir(track, args)
    variants = plan_variants(track, beatmap, out_dir, aspects)
    variant_jobs = max(1, int(getattr(args, "variant_jobs", 1) or 1))

    def work(variant):
        if not args.force and is_current(
            variant.path,
            render_fingerprint(track, variant.aspect, beatmap, variant.window),
        ):
            return variant, False
        render(track, variant.aspect, beatmap, variant.path, window=variant.window)
        return variant, True

    if variant_jobs > 1 and len(variants) > 1:
        # The 16:9 full render is the long pole while hooks are short; running
        # them concurrently cuts wall-clock for `--aspect all` (issue #12).
        # Finalize `auto` once up front so concurrent renders don't race.
        finalize_auto_style(track, beatmap)
        with ThreadPoolExecutor(max_workers=min(variant_jobs, len(variants))) as pool:
            results = list(pool.map(work, variants))
    else:
        results = [work(variant) for variant in variants]

    written: list[Path] = []
    for variant, did_render in results:
        if verbose:
            print(f"  {variant.path}" if did_render else f"  skip {variant.path}")
        if did_render:
            written.append(variant.path)
    return variants, written


def _cmd_analyze(args: argparse.Namespace) -> int:
    track = _load(Path(args.track), args)
    beatmap = load_beatmap(track, reanalyze=True)
    print(
        f"{track.title}: {beatmap['duration']:.1f}s, "
        f"{beatmap['tempo']:.1f} BPM, {len(beatmap['beats'])} beats"
    )
    print(f"wrote {beatmap_cache_path(track, track.overrides.get('bpm'))}")
    return 0


def _cmd_inspect(args: argparse.Namespace) -> int:
    track, beatmap = _prepare(Path(args.track), args)
    requested = track.style.get("preset")
    finalize_auto_style(track, beatmap)
    out_dir = _out_dir(track, args)
    variants = plan_variants(track, beatmap, out_dir, _aspects(args.aspect))

    print(f"{track.title} ({track.id})")
    print(
        f"  audio: {track.audio.name}  {beatmap['duration']:.2f}s  "
        f"{beatmap['tempo']:.1f} BPM  {len(beatmap['beats'])} beats"
    )
    preset_note = f"{track.style['preset']}"
    if requested == "auto":
        preset_note += " (auto from audio)"
    print(f"  preset: {preset_note}  fps: {track.style['fps']}  images: {len(track.images)}")
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
    print(f"rendering {track.title} ({track.id})")
    _, written = _render_track(track, beatmap, args, _aspects(args.aspect))
    if not written:
        print("  all outputs up to date")
    return 0


def _cmd_batch(args: argparse.Namespace) -> int:
    root = Path(args.root)
    if not root.is_dir():
        print(f"not a directory: {root}", file=sys.stderr)
        return 2

    tracks = discover_tracks(root)
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
        print(f"{track.title} ({track.id}): {len(written)} rendered")
        for path in written:
            print(f"  {path}")

    print(f"done: {len(tracks) - failures}/{len(tracks)} tracks rendered")
    return 1 if failures else 0


def _cmd_init(args: argparse.Namespace) -> int:
    folder = Path(args.track)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / IMAGES_DIRNAME).mkdir(exist_ok=True)

    if not manifest_path(folder).exists():
        title = folder.name.replace("_", " ").replace("-", " ").strip().title()
        write_manifest(folder, {"title": title, "artist": None, "handle": None})

    print(f"initialized {folder}")
    print(f"  add audio to {folder}/ and images to {folder}/images/")
    print(f"  then run: vp render {folder}")
    return 0


def _cmd_ui(args: argparse.Namespace) -> int:
    try:
        import uvicorn
    except ModuleNotFoundError as exc:  # pragma: no cover - env-dependent
        raise ValueError("the web UI needs optional deps: pip install -e '.[ui]'") from exc
    from .ui.server import create_app

    app = create_app(args.tracks, args.out, args.textures)
    url = f"http://{args.host}:{args.port}"
    if not args.no_browser:
        import webbrowser

        webbrowser.open(url)
    print(f"os.10.vs running at {url} (Ctrl-C to stop)")
    try:
        uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
    except KeyboardInterrupt:
        pass
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="vp", description="os.10.vs — beat visualizer studio")
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
    p_render.add_argument(
        "--variant-jobs",
        type=int,
        default=1,
        dest="variant_jobs",
        help="render aspects concurrently (default 1; try 2-3 for --aspect all)",
    )
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

    p_ui = sub.add_parser("ui", help="launch os.10.vs (local web studio)")
    p_ui.add_argument("--host", default="127.0.0.1")
    p_ui.add_argument("--port", type=int, default=8321)
    p_ui.add_argument("--tracks", default="tracks", help="root directory of track folders")
    p_ui.add_argument("--out", default="out", help="output root directory")
    p_ui.add_argument("--textures", default="textures", help="texture library directory")
    p_ui.add_argument("--no-browser", action="store_true")
    p_ui.set_defaults(func=_cmd_ui)

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
