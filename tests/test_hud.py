import pytest
from PIL import Image, ImageFont

from vp.aspects import HORIZONTAL, SQUARE, VERTICAL
from vp.hud import build_hud, find_font, layout_for, meta_line, wrap_to_width


def load_font(size: int = 80):
    try:
        path = find_font()
    except FileNotFoundError:
        pytest.skip("no system font available")
    return path, ImageFont.truetype(path, size)


def test_meta_line_combinations():
    assert meta_line(None, None) is None
    assert meta_line("AUSTIN", None) == "AUSTIN"
    assert meta_line(None, "@austinatchley") == "@austinatchley"
    assert meta_line("AUSTIN", "@austinatchley") == "AUSTIN · @austinatchley"


def test_wrap_to_width():
    _, font = load_font(80)
    lines = wrap_to_width("midnight drive into the void", font, 400)
    assert len(lines) > 1
    assert all(font.getlength(line) <= 400 for line in lines)
    assert " ".join(lines).replace(" ", "") == "midnight drive into the void".replace(
        " ", ""
    )


def test_layout_per_aspect():
    vertical = layout_for(VERTICAL)
    square = layout_for(SQUARE)
    horizontal = layout_for(HORIZONTAL)

    assert vertical.align == "center"
    assert square.align == "center"
    assert horizontal.align == "left"
    assert square.fontsize == int(SQUARE.width * 0.09)
    assert vertical.fontsize > horizontal.fontsize
    assert horizontal.max_width < HORIZONTAL.width
    assert horizontal.x == int(HORIZONTAL.width * 0.06)


@pytest.mark.parametrize(
    "aspect", [VERTICAL, SQUARE, HORIZONTAL], ids=["vertical", "square", "horizontal"]
)
def test_build_hud_dimensions(tmp_path, aspect):
    try:
        font_path = find_font()
    except FileNotFoundError:
        pytest.skip("no system font available")

    out = tmp_path / f"{aspect.key}.png"
    build_hud(
        out,
        aspect,
        "Midnight Drive",
        artist="AUSTIN",
        handle="@austinatchley",
        font_path=font_path,
    )

    image = Image.open(out)
    assert image.size == (aspect.width, aspect.height)
    assert image.mode == "RGBA"

    alpha = image.getchannel("A")
    assert alpha.getpixel((10, aspect.height - 5)) > 0
    assert image.getbbox() is not None