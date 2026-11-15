from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from .llm import local_endpoint
from .media_models import Category
from .world_models import Strict


class ImageSettings(Strict):
    enabled: bool = False
    adapter: Literal["comfyui-modern-v1"] = "comfyui-modern-v1"
    endpoint: str = Field(default="http://127.0.0.1:8188", max_length=200)
    timeout_seconds: int = Field(default=300, ge=10, le=1800)
    cancel_wait_seconds: int = Field(default=10, ge=1, le=60)
    minimum_free_vram_mib: int = Field(default=0, ge=0, le=262144)
    max_pixels: int = Field(default=1024 * 1024, ge=128 * 128, le=4 * 1024 * 1024)

    @field_validator("endpoint")
    @classmethod
    def endpoint_local(cls, value):
        return local_endpoint(value)


class ImageRequest(Strict):
    settings_revision: int = Field(ge=1)
    name: str = Field(min_length=1, max_length=300)
    prompt: str = Field(min_length=1, max_length=8000)
    negative_prompt: str = Field(default="", max_length=4000)
    checkpoint: str = Field(min_length=1, max_length=500)
    width: int = Field(default=512, ge=128, le=2048, strict=True)
    height: int = Field(default=512, ge=128, le=2048, strict=True)
    steps: int = Field(default=20, ge=1, le=100, strict=True)
    cfg: float = Field(default=7, ge=0, le=30)
    seed: int = Field(default=0, ge=0, le=2**53 - 1, strict=True)
    sampler: str = Field(default="euler", min_length=1, max_length=100)
    scheduler: str = Field(default="normal", min_length=1, max_length=100)
    category: Category = "illustration"
    tags: list[str] = Field(default_factory=list, max_length=100)
    story_id: UUID | None = None
    branch_id: UUID | None = None
    expected_revision: int | None = Field(default=None, ge=1)
    confirmed: bool = False

    @model_validator(mode="after")
    def limits(self):
        if self.width % 8 or self.height % 8:
            raise ValueError("Размеры должны быть кратны8")
        if self.category in ("music", "ambient"):
            raise ValueError("Генерация изображений не создаёт аудио")
        if any(not tag.strip() or len(tag) > 100 for tag in self.tags):
            raise ValueError("Недопустимый тег")
        if any(v is not None for v in (self.story_id, self.branch_id, self.expected_revision)) and any(
            v is None for v in (self.story_id, self.branch_id, self.expected_revision)
        ):
            raise ValueError("Источник истории требует story/branch/revision вместе")
        return self


class ImageLease(Strict):
    format: Literal[1] = 1
    state: Literal["running", "uncertain", "terminal", "manually_released"]
    endpoint: str
    prompt_id: UUID
    job_id: UUID
    request: ImageRequest
    acknowledged: bool = False
    external_status: str | None = None
    error_code: str | None = None
    result_asset_id: UUID | None = None
    created_at: str
    updated_at: str
    lm_before: list[str] | None = None
    lm_after: list[str] | None = None
    devices_before: list[dict] = Field(default_factory=list)
    manual_reason: str | None = None
