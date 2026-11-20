"""Pillow HUD overlay: bottom gradient, title, artist, and handle."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from .aspects import Aspect
from .config import DEFAULT_STYLE
from .fonts import FontFace, load_font, resolve_face


@dataclass(frozen=True)
class HudLayout:
    fontsize: int
    max_width: int
    block_bottom: int
    align: str
    x: int


def apply_case(text: str, mode: str) -> str:
    if mode == "upper":
        return text.upper()
    if mode == "lower":
        return text.lower()
    return text


def _measure_line(text: str, font: ImageFont.FreeTypeFont, tracking_px: float = 0.0) -> float:
    if tracking_px == 0 or not text:
        return font.getlength(text)
    return sum(font.getlength(char) for char in text) + tracking_px * (len(text) - 1)


def _break_word(
    word: str,
    font: ImageFont.FreeTypeFont,
    max_width: int,
    tracking_px: float = 0.0,
) -> list[str]:
    lines: list[str] = []
    current = ""
    for char in word:
        trial = current + char
        if not current or _measure_line(trial, font, tracking_px) <= max_width:
            current = trial
        else:
            lines.append(current)
            current = char
    if current:
        lines.append(current)
    return lines


def wrap_to_width(
    text: str,
    font: ImageFont.FreeTypeFont,
    max_width: int,
    tracking_px: float = 0.0,
) -> list[str]:
    lines: list[str] = []
    current = ""
    for word in text.split():
        if not current:
            if _measure_line(word, font, tracking_px) <= max_width:
                current = word
            else:
                lines.extend(_break_word(word, font, max_width, tracking_px))
            continue
        trial = f"{current} {word}"
        if _measure_line(trial, font, tracking_px) <= max_width:
            current = trial
        elif _measure_line(word, font, tracking_px) <= max_width:
            lines.append(current)
            current = word
        else:
            lines.append(current)
            lines.extend(_break_word(word, font, max_width, tracking_px))
            current = ""
    if current:
        lines.append(current)
    return lines


def meta_line(artist: str | None, handle: str | None) -> str | None:
    parts = [part for part in (artist, handle) if part]
    return " · ".join(parts) if parts else None


@dataclass(frozen=True)
class VgmConfig:
    """VGM-layer look: a giant translucent watermark over the footage."""

    # Defaults come from DEFAULT_STYLE so there is one source of truth (#50).
    watermark: bool = DEFAULT_STYLE["watermark"]
    watermark_text: str | None = DEFAULT_STYLE["watermark_text"]
    watermark_font: str = DEFAULT_STYLE["watermark_font"]
    watermark_opacity: float = DEFAULT_STYLE["watermark_opacity"]
    watermark_width: float = DEFAULT_STYLE["watermark_width"]
    watermark_stroke: float = DEFAULT_STYLE["watermark_stroke"]
    watermark_x: float = DEFAULT_STYLE["watermark_x"]
    watermark_y: float = DEFAULT_STYLE["watermark_y"]

    @property
    def active(self) -> bool:
        return self.watermark

    @classmethod
    def from_style(cls, style: dict) -> VgmConfig:
        """Build from a resolved style (every key present; see `resolve_style`)."""
        return cls(
            watermark=bool(style["watermark"]),
            watermark_text=style["watermark_text"],
            watermark_font=style["watermark_font"],
            watermark_opacity=float(style["watermark_opacity"]),
            watermark_width=float(style["watermark_width"]),
            watermark_stroke=float(style["watermark_stroke"]),
            watermark_x=float(style["watermark_x"]),
            watermark_y=float(style["watermark_y"]),
        )


def _fit_font(face: FontFace, text: str, target_width: int) -> ImageFont.FreeTypeFont:
    """Return the largest font size whose width for `text` fits `target_width`."""
    font = load_font(face, 96)
    size = 96
    if text:
        width = max(font.getlength(text), 1.0)
        size = max(8, int(96 * target_width / width))
        font = load_font(face, size)
    while text and size > 8 and font.getlength(text) > target_width:
        size -= 2
        font = load_font(face, size)
    return font


def _draw_watermark(canvas: Image.Image, cfg: VgmConfig, width: int, height: int) -> None:
    if not cfg.watermark or not cfg.watermark_text:
        return
    text = str(cfg.watermark_text)
    alpha = int(255 * cfg.watermark_opacity)
    if alpha <= 0:
        return
    face = resolve_face(cfg.watermark_font or "anton")
    font = _fit_font(face, text, int(width * cfg.watermark_width))
    stroke = max(1, int(font.size * cfg.watermark_stroke))
    cx = width / 2 + cfg.watermark_x * width / 2
    cy = height / 2 + cfg.watermark_y * height / 2
    ImageDraw.Draw(canvas).text(
        (cx, cy),
        text,
        font=font,
        fill=(255, 255, 255, alpha),
        stroke_width=stroke,
        stroke_fill=(0, 0, 0, alpha),
        anchor="mm",
    )


def build_vgm_overlay(size: tuple[int, int], cfg: VgmConfig) -> Image.Image:
    """Procedural VGM watermark overlay at target size."""
    width, height = size
    canvas = Image.new("RGBA", size, (0, 0, 0, 0))
    _draw_watermark(canvas, cfg, width, height)
    return canvas


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


def _line_height(font: ImageFont.FreeTypeFont, line_spacing: float = 1.12) -> int:
    ascent, descent = font.getmetrics()
    return int((ascent + descent) * line_spacing)


def _block_height(
    lines: list[str], font: ImageFont.FreeTypeFont, line_spacing: float = 1.12
) -> int:
    return _line_height(font, line_spacing) * len(lines)


def _draw_line(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: ImageFont.FreeTypeFont,
    x: float,
    y: float,
    fill: tuple,
    tracking_px: float = 0.0,
) -> None:
    if tracking_px == 0:
        draw.text((x, y), text, font=font, fill=fill)
        return
    cursor = x
    for char in text:
        draw.text((cursor, y), char, font=font, fill=fill)
        cursor += font.getlength(char) + tracking_px


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
    line_spacing: float = 1.12,
    tracking_px: float = 0.0,
) -> None:
    line_h = _line_height(font, line_spacing)
    y = top
    for line in lines:
        width = _measure_line(line, font, tracking_px)
        tx = x if align == "left" else x - width / 2
        _draw_line(draw, line, font, tx + shadow_offset, y + shadow_offset, shadow, tracking_px)
        _draw_line(draw, line, font, tx, y, fill, tracking_px)
        y += line_h


def build_hud(
    path: Path,
    aspect: Aspect,
    title: str,
    artist: str | None = None,
    handle: str | None = None,
    font_path: str | None = None,
    *,
    font_face: FontFace | None = None,
    meta_font_face: FontFace | None = None,
    text_case: str = "preserve",
    tracking: float = 0.0,
    line_spacing: float = 1.12,
    show_title: bool = True,
    overlay: Image.Image | None = None,
    block_bottom: int | None = None,
) -> Path:
    """`block_bottom`, when given, caps how low the title block sits (it only
    ever moves the block up), e.g. to clear a bottom-anchored spectrum."""
    font_face = font_face or resolve_face(None, font_path)
    meta_font_face = meta_font_face or font_face

    canvas = Image.new("RGBA", (aspect.width, aspect.height), (0, 0, 0, 0))

    if show_title:
        title = apply_case(title, text_case)
        layout = layout_for(aspect)

        gradient_start = int(aspect.height * 0.52)
        ramp = np.linspace(0.0, 1.0, aspect.height - gradient_start) ** 1.7
        gradient_alpha = (ramp * 190).astype(np.uint8)
        pixels = np.array(canvas)
        pixels[gradient_start:, :, 3] = np.maximum(
            pixels[gradient_start:, :, 3], gradient_alpha[:, None]
        )
        canvas = Image.fromarray(pixels, "RGBA")
        draw = ImageDraw.Draw(canvas)

        font = load_font(font_face, layout.fontsize)
        tracking_px = float(tracking) * layout.fontsize
        lines = wrap_to_width(title, font, layout.max_width, tracking_px)
        title_height = _block_height(lines, font, line_spacing)

        meta = meta_line(artist, handle)
        if meta:
            meta_size = int(layout.fontsize * 0.40)
            meta_font = load_font(meta_font_face, meta_size)
            gap = int(layout.fontsize * 0.28)
            meta_height = _block_height([meta], meta_font)
        else:
            meta_font = None
            gap = 0
            meta_height = 0

        bottom = layout.block_bottom
        if block_bottom is not None:
            bottom = min(bottom, block_bottom)
        title_top = bottom - title_height - (gap + meta_height if meta else 0)
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
            line_spacing=line_spacing,
            tracking_px=tracking_px,
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

    if overlay is not None:
        canvas = Image.alpha_composite(canvas, overlay)

    canvas.save(path)
    return path
