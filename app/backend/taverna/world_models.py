from __future__ import annotations

import re
from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class Month(Strict):
    name: str = Field(min_length=1, max_length=100)
    days: int = Field(ge=1, le=1000)


class Calendar(Strict):
    name: str = Field(default="Местный календарь", min_length=1, max_length=100)
    months: list[Month] = Field(
        default_factory=lambda: [Month(name="Месяц", days=30)], min_length=1, max_length=100
    )
    hours_per_day: int = Field(default=24, ge=1, le=100)
    minutes_per_hour: int = Field(default=60, ge=1, le=1000)
    epoch_year: int = Field(default=1, ge=-1000000, le=1000000)

    def display(self, minutes: int):
        day, minute = divmod(minutes, self.hours_per_day * self.minutes_per_hour)
        year, day = divmod(day, sum(month.days for month in self.months))
        for month in self.months:
            if day < month.days:
                hour, minute = divmod(minute, self.minutes_per_hour)
                return {
                    "year": self.epoch_year + year,
                    "month": month.name,
                    "day": day + 1,
                    "hour": hour,
                    "minute": minute,
                    "period": "ночь"
                    if hour * 24 // self.hours_per_day < 6
                    else "утро"
                    if hour * 24 // self.hours_per_day < 12
                    else "день"
                    if hour * 24 // self.hours_per_day < 18
                    else "вечер",
                }
            day -= month.days
        raise ValueError("Некорректный календарь")


class Entity(Strict):
    id: UUID = Field(default_factory=uuid4)
    kind: Literal["region", "location", "faction", "person", "resource", "culture", "history"] = "location"
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=32768)
    parent_id: UUID | None = None
    location_id: UUID | None = None
    attributes: dict[str, JsonValue] = Field(default_factory=dict, max_length=100)
    extras: dict[str, JsonValue] = Field(default_factory=dict)


class Link(Strict):
    source_id: UUID
    target_id: UUID
    kind: str = Field(min_length=1, max_length=100)
    description: str = Field(default="", max_length=4000)


class LoreEntry(Strict):
    id: UUID = Field(default_factory=uuid4)
    name: str = Field(default="Запись", min_length=1, max_length=200)
    text: str = Field(min_length=1, max_length=32768)
    keys: list[str] = Field(default_factory=list, max_length=100)
    secondary_keys: list[str] = Field(default_factory=list, max_length=100)
    selective: bool = False
    constant: bool = False
    enabled: bool = True
    public: bool = False
    knower_ids: list[UUID] = Field(default_factory=list, max_length=100)
    entity_ids: list[UUID] = Field(default_factory=list, max_length=100)
    priority: int = Field(default=50, ge=0, le=1000)
    extras: dict[str, JsonValue] = Field(default_factory=dict)

    @model_validator(mode="after")
    def valid(self):
        if any(not key or len(key) > 200 for key in self.keys + self.secondary_keys):
            raise ValueError("Некорректный ключ лорбука")
        return self


PATH = re.compile(
    r"(?:clock\.minutes|flags\.[\w-]{1,100}|resources\.[\w-]{1,100}|entities\.[a-f0-9-]{36}\.(?:location_id|attributes\.[\w-]{1,100}))$",
    re.UNICODE,
)


def validate_condition(node, depth=0, count=None):
    count = [0] if count is None else count
    count[0] += 1
    if depth > 12 or count[0] > 256 or not isinstance(node, dict):
        raise ValueError("Условие слишком сложное или имеет неверный формат")
    op = node.get("op")
    if op in ("all", "any"):
        if (
            set(node) != {"op", "args"}
            or not isinstance(node["args"], list)
            or not 1 <= len(node["args"]) <= 32
        ):
            raise ValueError("Некорректная группа условий")
        for arg in node["args"]:
            validate_condition(arg, depth + 1, count)
    elif op == "not":
        if set(node) != {"op", "arg"}:
            raise ValueError("Некорректное отрицание")
        validate_condition(node["arg"], depth + 1, count)
    elif op in ("eq", "ne", "gt", "gte", "lt", "lte", "exists"):
        if (
            set(node) != ({"op", "path"} if op == "exists" else {"op", "path", "value"})
            or not isinstance(node.get("path"), str)
            or not PATH.fullmatch(node["path"])
        ):
            raise ValueError("Недопустимый путь условия")
        if op != "exists" and (
            isinstance(node["value"], (dict, list))
            or node["value"] is not None
            and not isinstance(node["value"], (str, bool, int, float))
        ):
            raise ValueError("Для сравнения требуется скаляр")
    else:
        raise ValueError("Неизвестный оператор условия")
    return node


class Effect(Strict):
    op: Literal[
        "set_flag", "change_resource", "move_entity", "set_attribute", "add_arc", "resolve_arc", "set_clock"
    ]
    key: str = Field(default="", max_length=100)
    entity_id: UUID | None = None
    value: JsonValue = None
    expected: JsonValue = None
    check_expected: bool = False

    @model_validator(mode="after")
    def valid(self):
        if self.op in (
            "set_flag",
            "change_resource",
            "set_attribute",
            "add_arc",
            "resolve_arc",
        ) and not re.fullmatch(r"[\w-]{1,100}", self.key):
            raise ValueError("Укажите ключ эффекта")
        if self.op in ("move_entity", "set_attribute") and self.entity_id is None:
            raise ValueError("Укажите сущность эффекта")
        if self.op == "change_resource" and (
            type(self.value) not in (int, float) or not -1000000000 <= self.value <= 1000000000
        ):
            raise ValueError("Изменение ресурса должно быть конечным числом")
        if self.op == "set_clock" and (type(self.value) is not int or not 0 <= self.value <= 1000000000000):
            raise ValueError("Игровое время должно быть целым неотрицательным числом минут")
        if self.op == "move_entity":
            UUID(str(self.value))
        return self


class Rule(Strict):
    id: UUID = Field(default_factory=uuid4)
    name: str = Field(min_length=1, max_length=200)
    enabled: bool = True
    on: Literal["time_advance", "move", "manual", "message", "any"] = "time_advance"
    condition: dict[str, JsonValue]
    effects: list[Effect] = Field(min_length=1, max_length=64)
    once: bool = True
    cooldown_minutes: int = Field(default=1, ge=1, le=1000000000)

    @model_validator(mode="after")
    def valid(self):
        validate_condition(self.condition)
        return self


class World(Strict):
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=32768)
    calendar: Calendar = Field(default_factory=Calendar)
    entities: list[Entity] = Field(default_factory=list, max_length=2000)
    links: list[Link] = Field(default_factory=list, max_length=5000)
    lore: list[LoreEntry] = Field(default_factory=list, max_length=2000)
    rules: list[Rule] = Field(default_factory=list, max_length=256)
    flags: dict[str, JsonValue] = Field(default_factory=dict, max_length=1000)
    resources: dict[str, float] = Field(default_factory=dict, max_length=1000)
    extras: dict[str, JsonValue] = Field(default_factory=dict)

    @model_validator(mode="after")
    def valid(self):
        entities = {entity.id: entity for entity in self.entities}
        for values in (self.entities, self.lore, self.rules):
            if len({value.id for value in values}) != len(values):
                raise ValueError("Повторные идентификаторы")
        for entity in self.entities:
            if entity.parent_id is not None and entity.parent_id not in entities:
                raise ValueError("Неизвестная родительская сущность")
            if entity.location_id is not None and (
                entity.location_id not in entities
                or entities[entity.location_id].kind not in ("location", "region")
            ):
                raise ValueError("Неизвестная локация")
            seen = {entity.id}
            parent = entity.parent_id
            while parent:
                if parent in seen:
                    raise ValueError("Цикл иерархии сущностей")
                seen.add(parent)
                parent = entities[parent].parent_id
        if any(
            link.source_id not in entities or link.target_id not in entities for link in self.links
        ) or any(eid not in entities for entry in self.lore for eid in entry.entity_ids):
            raise ValueError("Связь с отсутствующей сущностью")
        for key in (*self.flags, *self.resources):
            if not re.fullmatch(r"[\w-]{1,100}", key):
                raise ValueError("Недопустимый ключ состояния")
        for rule in self.rules:
            for effect in rule.effects:
                if effect.entity_id and effect.entity_id not in entities:
                    raise ValueError("Эффект ссылается на неизвестную сущность")
                if effect.op == "move_entity" and (
                    UUID(str(effect.value)) not in entities
                    or entities[UUID(str(effect.value))].kind not in ("location", "region")
                ):
                    raise ValueError("Эффект перемещения требует локацию")
        return self


class Start(Strict):
    id: UUID = Field(default_factory=uuid4)
    name: str = Field(min_length=1, max_length=200)
    text: str = Field(default="", max_length=32768)
    location_id: UUID | None = None
    minutes: int = Field(default=0, ge=0, le=1000000000000)
    flags: dict[str, JsonValue] = Field(default_factory=dict, max_length=1000)
    resources: dict[str, float] = Field(default_factory=dict, max_length=1000)


class Scenario(Strict):
    world_id: UUID
    name: str = Field(min_length=1, max_length=200)
    premise: str = Field(default="", max_length=32768)
    goals: list[str] = Field(default_factory=list, max_length=100)
    starts: list[Start] = Field(default_factory=list, max_length=100)
    rules: list[Rule] = Field(default_factory=list, max_length=256)
    extras: dict[str, JsonValue] = Field(default_factory=dict)

    @model_validator(mode="after")
    def valid(self):
        if len({s.id for s in self.starts}) != len(self.starts) or len({r.id for r in self.rules}) != len(
            self.rules
        ):
            raise ValueError("Повторные идентификаторы")
        if any(not goal.strip() or len(goal) > 4000 for goal in self.goals):
            raise ValueError("Некорректная цель")
        return self
