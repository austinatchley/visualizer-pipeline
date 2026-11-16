"""vp.manifest: track.json IO, track discovery, image lookup (issue #49)."""

from __future__ import annotations

import json
import os
import stat

import pytest

from vp import manifest as m


def _touch(path, data=b""):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def test_read_missing_manifest_is_empty(tmp_path):
    assert m.read_manifest(tmp_path) == {}


def test_read_rejects_non_object(tmp_path):
    (tmp_path / "track.json").write_text("[1, 2]")
    with pytest.raises(ValueError, match="JSON object"):
        m.read_manifest(tmp_path)


def test_write_then_read_round_trips(tmp_path):
    data = {"title": "Song", "images": ["images/a.png"], "artist": None}
    m.write_manifest(tmp_path, data)
    assert m.read_manifest(tmp_path) == data
    assert (tmp_path / "track.json").read_text().endswith("}\n")


def test_write_is_atomic_and_leaves_no_temp(tmp_path, monkeypatch):
    m.write_manifest(tmp_path, {"title": "Before"})

    def boom(*_args, **_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(m.os, "replace", boom)
    with pytest.raises(OSError):
        m.write_manifest(tmp_path, {"title": "After"})
    # The old manifest survives intact and the temp file is cleaned up.
    assert m.read_manifest(tmp_path) == {"title": "Before"}
    assert [p.name for p in tmp_path.iterdir()] == ["track.json"]


def test_write_respects_umask(tmp_path):
    old = os.umask(0o022)
    try:
        m.write_manifest(tmp_path, {"title": "x"})
    finally:
        os.umask(old)
    mode = stat.S_IMODE((tmp_path / "track.json").stat().st_mode)
    assert mode == 0o644


def test_discover_tracks(tmp_path):
    _touch(tmp_path / "b" / "song.WAV")
    _touch(tmp_path / "a" / "song.mp3")
    _touch(tmp_path / "no-audio" / "cover.png")
    (tmp_path / "empty").mkdir()
    _touch(tmp_path / "loose.wav")
    assert [p.name for p in m.discover_tracks(tmp_path)] == ["a", "b"]
    assert m.discover_tracks(tmp_path / "missing") == []
    assert not m.is_track_folder(tmp_path / "loose.wav")


def test_image_dir_and_scan(tmp_path):
    _touch(tmp_path / "root.jpg")
    assert m.image_dir(tmp_path) == tmp_path
    assert [p.name for p in m.scan_images(tmp_path)] == ["root.jpg"]

    _touch(tmp_path / "images" / "b.PNG")
    _touch(tmp_path / "images" / "a.webp")
    _touch(tmp_path / "images" / "notes.txt")
    assert m.image_dir(tmp_path) == tmp_path / "images"
    assert [p.name for p in m.scan_images(tmp_path)] == ["a.webp", "b.PNG"]


def test_cli_init_writes_manifest_via_module(tmp_path):
    from vp.cli import main

    folder = tmp_path / "my_track"
    assert main(["init", str(folder)]) == 0
    assert json.loads((folder / "track.json").read_text()) == {
        "title": "My Track",
        "artist": None,
        "handle": None,
    }
    assert (folder / "images").is_dir()
