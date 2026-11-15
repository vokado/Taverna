from __future__ import annotations

from typing import Literal
from uuid import UUID, uuid4

from pydantic import Field, JsonValue, model_validator

from .world_models import Strict

Category = Literal[
    "portrait", "illustration", "expression", "costume", "animation", "background", "music", "ambient"
]
Mime = Literal[
    "image/png", "image/jpeg", "image/webp", "image/gif", "audio/wav", "audio/mpeg", "audio/ogg", "audio/flac"
]


class AssetMetadata(Strict):
    name: str = Field(min_length=1, max_length=300)
    description: str = Field(default="", max_length=8000)
    category: Category = "illustration"
    tags: list[str] = Field(default_factory=list, max_length=100)
    archived: bool = False


class Asset(Strict):
    relative_path: str = Field(min_length=1, max_length=300)
    checksum: str = Field(pattern="^[a-f0-9]{64}$")
    type: Mime
    name: str = Field(default="Портрет", min_length=1, max_length=300)
    description: str = Field(default="", max_length=8000)
    category: Category = "portrait"
    tags: list[str] = Field(default_factory=list, max_length=100)
    archived: bool = False
    info: dict[str, JsonValue] = Field(default_factory=dict)
    provenance: dict[str, JsonValue] = Field(default_factory=dict)

    @model_validator(mode="after")
    def compatible(self):
        if any(not t.strip() or len(t) > 100 for t in self.tags):
            raise ValueError("Некорректный тег")
        if self.type.startswith("audio/") != (self.category in ("music", "ambient")):
            raise ValueError("Категория не соответствует аудио/изображению")
        return self


class Layer(Strict):
    id: UUID = Field(default_factory=uuid4)
    name: str = Field(min_length=1, max_length=200)
    asset_id: UUID
    character_id: UUID | None = None
    enabled: bool = True
    x: float = Field(default=50, ge=-100, le=200)
    y: float = Field(default=50, ge=-100, le=200)
    width: float = Field(default=50, gt=0, le=300)
    height: float = Field(default=100, gt=0, le=300)
    opacity: float = Field(default=1, ge=0, le=1)
    z: int = Field(default=0, ge=-100, le=100)
    fit: Literal["contain", "cover", "fill"] = "contain"
    costume: str = Field(default="", max_length=100)
    pose: str = Field(default="", max_length=100)
    emotion: str = Field(default="auto", max_length=100)


class Variant(Strict):
    id: UUID = Field(default_factory=uuid4)
    character_id: UUID
    asset_id: UUID
    costume: str = Field(default="", max_length=100)
    pose: str = Field(default="", max_length=100)
    emotion: str = Field(default="", max_length=100)
    priority: int = Field(default=0, ge=-100, le=100)


class Binding(Strict):
    id: UUID = Field(default_factory=uuid4)
    asset_id: UUID
    kind: Literal["character", "scene", "event", "world"]
    target_id: UUID


class AudioTrack(Strict):
    asset_id: UUID | None = None
    enabled: bool = False
    volume: float = Field(default=0.5, ge=0, le=1)
    loop: bool = True


class Composition(Strict):
    visuals_enabled: bool = True
    layers: list[Layer] = Field(default_factory=list, max_length=32)
    enabled: bool = True
    music: AudioTrack = Field(default_factory=AudioTrack)
    ambient: AudioTrack = Field(default_factory=AudioTrack)
    effects_enabled: bool = True
    lighting: Literal["neutral", "warm", "cold", "night"] = "neutral"
    effect: Literal["none", "rain", "fog", "vignette"] = "none"
    transition_ms: int = Field(default=300, ge=0, le=3000)
    low_effects: bool = False

    @model_validator(mode="after")
    def unique(self):
        if len({layer.id for layer in self.layers}) != len(self.layers):
            raise ValueError("Повторные слои")
        return self


class MediaState(Strict):
    format: Literal[1] = 1
    compositions: dict[UUID, Composition] = Field(default_factory=dict, max_length=1000)
    variants: list[Variant] = Field(default_factory=list, max_length=1000)
    bindings: list[Binding] = Field(default_factory=list, max_length=5000)
    history: list[dict] = Field(default_factory=list)

    @model_validator(mode="after")
    def unique(self):
        for values in (self.variants, self.bindings):
            if len({v.id for v in values}) != len(values):
                raise ValueError("Повторные привязки/варианты")
        return self
