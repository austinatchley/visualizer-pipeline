import json
import os
import shutil
import time
import wave
from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient

from vp.analyze import audio_source, load_beatmap
from vp.aspects import VERTICAL
from vp.config import ANALYSIS_VERSION, STYLE_FIELDS, resolve_style
from vp.render import RenderCancelled, render
from vp.track import load_track
from vp.ui.server import Job, _export_lock, _job_summary, _preview_lock, create_app

def _has_asyncio_backend() -> bool:
    try:
        import anyio._backends._asyncio  # noqa: F401, PLC0415

        return True
    except Exception:
        return False


pytestmark = [
    pytest.mark.skipif(
        shutil.which("ffmpeg") is None, reason="ffmpeg not installed"
    ),
    pytest.mark.skipif(
        not _has_asyncio_backend(),
        reason="asyncio backend unavailable (TestClient needs ssl/networking)",
    ),
]


def _write_wav(path: Path, duration: float = 6.0, bpm: float = 120.0, sr: int = 22050):
    n = int(sr * duration)
    rng = np.random.default_rng(3)
    t = np.arange(n) / sr
    data = (0.3 * np.sin(2 * np.pi * (110 + 20 * t) * t)).astype(np.float32)
    data += 0.1 * rng.normal(0, 1, n)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes((data / (np.abs(data).max() or 1) * 32000).astype(np.int16).tobytes())


def _beatmap(path: Path, duration: float, bpm: float):
    beat = 60.0 / bpm
    beats = [round(b, 4) for b in np.arange(0, duration, beat)]
    return {
        "analysis_version": ANALYSIS_VERSION,
        "source": audio_source(path),
        "bpm_hint": None,
        "sr": 22050,
        "duration": round(duration, 4),
        "tempo": bpm,
        "beats": beats,
        "downbeats": beats[::4],
        "beat_energy": [0.5] * len(beats),
        "onset_novelty": [0.5] * len(beats),
        "bar_energy": [],
        "phrase_energy": [],
    }


def _make_track(root: Path, name: str):
    from PIL import Image

    track = root / name
    (track / "images").mkdir(parents=True)
    track.joinpath("audio.wav").parent.mkdir(exist_ok=True)
    _write_wav(track / "audio.wav")
    for i in range(2):
        Image.fromarray(np.zeros((64, 64, 3), dtype=np.uint8)).save(
            track / "images" / f"{i+1:02d}.jpg"
        )
    (track / "track.json").write_text(
        json.dumps({"title": name.replace("_", " ").title(), "hook": {"mode": "full"}})
        + "\n"
    )
    (track / ".vp").mkdir(exist_ok=True)
    (track / ".vp" / "beatmap.json").write_text(
        json.dumps(_beatmap(track / "audio.wav", 6.0, 120.0))
    )
    return track


@pytest.fixture()
def client(tmp_path):
    from PIL import Image

    root = tmp_path / "tracks"
    root.mkdir()
    _make_track(root, "demo_track")
    textures = tmp_path / "textures"
    textures.mkdir()
    Image.new("L", (8, 8), 128).save(textures / "grain.png")
    app = create_app(root=root, out=tmp_path / "out", textures=textures)
    return TestClient(app)


@pytest.fixture()
def demo_track(tmp_path):
    root = tmp_path / "lib"
    root.mkdir()
    return load_track(_make_track(root, "demo_track"))


def test_style_fields_cover_defaults():
    from vp.config import DEFAULT_STYLE

    assert set(STYLE_FIELDS) == set(DEFAULT_STYLE)


def test_boot_endpoint(client):
    data = client.get("/api/boot").json()
    assert set(data["fields"]) == set(data["defaults"])
    assert "clean" in data["presets"]
    assert "vertical" in data["aspects"]
    assert data["textures"] == [{"path": "grain.png", "label": "Grain"}]
    assert data["fields"]["texture"]["type"] == "texture"


def test_boot_fields_include_vgm(client):
    data = client.get("/api/boot").json()
    assert "vgm" in data["presets"]
    assert data["fields"]["grade"]["type"] == "choice"
    assert data["fields"]["watermark_text"]["type"] == "text"
    assert data["fields"]["show_title"]["type"] == "bool"


def test_list_and_detail(client):
    tracks = client.get("/api/tracks").json()
    assert len(tracks) == 1
    assert tracks[0]["id"] == "demo_track"
    detail = client.get("/api/tracks/demo_track").json()
    assert detail["title"] == "Demo Track"
    assert detail["image_count"] == 2


def test_save_style_writes_minimal_diff(client):
    res = client.post(
        "/api/tracks/demo_track/style",
        json={"style": resolve_style({"zoom": 0.2, "preset": "clean"})},
    )
    assert res.status_code == 200
    saved = res.json()["saved"]
    assert saved.get("zoom") == 0.2
    assert "fps" not in saved  # equals preset default -> not persisted

    manifest = json.loads(
        (Path(client.app.state.root) / "demo_track" / "track.json").read_text()
    )
    assert manifest["style"] == saved


def test_save_style_rejects_invalid(client):
    res = client.post(
        "/api/tracks/demo_track/style",
        json={"style": {"contrast": 5.0, "preset": "clean"}},
    )
    assert res.status_code == 400


def test_save_style_accepts_library_texture(client):
    style = resolve_style({"preset": "clean", "texture": "grain.png"})
    res = client.post("/api/tracks/demo_track/style", json={"style": style})
    assert res.status_code == 200, res.text
    assert res.json()["saved"].get("texture") == "grain.png"


def test_unknown_texture_is_rejected(client):
    style = {"preset": "clean", "texture": "missing.png"}
    res = client.post("/api/tracks/demo_track/style", json={"style": style})
    assert res.status_code == 400
    assert "unknown texture" in res.json()["detail"]

    res = client.post(
        "/api/tracks/demo_track/preview",
        json={"aspect": "vertical", "style": style},
    )
    assert res.status_code == 400
    assert "unknown texture" in res.json()["detail"]

    res = client.post(
        "/api/tracks/demo_track/render",
        json={"aspect": "vertical", "style": style, "preview": True},
    )
    assert res.status_code == 400
    assert "unknown texture" in res.json()["detail"]


def test_preview_with_waveform_does_not_break(client):
    style = {"preset": "punchy", "waveform": True, "contrast": 1.1}
    res = client.post(
        "/api/tracks/demo_track/preview",
        json={"aspect": "horizontal", "style": style},
    )
    assert res.status_code == 200
    dest = Path(client.app.state.out) / "preview" / "demo_track_horizontal.mp4"
    assert dest.exists()


def test_preview_with_library_texture(client):
    res = client.post(
        "/api/tracks/demo_track/preview",
        json={
            "aspect": "vertical",
            "style": {"preset": "clean", "texture": "grain.png"},
        },
    )
    assert res.status_code == 200, res.text
    dest = Path(client.app.state.out) / "preview" / "demo_track_vertical.mp4"
    assert dest.exists()


def test_preview_renders_short_no_audio(client):
    res = client.post(
        "/api/tracks/demo_track/preview",
        json={"aspect": "vertical", "style": resolve_style({"preset": "punchy"})},
    )
    assert res.status_code == 200
    url = res.json()["url"]
    dest = Path(client.app.state.out) / url.removeprefix("/api/files/")
    assert dest.exists()

    import subprocess

    probe = subprocess.run(
        [
            "ffprobe", "-v", "error", "-show_entries",
            "stream=codec_type,width,height", "-of", "csv=p=0", str(dest),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    streams = probe.stdout.strip().splitlines()
    assert "video,270,480" in streams  # quarter of 1080x1920
    assert len(streams) == 1  # no audio stream


def test_preview_renders_all_aspects(client):
    res = client.post(
        "/api/tracks/demo_track/preview",
        json={
            "aspects": ["vertical", "square", "horizontal"],
            "style": {"preset": "clean"},
        },
    )
    assert res.status_code == 200
    urls = res.json()["urls"]
    assert set(urls) == {"vertical", "square", "horizontal"}
    for url in urls.values():
        dest = Path(client.app.state.out) / url.removeprefix("/api/files/")
        assert dest.exists()


def test_preview_rejects_unknown_aspect_in_list(client):
    res = client.post(
        "/api/tracks/demo_track/preview",
        json={"aspects": ["vertical", "bogus"], "style": {"preset": "clean"}},
    )
    assert res.status_code == 400


def test_render_progress_is_monotonic_and_done(demo_track, tmp_path):
    beatmap = load_beatmap(demo_track)
    seen = []
    render(
        demo_track, VERTICAL, beatmap, tmp_path / "p.mp4",
        preview=True, progress=lambda p, s: seen.append(p),
    )
    assert seen, "progress callback never called"
    assert seen[0] == 0.0
    assert seen[-1] == 100.0
    assert seen == sorted(seen)


def test_render_cancel_hook(demo_track, tmp_path):
    beatmap = load_beatmap(demo_track)
    out = tmp_path / "c.mp4"
    with pytest.raises(RenderCancelled):
        render(demo_track, VERTICAL, beatmap, out, preview=True, cancel=lambda: True)
    assert not out.exists()


def _wait_job(client, job_id, timeout=40.0):
    deadline = time.time() + timeout
    job = None
    while time.time() < deadline:
        job = client.get(f"/api/jobs/{job_id}").json()
        if job["status"] in ("done", "failed", "skipped", "cancelled"):
            return job
        time.sleep(0.1)
    raise AssertionError(f"job {job_id} stuck at {job and job['status']}")


def test_render_job_lifecycle_and_skip(client):
    body = {"aspect": "vertical", "style": {"preset": "clean"}, "preview": True}
    job1 = _wait_job(client, client.post("/api/tracks/demo_track/render", json=body).json()["id"])
    assert job1["status"] == "done", job1.get("error")
    assert job1["url"]
    assert (Path(client.app.state.out) / job1["url"].removeprefix("/api/files/")).exists()

    job2 = _wait_job(client, client.post("/api/tracks/demo_track/render", json=body).json()["id"])
    assert job2["status"] == "skipped"  # unchanged inputs -> idempotent skip


def test_render_job_rerenders_when_style_changes(client):
    """Issue #40: a job with an unsaved style change must not be skipped."""
    body = {"aspect": "vertical", "style": {"preset": "clean"}, "preview": True}
    job1 = _wait_job(client, client.post("/api/tracks/demo_track/render", json=body).json()["id"])
    assert job1["status"] == "done", job1.get("error")

    body["style"] = {"preset": "clean", "zoom": 0.2}
    job2 = _wait_job(client, client.post("/api/tracks/demo_track/render", json=body).json()["id"])
    assert job2["status"] == "done", job2.get("error")


def test_job_url_hidden_until_complete(client):
    """A running render must not publish a URL: the browser would latch onto a
    partially written mp4 and never recover when the render completes."""
    dest = Path(client.app.state.out) / "pending.mp4"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(b"\x00\x00\x00\x18ftypmp42")
    job = Job(id="j1", track="demo_track", aspect="vertical", style={}, path=str(dest))
    job.status = "running"
    assert _job_summary(job, Path(client.app.state.out))["url"] is None
    job.status = "done"
    assert _job_summary(job, Path(client.app.state.out))["url"] == "/api/files/pending.mp4"


def test_render_job_accepts_library_texture(client):
    body = {
        "aspect": "vertical",
        "style": {"preset": "clean", "texture": "grain.png"},
        "preview": True,
    }
    job = _wait_job(
        client,
        client.post("/api/tracks/demo_track/render", json=body).json()["id"],
    )
    assert job["status"] == "done", job.get("error")
    assert job["url"]


def test_render_job_failed_reports_error(client):
    body = {"aspect": "vertical", "style": {"contrast": 9}, "preview": True}
    job = _wait_job(client, client.post("/api/tracks/demo_track/render", json=body).json()["id"])
    assert job["status"] == "failed"
    assert "contrast" in job["error"]


def test_cancel_job_endpoint(client):
    body = {"aspect": "vertical", "style": {"preset": "clean"}, "preview": True}
    job_id = client.post("/api/tracks/demo_track/render", json=body).json()["id"]
    res = client.post(f"/api/jobs/{job_id}/cancel")
    assert res.status_code == 200
    job = _wait_job(client, job_id)
    assert job["status"] in ("done", "cancelled")


def test_cancel_pending_job_is_immediate(client):
    body = {"aspect": "vertical", "style": {"preset": "clean"}, "preview": True}
    _preview_lock.acquire()
    try:
        job_id = client.post("/api/tracks/demo_track/render", json=body).json()["id"]
        job = client.get(f"/api/jobs/{job_id}").json()
        assert job["status"] == "pending"
        assert job["started"] is None

        res = client.post(f"/api/jobs/{job_id}/cancel")
        assert res.status_code == 200
        job = client.get(f"/api/jobs/{job_id}").json()
        assert job["status"] == "cancelled"
    finally:
        _preview_lock.release()


def test_create_track_with_uploads(client):
    from PIL import Image

    audio_bytes = (np.sin(np.linspace(0, 200, 8000)) * 32000).astype(np.int16).tobytes()
    img_bytes = b""
    import io

    buf = io.BytesIO()
    Image.fromarray(np.zeros((32, 32, 3), dtype=np.uint8)).save(buf, format="PNG")
    img_bytes = buf.getvalue()

    res = client.post(
        "/api/tracks",
        data={"title": "New Beat"},
        files=[
            ("audio", ("audio.wav", audio_bytes, "audio/wav")),
            ("images", ("01.png", img_bytes, "image/png")),
            ("images", ("02.png", img_bytes, "image/png")),
        ],
    )
    assert res.status_code == 200, res.text
    data = res.json()
    assert data["id"] == "new-beat"

    assert (Path(client.app.state.root) / "new-beat" / "audio.wav").is_file()
    detail = client.get("/api/tracks/new-beat").json()
    assert detail["title"] == "New Beat"
    assert detail["image_count"] == 2


def test_create_track_rejects_bad_extension(client):
    import io

    from PIL import Image

    buf = io.BytesIO()
    Image.fromarray(np.zeros((16, 16, 3), dtype=np.uint8)).save(buf, format="PNG")
    img_bytes = buf.getvalue()
    res = client.post(
        "/api/tracks",
        data={"title": "Bad"},
        files=[
            ("audio", ("a.txt", b"nope", "text/plain")),
            ("images", ("01.png", img_bytes, "image/png")),
        ],
    )
    assert res.status_code == 400
    assert "audio" in res.json()["detail"]

    import io

    from PIL import Image

    buf = io.BytesIO()
    Image.fromarray(np.zeros((16, 16, 3), dtype=np.uint8)).save(buf, format="PNG")
    res = client.post(
        "/api/tracks",
        data={"title": "No audio"},
        files=[("images", ("01.png", buf.getvalue(), "image/png"))],
    )
    assert res.status_code == 400
    assert "audio" in res.json()["detail"]

    res = client.post(
        "/api/tracks",
        data={"title": "Bad images"},
        files=[("audio", ("a.wav", b"x", "audio/wav")), ("images", ("b.txt", b"x", "text/plain"))],
    )
    assert res.status_code == 400
    assert "images" in res.json()["detail"]


def test_delete_track(client):
    res = client.delete("/api/tracks/demo_track")
    assert res.status_code == 200
    assert res.json() == {"deleted": "demo_track"}
    folder = Path(client.app.state.root) / "demo_track"
    assert not folder.exists()
    assert client.delete("/api/tracks/demo_track").status_code == 404


def test_delete_track_unknown_404(client):
    assert client.delete("/api/tracks/bogus").status_code == 404


def test_serve_image(client):
    res = client.get("/api/tracks/demo_track/images/01.jpg")
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("image/")
    assert res.content
    assert client.get("/api/tracks/demo_track/images/nope.jpg").status_code == 404


def test_add_images(client):
    import io

    import numpy as np
    from PIL import Image

    buf = io.BytesIO()
    Image.fromarray(np.zeros((32, 32, 3), dtype=np.uint8)).save(buf, format="PNG")
    res = client.post(
        "/api/tracks/demo_track/images",
        files=[("images", ("03.png", buf.getvalue(), "image/png"))],
    )
    assert res.status_code == 200, res.text
    data = res.json()
    assert "03.png" in data["images"]
    manifest = json.loads(
        (Path(client.app.state.root) / "demo_track" / "track.json").read_text()
    )
    assert "images/03.png" in manifest["images"]


def test_add_images_rejects_bad_extension(client):
    res = client.post(
        "/api/tracks/demo_track/images",
        files=[("images", ("a.txt", b"x", "text/plain"))],
    )
    assert res.status_code == 400
    assert "images must be" in res.json()["detail"]


def test_remove_images(client):
    res = client.request(
        "DELETE", "/api/tracks/demo_track/images", json={"names": ["01.jpg"]}
    )
    assert res.status_code == 200
    assert "01.jpg" not in res.json()["images"]
    folder = Path(client.app.state.root) / "demo_track"
    assert not (folder / "images" / "01.jpg").exists()


def test_reorder_images(client):
    res = client.put(
        "/api/tracks/demo_track/images", json={"order": ["02.jpg", "01.jpg"]}
    )
    assert res.status_code == 200
    assert res.json()["images"] == ["02.jpg", "01.jpg"]
    manifest = json.loads(
        (Path(client.app.state.root) / "demo_track" / "track.json").read_text()
    )
    assert manifest["images"] == ["images/02.jpg", "images/01.jpg"]


def test_reorder_images_rejects_non_permutation(client):
    res = client.put(
        "/api/tracks/demo_track/images", json={"order": ["02.jpg"]}
    )
    assert res.status_code == 400
    assert "permutation" in res.json()["detail"]


def test_update_metadata_round_trips(client):
    res = client.put(
        "/api/tracks/demo_track/metadata",
        json={"title": "Renamed Beat", "artist": "AUSTIN", "handle": "@austin"},
    )
    assert res.status_code == 200, res.text
    data = res.json()
    assert data["title"] == "Renamed Beat"
    assert data["id"] == "demo_track"  # id is the folder; renames keep it (#42)
    assert data["artist"] == "AUSTIN"
    assert data["handle"] == "@austin"

    folder = Path(client.app.state.root) / "demo_track"
    manifest = json.loads((folder / "track.json").read_text())
    assert manifest["title"] == "Renamed Beat"
    assert manifest["artist"] == "AUSTIN"
    assert manifest["handle"] == "@austin"

    # The id is stable across renames, so the open editor keeps working.
    assert client.get("/api/tracks/demo_track").json()["title"] == "Renamed Beat"
    assert client.get("/api/tracks/renamed-beat").status_code == 404


def test_update_metadata_rejects_empty_title(client):
    res = client.put("/api/tracks/demo_track/metadata", json={"title": "   "})
    assert res.status_code == 400
    assert "title" in res.json()["detail"]


def test_update_metadata_clears_optional_fields(client):
    client.put(
        "/api/tracks/demo_track/metadata",
        json={"artist": "AUSTIN", "handle": "@austin"},
    )
    res = client.put(
        "/api/tracks/demo_track/metadata", json={"artist": "", "handle": ""}
    )
    assert res.status_code == 200
    assert res.json()["artist"] is None
    assert res.json()["handle"] is None

def test_ab_preview_jobs_render_distinct_files(client):
    """Issue #41: A and B with different looks must not share an output file,
    and preview jobs must never touch the real exports in out/<id>/."""
    out = Path(client.app.state.out)
    exports = out / "demo_track"
    before = sorted(exports.rglob("*")) if exports.exists() else []

    def run(style):
        body = {"aspect": "vertical", "style": style, "preview": True}
        job = _wait_job(client, client.post("/api/tracks/demo_track/render", json=body).json()["id"])
        assert job["status"] in ("done", "skipped"), job.get("error")
        return job["url"]

    url_a = run({"preset": "clean"})
    url_b = run({"preset": "punchy"})
    assert url_a != url_b
    for url in (url_a, url_b):
        assert (out / url.removeprefix("/api/files/")).is_file()

    assert run({"preset": "clean"}) == url_a  # same look reuses its clip
    after = sorted(exports.rglob("*")) if exports.exists() else []
    assert after == before


def test_preview_job_clips_are_pruned(tmp_path):
    from vp.ui.server import PREVIEW_JOB_KEEP, _preview_job_path, _prune_preview_jobs

    (tmp_path / "preview").mkdir()
    clips = []
    for i in range(PREVIEW_JOB_KEEP + 3):
        clip = _preview_job_path(tmp_path, "demo", "vertical", f"{i:012x}")
        clip.write_bytes(b"x")
        os.utime(clip, (1000 + i, 1000 + i))
        clips.append(clip)
    other = _preview_job_path(tmp_path, "demo", "square", "f" * 12)
    other.write_bytes(b"x")
    grid = tmp_path / "preview" / "demo_vertical.mp4"  # grid preview, not a job clip
    grid.write_bytes(b"x")

    keep = clips[0]  # oldest, but the one being rendered now
    _prune_preview_jobs(tmp_path, "demo", "vertical", keep=keep)

    remaining = sorted(p for p in clips if p.exists())
    assert keep in remaining
    assert len(remaining) == PREVIEW_JOB_KEEP
    assert clips[-1].exists()  # newest kept
    assert other.exists() and grid.exists()


def test_duplicate_titles_are_distinct_tracks(client):
    """Issue #42: two folders with the same title are separate tracks."""
    root = Path(client.app.state.root)
    twin = _make_track(root, "demo_track_2")
    (twin / "track.json").write_text(json.dumps({"title": "Demo Track"}))

    ids = sorted(t["id"] for t in client.get("/api/tracks").json())
    assert ids == ["demo_track", "demo_track_2"]
    assert client.get("/api/tracks/demo_track_2").json()["path"] == str(twin)


@pytest.mark.parametrize("bad", ["..%2Fdemo_track", "nope"])
def test_track_lookup_rejects_unknown_and_traversal(client, bad):
    assert client.get(f"/api/tracks/{bad}").status_code == 404


@pytest.mark.parametrize("bad", ["", ".", "..", "../demo_track", "a/b"])
def test_find_track_folder_rejects_non_names(tmp_path, bad):
    from fastapi import HTTPException

    from vp.ui.server import _find_track_folder

    root = tmp_path / "tracks"
    root.mkdir()
    _make_track(root, "demo_track")
    with pytest.raises(HTTPException) as exc:
        _find_track_folder(root / "demo_track", bad)
    assert exc.value.status_code == 404


def test_track_id_with_spaces_resolves(client):
    root = Path(client.app.state.root)
    _make_track(root, "My Beat #2")
    res = client.get("/api/tracks/My%20Beat%20%232")
    assert res.status_code == 200, res.text
    assert res.json()["id"] == "My Beat #2"


def _run_with_timeout(fn, timeout=30.0):
    import threading

    result = {}
    thread = threading.Thread(target=lambda: result.setdefault("value", fn()), daemon=True)
    thread.start()
    thread.join(timeout)
    assert not thread.is_alive(), "blocked: preview waited behind the export lane"
    return result["value"]


def test_preview_not_blocked_by_running_export(client):
    """Issue #44: a grid preview and a preview job complete while an export
    holds the export lane."""
    _export_lock.acquire()  # simulate a long full-track export in progress
    try:
        res = _run_with_timeout(
            lambda: client.post(
                "/api/tracks/demo_track/preview",
                json={"aspect": "vertical", "style": {"preset": "clean"}},
            )
        )
        assert res.status_code == 200, res.text

        body = {"aspect": "vertical", "style": {"preset": "clean", "zoom": 0.3}, "preview": True}
        job_id = client.post("/api/tracks/demo_track/render", json=body).json()["id"]
        job = _run_with_timeout(lambda: _wait_job(client, job_id))
        assert job["status"] == "done", job.get("error")
    finally:
        _export_lock.release()


def test_export_job_waits_for_export_lane(client):
    """Exports still run one at a time."""
    body = {"aspect": "vertical", "style": {"preset": "clean"}, "preview": False}
    _export_lock.acquire()
    try:
        job_id = client.post("/api/tracks/demo_track/render", json=body).json()["id"]
        time.sleep(0.5)
        assert client.get(f"/api/jobs/{job_id}").json()["status"] == "pending"
    finally:
        _export_lock.release()
    assert _wait_job(client, job_id)["status"] == "done"
