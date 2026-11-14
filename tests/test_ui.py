import json
import shutil
import time
import wave
from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient

from vp.analyze import load_beatmap
from vp.aspects import VERTICAL
from vp.config import STYLE_FIELDS, resolve_style
from vp.render import RenderCancelled, render
from vp.track import load_track
from vp.ui.server import _render_lock, create_app

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
        "analysis_version": 2,
        "audio": str(path),
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
    assert tracks[0]["slug"] == "demo-track"
    detail = client.get("/api/tracks/demo-track").json()
    assert detail["title"] == "Demo Track"
    assert detail["image_count"] == 2


def test_save_style_writes_minimal_diff(client):
    res = client.post(
        "/api/tracks/demo-track/style",
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
        "/api/tracks/demo-track/style",
        json={"style": {"contrast": 5.0, "preset": "clean"}},
    )
    assert res.status_code == 400


def test_save_style_accepts_library_texture(client):
    style = resolve_style({"preset": "clean", "texture": "grain.png"})
    res = client.post("/api/tracks/demo-track/style", json={"style": style})
    assert res.status_code == 200, res.text
    assert res.json()["saved"].get("texture") == "grain.png"


def test_unknown_texture_is_rejected(client):
    style = {"preset": "clean", "texture": "missing.png"}
    res = client.post("/api/tracks/demo-track/style", json={"style": style})
    assert res.status_code == 400
    assert "unknown texture" in res.json()["detail"]

    res = client.post(
        "/api/tracks/demo-track/preview",
        json={"aspect": "vertical", "style": style},
    )
    assert res.status_code == 400
    assert "unknown texture" in res.json()["detail"]

    res = client.post(
        "/api/tracks/demo-track/render",
        json={"aspect": "vertical", "style": style, "preview": True},
    )
    assert res.status_code == 400
    assert "unknown texture" in res.json()["detail"]


def test_preview_with_waveform_does_not_break(client):
    style = {"preset": "punchy", "waveform": True, "contrast": 1.1}
    res = client.post(
        "/api/tracks/demo-track/preview",
        json={"aspect": "horizontal", "style": style},
    )
    assert res.status_code == 200
    dest = Path(client.app.state.out) / "preview" / "demo-track_horizontal.mp4"
    assert dest.exists()


def test_preview_with_library_texture(client):
    res = client.post(
        "/api/tracks/demo-track/preview",
        json={
            "aspect": "vertical",
            "style": {"preset": "clean", "texture": "grain.png"},
        },
    )
    assert res.status_code == 200, res.text
    dest = Path(client.app.state.out) / "preview" / "demo-track_vertical.mp4"
    assert dest.exists()


def test_preview_renders_short_no_audio(client):
    res = client.post(
        "/api/tracks/demo-track/preview",
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
        "/api/tracks/demo-track/preview",
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
        "/api/tracks/demo-track/preview",
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
    job1 = _wait_job(client, client.post("/api/tracks/demo-track/render", json=body).json()["id"])
    assert job1["status"] == "done", job1.get("error")
    assert job1["url"]
    assert (Path(client.app.state.out) / job1["url"].removeprefix("/api/files/")).exists()

    job2 = _wait_job(client, client.post("/api/tracks/demo-track/render", json=body).json()["id"])
    assert job2["status"] == "skipped"  # unchanged inputs -> idempotent skip


def test_render_job_accepts_library_texture(client):
    body = {
        "aspect": "vertical",
        "style": {"preset": "clean", "texture": "grain.png"},
        "preview": True,
    }
    job = _wait_job(
        client,
        client.post("/api/tracks/demo-track/render", json=body).json()["id"],
    )
    assert job["status"] == "done", job.get("error")
    assert job["url"]


def test_render_job_failed_reports_error(client):
    body = {"aspect": "vertical", "style": {"contrast": 9}, "preview": True}
    job = _wait_job(client, client.post("/api/tracks/demo-track/render", json=body).json()["id"])
    assert job["status"] == "failed"
    assert "contrast" in job["error"]


def test_cancel_job_endpoint(client):
    body = {"aspect": "vertical", "style": {"preset": "clean"}, "preview": True}
    job_id = client.post("/api/tracks/demo-track/render", json=body).json()["id"]
    res = client.post(f"/api/jobs/{job_id}/cancel")
    assert res.status_code == 200
    job = _wait_job(client, job_id)
    assert job["status"] in ("done", "cancelled")


def test_cancel_pending_job_is_immediate(client):
    body = {"aspect": "vertical", "style": {"preset": "clean"}, "preview": True}
    _render_lock.acquire()
    try:
        job_id = client.post("/api/tracks/demo-track/render", json=body).json()["id"]
        job = client.get(f"/api/jobs/{job_id}").json()
        assert job["status"] == "pending"
        assert job["started"] is None

        res = client.post(f"/api/jobs/{job_id}/cancel")
        assert res.status_code == 200
        job = client.get(f"/api/jobs/{job_id}").json()
        assert job["status"] == "cancelled"
    finally:
        _render_lock.release()


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
    assert data["slug"] == "new-beat"

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