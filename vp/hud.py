"""Pillow HUD overlay: bottom gradient, title, artist, and handle."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from .aspects import Aspect

FONT_CANDIDATES = [
    # macOS
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/System/Library/Fonts/Supplemental/Impact.ttf",
    "/System/Library/Fonts/Supplemental/Arial Black.ttf",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
    # Linux (CI and most distributions)
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf",
    "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
]


@dataclass(frozen=True)
class HudLayout:
    fontsize: int
    max_width: int
    block_bottom: int
    align: str
    x: int


def find_font(override: str | None = None) -> str:
    if override:
        if Path(override).is_file():
            return override
        raise FileNotFoundError(f"title_font not found: {override}")
    for candidate in FONT_CANDIDATES:
        if Path(candidate).is_file():
            return candidate
    raise FileNotFoundError("no usable system font found; set style.title_font")


def wrap_to_width(
    text: str, font: ImageFont.FreeTypeFont, max_width: int
) -> list[str]:
    lines: list[str] = []
    current = ""
    for word in text.split():
        trial = f"{current} {word}".strip()
        if not current or font.getlength(trial) <= max_width:
            current = trial
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def meta_line(artist: str | None, handle: str | None) -> str | None:
    parts = [part for part in (artist, handle) if part]
    return " · ".join(parts) if parts else None


def layout_for(aspect: Aspect) -> HudLayout:
    if aspect.kind == "vertical":
        return HudLayout(
            fontsize=int(aspect.width * 0.085),
            max_width=int(aspect.width * 0.86),
            block_bottom=int(aspect.height * 0.80),
            align="center",
            x=aspect.width // 2,
        )
    if aspect.kind == "square":
        return HudLayout(
            fontsize=int(aspect.width * 0.09),
            max_width=int(aspect.width * 0.82),
            block_bottom=int(aspect.height * 0.82),
            align="center",
            x=aspect.width // 2,
        )
    return HudLayout(
        fontsize=int(aspect.height * 0.075),
        max_width=int(aspect.width * 0.55),
        block_bottom=int(aspect.height * 0.87),
        align="left",
        x=int(aspect.width * 0.06),
    )


def _line_height(font: ImageFont.FreeTypeFont) -> int:
    ascent, descent = font.getmetrics()
    return int((ascent + descent) * 1.12)


def _block_height(lines: list[str], font: ImageFont.FreeTypeFont) -> int:
    return _line_height(font) * len(lines)


def _draw_text_block(
    draw: ImageDraw.ImageDraw,
    lines: list[str],
    font: ImageFont.FreeTypeFont,
    x: int,
    top: int,
    fill: tuple,
    shadow: tuple,
    align: str,
    shadow_offset: int,
) -> None:
    line_h = _line_height(font)
    y = top
    for line in lines:
        width = font.getlength(line)
        tx = x if align == "left" else x - width / 2
        draw.text((tx + shadow_offset, y + shadow_offset), line, font=font, fill=shadow)
        draw.text((tx, y), line, font=font, fill=fill)
        y += line_h


def build_hud(
    path: Path,
    aspect: Aspect,
    title: str,
    artist: str | None = None,
    handle: str | None = None,
    font_path: str | None = None,
) -> Path:
    font_path = font_path or find_font()
    layout = layout_for(aspect)

    canvas = Image.new("RGBA", (aspect.width, aspect.height), (0, 0, 0, 0))

    gradient_start = int(aspect.height * 0.52)
    ramp = np.linspace(0.0, 1.0, aspect.height - gradient_start) ** 1.7
    gradient_alpha = (ramp * 190).astype(np.uint8)
    pixels = np.array(canvas)
    pixels[gradient_start:, :, 3] = np.maximum(
        pixels[gradient_start:, :, 3], gradient_alpha[:, None]
    )
    canvas = Image.fromarray(pixels, "RGBA")
    draw = ImageDraw.Draw(canvas)

    font = ImageFont.truetype(font_path, layout.fontsize)
    lines = wrap_to_width(title, font, layout.max_width)
    title_height = _block_height(lines, font)

    meta = meta_line(artist, handle)
    if meta:
        meta_size = int(layout.fontsize * 0.40)
        meta_font = ImageFont.truetype(font_path, meta_size)
        gap = int(layout.fontsize * 0.28)
        meta_height = _block_height([meta], meta_font)
    else:
        meta_font = None
        gap = 0
        meta_height = 0

    title_top = layout.block_bottom - title_height - (gap + meta_height if meta else 0)
    shadow = max(2, layout.fontsize // 40)
    _draw_text_block(
        draw,
        lines,
        font,
        layout.x,
        title_top,
        fill=(255, 255, 255, 255),
        shadow=(0, 0, 0, 150),
        align=layout.align,
        shadow_offset=shadow,
    )

    if meta and meta_font is not None:
        _draw_text_block(
            draw,
            [meta],
            meta_font,
            layout.x,
            title_top + title_height + gap,
            fill=(255, 255, 255, 190),
            shadow=(0, 0, 0, 120),
            align=layout.align,
            shadow_offset=max(1, meta_size // 40),
        )

    canvas.save(path)
    return path