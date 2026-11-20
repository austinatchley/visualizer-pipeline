"""Data readouts drawn per frame (issue #82, epic #80).

An instrument strip across the top of the frame: the left block shows the clock
(track position / total) over a beat-in-bar indicator, and the right block shows
BPM and key beside sub / low / mid / high band meters. Everything that moves is
precomputed per frame here (`ReadoutFrames`), so `ReadoutPainter.frame` is a
pure function of the frame index, like the spectrum layer.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from .config import parse_color
from .fonts import READOUT_FONT, load_font, resolve_face
from .music import short_key
from .spectrum import BANDS

BEAT_FLASH_S = 0.18  # a lit beat cell decays to its resting level over this
METER_SEGMENTS = 10
PLATE_ALPHA = 0.75  # plate opacity at backplate = 1.0 (matches the spectrum's)
PLATE_RGB = (10, 9, 12)
TAG_RGBA = (236, 230, 226, 150)
VALUE_RGBA = (245, 240, 236, 245)
SHADOW_RGBA = (0, 0, 0, 140)
UNLIT_RGBA = (255, 255, 255, 46)


def wants_readouts(style: dict) -> bool:
    return any(style[k] for k in ("show_clock", "show_tempo", "show_beats", "show_meters"))


@dataclass(frozen=True)
class ReadoutFrames:
    """Per-frame readout state, length = frames."""

    clock: np.ndarray  # absolute track seconds
    beat_pos: np.ndarray  # beat in bar 0..3; -1 before the first beat
    flash: np.ndarray  # 1.0 on a beat, decaying toward 0
    bands: np.ndarray  # (frames, len(BANDS)) meter levels in [0, 1]


def beat_state(
    beats: list[float], downbeats: list[float], times: np.ndarray
) -> tuple[np.ndarray, np.ndarray]:
    """Beat-in-bar position and beat flash at each time."""
    b = np.asarray(beats, dtype=float)
    pos = np.full(len(times), -1, np.int8)
    flash = np.zeros(len(times), np.float32)
    if len(b) == 0:
        return pos, flash
    i = np.searchsorted(b, times, side="right") - 1
    db_index = np.searchsorted(b, np.asarray(downbeats, dtype=float) - 1e-6)
    if len(db_index):
        j = np.searchsorted(db_index, i, side="right") - 1
        base = np.where(j >= 0, db_index[np.clip(j, 0, None)], 0)
    else:
        base = np.zeros_like(i)
    on = i >= 0
    pos[on] = ((i[on] - base[on]) % 4).astype(np.int8)
    flash[on] = np.exp(-(times[on] - b[i[on]]) / BEAT_FLASH_S)
    return pos, flash


def readout_frames(
    beatmap: dict, window: tuple[float, float], fps: int, bands: np.ndarray
) -> ReadoutFrames:
    times = float(window[0]) + np.arange(len(bands)) / fps
    pos, flash = beat_state(beatmap.get("beats") or [], beatmap.get("downbeats") or [], times)
    return ReadoutFrames(times, pos, flash, bands)


def format_clock(seconds: float) -> str:
    minutes, secs = divmod(max(0, int(seconds)), 60)
    return f"{minutes:02d}:{secs:02d}"


@dataclass(frozen=True)
class Rect:
    x: int
    y: int
    width: int
    height: int


def strip_origin(size: tuple[int, int]) -> tuple[int, int]:
    """(left margin, top) of the readout strip; the title aligns to it too (#98)."""
    width, height = size
    vertical = height > width * 1.2
    horizontal = width > height * 1.2
    margin = round(width * (0.06 if horizontal else 0.07))
    return margin, round(height * (0.085 if vertical else 0.06))


class ReadoutPainter:
    """Lays out the strip once, draws the static parts into `base`, and draws
    the moving parts per frame. `rect` is the encoded clip in output pixels."""

    def __init__(self, size: tuple[int, int], style: dict, beatmap: dict):
        width, height = size
        unit = min(width, height)
        self.show = {k: bool(style[f"show_{k}"]) for k in ("clock", "tempo", "beats", "meters")}
        self.accent = parse_color(style["accent_color"])
        self.total = format_clock(float(beatmap.get("duration") or 0.0))
        tempo = float(beatmap.get("tempo") or 0.0)
        self.bpm_text = f"{tempo:.0f}" if tempo > 0 else "--"
        self.key_text = short_key(beatmap.get("key"))

        self.fv = max(6, round(unit * 0.032))  # value text size
        self.ft = max(5, round(unit * 0.017))  # tag text size
        margin, top = strip_origin(size)
        self.row_tag = top
        self.row_val = top + round(self.ft * 1.6)
        self.cell = max(3, round(self.fv * 0.42))
        self.row_cells = self.row_val + round(self.fv * 1.35)
        if self.show["beats"] and not self.show["clock"]:
            self.row_cells = self.row_val + round(self.fv * 0.3)
        has_cells = self.show["beats"]
        bottom = self.row_cells + self.cell if has_cells else self.row_val + self.fv
        self.bottom = bottom

        tag_font, val_font = self._fonts()
        clock_w = round(val_font.getlength(f"{self.total} / {self.total}"))
        self.cells_w = 4 * self.cell + 3 * round(self.cell * 0.7)
        left_w = max(clock_w if self.show["clock"] else 0, self.cells_w if has_cells else 0)
        self.left = Rect(margin, top, left_w, bottom - top) if left_w else None

        # Right block, laid out right to left: meters, then the KEY and BPM columns.
        cursor = width - margin
        self.meter_w = max(2, round(self.fv * 0.34))
        self.meter_gap = max(1, round(self.fv * 0.26))
        self.meters_x = None
        if self.show["meters"]:
            meters_w = len(BANDS) * self.meter_w + (len(BANDS) - 1) * self.meter_gap
            self.meters_x = cursor - meters_w
            cursor = self.meters_x - round(self.fv * 1.0)
        self.tempo_cols: list[tuple[int, str, str]] = []
        if self.show["tempo"]:
            for tag, value in (("KEY", self.key_text), ("BPM", self.bpm_text)):
                col_w = round(max(val_font.getlength(value), tag_font.getlength(tag)))
                self.tempo_cols.append((cursor - col_w, tag, value))
                cursor -= col_w + round(self.fv * 0.9)
            cursor += round(self.fv * 0.9)
        right_x = cursor if self.tempo_cols else (self.meters_x or width - margin)
        right_w = width - margin - right_x
        self.right = Rect(right_x, top, right_w, bottom - top) if right_w > 0 else None

        pad = round(self.fv * 1.4) if float(style["backplate"]) > 0 else round(self.fv * 0.3)
        y0, y1 = max(0, top - pad), min(height, bottom + pad)
        x0 = max(0, (self.left.x if self.left else right_x) - pad)
        x1 = min(width, (width - margin if self.right else self.left.x + self.left.width) + pad)
        self.rect = Rect(x0, y0, x1 - x0, y1 - y0)
        self.base = self._base(float(style["backplate"]), pad)

    # Fonts are loaded lazily and not pickled: the painter is shipped to the
    # process pool once per worker, and FreeType handles don't pickle.
    def _fonts(self) -> tuple[ImageFont.FreeTypeFont, ImageFont.FreeTypeFont]:
        fonts = getattr(self, "_font_cache", None)
        if fonts is None:
            face = resolve_face(READOUT_FONT)
            fonts = (load_font(face, self.ft), load_font(face, self.fv))
            self._font_cache = fonts
        return fonts

    def __getstate__(self) -> dict:
        state = dict(self.__dict__)
        state.pop("_font_cache", None)
        return state

    def _local(self, x: float, y: float) -> tuple[float, float]:
        return x - self.rect.x, y - self.rect.y

    def _text(self, draw: ImageDraw.ImageDraw, x, y, text, font, fill) -> None:
        lx, ly = self._local(x, y)
        off = max(1, round(font.size / 22))
        draw.text((lx + off, ly + off), text, font=font, fill=SHADOW_RGBA, anchor="la")
        draw.text((lx, ly), text, font=font, fill=fill, anchor="la")

    def _plate(self, strength: float, pad: int) -> Image.Image:
        plate = Image.new("RGBA", (self.rect.width, self.rect.height), (*PLATE_RGB, 0))
        if strength <= 0:
            return plate
        mask = Image.new("L", plate.size, 0)
        draw = ImageDraw.Draw(mask)
        inset = round(self.fv * 0.45)
        alpha = round(255 * min(1.0, strength) * PLATE_ALPHA)
        for block in (self.left, self.right):
            if block is None:
                continue
            x, y = self._local(block.x - inset, block.y - inset)
            draw.rounded_rectangle(
                (x, y, x + block.width + 2 * inset, y + block.height + 2 * inset),
                radius=inset,
                fill=alpha,
            )
        plate.putalpha(mask.filter(ImageFilter.GaussianBlur(max(1, pad * 0.35))))
        return plate

    def _cells(self) -> list[tuple[int, int]]:
        step = self.cell + round(self.cell * 0.7)
        assert self.left is not None
        return [(self.left.x + i * step, self.row_cells) for i in range(4)]

    def _meter_box(self, i: int) -> tuple[int, int, int, int]:
        assert self.meters_x is not None
        x = self.meters_x + i * (self.meter_w + self.meter_gap)
        return x, self.row_val, x + self.meter_w, self.bottom

    def _segments(self, i: int) -> list[tuple[int, int, int, int]]:
        """LED segments bottom-up; fewer of them when the meter is tiny (previews)."""
        x0, y0, x1, y1 = self._meter_box(i)
        count = max(1, min(METER_SEGMENTS, (y1 - y0) // 3))
        gap = max(1, round((y1 - y0) / count * 0.22)) if count > 1 else 0
        seg = (y1 - y0 + gap) / count
        return [
            (
                x0,
                round(y1 - (n + 1) * seg + gap),
                x1 - 1,
                max(round(y1 - (n + 1) * seg + gap), round(y1 - n * seg) - 1),
            )
            for n in range(count)
        ]

    def _base(self, backplate: float, pad: int) -> Image.Image:
        img = self._plate(backplate, pad)
        draw = ImageDraw.Draw(img)
        tag_font, val_font = self._fonts()
        if self.left is not None:
            if self.show["clock"]:
                self._text(draw, self.left.x, self.row_tag, "TIME", tag_font, TAG_RGBA)
            elif self.show["beats"]:
                self._text(draw, self.left.x, self.row_tag, "BEAT", tag_font, TAG_RGBA)
            if self.show["beats"]:
                for x, y in self._cells():
                    lx, ly = self._local(x, y)
                    draw.rectangle(
                        (lx, ly, lx + self.cell - 1, ly + self.cell - 1), fill=UNLIT_RGBA
                    )
        for x, tag, value in self.tempo_cols:
            self._text(draw, x, self.row_tag, tag, tag_font, TAG_RGBA)
            self._text(draw, x, self.row_val, value, val_font, VALUE_RGBA)
        if self.meters_x is not None:
            for i, (name, _, _) in enumerate(BANDS):
                x0, _, x1, _ = self._meter_box(i)
                letter = name[0]
                lx = (x0 + x1) / 2 - tag_font.getlength(letter) / 2
                self._text(draw, lx, self.row_tag, letter, tag_font, TAG_RGBA)
                for box in self._segments(i):
                    bx0, by0 = self._local(box[0], box[1])
                    bx1, by1 = self._local(box[2], box[3])
                    draw.rectangle((bx0, by0, bx1, by1), fill=UNLIT_RGBA)
        return img

    def frame(self, clock: float, beat_pos: int, flash: float, bands: np.ndarray) -> Image.Image:
        out = self.base.copy()
        draw = ImageDraw.Draw(out)
        _, val_font = self._fonts()
        if self.left is not None and self.show["clock"]:
            text = f"{format_clock(clock)} / {self.total}"
            self._text(draw, self.left.x, self.row_val, text, val_font, VALUE_RGBA)
        if self.left is not None and self.show["beats"] and beat_pos >= 0:
            for i, (x, y) in enumerate(self._cells()):
                if i > beat_pos:
                    break
                lx, ly = self._local(x, y)
                if i < beat_pos:
                    fill = (255, 255, 255, 110)
                else:
                    rgb = self.accent if i == 0 else (255, 255, 255)
                    fill = (*rgb, round(255 * (0.7 + 0.3 * float(flash))))
                draw.rectangle((lx, ly, lx + self.cell - 1, ly + self.cell - 1), fill=fill)
        if self.meters_x is not None:
            for i in range(len(BANDS)):
                segments = self._segments(i)
                count = len(segments)
                lit = int(math.floor(float(bands[i]) * count + 0.5))
                for n, box in enumerate(segments[:lit]):
                    hot = n >= count - max(1, round(count * 0.3))
                    fill = (*self.accent, 240) if hot else (240, 235, 230, 225)
                    bx0, by0 = self._local(box[0], box[1])
                    bx1, by1 = self._local(box[2], box[3])
                    draw.rectangle((bx0, by0, bx1, by1), fill=fill)
        return out
