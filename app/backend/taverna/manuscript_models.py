from __future__ import annotations

from typing import Literal
from urllib.parse import urlsplit
from uuid import UUID, uuid4

from pydantic import Field, model_validator

from .world_models import Strict


class BookLink(Strict):
    label: str = Field(min_length=1, max_length=500)
    target: str = Field(min_length=1, max_length=2000)

    @model_validator(mode="after")
    def safe(self):
        if self.target.startswith("#chapter-"):
            UUID(self.target[9:])
            return self
        value = urlsplit(self.target)
        _ = value.port  # Reject malformed/non-numeric/out-of-range URL ports.
        if (
            value.scheme not in ("https", "http")
            or not value.hostname
            or value.username
            or value.password
            or any(ord(c) <= 32 or ord(c) == 127 for c in self.target)
            or "\\" in self.target
        ):
            raise ValueError("Разрешены HTTP(S) ссылки без credentials или #chapter-UUID")
        return self


class BookMedia(Strict):
    asset_id: UUID
    caption: str = Field(default="", max_length=2000)


class Chapter(Strict):
    id: UUID = Field(default_factory=uuid4)
    title: str = Field(min_length=1, max_length=300)
    text: str = Field(default="", max_length=1024 * 1024)
    links: list[BookLink] = Field(default_factory=list, max_length=100)
    media: list[BookMedia] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def nonblank(self):
        if not self.title.strip():
            raise ValueError("Название главы не может быть пустым")
        return self


class Manuscript(Strict):
    format: Literal[1] = 1
    title: str = Field(min_length=1, max_length=300)
    author: str = Field(default="", max_length=300)
    language: str = Field(default="ru", pattern=r"^[a-zA-Z]{2,8}(?:-[a-zA-Z0-9]{1,8})*$")
    chapters: list[Chapter] = Field(default_factory=list, max_length=500)

    @model_validator(mode="after")
    def references(self):
        if not self.title.strip():
            raise ValueError("Название книги не может быть пустым")

        def strings(value):
            if isinstance(value, str):
                yield value
            elif isinstance(value, list):
                for item in value:
                    yield from strings(item)
            elif isinstance(value, dict):
                for item in value.values():
                    yield from strings(item)

        for text in strings(self.model_dump(mode="json")):
            if any(
                (ord(c) < 32 and c not in "\n\r\t")
                or 0xD800 <= ord(c) <= 0xDFFF
                or ord(c) in (0xFFFE, 0xFFFF)
                for c in text
            ):
                raise ValueError("Текст содержит символы, недопустимые в XML/UTF-8")
        ids = {str(c.id) for c in self.chapters}
        if len(ids) != len(self.chapters):
            raise ValueError("Повторные ID глав")
        if sum(len(c.text.encode("utf-8")) for c in self.chapters) > 2 * 1024 * 1024:
            raise ValueError("Рукопись больше2МиБ")
        for chapter in self.chapters:
            for link in chapter.links:
                if link.target.startswith("#chapter-") and link.target[9:] not in ids:
                    raise ValueError("Ссылка на отсутствующую главу")
        return self


class BookWrite(Strict):
    expected_revision: int = Field(ge=0)
    payload: Manuscript
    reason: str = Field(min_length=1, max_length=2000)


class BookAI(Strict):
    operation: Literal["rewrite", "style", "shorten", "expand", "coherence", "chat_to_prose"]
    manuscript_id: UUID | None = None
    expected_revision: int = Field(default=0, ge=0)
    chapter_id: UUID | None = None
    start: int = Field(default=0, ge=0)
    end: int = Field(default=0, ge=0)
    story_id: UUID | None = None
    branch_id: UUID | None = None
    branch_revision: int | None = Field(default=None, ge=1)
    message_ids: list[UUID] = Field(default_factory=list, max_length=200)
    instruction: str = Field(default="", max_length=8000)
    title: str = Field(default="Новая рукопись", min_length=1, max_length=300)
    model_id: str | None = Field(default=None, min_length=1, max_length=256)
    profile_id: UUID | None = None

    @model_validator(mode="after")
    def selection(self):
        if self.operation == "chat_to_prose":
            if not all([self.story_id, self.branch_id, self.branch_revision, self.message_ids]):
                raise ValueError("Выберите источник и сообщения")
            if self.manuscript_id or self.chapter_id or self.expected_revision:
                raise ValueError("Преобразование создаёт отдельную рукопись")
        elif (
            not self.manuscript_id
            or not self.chapter_id
            or not self.expected_revision
            or self.end <= self.start
        ):
            raise ValueError("Выберите рукопись, главу и непустой фрагмент")
        return self
