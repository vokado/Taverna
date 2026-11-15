from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import Field, model_validator

from .knowledge import RecordInput
from .world_models import Effect, Strict

Mode = Literal["fixed", "flexible", "developing"]
Trait = Literal[
    "trustfulness", "courage", "patience", "assertiveness", "curiosity", "empathy", "optimism", "discipline"
]


class Emotion(Strict):
    label: Literal[
        "спокойствие",
        "радость",
        "печаль",
        "страх",
        "гнев",
        "интерес",
        "удивление",
        "отвращение",
        "смущение",
        "надежда",
    ] = "спокойствие"
    intensity: float = Field(default=0.2, ge=0, le=1)
    valence: float = Field(default=0, ge=-1, le=1)
    arousal: float = Field(default=0.2, ge=0, le=1)
    confidence: float | None = Field(default=None, ge=0, le=1)


class DerivedSources(Strict):
    source_ids: list[UUID] = Field(default_factory=list, max_length=1000)
    source_hashes: dict[UUID, str] = Field(default_factory=dict, max_length=1000)
    source_fact_versions: dict[UUID, int] = Field(default_factory=dict, max_length=1000)
    source_card_sha256: str | None = Field(default=None, pattern="^[a-f0-9]{64}$")


class Intention(DerivedSources):
    id: UUID
    text: str = Field(min_length=1, max_length=4000)
    proposal_id: UUID
    scene_id: UUID
    status: Literal["proposed_action", "turn_generated", "dismissed"] = "proposed_action"
    result_message_id: UUID | None = None


class Arc(DerivedSources):
    id: UUID
    description: str = Field(min_length=1, max_length=4000)
    resolution: str | None = Field(default=None, max_length=4000)
    status: Literal["open", "resolved"]
    knower_ids: list[UUID] = Field(min_length=1, max_length=100)
    proposal_id: UUID


class NPCState(Strict):
    format: Literal[1] = 1
    mode: Mode = "fixed"
    emotions_enabled: bool = True
    relationships_enabled: bool = True
    emotion: Emotion = Field(default_factory=Emotion)
    traits: dict[Trait, float] = Field(default_factory=dict, max_length=8)
    adaptations: dict[Trait, float] = Field(default_factory=dict, max_length=8)
    adaptation_scene_id: UUID | None = None
    intentions: list[Intention] = Field(default_factory=list, max_length=100)
    history: list[dict] = Field(default_factory=list)
    extras: dict = Field(default_factory=dict)

    @model_validator(mode="after")
    def bounds(self):
        if any(not -1 <= value <= 1 for value in (*self.traits.values(), *self.adaptations.values())):
            raise ValueError("Тенденции должны быть в пределах -1–1")
        return self


class DirectorPolicy(Strict):
    enabled: bool = False
    before_reply: bool = True
    after_every_messages: int = Field(default=4, ge=1, le=100)
    pre_timeout_seconds: float = Field(default=15, ge=1, le=60)
    max_actions: int = Field(default=8, ge=1, le=16)
    max_output_tokens: int = Field(default=768, ge=128, le=2048)
    model_id: str | None = Field(default=None, min_length=1, max_length=256)
    profile_id: UUID | None = None
    auto_apply: bool = False
    auto_initiative: bool = False
    offscene: bool = True
    actor_entities: dict[UUID, UUID] = Field(default_factory=dict, max_length=100)
    actor_attribute_keys: dict[UUID, list[str]] = Field(default_factory=dict, max_length=100)

    @model_validator(mode="after")
    def allowed_keys(self):
        if any(
            len(keys) > 100 or any(not key or len(key) > 100 for key in keys) or len(set(keys)) != len(keys)
            for keys in self.actor_attribute_keys.values()
        ):
            raise ValueError("Недопустимый список атрибутов NPC")
        return self


class DirectorState(Strict):
    format: Literal[1] = 1
    policy: DirectorPolicy = Field(default_factory=DirectorPolicy)
    arcs: dict[UUID, Arc] = Field(default_factory=dict)
    audit: list[dict] = Field(default_factory=list)

    @model_validator(mode="after")
    def identities(self):
        if any(key != value.id for key, value in self.arcs.items()):
            raise ValueError("Неверный ID сюжетной линии")
        return self


class DirectorAction(Strict):
    kind: Literal[
        "emotion", "adaptation", "evolution", "relationship", "knowledge", "world_action", "initiative", "arc"
    ]
    reason: str = Field(min_length=1, max_length=2000)
    source_ids: list[UUID] = Field(default_factory=list, max_length=1000)
    source_fact_versions: dict[UUID, int] = Field(default_factory=dict, max_length=1000)
    source_card: bool = False
    emotion: Emotion | None = None
    trait: Trait | None = None
    delta: float | None = Field(default=None, ge=-0.1, le=0.1)
    record: RecordInput | None = None
    record_id: UUID | None = None
    expected_record_version: int = Field(default=0, ge=0)
    effect: Effect | None = None
    intent: str | None = Field(default=None, min_length=1, max_length=4000)
    arc_id: UUID | None = None
    resolve_arc: bool = False

    @model_validator(mode="after")
    def shape(self):
        if self.kind == "emotion" and self.emotion is None:
            raise ValueError("Отсутствует эмоция")
        if self.kind in ("adaptation", "evolution") and (self.trait is None or self.delta is None):
            raise ValueError("Отсутствует ограниченное изменение тенденции")
        if self.kind in ("relationship", "knowledge") and self.record is None:
            raise ValueError("Отсутствует запись памяти")
        if self.record is not None and "confidence" not in self.record.model_fields_set:
            raise ValueError("Укажите явную самооценку уверенности записи")
        if self.kind == "world_action" and self.effect is None:
            raise ValueError("Отсутствует последствие мира")
        if self.kind in ("initiative", "arc") and not self.intent:
            raise ValueError("Отсутствует предложение инициативы/линии")
        if not self.source_ids and not self.source_fact_versions and not self.source_card:
            raise ValueError("Укажите наблюдаемые источники или собственную карточку")
        if any(version < 1 for version in self.source_fact_versions.values()) or len(
            set(self.source_ids)
        ) != len(self.source_ids):
            raise ValueError("Недопустимые источники")
        return self


class DirectorResponse(Strict):
    actions: list[DirectorAction] = Field(max_length=16)
    notes: str = Field(default="", max_length=4000)


class DirectorProposal(Strict):
    story_id: UUID
    branch_id: UUID
    character_id: UUID
    base_revision: int = Field(ge=1)
    status: Literal["pending", "invalid", "accepted", "rejected"]
    actions: list[DirectorAction] = Field(max_length=16)
    notes: str = Field(default="", max_length=4000)
    error: dict[str, str] | None = None
    source_hashes: dict[UUID, str] = Field(default_factory=dict)
    context_trace_id: UUID | None = None
    policy_sha256: str = Field(pattern="^[a-f0-9]{64}$")
    base_npc_sha256: str = Field(pattern="^[a-f0-9]{64}$")
    base_entity_sha256: str | None = Field(default=None, pattern="^[a-f0-9]{64}$")
    rebased_to_revision: int | None = Field(default=None, ge=1)
    automatic: bool = False
    receipts: list[dict] = Field(default_factory=list)
