"""Typed, data-only plugin plans. No storage paths or executable expressions."""

from __future__ import annotations

from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, JsonValue, model_validator

from .manuscript_models import Manuscript
from .tool_models import ToolEntry
from .world_models import Strict


class PluginIdentity(Strict):
    catalog_revision: int = Field(ge=1)
    plugin_revision: int = Field(ge=1)
    permission_epoch: int = Field(ge=0)
    package_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class PluginRead(Strict):
    kind: Literal["own", "note", "book"]
    target: str = Field(min_length=1, max_length=64)
    expected_revision: int = Field(ge=0)

    @model_validator(mode="after")
    def target_valid(self):
        if self.kind == "own":
            import re

            if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", self.target):
                raise ValueError("Ключ собственной области: латиница, цифры, _ и -")
        else:
            self.target = str(UUID(self.target))
        return self


class PluginRun(PluginIdentity):
    parameters: dict[str, JsonValue] = Field(default_factory=dict, max_length=50)
    reads: list[PluginRead] = Field(default_factory=list, max_length=32)
    confirmed_full_access_run: bool = False

    @model_validator(mode="after")
    def no_duplicates(self):
        if len({(r.kind, r.target) for r in self.reads}) != len(self.reads):
            raise ValueError("Повторный источник данных")
        return self


class OwnSet(Strict):
    type: Literal["own.set"]
    target: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")
    expected_revision: int = Field(ge=0)
    value: JsonValue


class OwnDelete(Strict):
    type: Literal["own.delete"]
    target: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")
    expected_revision: int = Field(ge=1)


class NoteSave(Strict):
    type: Literal["note.save"]
    target: UUID
    expected_revision: int = Field(ge=0)
    payload: ToolEntry

    @model_validator(mode="after")
    def note_only(self):
        if self.payload.kind not in ("note", "bookmark", "dictionary"):
            raise ValueError("notes.write не выдаёт прав на генераторы и шаблоны")
        return self


class BookSave(Strict):
    type: Literal["book.save"]
    target: UUID
    expected_revision: int = Field(ge=0)
    payload: Manuscript


PluginAction = Annotated[OwnSet | OwnDelete | NoteSave | BookSave, Field(discriminator="type")]


class PluginPlan(Strict):
    format: Literal[1] = 1
    actions: list[PluginAction] = Field(default_factory=list, max_length=32)
    message: str = Field(default="", max_length=8000)


class PluginDecision(Strict):
    expected_revision: int = Field(ge=1)
    confirmed: bool = False
