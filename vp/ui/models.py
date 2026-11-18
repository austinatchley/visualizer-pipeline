"""Studio API request and response models (issue #61).

Response models give `/openapi.json` real schemas, which the frontend type
checks build on (#62, #63), and let FastAPI validate every response. They
mirror the existing payloads exactly: optional keys that a payload may omit
are modeled with `TypedDict`/`NotRequired` rather than `None` defaults, so
serialization never adds keys.
"""

from __future__ import annotations

from typing import Any, Literal, NotRequired, TypedDict

from pydantic import BaseModel, ConfigDict, Field, create_model, with_config

from ..config import DEFAULT_STYLE, STYLE_FIELDS

# --- Style ---------------------------------------------------------------------

_SCALARS: dict[str, Any] = {"int": int, "float": float, "bool": bool, "preset": str, "color": str}


def _style_annotation(spec: dict) -> Any:
    kind = spec["type"]
    if kind == "choice":
        return Literal[tuple(spec["choices"])]
    if kind in _SCALARS:
        return _SCALARS[kind]
    # path | texture | text: free-form strings where null means "off".
    return str | None


def _build_style_model() -> type[BaseModel]:
    # One field per style key, derived from STYLE_FIELDS so adding a key to the
    # schema updates the API contract too.
    fields = {
        key: (_style_annotation(spec), DEFAULT_STYLE[key]) for key, spec in STYLE_FIELDS.items()
    }
    return create_model(
        "Style",
        __config__=ConfigDict(extra="forbid"),
        __doc__="A fully resolved style: every key of DEFAULT_STYLE.",
        **fields,
    )


Style = _build_style_model()


# extra="forbid": a new spec key must be added here, never silently dropped.
@with_config(ConfigDict(extra="forbid"))
class StyleFieldSpec(TypedDict):
    """One `STYLE_FIELDS` entry: drives validation, CLI flags, and UI controls."""

    type: Literal["preset", "int", "float", "bool", "choice", "path", "texture", "text", "color"]
    help: str
    min: NotRequired[float]
    max: NotRequired[float]
    step: NotRequired[float]
    exclusive_min: NotRequired[bool]
    choices: NotRequired[list[str | int]]


# --- Requests ------------------------------------------------------------------


class StylePayload(BaseModel):
    style: dict[str, object]


class MetadataPayload(BaseModel):
    title: str | None = None
    artist: str | None = None
    handle: str | None = None


class ImageNamesPayload(BaseModel):
    names: list[str]


class OrderPayload(BaseModel):
    order: list[str]


class RenderPayload(BaseModel):
    aspect: str = "vertical"
    aspects: list[str] | None = None
    style: dict[str, object]
    preview: bool = False


# --- Responses -----------------------------------------------------------------


class TextureEntry(BaseModel):
    path: str
    label: str


class AspectInfo(BaseModel):
    label: str
    kind: str


class BootData(BaseModel):
    fields: dict[str, StyleFieldSpec]
    defaults: Style
    presets: list[str]
    textures: list[TextureEntry]
    aspects: dict[str, AspectInfo]


class TrackSummary(BaseModel):
    id: str
    title: str
    artist: str | None
    path: str
    audio: str
    image_count: int
    preset: str


class TrackError(BaseModel):
    """A catalog entry for a folder that failed to load; keeps the studio usable."""

    id: str
    title: str
    path: str
    error: str


class CreatedTrack(BaseModel):
    id: str
    title: str
    path: str
    audio: str
    image_count: int


class Hook(BaseModel):
    mode: Literal["auto", "full", "manual"]
    target: float
    start: float | None
    end: float | None


class TrackDetail(BaseModel):
    id: str
    title: str
    artist: str | None
    handle: str | None
    path: str
    audio: str
    images: list[str]
    image_count: int
    style: Style
    hook: Hook


class DeletedTrack(BaseModel):
    deleted: str


class ImagesAdded(BaseModel):
    added: list[str]
    images: list[str]


class ImagesRemoved(BaseModel):
    removed: list[str]
    images: list[str]


class ImageOrder(BaseModel):
    images: list[str]


class MetadataResult(BaseModel):
    id: str
    title: str
    artist: str | None
    handle: str | None


class StyleSaveResult(BaseModel):
    saved: dict[str, Any] = Field(
        description="Only the values that differ from the preset (what track.json stores)."
    )
    resolved: Style


class PreviewResult(BaseModel):
    urls: dict[str, str]
    url: str


class JobCreated(BaseModel):
    id: str


JobStatus = Literal["pending", "running", "done", "skipped", "cancelled", "failed"]


class JobSummary(BaseModel):
    id: str
    track: str
    aspect: str
    preview: bool
    status: JobStatus
    progress: float
    error: str | None
    url: str | None
    created: float
    started: float | None
    ended: float | None


class JobCancelled(BaseModel):
    id: str
    cancelled: Literal[True]
