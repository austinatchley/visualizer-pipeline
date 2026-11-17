"""Local web UI for vp: a thin FastAPI client over the engine.

The UI never re-implements config/planning/rendering; it drives the same
`resolve_style` + `render()` functions the CLI uses, so previews and exports are
identical to a headless run.
"""

from __future__ import annotations

import glob
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

from ..analyze import load_beatmap
from ..aspects import ASPECTS
from ..config import DEFAULT_STYLE, PRESETS_DIR, STYLE_FIELDS, resolve_style
from ..manifest import (
    AUDIO_EXTS,
    IMAGE_EXTS,
    IMAGES_DIRNAME,
    discover_tracks,
    image_dir,
    is_track_folder,
    read_manifest,
    scan_images,
    write_manifest,
)
from ..render import RenderCancelled, plan_variants, render, window_for
from ..status import is_current, render_fingerprint, sidecar_path
from ..textures import resolve_texture, texture_library
from ..track import load_track, slugify
from .models import (
    BootData,
    CreatedTrack,
    DeletedTrack,
    ImageNamesPayload,
    ImageOrder,
    ImagesAdded,
    ImagesRemoved,
    JobCancelled,
    JobCreated,
    JobSummary,
    MetadataPayload,
    MetadataResult,
    OrderPayload,
    PreviewResult,
    RenderPayload,
    Style,
    StylePayload,
    StyleSaveResult,
    TrackDetail,
    TrackError,
    TrackSummary,
)

STATIC_DIR = Path(__file__).parent / "static"
# Two render lanes (issue #44): full exports run one at a time, and so do
# previews, but a preview never waits behind a minutes-long export. Previews are
# quarter-res and audio-less, so one alongside an export is cheap.
_export_lock = threading.Lock()
_preview_lock = threading.Lock()


def _find_track_folder(root: Path, track_id: str) -> Path:
    """Resolve a track id (its folder name) to a folder under `root` (issue #42).

    O(1) and path-safe; does not load the track, so broken tracks are still
    deletable from the studio catalog."""
    if track_id in ("", ".", "..") or Path(track_id).name != track_id:
        raise HTTPException(status_code=404, detail=f"track {track_id!r} not found")
    folder = root / track_id
    if not is_track_folder(folder):
        raise HTTPException(status_code=404, detail=f"track {track_id!r} not found")
    return folder


def _find_track(root: Path, track_id: str):
    folder = _find_track_folder(root, track_id)
    try:
        return load_track(folder)
    except (FileNotFoundError, NotADirectoryError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None


def _image_names(folder: Path) -> list[str]:
    """Ordered bare image filenames (UI-facing). Manifest stores root-relative paths."""
    manifest = read_manifest(folder)
    if manifest.get("images") is not None:
        return [Path(p).name for p in manifest["images"] if (folder / str(p)).is_file()]
    return [p.name for p in scan_images(folder)]


def _check_image_names(uploads: list[UploadFile]) -> None:
    """400 if any named upload is not a supported image type."""
    bad = [
        f.filename
        for f in uploads
        if f.filename and Path(f.filename).suffix.lower() not in IMAGE_EXTS
    ]
    if bad:
        allowed = ", ".join(sorted(IMAGE_EXTS))
        raise HTTPException(
            status_code=400,
            detail=f"images must be one of: {allowed} (got {', '.join(bad)})",
        )


def _persist_order(folder: Path, names: list[str]) -> None:
    """Persist an explicit image order as root-relative paths in `track.json`."""
    d = image_dir(folder)
    rel: list[str] = []
    for n in names:
        candidate = d / n
        rel.append(str(candidate.relative_to(folder)) if candidate.is_file() else n)
    manifest = read_manifest(folder)
    manifest["images"] = rel
    write_manifest(folder, manifest)


@dataclass
class Job:
    id: str
    track: str
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
    status_lock: threading.Lock = field(default_factory=threading.Lock, repr=False, compare=False)


PREVIEW_JOB_KEEP = 8


def _preview_job_path(out: Path, track_id: str, aspect: str, fingerprint: str) -> Path:
    return out / "preview" / f"{track_id}_{aspect}_{fingerprint[:12]}.mp4"


def _prune_preview_jobs(out: Path, track_id: str, aspect: str, keep: Path) -> None:
    """Bound preview-job clips per track/aspect: keep the newest few (plus `keep`)
    so repeated A/B comparisons don't fill the disk."""
    pattern = f"{glob.escape(f'{track_id}_{aspect}_')}{'[0-9a-f]' * 12}.mp4"
    clips = sorted(
        (p for p in (out / "preview").glob(pattern) if p != keep),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    for stale in clips[PREVIEW_JOB_KEEP - 1 :]:
        stale.unlink(missing_ok=True)
        sidecar_path(stale).unlink(missing_ok=True)


def _run_job(job: Job, root: Path, out: Path, textures: Path) -> None:
    try:
        track = _find_track(root, job.track)
        track.style = resolve_style(job.style)
        track.style_explicit = None  # full studio model, not layered values
        aspect = ASPECTS[job.aspect]
        beatmap = load_beatmap(track)

        if job.preview:
            window = window_for(track, beatmap, aspect)
        else:
            variant = plan_variants(track, beatmap, out / track.id, [job.aspect])[0]
            window = variant.window
        fingerprint = render_fingerprint(
            track, aspect, beatmap, window, preview=job.preview, textures_dir=textures
        )
        if job.preview:
            # Content-addressed so A/B jobs with different looks never share a
            # file (issue #41), while an identical look reuses its clip.
            dest = _preview_job_path(out, track.id, job.aspect, fingerprint)
            _prune_preview_jobs(out, track.id, job.aspect, keep=dest)
        else:
            dest = variant.path
        job.path = str(dest)

        if is_current(dest, fingerprint):
            with job.status_lock:
                if job.status == "cancelled" or job.cancel.is_set():
                    job.status = "cancelled"
                else:
                    job.status, job.progress = "skipped", 100.0
            return

        def _progress(percent: float, _stage: str) -> None:
            job.progress = percent

        with _preview_lock if job.preview else _export_lock:
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
        "track": job.track,
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

    @app.get("/api/boot", response_model=BootData)
    def boot():
        return {
            "fields": STYLE_FIELDS,
            "defaults": DEFAULT_STYLE,
            "presets": sorted(p.stem for p in PRESETS_DIR.glob("*.json")),
            "textures": texture_library(app.state.textures),
            "aspects": {k: {"label": a.label, "kind": a.kind} for k, a in ASPECTS.items()},
        }

    @app.get("/api/style", response_model=Style)
    def style_for_preset(preset: str):
        try:
            return resolve_style(preset=preset)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None

    @app.get("/api/tracks", response_model=list[TrackSummary | TrackError])
    def list_tracks():
        tracks = []
        for folder in discover_tracks(root):
            try:
                track = load_track(folder)
                tracks.append(
                    {
                        "id": track.id,
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
                        "id": folder.name,
                        "title": folder.name,
                        "path": str(folder),
                        "error": str(exc),
                    }
                )
        return tracks

    @app.post("/api/tracks", response_model=CreatedTrack)
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
        _check_image_names(images)

        base = slugify(title)
        folder = root / base
        n = 2
        while folder.exists():
            folder = root / f"{base}-{n}"
            n += 1
        folder.mkdir()
        (folder / IMAGES_DIRNAME).mkdir()
        try:
            p = folder / f"audio{Path(audio.filename).suffix.lower()}"
            with p.open("wb") as fh:
                shutil.copyfileobj(audio.file, fh)
            for img in images:
                name = re.sub(r"[^A-Za-z0-9._-]+", "-", Path(img.filename).name)
                with (folder / IMAGES_DIRNAME / name).open("wb") as fh:
                    shutil.copyfileobj(img.file, fh)
            write_manifest(folder, {"title": title or "Untitled"})
        except Exception:
            shutil.rmtree(folder, ignore_errors=True)
            raise

        track = load_track(folder)
        return {
            "id": track.id,
            "title": track.title,
            "path": str(folder),
            "audio": track.audio.name,
            "image_count": len(track.images),
        }

    @app.get("/api/tracks/{track_id}", response_model=TrackDetail)
    def track_detail(track_id: str):
        track = _find_track(root, track_id)
        return {
            "id": track.id,
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

    @app.delete("/api/tracks/{track_id}", response_model=DeletedTrack)
    def delete_track(track_id: str):
        folder = _find_track_folder(root, track_id)
        with app.state.jobs_lock:
            for job in app.state.jobs.values():
                if job.track == track_id:
                    job.cancel.set()
        shutil.rmtree(folder)
        return {"deleted": track_id}

    @app.get(
        "/api/tracks/{track_id}/images/{name}",
        response_class=FileResponse,
        responses={200: {"content": {"image/*": {}}, "description": "The image file"}},
    )
    def track_image(track_id: str, name: str):
        track = _find_track(root, track_id)
        safe = Path(name).name
        if safe != name:
            raise HTTPException(status_code=400, detail="invalid image name")
        candidates = [track.root / IMAGES_DIRNAME / safe, track.root / safe]
        for p in candidates:
            if p.is_file():
                return FileResponse(str(p))
        raise HTTPException(status_code=404, detail=f"image {name!r} not found")

    @app.post("/api/tracks/{track_id}/images", response_model=ImagesAdded)
    async def add_images(track_id: str, images: list[UploadFile] = File(...)):
        track = _find_track(root, track_id)
        _check_image_names(images)
        img_dir = track.root / IMAGES_DIRNAME
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

    @app.delete("/api/tracks/{track_id}/images", response_model=ImagesRemoved)
    def remove_images(track_id: str, payload: ImageNamesPayload):
        track = _find_track(root, track_id)
        img_dir = track.root / IMAGES_DIRNAME
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
            manifest = read_manifest(track.root)
            manifest.pop("images", None)
            write_manifest(track.root, manifest)
        return {"removed": payload.names, "images": remaining}

    @app.put("/api/tracks/{track_id}/images", response_model=ImageOrder)
    def reorder_images(track_id: str, payload: OrderPayload):
        track = _find_track(root, track_id)
        order = [str(n) for n in payload.order]
        current = _image_names(track.root)
        if sorted(order) != sorted(current):
            raise HTTPException(
                status_code=400,
                detail="order must be a permutation of the current images",
            )
        _persist_order(track.root, order)
        return {"images": order}

    @app.put("/api/tracks/{track_id}/metadata", response_model=MetadataResult)
    def update_metadata(track_id: str, payload: MetadataPayload):
        track = _find_track(root, track_id)
        manifest = read_manifest(track.root)
        if payload.title is not None:
            title = payload.title.strip()
            if not title:
                raise HTTPException(status_code=400, detail="title cannot be empty")
            manifest["title"] = title
        if payload.artist is not None:
            manifest["artist"] = payload.artist.strip() or None
        if payload.handle is not None:
            manifest["handle"] = payload.handle.strip() or None
        write_manifest(track.root, manifest)
        updated = load_track(track.root)
        return {
            "id": updated.id,
            "title": updated.title,
            "artist": updated.artist,
            "handle": updated.handle,
        }

    @app.post("/api/tracks/{track_id}/style", response_model=StyleSaveResult)
    def save_style(track_id: str, payload: StylePayload):
        track = _find_track(root, track_id)
        try:
            resolved = resolve_style(payload.style)
            _check_texture(resolved)
            baseline = resolve_style(preset=resolved["preset"])
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None

        # Persist only values that differ from what the preset alone would give, so
        # track.json stays minimal and preset changes still propagate.
        saved = {
            k: v for k, v in resolved.items() if k != "preset" and resolved[k] != baseline.get(k)
        }
        if resolved["preset"] != DEFAULT_STYLE["preset"]:
            saved["preset"] = resolved["preset"]

        manifest = read_manifest(track.root)
        manifest["style"] = saved
        write_manifest(track.root, manifest)
        return {"saved": saved, "resolved": resolved}

    @app.post("/api/tracks/{track_id}/preview", response_model=PreviewResult)
    def render_preview(track_id: str, payload: RenderPayload):
        requested = list(dict.fromkeys(payload.aspects or [payload.aspect]))
        unknown = [key for key in requested if key not in ASPECTS]
        if unknown:
            raise HTTPException(status_code=400, detail=f"unknown aspect: {unknown[0]}")
        track = _find_track(root, track_id)
        try:
            track.style = resolve_style(payload.style)
            track.style_explicit = None  # full studio model, not layered values
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None
        _check_texture(track.style)

        beatmap = load_beatmap(track)
        urls: dict[str, str] = {}
        with _preview_lock:
            for key in requested:
                aspect = ASPECTS[key]
                dest = out / "preview" / f"{track.id}_{key}.mp4"
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

    @app.post("/api/tracks/{track_id}/render", response_model=JobCreated)
    def start_render(track_id: str, payload: RenderPayload):
        if payload.aspect not in ASPECTS:
            raise HTTPException(status_code=400, detail=f"unknown aspect: {payload.aspect}")
        _find_track(root, track_id)  # 404 fast if unknown
        _check_texture(payload.style)
        job = Job(
            id=uuid.uuid4().hex[:12],
            track=track_id,
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

    @app.get("/api/jobs/{job_id}", response_model=JobSummary)
    def job_detail(job_id: str):
        with app.state.jobs_lock:
            job = app.state.jobs.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="job not found")
        return _job_summary(job, out)

    @app.get("/api/jobs", response_model=list[JobSummary])
    def list_jobs():
        with app.state.jobs_lock:
            order = list(app.state.jobs_order)
        jobs = []
        for job_id in order:
            with app.state.jobs_lock:
                job = app.state.jobs[job_id]
            jobs.append(_job_summary(job, out))
        return jobs

    @app.post("/api/jobs/{job_id}/cancel", response_model=JobCancelled)
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

    @app.get("/", response_class=FileResponse, include_in_schema=False)
    def index():
        return FileResponse(str(STATIC_DIR / "index.html"))

    app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")
    return app
