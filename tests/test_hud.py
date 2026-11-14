import pytest
from PIL import Image, ImageFont

from vp.aspects import HORIZONTAL, SQUARE, VERTICAL
from vp.fonts import FONT_CHOICES, resolve_face
from vp.hud import (
    VgmConfig,
    apply_case,
    build_hud,
    build_vgm_overlay,
    find_font,
    layout_for,
    meta_line,
    wrap_to_width,
)


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


def test_apply_case():
    assert apply_case("midnight drive", "upper") == "MIDNIGHT DRIVE"
    assert apply_case("Midnight Drive", "lower") == "midnight drive"
    assert apply_case("Midnight Drive", "preserve") == "Midnight Drive"


def test_text_case_tracking_and_bundled_font(tmp_path):
    out = tmp_path / "typed.png"
    build_hud(
        out,
        SQUARE,
        "midnight drive",
        artist="AUSTIN",
        handle="@austinatchley",
        font_face=resolve_face("tiny5-round"),
        text_case="upper",
        tracking=0.03,
        line_spacing=1.2,
    )
    image = Image.open(out)
    assert image.size == (SQUARE.width, SQUARE.height)
    assert image.mode == "RGBA"
    assert image.getbbox() is not None


@pytest.mark.parametrize("key", FONT_CHOICES)
def test_all_font_choices_render(tmp_path, key):
    try:
        face = resolve_face(key)
    except FileNotFoundError:
        pytest.skip(f"font unavailable: {key}")
    out = tmp_path / f"{key}.png"
    build_hud(out, SQUARE, "VGM", font_face=face)
    assert Image.open(out).size == (SQUARE.width, SQUARE.height)


def test_vgm_config_active_flag():
    assert VgmConfig().active is False
    assert VgmConfig(watermark=True).active is True
    assert VgmConfig(watermark=False, watermark_text="VGM").active is False


def test_vgm_overlay_watermark():
    img = build_vgm_overlay(
        (SQUARE.width, SQUARE.height),
        VgmConfig(watermark=True, watermark_text="VGM", watermark_opacity=0.2),
    )
    assert img.size == (SQUARE.width, SQUARE.height)
    assert img.mode == "RGBA"
    assert img.getbbox() is not None
    extrema = img.getchannel("A").getextrema()
    assert extrema[0] == 0
    assert extrema[1] <= 0.2 * 255 + 4


def test_vgm_overlay_empty_is_transparent():
    assert build_vgm_overlay((SQUARE.width, SQUARE.height), VgmConfig()).getbbox() is None
    # toggle on but no text -> nothing drawn
    assert (
        build_vgm_overlay((SQUARE.width, SQUARE.height), VgmConfig(watermark=True)).getbbox()
        is None
    )


def test_vgm_overlay_off_with_text_is_transparent():
    img = build_vgm_overlay(
        (SQUARE.width, SQUARE.height),
        VgmConfig(watermark=False, watermark_text="VGM"),
    )
    assert img.getbbox() is None


def test_vgm_overlay_from_style():
    img = build_vgm_overlay(
        (SQUARE.width, SQUARE.height),
        style={"watermark": True, "watermark_text": "VGM", "watermark_opacity": 0.15},
    )
    assert img.getbbox() is not None


def test_build_hud_without_title_composes_overlay(tmp_path):
    out = tmp_path / "vgm.png"
    overlay = build_vgm_overlay(
        (SQUARE.width, SQUARE.height),
        VgmConfig(watermark=True, watermark_text="VGM"),
    )
    build_hud(out, SQUARE, "Hidden Title", show_title=False, overlay=overlay)
    img = Image.open(out)
    assert img.size == (SQUARE.width, SQUARE.height)
    assert img.mode == "RGBA"
    assert img.getbbox() is not None


def test_build_hud_without_title_is_empty(tmp_path):
    out = tmp_path / "hidden.png"
    build_hud(out, SQUARE, "Hidden Title", show_title=False)
    assert Image.open(out).getbbox() is None