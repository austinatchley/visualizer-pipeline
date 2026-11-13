from pathlib import Path

import pytest
from PIL import Image

from vp.config import STYLE_FIELDS
from vp.textures import resolve_texture, texture_library


def _png(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("L", (4, 4), 128).save(path)


def test_texture_field_is_library_picker():
    assert STYLE_FIELDS["texture"]["type"] == "texture"


def test_library_lists_images_sorted_with_labels(tmp_path):
    _png(tmp_path / "grain-35mm.png")
    _png(tmp_path / "sub" / "dust_scratches.jpg")
    (tmp_path / "notes.txt").write_text("not a texture")

    library = texture_library(tmp_path)
    assert [item["path"] for item in library] == [
        "grain-35mm.png",
        "sub/dust_scratches.jpg",
    ]
    assert [item["label"] for item in library] == [
        "Grain 35mm",
        "Sub Dust Scratches",
    ]


def test_missing_library_is_empty(tmp_path):
    assert texture_library(tmp_path / "missing") == []


def test_resolve_library_relative_absolute_and_empty(tmp_path):
    texture = tmp_path / "grain.png"
    _png(texture)

    assert resolve_texture("grain.png", tmp_path) == texture
    assert resolve_texture(str(texture), tmp_path) == texture
    assert resolve_texture(None, tmp_path) is None
    assert resolve_texture("", tmp_path) is None


def test_resolve_custom_relative_path(tmp_path, monkeypatch):
    textures = tmp_path / "textures"
    textures.mkdir()
    custom = tmp_path / "custom" / "tex.png"
    _png(custom)
    monkeypatch.chdir(tmp_path)

    assert resolve_texture("custom/tex.png", textures) == Path("custom/tex.png")


def test_resolve_unknown_texture_raises(tmp_path):
    with pytest.raises(FileNotFoundError, match="unknown texture"):
        resolve_texture("nope.png", tmp_path)
