"""Bundled font registry and loading helpers."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PIL import ImageFont

FONT_DIR = Path(__file__).resolve().parent / "assets" / "fonts"

SYSTEM_FONT_CANDIDATES = [
    # macOS
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/System/Library/Fonts/Supplemental/Arial Rounded Bold.ttf",
    "/System/Library/Fonts/Supplemental/Arial Black.ttf",
    "/System/Library/Fonts/Supplemental/Impact.ttf",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
    # Linux (CI and most distributions)
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf",
    "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
]


@dataclass(frozen=True)
class FontFace:
    key: str
    label: str
    path: Path | None
    axes: tuple[float, ...] | None = None
    source: str = "SIL OFL"


# Order is the UI dropdown order. `clean` uses the system fallback.
FONT_FACES: dict[str, FontFace] = {
    "clean": FontFace("clean", "Clean (system)", None),
    "redaction": FontFace(
        "redaction", "Redaction 50 Bold", FONT_DIR / "Redaction50-Bold.ttf"
    ),
    "redaction-regular": FontFace(
        "redaction-regular", "Redaction 50", FONT_DIR / "Redaction50-Regular.ttf"
    ),
    "redaction-italic": FontFace(
        "redaction-italic", "Redaction 50 Italic", FONT_DIR / "Redaction50-Italic.ttf"
    ),
    "tiny5": FontFace("tiny5", "Tiny5 Duo Black", FONT_DIR / "Tiny5Duo-Black.ttf"),
    "tiny5-round": FontFace(
        "tiny5-round",
        "Tiny5 Duo Round",
        FONT_DIR / "Tiny5Duo-Variable.ttf",
        (900, 100, 100, 60, 0),
    ),
    "tiny5-jitter": FontFace(
        "tiny5-jitter",
        "Tiny5 Duo Jitter",
        FONT_DIR / "Tiny5Duo-Variable.ttf",
        (900, 100, 100, 60, 30),
    ),
    "pixelify": FontFace(
        "pixelify", "Pixelify Sans Bold", FONT_DIR / "PixelifySans-Variable.ttf", (700,)
    ),
    "silkscreen": FontFace(
        "silkscreen", "Silkscreen", FONT_DIR / "Silkscreen-Regular.ttf"
    ),
    "syne": FontFace(
        "syne", "Syne ExtraBold", FONT_DIR / "Syne-Variable.ttf", (800,)
    ),
    "instrument": FontFace(
        "instrument", "Instrument Serif", FONT_DIR / "InstrumentSerif-Regular.ttf"
    ),
    "anton": FontFace("anton", "Anton", FONT_DIR / "Anton-Regular.ttf"),
}

FONT_CHOICES = list(FONT_FACES)


def _system_path() -> Path:
    for candidate in SYSTEM_FONT_CANDIDATES:
        path = Path(candidate)
        if path.is_file():
            return path
    raise FileNotFoundError("no usable system font found; set style.title_font")


def resolve_face(name: str | None = None, override: str | None = None) -> FontFace:
    """Resolve a font choice, with `title_font` path taking precedence."""
    if override:
        path = Path(override)
        if not path.is_file():
            raise FileNotFoundError(f"title_font not found: {override}")
        return FontFace("custom", "Custom", path)

    if name and name != "clean":
        face = FONT_FACES.get(name)
        if face is None:
            raise ValueError(
                f"unknown font {name!r}; available: {', '.join(FONT_CHOICES)}"
            )
        if face.path is None or not face.path.is_file():
            raise FileNotFoundError(f"bundled font missing: {face.path}")
        return face

    return FontFace("clean", "Clean (system)", _system_path())


def find_font(override: str | None = None) -> str:
    """Backward-compatible helper: return a usable font path."""
    face = resolve_face(None, override)
    assert face.path is not None
    return str(face.path)


def load_font(
    face: FontFace | str | Path | None, size: int
) -> ImageFont.FreeTypeFont:
    if face is None:
        face = resolve_face()
    if isinstance(face, (str, Path)):
        face = FontFace("custom", "Custom", Path(face))
    if face.path is None:
        face = FontFace(face.key, face.label, _system_path(), face.axes)

    font = ImageFont.truetype(str(face.path), size)
    if face.axes:
        font.set_variation_by_axes(list(face.axes))
    return font