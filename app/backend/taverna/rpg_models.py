from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import Field, JsonValue, StrictInt, model_validator

from .world_models import Strict

Module = Literal["inventory", "stats", "skills", "dice", "economy", "conflicts", "random_events"]
MODULES = ("inventory", "stats", "skills", "dice", "economy", "conflicts", "random_events")
LIMIT = 10**12


class WorldRpgPolicy(Strict):
    allowed_modules: list[Module] = Field(default_factory=lambda: list(MODULES), max_length=7)
    allowed_locations: list[UUID] = Field(default_factory=list, max_length=1000)
    blocked_actor_ids: list[UUID] = Field(default_factory=list, max_length=100)
    allow_offscene: bool = False
    required_flags: dict[str, JsonValue] = Field(default_factory=dict, max_length=100)


class Item(Strict):
    name: str = Field(min_length=1, max_length=200)
    weight_grams: int = Field(default=0, ge=0, le=10**9, strict=True)
    description: str = Field(default="", max_length=4000)


class Currency(Strict):
    name: str = Field(min_length=1, max_length=100)
    decimals: int = Field(default=0, ge=0, le=6)


class Reward(Strict):
    kind: Literal["item", "currency", "health", "attribute"]
    key: str = Field(default="", max_length=100)
    amount: int = Field(ge=-1000000, le=1000000, strict=True)


class Outcome(Strict):
    name: str = Field(min_length=1, max_length=200)
    text: str = Field(min_length=1, max_length=4000)
    weight: int = Field(default=1, ge=1, le=1000000, strict=True)
    rewards: list[Reward] = Field(default_factory=list, max_length=16)


class RandomTable(Strict):
    name: str = Field(min_length=1, max_length=200)
    outcomes: list[Outcome] = Field(min_length=1, max_length=100)


class RpgSettings(Strict):
    enabled: dict[Module, bool] = Field(default_factory=dict, max_length=7)
    world_policy: WorldRpgPolicy = Field(default_factory=WorldRpgPolicy)
    items: dict[str, Item] = Field(default_factory=dict, max_length=1000)
    currencies: dict[str, Currency] = Field(default_factory=dict, max_length=100)
    tables: dict[str, RandomTable] = Field(default_factory=dict, max_length=100)

    @model_validator(mode="after")
    def catalogs(self):
        for catalog in (self.items, self.currencies, self.tables):
            if any(not k.strip() or len(k) > 100 for k in catalog):
                raise ValueError("Ключ каталога должен содержать 1–100 символов")
        for table in self.tables.values():
            for outcome in table.outcomes:
                for reward in outcome.rewards:
                    if reward.kind == "item" and reward.key not in self.items:
                        raise ValueError("Неизвестный предмет награды")
                    if reward.kind == "currency" and reward.key not in self.currencies:
                        raise ValueError("Неизвестная валюта награды")
                    if reward.kind == "attribute" and not reward.key.strip():
                        raise ValueError("Не задана характеристика награды")
        return self


class ActorSheet(Strict):
    attributes: dict[str, StrictInt] = Field(default_factory=dict, max_length=100)
    skills: dict[str, StrictInt] = Field(default_factory=dict, max_length=100)
    inventory: dict[str, StrictInt] = Field(default_factory=dict, max_length=1000)
    balances: dict[str, StrictInt] = Field(default_factory=dict, max_length=100)
    health: int = Field(default=10, ge=0, le=1000000, strict=True)
    max_health: int = Field(default=10, ge=1, le=1000000, strict=True)
    capacity_grams: int | None = Field(default=None, ge=0, le=LIMIT, strict=True)

    @model_validator(mode="after")
    def bounds(self):
        if self.health > self.max_health:
            raise ValueError("Здоровье выше максимума")
        for values in (self.attributes, self.skills, self.inventory, self.balances):
            if any(not k.strip() or len(k) > 100 or isinstance(v, bool) for k, v in values.items()):
                raise ValueError("Некорректное имя/значение параметра")
        if any(not -1000 <= v <= 1000 for v in (*self.attributes.values(), *self.skills.values())):
            raise ValueError("Параметры должны быть в пределах -1000–1000")
        if any(not 0 <= v <= LIMIT for v in (*self.inventory.values(), *self.balances.values())):
            raise ValueError("Остатки должны быть неотрицательными ограниченными целыми числами")
        return self


class DiceRequest(Strict):
    count: int = Field(default=1, ge=1, le=100, strict=True)
    sides: int = Field(default=20, ge=2, le=1000000, strict=True)
    modifier: int = Field(default=0, ge=-1000000, le=1000000, strict=True)


class Conflict(Strict):
    id: UUID
    participants: list[UUID] = Field(min_length=2, max_length=20)
    order: list[UUID] = Field(min_length=2, max_length=20)
    turn: UUID
    round: int = Field(default=1, ge=1)
    status: Literal["active", "ended"] = "active"


class RpgState(Strict):
    format: Literal[1] = 1
    rng_algorithm: Literal["sha256-counter-v1"] = "sha256-counter-v1"
    seed: str = Field(default="", max_length=128)
    counter: int = Field(default=0, ge=0, le=2**63 - 1)
    settings: RpgSettings = Field(default_factory=RpgSettings)
    actors: dict[UUID, ActorSheet] = Field(default_factory=dict, max_length=100)
    conflicts: dict[UUID, Conflict] = Field(default_factory=dict)
    audit: list["RpgReceipt"] = Field(default_factory=list)


class RpgAction(Strict):
    branch_id: UUID
    expected_revision: int = Field(ge=1)
    event_id: UUID
    actor_id: UUID
    kind: Literal[
        "set_attribute",
        "set_skill",
        "set_health",
        "set_capacity",
        "adjust_item",
        "transfer_item",
        "adjust_currency",
        "transfer_currency",
        "trade",
        "roll",
        "check",
        "start_conflict",
        "attack",
        "end_conflict",
        "random_event",
    ]
    target_id: UUID | None = None
    key: str = Field(default="", max_length=100)
    amount: int = Field(default=0, ge=-LIMIT, le=LIMIT, strict=True)
    quantity: int = Field(default=1, ge=1, le=1000000, strict=True)
    currency: str = Field(default="", max_length=100)
    price: int = Field(default=1, ge=1, le=LIMIT, strict=True)
    attribute: str = Field(default="", max_length=100)
    skill: str = Field(default="", max_length=100)
    max_health: int | None = Field(default=None, ge=1, le=1000000, strict=True)
    difficulty: int = Field(default=10, ge=-1000000, le=1000000, strict=True)
    dice: DiceRequest = Field(default_factory=DiceRequest)
    participants: list[UUID] = Field(default_factory=list, max_length=20)
    conflict_id: UUID | None = None
    reason: str = Field(min_length=1, max_length=2000)
    confirmed: bool = False


class RandomDraw(Strict):
    counter: int = Field(ge=0, le=2**63 - 1)
    bound: int = Field(ge=1, le=LIMIT)
    value: int = Field(ge=0, lt=LIMIT)

    @model_validator(mode="after")
    def range(self):
        if self.value >= self.bound:
            raise ValueError("Результат RNG вне диапазона")
        return self


class RpgReceipt(Strict):
    id: UUID
    kind: Literal[
        "settings",
        "set_attribute",
        "set_skill",
        "set_health",
        "set_capacity",
        "adjust_item",
        "transfer_item",
        "adjust_currency",
        "transfer_currency",
        "trade",
        "roll",
        "check",
        "start_conflict",
        "attack",
        "end_conflict",
        "random_event",
    ]
    branch_id: UUID
    actor_id: UUID | None = None
    fingerprint: str | None = Field(default=None, pattern="^[a-f0-9]{64}$")
    input: RpgAction | None = None
    before: dict[str, JsonValue]
    after: dict[str, JsonValue]
    seed: str | None = Field(default=None, max_length=128)
    counter_before: int | None = Field(default=None, ge=0, le=2**63 - 1)
    counter_after: int | None = Field(default=None, ge=0, le=2**63 - 1)
    draws: list[RandomDraw] = Field(default_factory=list, max_length=2048)
    result: dict[str, JsonValue] | None = None
    reason: str = Field(min_length=1, max_length=2000)
    created_at: str = Field(min_length=1, max_length=100)
    previous_seed: str | None = Field(default=None, max_length=128)
    previous_counter: int | None = Field(default=None, ge=0, le=2**63 - 1)

    @model_validator(mode="after")
    def receipt_shape(self):
        if self.kind != "settings" and (
            self.input is None
            or self.actor_id is None
            or self.fingerprint is None
            or self.result is None
            or self.seed is None
            or self.counter_before is None
            or self.counter_after is None
        ):
            raise ValueError("Неполная квитанция игровой операции")
        if (
            self.counter_before is not None
            and self.counter_after is not None
            and self.counter_after < self.counter_before
        ):
            raise ValueError("Счётчик RNG уменьшился внутри операции")
        if self.kind == "random_event":
            outcome = (self.result or {}).get("outcome")
            if not isinstance(outcome, dict) or not isinstance(outcome.get("text"), str):
                raise ValueError("Событие не содержит проверяемого текста результата")
        return self


RpgState.model_rebuild()
