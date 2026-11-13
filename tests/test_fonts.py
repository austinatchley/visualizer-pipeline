from pathlib import Path

import pytest

from vp.fonts import FONT_CHOICES, FONT_FACES, find_font, load_font, resolve_face


def test_choices_match_faces():
    assert FONT_CHOICES == list(FONT_FACES)


def test_bundled_faces_exist():
    for key, face in FONT_FACES.items():
        if key == "clean":
            continue
        assert face.path is not None, key
        assert face.path.is_file(), key


def test_clean_uses_system_font():
    face = resolve_face("clean")
    assert face.key == "clean"
    assert face.path is not None and face.path.is_file()
    assert Path(find_font()).is_file()


def test_override_beats_choice():
    custom = FONT_FACES["anton"].path
    assert custom is not None
    face = resolve_face("tiny5", str(custom))
    assert face.key == "custom"
    assert face.path == custom


def test_unknown_face_raises():
    with pytest.raises(ValueError, match="unknown font"):
        resolve_face("not-a-font")


def test_variable_faces_load():
    for key in ("tiny5-round", "tiny5-jitter", "pixelify", "syne"):
        font = load_font(resolve_face(key), 32)
        assert font.getlength("VGM") > 0


def test_missing_override_raises(tmp_path):
    with pytest.raises(FileNotFoundError, match="title_font not found"):
        resolve_face(None, str(tmp_path / "missing.ttf"))