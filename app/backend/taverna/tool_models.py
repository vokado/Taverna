from __future__ import annotations

import re
from typing import Literal
from uuid import UUID

from pydantic import Field, model_validator

from .library_models import OwnerReference
from .manuscript_models import BookLink
from .world_models import Strict


class ToolTemplate(Strict):
    action: Literal["note", "chapter"] = "note"
    text: str = Field(default="", max_length=64000)
    variables: list[str] = Field(default_factory=list, max_length=32)
    grants: list[Literal["notes.write", "manuscripts.write"]] = Field(default_factory=list, max_length=2)

    @model_validator(mode="after")
    def variable_names(self):
        if len(set(self.variables)) != len(self.variables) or any(
            not re.fullmatch(r"[A-Za-zА-Яа-яЁё_][A-Za-zА-Яа-яЁё_0-9]{0,63}", name) for name in self.variables
        ):
            raise ValueError("Некорректные переменные")
        used = set(re.findall(r"\{\{\s*([^{}]+?)\s*\}\}", self.text))
        if not used.issubset(self.variables):
            raise ValueError("Объявите все переменные шаблона")
        return self


class NameGenerator(Strict):
    prefixes: list[str] = Field(
        default_factory=lambda: ["Аль", "Мир", "Вел", "Ор"], min_length=1, max_length=200
    )
    middles: list[str] = Field(default_factory=lambda: ["а", "и", "о", ""], min_length=1, max_length=200)
    suffixes: list[str] = Field(
        default_factory=lambda: ["на", "дар", "ис", "ен"], min_length=1, max_length=200
    )

    @model_validator(mode="after")
    def lengths(self):
        if any(
            len(x) > 100 or any(ord(c) < 32 for c in x) for x in self.prefixes + self.middles + self.suffixes
        ):
            raise ValueError("Некорректная часть имени")
        return self


class ToolEntry(Strict):
    format: Literal[1] = 1
    kind: Literal["note", "bookmark", "dictionary", "name_generator", "template"]
    title: str = Field(min_length=1, max_length=300)
    text: str = Field(default="", max_length=256 * 1024)
    references: list[OwnerReference] = Field(default_factory=list, max_length=100)
    links: list[BookLink] = Field(default_factory=list, max_length=100)
    archived: bool = False
    generator: NameGenerator | None = None
    template: ToolTemplate | None = None

    @model_validator(mode="after")
    def data(self):
        if (self.kind == "name_generator") != (self.generator is not None):
            raise ValueError("Настройки генератора только для генератора")
        if (self.kind == "template") != (self.template is not None):
            raise ValueError("Настройки шаблона только для шаблона")
        return self


class ToolWrite(Strict):
    expected_revision: int = Field(ge=0)
    payload: ToolEntry


class NameRun(Strict):
    expected_revision: int = Field(ge=1)
    seed: str = Field(min_length=1, max_length=200)
    count: int = Field(default=10, ge=1, le=100)


class TemplatePreview(Strict):
    expected_revision: int = Field(ge=1)
    inputs: dict[str, str] = Field(default_factory=dict, max_length=32)
    manuscript_id: UUID | None = None
    manuscript_revision: int | None = Field(default=None, ge=1)
    chapter_title: str = Field(default="Новая глава", min_length=1, max_length=300)


class ToolExecute(Strict):
    expected_revision: int = Field(ge=1)
    confirmed: bool = False
