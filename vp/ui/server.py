"""Local web UI for vp: a thin FastAPI client over the engine.

The UI never re-implements config/planning/rendering; it drives the same
`resolve_style` + `render()` functions the CLI uses, so previews and exports are
identical to a headless run.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from ..analyze import load_beatmap
from ..aspects import ASPECTS
from ..config import DEFAULT_STYLE, PRESETS_DIR, STYLE_FIELDS, resolve_style
from ..render import RenderCancelled, plan_variants, render, window_for
from ..status import input_mtime, is_up_to_date
from ..textures import resolve_texture, texture_library
from ..track import AUDIO_EXTS, IMAGE_EXTS, load_track

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


def _slugify(title: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", (title or "untitled").lower()).strip("-")
    return slug or "untitled"


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


def _find_track_folder(root: Path, slug: str) -> Path:
    """Locate a track folder by loaded slug or folder name (lenient, so broken
    tracks are still deletable from the UI catalog)."""
    for folder in _track_folders(root):
        if folder.name == slug:
            return folder
        try:
            if load_track(folder).slug == slug:
                return folder
        except Exception:
            continue
    raise HTTPException(status_code=404, detail=f"track {slug!r} not found")


def _image_dir(folder: Path) -> Path:
    img_dir = folder / "images"
    return img_dir if img_dir.is_dir() else folder


def _image_names(folder: Path) -> list[str]:
    """Ordered bare image filenames (UI-facing). Manifest stores root-relative paths."""
    manifest = _manifest(folder / "track.json")
    if manifest.get("images") is not None:
        return [
            Path(p).name
            for p in manifest["images"]
            if (folder / str(p)).is_file()
        ]
    d = _image_dir(folder)
    return sorted(
        p.name for p in d.iterdir() if p.is_file() and p.suffix.lower() in IMAGE_EXTS
    )


def _persist_order(folder: Path, names: list[str]) -> None:
    """Persist an explicit image order as root-relative paths in `track.json`."""
    d = _image_dir(folder)
    rel: list[str] = []
    for n in names:
        candidate = d / n
        rel.append(str(candidate.relative_to(folder)) if candidate.is_file() else n)
    manifest = _manifest(folder / "track.json")
    manifest["images"] = rel
    _write_manifest(folder / "track.json", manifest)


class StylePayload(BaseModel):
    style: dict[str, object]


class ImageNamesPayload(BaseModel):
    names: list[str]


class OrderPayload(BaseModel):
    order: list[str]


class RenderPayload(BaseModel):
    aspect: str = "vertical"
    aspects: list[str] | None = None
    style: dict[str, object]
    preview: bool = False


@dataclass
class Job:
    id: str
    slug: str
    aspect: str
    style: dict
    status: str = "pending"  # pending|running|done|skipped|cancelled|failed
    progress: float = 0.0
    error: str | None = None
    path: str | None = None
    preview: bool = False
    created: float = field(default_factory=time.time)
    started: float | None = None
    ended: float | None = None
    cancel: threading.Event = field(default_factory=threading.Event)
    status_lock: threading.Lock = field(
        default_factory=threading.Lock, repr=False, compare=False
    )


def _run_job(job: Job, root: Path, out: Path, textures: Path) -> None:
    try:
        track = _find_track(root, job.slug)
        track.style = resolve_style(job.style)
        aspect = ASPECTS[job.aspect]
        beatmap = load_beatmap(track)

        if job.preview:
            dest = out / "preview" / f"{track.slug}_{job.aspect}.mp4"
            window = window_for(track, beatmap, aspect)
        else:
            variant = plan_variants(track, beatmap, out / track.slug, [job.aspect])[0]
            dest, window = variant.path, variant.window
        job.path = str(dest)

        mtime = input_mtime(track)
        if is_up_to_date(dest, mtime):
            with job.status_lock:
                if job.status == "cancelled" or job.cancel.is_set():
                    job.status = "cancelled"
                else:
                    job.status, job.progress = "skipped", 100.0
            return

        def _progress(percent: float, _stage: str) -> None:
            job.progress = percent

        with _render_lock:
            with job.status_lock:
                if job.status == "cancelled" or job.cancel.is_set():
                    job.status = "cancelled"
                    return
                job.status = "running"
                job.started = time.time()
            render(
                track,
                aspect,
                beatmap,
                dest,
                window=window,
                preview=job.preview,
                progress=_progress,
                cancel=job.cancel.is_set,
                textures_dir=textures,
            )
        with job.status_lock:
            if job.status != "cancelled":
                job.status, job.progress = "done", 100.0
    except RenderCancelled:
        with job.status_lock:
            job.status = "cancelled"
    except Exception as exc:  # noqa: BLE001 - surface any failure to the UI
        with job.status_lock:
            if job.status == "cancelled" or job.cancel.is_set():
                job.status = "cancelled"
            else:
                job.status = "failed"
                job.error = str(exc)
    finally:
        with job.status_lock:
            job.ended = time.time()


def _job_summary(job: Job, out: Path) -> dict:
    with job.status_lock:
        status = job.status
        progress = job.progress
        error = job.error
        started = job.started
        ended = job.ended
        path_value = job.path
    url = None
    # Only expose a URL once the file is complete; publishing it mid-render lets
    # the browser latch onto a partially written mp4 that never recovers.
    if path_value and status in {"done", "skipped"}:
        path = Path(path_value)
        if path.exists():
            try:
                rel = os.path.relpath(path, out)
                url = f"/api/files/{rel}"
            except ValueError:
                url = None
    return {
        "id": job.id,
        "slug": job.slug,
        "aspect": job.aspect,
        "preview": job.preview,
        "status": status,
        "progress": round(progress, 1),
        "error": error,
        "url": url,
        "created": job.created,
        "started": started,
        "ended": ended,
    }


def create_app(
    root: str | Path = "tracks",
    out: str | Path = "out",
    textures: str | Path = "textures",
) -> FastAPI:
    root = Path(root)
    out = Path(out)
    textures = Path(textures)
    out.mkdir(parents=True, exist_ok=True)
    (out / "preview").mkdir(parents=True, exist_ok=True)

    app = FastAPI(title="os.10.vs", version="0.1.0")
    app.state.root = root
    app.state.out = out
    app.state.textures = textures
    app.state.jobs: dict[str, Job] = {}
    app.state.jobs_order: list[str] = []
    app.state.jobs_lock = threading.Lock()

    def _check_texture(style: dict) -> None:
        try:
            resolve_texture(style.get("texture"), app.state.textures)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None

    @app.get("/api/boot")
    def boot():
        return {
            "fields": STYLE_FIELDS,
            "defaults": DEFAULT_STYLE,
            "presets": sorted(p.stem for p in PRESETS_DIR.glob("*.json")),
            "textures": texture_library(app.state.textures),
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

    @app.post("/api/tracks")
    async def create_track(
        title: str = Form(""),
        audio: UploadFile | None = File(None),
        images: list[UploadFile] = File(...),
    ):
        root.mkdir(parents=True, exist_ok=True)
        if audio is None:
            raise HTTPException(status_code=400, detail="one audio file is required")
        if audio.filename is None or Path(audio.filename).suffix.lower() not in AUDIO_EXTS:
            raise HTTPException(
                status_code=400,
                detail=f"audio must be one of: {', '.join(sorted(AUDIO_EXTS))}",
            )
        images = [f for f in images if f.filename]
        if not images:
            raise HTTPException(status_code=400, detail="at least one image is required")
        bad = [
            f.filename
            for f in images
            if Path(f.filename).suffix.lower() not in IMAGE_EXTS
        ]
        if bad:
            raise HTTPException(
                status_code=400,
                detail=f"images must be one of: {', '.join(sorted(IMAGE_EXTS))} (got {', '.join(bad)})",
            )

        base = _slugify(title)
        folder = root / base
        n = 2
        while folder.exists():
            folder = root / f"{base}-{n}"
            n += 1
        folder.mkdir()
        (folder / "images").mkdir()
        try:
            p = folder / f"audio{Path(audio.filename).suffix.lower()}"
            with p.open("wb") as fh:
                shutil.copyfileobj(audio.file, fh)
            for img in images:
                name = re.sub(r"[^A-Za-z0-9._-]+", "-", Path(img.filename).name)
                with (folder / "images" / name).open("wb") as fh:
                    shutil.copyfileobj(img.file, fh)
            (folder / "track.json").write_text(
                json.dumps({"title": title or _slugify(title).replace("-", " ").title()}, indent=2)
                + "\n"
            )
        except Exception:
            shutil.rmtree(folder, ignore_errors=True)
            raise

        track = load_track(folder)
        return {
            "slug": track.slug,
            "title": track.title,
            "path": str(folder),
            "audio": track.audio.name,
            "image_count": len(track.images),
        }

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

    @app.delete("/api/tracks/{slug}")
    def delete_track(slug: str):
        folder = _find_track_folder(root, slug)
        with app.state.jobs_lock:
            for job in app.state.jobs.values():
                if job.slug == slug:
                    job.cancel.set()
        shutil.rmtree(folder)
        return {"deleted": slug}

    @app.get("/api/tracks/{slug}/images/{name}")
    def track_image(slug: str, name: str):
        track = _find_track(root, slug)
        safe = Path(name).name
        if safe != name:
            raise HTTPException(status_code=400, detail="invalid image name")
        candidates = [track.root / "images" / safe, track.root / safe]
        for p in candidates:
            if p.is_file():
                return FileResponse(str(p))
        raise HTTPException(status_code=404, detail=f"image {name!r} not found")

    @app.post("/api/tracks/{slug}/images")
    async def add_images(slug: str, images: list[UploadFile] = File(...)):
        track = _find_track(root, slug)
        bad = [
            f.filename
            for f in images
            if f.filename and Path(f.filename).suffix.lower() not in IMAGE_EXTS
        ]
        if bad:
            raise HTTPException(
                status_code=400,
                detail=f"images must be one of: {', '.join(sorted(IMAGE_EXTS))} (got {', '.join(bad)})",
            )
        img_dir = track.root / "images"
        img_dir.mkdir(exist_ok=True)
        existing = _image_names(track.root)
        added: list[str] = []
        for f in images:
            if not f.filename:
                continue
            base = re.sub(r"[^A-Za-z0-9._-]+", "-", Path(f.filename).name)
            name, n = base, 2
            while (img_dir / name).exists():
                stem, _, ext = base.rpartition(".")
                name = f"{stem}-{n}.{ext}" if ext else f"{base}-{n}"
                n += 1
            with (img_dir / name).open("wb") as fh:
                shutil.copyfileobj(f.file, fh)
            added.append(name)
        _persist_order(track.root, existing + [n for n in added if n not in existing])
        return {"added": added, "images": _image_names(track.root)}

    @app.delete("/api/tracks/{slug}/images")
    def remove_images(slug: str, payload: ImageNamesPayload):
        track = _find_track(root, slug)
        img_dir = track.root / "images"
        for name in payload.names:
            safe = Path(name).name
            for p in (img_dir / safe, track.root / safe):
                if p.is_file() and track.root in p.parents:
                    p.unlink()
                    break
        remaining = [n for n in _image_names(track.root) if n not in payload.names]
        if remaining:
            _persist_order(track.root, remaining)
        else:
            manifest = _manifest(track.root / "track.json")
            manifest.pop("images", None)
            _write_manifest(track.root / "track.json", manifest)
        return {"removed": payload.names, "images": remaining}

    @app.put("/api/tracks/{slug}/images")
    def reorder_images(slug: str, payload: OrderPayload):
        track = _find_track(root, slug)
        order = [str(n) for n in payload.order]
        current = _image_names(track.root)
        if sorted(order) != sorted(current):
            raise HTTPException(
                status_code=400,
                detail="order must be a permutation of the current images",
            )
        _persist_order(track.root, order)
        return {"images": order}

    @app.post("/api/tracks/{slug}/style")
    def save_style(slug: str, payload: StylePayload):
        track = _find_track(root, slug)
        try:
            resolved = resolve_style(payload.style)
            _check_texture(resolved)
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
    def render_preview(slug: str, payload: RenderPayload):
        requested = list(dict.fromkeys(payload.aspects or [payload.aspect]))
        unknown = [key for key in requested if key not in ASPECTS]
        if unknown:
            raise HTTPException(status_code=400, detail=f"unknown aspect: {unknown[0]}")
        track = _find_track(root, slug)
        try:
            track.style = resolve_style(payload.style)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None
        _check_texture(track.style)

        beatmap = load_beatmap(track)
        urls: dict[str, str] = {}
        with _render_lock:
            for key in requested:
                aspect = ASPECTS[key]
                dest = out / "preview" / f"{track.slug}_{key}.mp4"
                render(
                    track,
                    aspect,
                    beatmap,
                    dest,
                    window=window_for(track, beatmap, aspect),
                    preview=True,
                    textures_dir=app.state.textures,
                )
                urls[key] = f"/api/files/preview/{dest.name}"
        return {"urls": urls, "url": urls[requested[0]]}

    @app.post("/api/tracks/{slug}/render")
    def start_render(slug: str, payload: RenderPayload):
        if payload.aspect not in ASPECTS:
            raise HTTPException(status_code=400, detail=f"unknown aspect: {payload.aspect}")
        _find_track(root, slug)  # 404 fast if unknown
        _check_texture(payload.style)
        job = Job(
            id=uuid.uuid4().hex[:12],
            slug=slug,
            aspect=payload.aspect,
            style=dict(payload.style),
            preview=payload.preview,
        )
        with app.state.jobs_lock:
            app.state.jobs[job.id] = job
            app.state.jobs_order.append(job.id)
        threading.Thread(
            target=_run_job,
            args=(job, root, out, app.state.textures),
            daemon=True,
        ).start()
        return {"id": job.id}

    @app.get("/api/jobs/{job_id}")
    def job_detail(job_id: str):
        with app.state.jobs_lock:
            job = app.state.jobs.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="job not found")
        return _job_summary(job, out)

    @app.get("/api/jobs")
    def list_jobs():
        with app.state.jobs_lock:
            order = list(app.state.jobs_order)
        jobs = []
        for job_id in order:
            with app.state.jobs_lock:
                job = app.state.jobs[job_id]
            jobs.append(_job_summary(job, out))
        return jobs

    @app.post("/api/jobs/{job_id}/cancel")
    def cancel_job(job_id: str):
        with app.state.jobs_lock:
            job = app.state.jobs.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="job not found")
        job.cancel.set()
        with job.status_lock:
            if job.status == "pending":
                job.status = "cancelled"
                job.ended = time.time()
        return {"id": job.id, "cancelled": True}

    app.mount("/api/files", StaticFiles(directory=str(out)), name="files")

    @app.get("/")
    def index():
        return FileResponse(str(STATIC_DIR / "index.html"))

    app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")
    return app