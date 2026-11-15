from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import Field, model_validator

from .world_models import Strict


class OwnerReference(Strict):
    world_id: UUID | None = None
    story_id: UUID | None = None
    branch_id: UUID | None = None
    scene_id: UUID | None = None
    event_id: UUID | None = None
    message_id: UUID | None = None

    @model_validator(mode="after")
    def scope(self):
        if any([self.scene_id, self.event_id, self.message_id, self.branch_id]) and not all(
            [self.story_id, self.branch_id]
        ):
            raise ValueError("Привязка к ветви требует историю и ветвь")
        return self


class MaterialMetadata(Strict):
    name: str = Field(min_length=1, max_length=300)
    description: str = Field(default="", max_length=16000)
    tags: list[str] = Field(default_factory=list, max_length=100)
    references: list[OwnerReference] = Field(default_factory=list, max_length=100)
    archived: bool = False

    @model_validator(mode="after")
    def tags_valid(self):
        if any(not t.strip() or len(t) > 100 for t in self.tags):
            raise ValueError("Недопустимый тег")
        return self


class Material(MaterialMetadata):
    filename: str
    relative_path: str
    checksum: str
    mime: str
    text: str = Field(default="", max_length=512 * 1024)
    asset_id: UUID | None = None
    probe: Literal["utf8", "pdf-structural", "docx-bounded", "image"]
