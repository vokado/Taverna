from __future__ import annotations

import copy
from typing import Literal
from uuid import NAMESPACE_URL, UUID, uuid5

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .core import AppError
from .generation_api import GenerationParameters
from .storage import JsonStore, Mutation, document


def stable(name: str):
    return str(uuid5(NAMESPACE_URL, "taverna/profile/" + name))


class Condition(BaseModel):
    model_config = ConfigDict(extra="forbid")
    field: Literal["group", "has_world", "has_memory", "performance", "role"]
    op: Literal["equals", "not_equals"] = "equals"
    value: str | bool

    @model_validator(mode="after")
    def valid_value(self):
        if self.field in ("group", "has_world", "has_memory") and type(self.value) is not bool:
            raise ValueError("Для условия требуется логическое значение.")
        if self.field == "performance" and self.value not in ("economy", "balanced", "quality"):
            raise ValueError("Неизвестный режим производительности.")
        if self.field == "role" and self.value not in (
            "generation",
            "memory",
            "director",
            "editor",
            "character",
            "world",
            "quality",
        ):
            raise ValueError("Неизвестная роль.")
        return self


class PromptBlock(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    name: str = Field(min_length=1, max_length=100)
    source: Literal[
        "system",
        "character",
        "scenario",
        "scene",
        "lore",
        "memory",
        "relationships",
        "history",
        "post_history",
        "custom",
    ]
    enabled: bool = True
    role: Literal["system", "user", "assistant"] = "system"
    priority: int = Field(default=50, ge=0, le=100)
    pinned: bool = False
    max_tokens: int | None = Field(default=None, ge=16, le=131072)
    template: str = Field(default="", max_length=65536)
    condition: Condition | None = None

    @model_validator(mode="after")
    def valid_id(self):
        UUID(self.id)
        return self


class QualitySettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: Literal["warn", "retry", "reject"] = "warn"
    max_attempts: int = Field(default=2, ge=1, le=3)
    repeat_threshold: float = Field(default=0.92, ge=0.5, le=1)
    format: Literal["text", "json", "dialogue"] = "text"
    retry_on: list[Literal["empty", "repetition", "role", "format"]] = Field(
        default_factory=lambda: ["empty", "repetition", "role", "format"], max_length=4
    )


class ProfileSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    model_id: str | None = Field(default=None, max_length=256)
    performance: Literal["economy", "balanced", "quality"] = "balanced"
    context_limit: int = Field(default=8192, ge=256, le=1048576)
    automatic_context: bool = True
    estimator: Literal["balanced", "conservative"] = "balanced"
    context_safety_margin: int = Field(default=128, ge=0, le=32768)
    parameters: GenerationParameters = Field(default_factory=GenerationParameters)
    blocks: list[PromptBlock] = Field(min_length=1, max_length=64)
    budgets: dict[str, int] = Field(
        default_factory=lambda: {"memory": 1200, "lore": 1200, "scene": 1200, "relationships": 600}
    )
    quality: QualitySettings = Field(default_factory=QualitySettings)
    save_context: bool = True
    show_reasoning: bool = False
    extras: dict = Field(default_factory=dict)

    @model_validator(mode="after")
    def limits(self):
        if len({block.id for block in self.blocks}) != len(self.blocks):
            raise ValueError("Повторный ID блока.")
        if any(
            name
            not in {
                "system",
                "character",
                "scenario",
                "scene",
                "lore",
                "memory",
                "relationships",
                "history",
                "post_history",
                "custom",
            }
            or type(value) is not int
            or not 0 <= value <= 1048576
            for name, value in self.budgets.items()
        ):
            raise ValueError("Некорректный бюджет блока.")
        return self


def default_blocks():
    result = []
    for source, name, priority, pinned in [
        ("system", "Системные инструкции", 100, True),
        ("character", "Карточка персонажа", 95, True),
        ("scenario", "Сценарий", 70, False),
        ("scene", "Текущая сцена", 75, False),
        ("lore", "Лорбук", 60, False),
        ("memory", "Известные персонажу факты", 70, False),
        ("relationships", "Отношения", 65, False),
        ("history", "Последние сообщения", 80, False),
        ("post_history", "Инструкция после истории", 90, False),
    ]:
        result.append(
            {
                "id": stable("block/" + source),
                "name": name,
                "source": source,
                "priority": priority,
                "pinned": pinned,
                "template": "Ты участвуешь в истории от лица {{char}}. Следуй характеру и известным ему событиям."
                if source == "system"
                else "",
            }
        )
    return result


DEFAULTS = {
    "model_id": None,
    "performance": "balanced",
    "context_limit": 8192,
    "automatic_context": True,
    "estimator": "balanced",
    "context_safety_margin": 128,
    "parameters": {"temperature": 0.8, "top_p": 0.95, "max_tokens": 1024},
    "blocks": default_blocks(),
    "budgets": {"memory": 1200, "lore": 1200, "scene": 1200, "relationships": 600},
    "quality": {"mode": "warn", "max_attempts": 2, "repeat_threshold": 0.92, "format": "text"},
    "save_context": True,
    "show_reasoning": False,
    "extras": {},
}


def merge(base, patch):
    value = copy.deepcopy(base)
    for key, item in patch.items():
        value[key] = (
            merge(value[key], item)
            if isinstance(item, dict) and isinstance(value.get(key), dict)
            else copy.deepcopy(item)
        )
    return value


def normalize_patch(raw, normalized):
    return {
        key: normalize_patch(value, normalized.get(key, {}))
        if isinstance(value, dict) and isinstance(normalized.get(key), dict)
        else copy.deepcopy(normalized.get(key, value))
        for key, value in raw.items()
    }


def validate_settings(raw):
    try:
        parsed = ProfileSettings.model_validate(raw)
        result = parsed.model_dump(mode="json")
        result["parameters"] = parsed.parameters.model_dump(exclude_unset=True, exclude_none=True)
        return result
    except ValueError as exc:
        raise AppError(
            "invalid_profile",
            "Профиль содержит недопустимые поля, пределы или условия. Проверьте схему настроек.",
            422,
        ) from exc


class ProfileWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=0)
    name: str = Field(min_length=1, max_length=200)
    patch: dict


class PatchValidation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    patch: dict


class BindingWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=0)
    scope: Literal["global", "character", "story", "service"]
    scope_id: str
    profile_id: str | None


class ResolveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    character_id: str | None = None
    story_id: str | None = None
    role: Literal["generation", "memory", "director", "editor", "character", "world", "quality"] = (
        "generation"
    )
    profile_id: str | None = None
    parameters: GenerationParameters | None = None


class ProfileService:
    def __init__(self, store: JsonStore):
        self.store = store
        store.register(
            "generation_profile",
            1,
            {
                "type": "object",
                "required": ["name", "patch", "builtin"],
                "properties": {
                    "name": {"type": "string", "minLength": 1},
                    "patch": {"type": "object"},
                    "builtin": {"type": "boolean"},
                },
            },
        )
        changes = []
        for name, title, patch in [
            (
                "economy",
                "Экономичный",
                {
                    "performance": "economy",
                    "parameters": {"max_tokens": 512},
                    "budgets": {"memory": 600, "lore": 600},
                    "quality": {"max_attempts": 1},
                },
            ),
            ("balanced", "Сбалансированный", {}),
            (
                "quality",
                "Максимальное качество",
                {
                    "performance": "quality",
                    "parameters": {"max_tokens": 2048},
                    "budgets": {"memory": 2400, "lore": 2400},
                },
            ),
        ]:
            key = self.key(stable(name))
            if self.store.get(key) is None:
                changes.append(
                    Mutation(
                        key,
                        document(
                            "generation_profile",
                            {"name": title, "patch": patch, "builtin": True},
                            entity_id=stable(name),
                        ),
                        0,
                    )
                )
        if changes:
            self.store.transaction(changes)

    def key(self, profile_id):
        try:
            UUID(profile_id)
        except (ValueError, TypeError) as exc:
            raise AppError("invalid_profile_id", "Недопустимый идентификатор профиля.", 422) from exc
        return f"presets/profiles/{profile_id}.json"

    def get(self, profile_id):
        value = self.store.get(self.key(profile_id))
        if value is None:
            raise AppError("profile_missing", "Назначенный профиль не найден. Выберите другой профиль.", 404)
        return value

    def write(self, body: ProfileWrite, profile_id=None):
        settings = validate_settings(merge(DEFAULTS, body.patch))
        value = self.get(profile_id) if profile_id else document("generation_profile", {"builtin": False})
        value["payload"].update(name=body.name, patch=normalize_patch(body.patch, settings))
        self.store.transaction([Mutation(self.key(value["id"]), value, body.expected_revision)])
        return self.get(value["id"])

    def binding_key(self, scope, scope_id):
        if scope == "global":
            if scope_id != "default":
                raise AppError("invalid_scope", "Общий профиль использует область default.", 422)
        elif scope == "service":
            if scope_id not in (
                "generation",
                "memory",
                "director",
                "editor",
                "character",
                "world",
                "quality",
            ):
                raise AppError("invalid_scope", "Неизвестная служебная роль.", 422)
        else:
            try:
                UUID(scope_id)
            except (ValueError, TypeError) as exc:
                raise AppError("invalid_scope", "Недопустимая область профиля.", 422) from exc
        return f"config/profile-bindings/{scope}-{scope_id}.json"

    def bind(self, body: BindingWrite):
        key = self.binding_key(body.scope, body.scope_id)
        if body.scope in ("character", "story"):
            entity_key = (
                f"characters/{body.scope_id}.json"
                if body.scope == "character"
                else f"chats/stories/{body.scope_id}.json"
            )
            if self.store.get(entity_key) is None:
                raise AppError("scope_missing", "Персонаж или история не найдены.", 404)
        old = self.store.get(key)
        if body.profile_id is None:
            value = None
        else:
            self.get(body.profile_id)
            value = old or document("profile_binding", {})
            value["payload"] = {"scope": body.scope, "scope_id": body.scope_id, "profile_id": body.profile_id}
            value["refs"] = [self.key(body.profile_id)]
            if body.scope in ("character", "story"):
                value["refs"].append(entity_key)
        self.store.transaction([Mutation(key, value, body.expected_revision)])
        return {"binding": self.store.get(key)}

    def resolve(self, **kwargs):
        with self.store.view() as view:
            return self._resolve(view, **kwargs)

    def _resolve(
        self, view, *, character_id=None, story_id=None, role="generation", profile_id=None, parameters=None
    ):
        def read_profile(profile_id):
            value = view.get(self.key(profile_id))
            if value is None:
                raise AppError("profile_missing", "Назначенный профиль не найден.", 404)
            return value

        settings = copy.deepcopy(DEFAULTS)
        chain = []
        base = read_profile(stable("balanced"))
        settings = merge(settings, base["payload"]["patch"])
        chain.append(
            {
                "scope": "builtin",
                "profile_id": base["id"],
                "revision": base["revision"],
                "name": base["payload"]["name"],
            }
        )
        scopes = [("global", "default"), ("service", role)]
        if character_id:
            scopes.append(("character", character_id))
        if story_id:
            scopes.append(("story", story_id))
        for scope, scope_id in scopes:
            binding = view.get(self.binding_key(scope, scope_id))
            if binding:
                profile = read_profile(binding["payload"]["profile_id"])
                settings = merge(settings, profile["payload"]["patch"])
                chain.append(
                    {
                        "scope": scope,
                        "scope_id": scope_id,
                        "profile_id": profile["id"],
                        "revision": profile["revision"],
                        "name": profile["payload"]["name"],
                    }
                )
        if profile_id:
            profile = read_profile(profile_id)
            settings = merge(settings, profile["payload"]["patch"])
            chain.append(
                {
                    "scope": "task",
                    "profile_id": profile_id,
                    "revision": profile["revision"],
                    "name": profile["payload"]["name"],
                }
            )
        if parameters:
            settings = merge(settings, {"parameters": parameters})
        result = validate_settings(settings)
        return {"settings": result, "chain": chain, "profile_id": chain[-1]["profile_id"]}

    def router(self):
        router = APIRouter(prefix="/api/v1")

        @router.get("/profiles")
        def profiles():
            rows, errors = self.store.scan("presets/profiles/", tolerate_corrupt=True)
            return {
                "profiles": [row["value"] for row in rows],
                "defaults": copy.deepcopy(DEFAULTS),
                "errors": errors,
            }

        @router.post("/profiles")
        def create(body: ProfileWrite):
            return self.write(body)

        @router.post("/profiles/validate")
        def validate_patch(body: PatchValidation):
            settings = validate_settings(merge(DEFAULTS, body.patch))
            return {"patch": normalize_patch(body.patch, settings), "resolved": settings}

        @router.get("/profiles/bindings")
        def bindings():
            rows, errors = self.store.scan("config/profile-bindings/", tolerate_corrupt=True)
            return {"bindings": [row["value"] for row in rows], "errors": errors}

        @router.put("/profiles/bindings")
        def binding(body: BindingWrite):
            return self.bind(body)

        @router.post("/profiles/resolve")
        def resolve(body: ResolveRequest):
            return self.resolve(**body.model_dump(exclude_none=True, exclude_unset=True))

        @router.get("/profiles/{profile_id}")
        def read(profile_id: str):
            return self.get(profile_id)

        @router.patch("/profiles/{profile_id}")
        def update(profile_id: str, body: ProfileWrite):
            return self.write(body, profile_id)

        return router
