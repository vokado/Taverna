"""Shape-only draft validation: incomplete text/IDs are retained; publish validation stays strict."""

from pydantic import BaseModel, ConfigDict, Field, JsonValue


class Shape(BaseModel):
    model_config = ConfigDict(extra="allow", allow_inf_nan=False)


class DraftEntity(Shape):
    id: str
    name: str
    kind: str
    description: str
    attributes: dict[str, JsonValue]


class DraftLore(Shape):
    id: str
    name: str
    text: str
    keys: list[str]
    knower_ids: list[str]
    enabled: bool
    constant: bool
    public: bool


class DraftStart(Shape):
    id: str
    name: str
    text: str
    minutes: int
    location_id: str | None


class DraftCalendar(Shape):
    name: str
    months: list[dict[str, str | int]]
    hours_per_day: int
    minutes_per_hour: int
    epoch_year: int


class DraftLink(Shape):
    source_id: str
    target_id: str
    kind: str


class DraftRule(Shape):
    id: str
    name: str
    on: str
    condition: dict[str, JsonValue]
    effects: list[dict[str, JsonValue]]


class DraftPayload(Shape):
    name: str = Field(max_length=200)
    description: str = ""
    world_id: str = ""
    premise: str = ""
    goals: list[str] = Field(default_factory=list)
    entities: list[DraftEntity] = Field(default_factory=list)
    lore: list[DraftLore] = Field(default_factory=list)
    starts: list[DraftStart] = Field(default_factory=list)
    links: list[DraftLink] = Field(default_factory=list)
    rules: list[DraftRule] = Field(default_factory=list)
    calendar: DraftCalendar | None = None
    resources: dict[str, float] = Field(default_factory=dict)
    flags: dict[str, JsonValue] = Field(default_factory=dict)
    extras: dict[str, JsonValue] = Field(default_factory=dict)
