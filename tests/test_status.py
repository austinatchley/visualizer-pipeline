"""Render fingerprint + sidecar idempotency (issue #40). No ffmpeg needed."""

import os
from pathlib import Path

import pytest

from vp.aspects import HORIZONTAL, VERTICAL
from vp.config import resolve_style
from vp.status import (
    RENDER_VERSION,
    invalidate,
    is_current,
    record,
    render_fingerprint,
    sidecar_path,
)
from vp.track import Track

BEATMAP = {
    "duration": 60.0,
    "tempo": 120.0,
    "beats": [i * 0.5 for i in range(120)],
    "beat_energy": [0.5] * 120,
    "onset_novelty": [0.5] * 120,
}
WINDOW = (0.0, 60.0)


@pytest.fixture
def track(tmp_path):
    audio = tmp_path / "audio.wav"
    audio.write_bytes(b"RIFF-audio")
    images = []
    for name in ("a.png", "b.png"):
        img = tmp_path / name
        img.write_bytes(name.encode())
        images.append(img)
    return Track(
        root=tmp_path,
        audio=audio,
        title="Demo",
        images=images,
        artist="Artist",
        handle="@demo",
        # Bundled font so the test does not depend on a system font.
        style=resolve_style({"font": "anton"}),
        overrides={"cuts": None},
    )


def fp(track, **kw):
    params = {"aspect": VERTICAL, "beatmap": BEATMAP, "window": WINDOW}
    params.update(kw)
    return render_fingerprint(track, **params)


def test_fingerprint_is_stable(track):
    assert fp(track) == fp(track)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda t: setattr(t, "style", resolve_style({"font": "anton"}, preset="vgm")),
        lambda t: setattr(t, "style", resolve_style({"font": "anton", "zoom": 0.2})),
        lambda t: setattr(t, "title", "Other"),
        lambda t: setattr(t, "handle", "@other"),
        lambda t: setattr(t, "images", list(reversed(t.images))),
        lambda t: setattr(t, "overrides", {"cuts": [10.0, 20.0]}),
    ],
    ids=["preset", "style-flag", "title", "handle", "image-order", "cuts"],
)
def test_fingerprint_changes_with_settings(track, mutate):
    before = fp(track)
    mutate(track)
    assert fp(track) != before


def test_fingerprint_changes_with_render_params(track):
    base = fp(track)
    assert fp(track, aspect=HORIZONTAL) != base
    assert fp(track, window=(0.0, 45.0)) != base
    assert fp(track, preview=True) != base
    assert fp(track, beatmap={**BEATMAP, "tempo": 140.0}) != base


def test_fingerprint_changes_when_input_file_changes(track):
    before = fp(track)
    st = track.audio.stat()
    track.audio.write_bytes(b"RIFF-new-audio")
    os.utime(track.audio, ns=(st.st_atime_ns, st.st_mtime_ns))  # same mtime
    assert fp(track) != before  # size differs

    before = fp(track)
    st = track.images[0].stat()
    os.utime(track.images[0], ns=(st.st_atime_ns, st.st_mtime_ns + 10**9))
    assert fp(track) != before  # mtime differs


def test_fingerprint_tracks_texture_file(track, tmp_path):
    tex_dir = tmp_path / "textures"
    tex_dir.mkdir()
    tex = tex_dir / "grain.png"
    tex.write_bytes(b"one")
    track.style = resolve_style({"font": "anton", "texture": "grain.png"})
    before = fp(track, textures_dir=tex_dir)
    tex.write_bytes(b"two-bytes")
    assert fp(track, textures_dir=tex_dir) != before


def test_sidecar_roundtrip(tmp_path):
    out = tmp_path / "x.mp4"
    assert not is_current(out, "abc")

    out.write_bytes(b"video")
    assert not is_current(out, "abc")  # no sidecar -> stale

    record(out, "abc")
    assert sidecar_path(out) == tmp_path / "x.mp4.vp.json"
    assert is_current(out, "abc")
    assert not is_current(out, "def")
    assert str(RENDER_VERSION) in sidecar_path(out).read_text()

    invalidate(out)
    assert not is_current(out, "abc")
    invalidate(out)  # idempotent


def test_corrupt_sidecar_is_stale(tmp_path):
    out = tmp_path / "x.mp4"
    out.write_bytes(b"video")
    sidecar_path(out).write_text("{not json")
    assert not is_current(out, "abc")
    sidecar_path(out).write_text("[1, 2]")
    assert not is_current(out, "abc")


def test_missing_output_is_stale_even_with_sidecar(tmp_path):
    out = tmp_path / "x.mp4"
    out.write_bytes(b"video")
    record(out, "abc")
    out.unlink()
    assert not is_current(out, "abc")
    assert not Path(str(out) + ".vp.json.tmp").exists()
