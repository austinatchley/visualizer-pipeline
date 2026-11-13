"""Local web UI for vp: a thin FastAPI client over the engine.

The UI never re-implements config/planning/rendering; it drives the same
`resolve_style` + `render()` functions the CLI uses, so previews and exports are
identical to a headless run.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from ..analyze import load_beatmap
from ..aspects import ASPECTS
from ..config import DEFAULT_STYLE, PRESETS_DIR, STYLE_FIELDS, resolve_style
from ..render import render, window_for
from ..track import AUDIO_EXTS, load_track

STATIC_DIR = Path(__file__).parent / "static"
_render_lock = threading.Lock()


def _track_folders(root: Path):
    if not root.is_dir():
        return
    for folder in sorted(root.iterdir()):
        if not folder.is_dir():
            continue
        if any(
            p.is_file() and p.suffix.lower() in AUDIO_EXTS for p in folder.iterdir()
        ):
            yield folder


def _find_track(root: Path, slug: str):
    for folder in _track_folders(root):
        try:
            track = load_track(folder)
        except Exception:
            continue
        if track.slug == slug:
            return track
    raise HTTPException(status_code=404, detail=f"track {slug!r} not found")


def _manifest(path: Path) -> dict:
    if path.is_file():
        return json.loads(path.read_text())
    return {}


def _write_manifest(path: Path, manifest: dict) -> None:
    path.write_text(json.dumps(manifest, indent=2) + "\n")


class StylePayload(BaseModel):
    style: dict[str, object]


class PreviewPayload(BaseModel):
    aspect: str = "vertical"
    style: dict[str, object]


def create_app(root: str | Path = "tracks", out: str | Path = "out") -> FastAPI:
    root = Path(root)
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "preview").mkdir(parents=True, exist_ok=True)

    app = FastAPI(title="vp studio", version="0.1.0")
    app.state.root = root
    app.state.out = out

    @app.get("/api/boot")
    def boot():
        return {
            "fields": STYLE_FIELDS,
            "defaults": DEFAULT_STYLE,
            "presets": sorted(p.stem for p in PRESETS_DIR.glob("*.json")),
            "aspects": {k: {"label": a.label, "kind": a.kind} for k, a in ASPECTS.items()},
        }

    @app.get("/api/style")
    def style_for_preset(preset: str):
        try:
            return resolve_style(preset=preset)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None

    @app.get("/api/tracks")
    def list_tracks():
        tracks = []
        for folder in _track_folders(root):
            try:
                track = load_track(folder)
                tracks.append(
                    {
                        "slug": track.slug,
                        "title": track.title,
                        "artist": track.artist,
                        "path": str(folder),
                        "audio": track.audio.name,
                        "image_count": len(track.images),
                        "preset": track.style.get("preset"),
                    }
                )
            except Exception as exc:  # keep the catalog usable
                tracks.append(
                    {
                        "slug": folder.name,
                        "title": folder.name,
                        "path": str(folder),
                        "error": str(exc),
                    }
                )
        return tracks

    @app.get("/api/tracks/{slug}")
    def track_detail(slug: str):
        track = _find_track(root, slug)
        return {
            "slug": track.slug,
            "title": track.title,
            "artist": track.artist,
            "handle": track.handle,
            "path": str(track.root),
            "audio": track.audio.name,
            "images": [p.name for p in track.images],
            "image_count": len(track.images),
            "style": track.style,
            "hook": track.hook,
        }

    @app.post("/api/tracks/{slug}/style")
    def save_style(slug: str, payload: StylePayload):
        track = _find_track(root, slug)
        try:
            resolved = resolve_style(payload.style)
            baseline = resolve_style(preset=resolved["preset"])
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None

        # Persist only values that differ from what the preset alone would give, so
        # track.json stays minimal and preset changes still propagate.
        saved = {
            k: v
            for k, v in resolved.items()
            if k != "preset" and resolved[k] != baseline.get(k)
        }
        if resolved["preset"] != DEFAULT_STYLE["preset"]:
            saved["preset"] = resolved["preset"]

        manifest = _manifest(track.root / "track.json")
        manifest["style"] = saved
        _write_manifest(track.root / "track.json", manifest)
        return {"saved": saved, "resolved": resolved}

    @app.post("/api/tracks/{slug}/preview")
    def render_preview(slug: str, payload: PreviewPayload):
        if payload.aspect not in ASPECTS:
            raise HTTPException(status_code=400, detail=f"unknown aspect: {payload.aspect}")
        track = _find_track(root, slug)
        try:
            track.style = resolve_style(payload.style)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None

        beatmap = load_beatmap(track)
        aspect = ASPECTS[payload.aspect]

        dest = out / "preview" / f"{track.slug}_{payload.aspect}.mp4"
        with _render_lock:
            render(track, aspect, beatmap, dest, window=window_for(track, beatmap, aspect), preview=True)
        return {"url": f"/api/files/preview/{dest.name}"}

    app.mount("/api/files", StaticFiles(directory=str(out)), name="files")

    @app.get("/")
    def index():
        return FileResponse(str(STATIC_DIR / "index.html"))

    app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")
    return app