"""Output aspects (vertical, square, horizontal) and their metadata."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Aspect:
    key: str
    width: int
    height: int
    label: str
    kind: str


VERTICAL = Aspect("vertical", 1080, 1920, "9x16", "vertical")
SQUARE = Aspect("square", 1080, 1080, "1x1", "square")
HORIZONTAL = Aspect("horizontal", 1920, 1080, "16x9", "horizontal")

ASPECTS = {
    "vertical": VERTICAL,
    "square": SQUARE,
    "horizontal": HORIZONTAL,
}