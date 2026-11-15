from __future__ import annotations

import copy
import hashlib
import re
from datetime import datetime
from typing import ClassVar, Literal
from uuid import NAMESPACE_URL, UUID, uuid5

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Level = Literal["operational", "episodic", "personal", "relationship", "world", "plot"]
Nature = Literal["fact", "observation", "report", "rumor", "belief", "secret", "summary"]
Status = Literal["active", "archived", "superseded", "deleted"]


def text_hash(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def player_id(story_id):
    return str(uuid5(NAMESPACE_URL, f"taverna/player/{story_id}"))


class Relation(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    parties: list[UUID] = Field(min_length=2, max_length=2)
    metrics: dict[str, float] = Field(default_factory=dict, max_length=32)
    episode_ids: list[UUID] = Field(default_factory=list, max_length=1000)

    @model_validator(mode="after")
    def valid(self):
        if len(set(self.parties)) != 2 or any(
            not key or len(key) > 100 or not -100 <= value <= 100 for key, value in self.metrics.items()
        ):
            raise ValueError("Показатели отношений должны быть в пределах -100–100.")
        return self


class RecordInput(BaseModel):
    allow_empty_text: ClassVar[bool] = False
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    level: Level = "personal"
    nature: Nature = "fact"
    text: str = Field(min_length=1, max_length=32768)
    subject_ids: list[UUID] = Field(default_factory=list, max_length=100)
    knower_ids: list[UUID] = Field(min_length=1, max_length=100)
    confidence: float = Field(default=1, ge=0, le=1)
    salience: float = Field(default=0.5, ge=0, le=1)
    status: Status = "active"
    pinned: bool = False
    source_ids: list[UUID] = Field(default_factory=list, max_length=1000)
    source_fact_versions: dict[UUID, int] = Field(default_factory=dict, max_length=1000)
    claim_key: str = Field(default="", max_length=200)
    location_id: str | None = Field(default=None, max_length=200)
    story_time: str = Field(default="", max_length=200)
    cause_ids: list[UUID] = Field(default_factory=list, max_length=1000)
    relation: Relation | None = None
    extras: dict = Field(default_factory=dict)

    @field_validator("subject_ids", "knower_ids", "source_ids", "cause_ids")
    @classmethod
    def unique(cls, value):
        if len(set(value)) != len(value):
            raise ValueError("Повторные идентификаторы.")
        return value

    @model_validator(mode="after")
    def valid(self):
        if not self.allow_empty_text and not self.text.strip():
            raise ValueError("Текст памяти не может быть пустым.")
        if any(version < 1 for version in self.source_fact_versions.values()):
            raise ValueError("Некорректная версия источника.")
        if self.level == "relationship" and self.relation is None:
            raise ValueError("Для отношений нужны участники и показатели.")
        if self.relation is not None and self.level != "relationship":
            raise ValueError("Показатели отношений доступны только соответствующему уровню.")
        return self


class DraftRecordInput(RecordInput):
    allow_empty_text: ClassVar[bool] = True
    text: str = Field(default="", max_length=32768)


class Provenance(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: Literal["manual", "model", "import"]
    note: str = Field(min_length=1, max_length=2000)
    source_hashes: dict[UUID, str] = Field(default_factory=dict)

    @field_validator("source_hashes")
    @classmethod
    def hashes(cls, value):
        if any(not re.fullmatch(r"[a-f0-9]{64}", item) for item in value.values()):
            raise ValueError("Недопустимый хеш источника.")
        return value


class MemoryRecord(RecordInput):
    id: UUID
    story_id: UUID
    branch_id: UUID
    version: int = Field(ge=1)
    created_at: datetime
    updated_at: datetime
    provenance: Provenance
    history: list[dict] = Field(default_factory=list)

    @field_validator("created_at", "updated_at")
    @classmethod
    def aware_time(cls, value):
        if value.tzinfo is None:
            raise ValueError("Требуется часовой пояс.")
        return value


class KnowledgePolicy:
    def __init__(self, story_id, branch_id, records, messages):
        self.story_id, self.branch_id = story_id, branch_id
        self.records, self.messages = dict(records), dict(messages)
        self._results = {}

    def _key(self, record, actor):
        return (actor, id(record), record.get("version") if isinstance(record, dict) else None)

    def _intrinsic(self, record, actor):
        if not isinstance(record, dict):
            return "invalid_record", []
        try:
            MemoryRecord.model_validate(record)
        except ValueError:
            return "legacy_or_invalid_record", []
        if self.records.get(record["id"]) is not record:
            return "record_identity_mismatch", []
        if record["story_id"] != self.story_id or record["branch_id"] != self.branch_id:
            return "scope", []
        if record["status"] != "active":
            return "inactive", []
        if actor not in record["knower_ids"]:
            return "unknown_to_actor", []
        for episode_id in (record.get("relation") or {}).get("episode_ids", []):
            source = self.records.get(episode_id)
            if (
                not isinstance(source, dict)
                or source.get("level") != "episodic"
                or episode_id not in record["source_fact_versions"]
            ):
                return "untracked_relationship_episode", []
        for sid in record["source_ids"]:
            source = self.messages.get(sid)
            if not isinstance(source, dict):
                return "source_missing", []
            if record["provenance"]["source_hashes"].get(sid) != text_hash(source["text"]):
                return "source_changed", []
            if (
                record["provenance"]["mode"] == "model"
                and actor not in source.get("present_ids", [])
                and source.get("author_id") != actor
            ):
                return "source_not_observed", []
        dependencies = []
        for sid, version in record["source_fact_versions"].items():
            source = self.records.get(sid)
            if not isinstance(source, dict) or source.get("version") != version:
                return "source_fact_changed", []
            dependencies.append(source)
        return None, dependencies

    def reason(self, record, actor):
        # Iterative DFS handles deep dependency graphs without Python recursion or repeated DAG walks.
        root = self._key(record, actor)
        visiting = set()
        stack = [(record, False)]
        while stack:
            current, expanded = stack.pop()
            key = self._key(current, actor)
            if key in self._results:
                continue
            error, dependencies = self._intrinsic(current, actor)
            if error:
                self._results[key] = error
                continue
            if expanded:
                visiting.discard(key)
                episode_ids = (current.get("relation") or {}).get("episode_ids", [])
                failed = next(
                    (
                        source
                        for source in dependencies
                        if self._results.get(self._key(source, actor)) is not None
                    ),
                    None,
                )
                self._results[key] = (
                    ("relationship_episode_hidden" if failed["id"] in episode_ids else "source_fact_hidden")
                    if failed
                    else None
                )
                continue
            if key in visiting:
                self._results[key] = "source_cycle"
                continue
            visiting.add(key)
            stack.append((current, True))
            for source in reversed(dependencies):
                source_key = self._key(source, actor)
                if source_key in visiting:
                    self._results[source_key] = "source_cycle"
                elif source_key not in self._results:
                    stack.append((source, False))
        return self._results.get(root, "invalid_record")

    def visible(self, actor):
        return [record for record in self.records.values() if self.reason(record, actor) is None]

    def project(self, record, actor):
        """NPC views omit owner-only histories, notes and extension payloads."""
        if self.reason(record, actor):
            return None
        fields = set(RecordInput.model_fields) - {"extras", "knower_ids", "source_ids", "cause_ids"}
        fields.update(("id", "story_id", "branch_id", "version", "created_at", "updated_at"))
        value = {key: copy.deepcopy(record[key]) for key in sorted(fields)}
        value.update(
            knower_ids=[actor],
            source_ids=[
                sid
                for sid in record["source_ids"]
                if actor in self.messages[sid].get("present_ids", [])
                or self.messages[sid].get("author_id") == actor
            ],
            cause_ids=[
                sid
                for sid in record["cause_ids"]
                if sid in self.records and self.reason(self.records[sid], actor) is None
            ],
            projected=True,
            viewer_id=actor,
        )
        return value

    def project_message(self, message, actor):
        if actor not in message.get("present_ids", []) and message.get("author_id") != actor:
            return None
        return {
            **{
                key: copy.deepcopy(message[key])
                for key in (
                    "id",
                    "story_id",
                    "branch_id",
                    "version",
                    "created_at",
                    "role",
                    "author_id",
                    "text",
                )
            },
            "projected": True,
            "viewer_id": actor,
        }


def words(text):
    return set(re.findall(r"\w+", text.casefold(), flags=re.UNICODE))


def relevance(text, query):
    terms = words(query)
    if not terms:
        return 0.0
    lower = text.casefold()
    return sum(term in lower for term in terms) / len(terms)
