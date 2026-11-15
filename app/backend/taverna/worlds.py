from __future__ import annotations

import copy
import random
from uuid import UUID, uuid4

from .chat import identifier
from .core import AppError
from .storage import Mutation, digest, document, encoded, now
from .world_models import Calendar, Effect, Scenario, World, validate_condition
from .world_packages import validate_scenario

MISSING = object()


def lookup(data, path):
    value = data
    for key in path.split("."):
        if not isinstance(value, dict) or key not in value:
            return MISSING
        value = value[key]
    return value


def condition(node, data):
    op = node["op"]
    if op == "all":
        return all(condition(arg, data) for arg in node["args"])
    if op == "any":
        return any(condition(arg, data) for arg in node["args"])
    if op == "not":
        return not condition(node["arg"], data)
    left = lookup(data, node["path"])
    if op == "exists":
        return left is not MISSING
    if left is MISSING:
        return False
    right = node["value"]
    if op in ("eq", "ne"):
        equal = type(left) is type(right) and left == right
        if type(left) in (int, float) and type(right) in (int, float):
            equal = left == right
        return equal if op == "eq" else not equal
    if type(left) not in (int, float) or type(right) not in (int, float):
        return False
    return {"gt": left > right, "gte": left >= right, "lt": left < right, "lte": left <= right}[op]


def effect(data, raw):
    value = Effect.model_validate(raw).model_dump(mode="json")
    op, key, entity_id = value["op"], value["key"], value["entity_id"]
    if op == "set_clock":
        target, key = data["clock"], "minutes"
    elif op == "set_flag":
        target = data["flags"]
    elif op == "change_resource":
        target = data["resources"]
    elif op in ("add_arc", "resolve_arc"):
        target = data["arcs"]
    else:
        entity = data["entities"].get(entity_id)
        if not entity:
            raise AppError("invalid_effect", "Сущность эффекта отсутствует.", 422)
        if op == "move_entity":
            location = data["entities"].get(value["value"])
            if not location or location["kind"] not in ("location", "region"):
                raise AppError("invalid_effect", "Целевая локация отсутствует.", 422)
            target, key = entity, "location_id"
        else:
            target = entity["attributes"]
    previous = copy.deepcopy(target.get(key))
    if value["check_expected"] and (
        key not in target or type(previous) is not type(value["expected"]) or previous != value["expected"]
    ):
        raise AppError("world_state_conflict", "Предыдущее значение эффекта изменилось.", 409)
    if op == "change_resource":
        old = target.get(key, 0)
        if type(old) not in (int, float) or not -1e15 <= old + value["value"] <= 1e15:
            raise AppError(
                "world_state_conflict", "Ресурс не является числом или превышен допустимый диапазон.", 409
            )
        target[key] = old + value["value"]
    elif op == "resolve_arc":
        if key not in target:
            raise AppError("world_state_conflict", "Сюжетная линия не найдена.", 409)
        target[key] = {**target[key], "status": "resolved", "resolution": value["value"]}
    elif op == "add_arc":
        if key in target and target[key].get("status") == "open":
            raise AppError("world_state_conflict", "Такая сюжетная линия уже открыта.", 409)
        target[key] = {"status": "open", "description": value["value"]}
    else:
        target[key] = copy.deepcopy(value["value"])
    return {
        "op": op,
        "entity_id": entity_id,
        "key": key,
        "before": previous,
        "after": copy.deepcopy(target[key]),
    }


class WorldService:
    def __init__(self, chat):
        self.chat, self.store, self.generation = chat, chat.store, chat.generation
        chat.state_reducer = self.message_state
        chat.state_cloner = self.clone_scope
        self.store.register("world_template", 1, World.model_json_schema())
        self.store.register("scenario_template", 1, Scenario.model_json_schema())

    def key(self, kind, entity_id):
        if kind not in ("worlds", "scenarios"):
            raise AppError("invalid_world", "Неизвестный тип шаблона.", 422)
        return f"{kind}/{identifier(str(entity_id))}.json"

    def get(self, kind, entity_id, *, view=None):
        value = (view or self.store).get(self.key(kind, entity_id))
        if not value:
            raise AppError(
                "world_missing" if kind == "worlds" else "scenario_missing", "Шаблон не найден.", 404
            )
        return value

    def list(self, kind):
        self.key(kind, str(UUID(int=0)))
        rows, corrupt = self.store.scan(kind + "/", tolerate_corrupt=True)
        expected_kind = "world_template" if kind == "worlds" else "scenario_template"
        return {
            "items": [r["value"] for r in rows if r["value"]["kind"] == expected_kind],
            "corrupt": corrupt,
        }

    def validate(self, kind, payload):
        try:
            model = World.model_validate(payload) if kind == "worlds" else Scenario.model_validate(payload)
            data = model.model_dump(mode="json")
            encoded(data)
            if kind == "scenarios":
                world = self.get("worlds", data["world_id"])["payload"]
                validate_scenario(world, data)
            return data
        except ValueError as exc:
            raise AppError(
                "invalid_world", "Шаблон или ссылки не соответствуют схеме мира/сценария.", 422
            ) from exc

    @staticmethod
    def catalog_change(view):
        key = "worlds/catalog.json"
        current = view.get(key)
        value = copy.deepcopy(current) if current else document("template_catalog", {"epoch": 0})
        value["payload"]["epoch"] += 1
        return Mutation(key, value, current["revision"] if current else 0)

    def plan_save(self, kind, payload, expected_revision, entity_id=None):
        data = self.validate(kind, payload)
        with self.store.view() as view:
            current = self.get(kind, entity_id, view=view) if entity_id else None
            catalog = self.catalog_change(view)
            guards = {}
            if kind == "scenarios":
                world = self.get("worlds", data["world_id"], view=view)
                try:
                    validate_scenario(world["payload"], data)
                except ValueError as exc:
                    raise AppError(
                        "world_state_conflict", "Мир изменился; проверьте ссылки сценария.", 409
                    ) from exc
                guards[self.key("worlds", world["id"])] = world["revision"]
            elif current:
                for key in view.manifest["entries"]:
                    if key.startswith("scenarios/"):
                        scenario = view.get(key)
                        if (
                            scenario["kind"] == "scenario_template"
                            and scenario["payload"]["world_id"] == current["id"]
                        ):
                            try:
                                validate_scenario(data, scenario["payload"])
                            except ValueError as exc:
                                raise AppError(
                                    "world_state_conflict",
                                    "Изменение нарушает ссылки сохранённого сценария. Сначала исправьте сценарий.",
                                    409,
                                ) from exc
                            guards[key] = scenario["revision"]
        value = (
            copy.deepcopy(current)
            if current
            else document("world_template" if kind == "worlds" else "scenario_template", data)
        )
        value["payload"] = data
        value["refs"] = [self.key("worlds", data["world_id"])] if kind == "scenarios" else []
        return value, [Mutation(self.key(kind, value["id"]), value, expected_revision), catalog], guards

    def save(self, kind, payload, expected_revision, entity_id=None):
        value, changes, guards = self.plan_save(kind, payload, expected_revision, entity_id)
        self.store.transaction(changes, guards=guards)
        return self.get(kind, value["id"])

    def _snapshot(self, view, story_id, branch_id, expected_revision=None):
        story = view.get(self.chat.story_key(story_id))
        branch = view.get(self.chat.branch_key(story_id, branch_id))
        if not story or not branch:
            raise AppError("story_missing", "История или ветвь не найдена.", 404)
        state = view.get(self.chat.state_key(story_id, branch["payload"]["state_id"]))
        if not state:
            raise AppError("state_missing", "Состояние ветви отсутствует.", 503)
        if expected_revision is not None and branch["revision"] != expected_revision:
            raise AppError("branch_conflict", "Ветвь изменилась.", 409)
        if story["payload"]["active_branch"] != branch_id:
            raise AppError("inactive_branch", "Выберите активную ветвь.", 409)
        return story, branch, state

    def snapshot(self, story_id, branch_id, expected_revision=None):
        with self.store.view() as view:
            return self._snapshot(view, story_id, branch_id, expected_revision)

    def attach(
        self,
        story_id,
        branch_id,
        expected_revision,
        world_id,
        scenario_id=None,
        start_id=None,
        seed=None,
        confirmed=False,
    ):
        if not confirmed:
            raise AppError("confirmation_required", "Подтвердите создание снимка мира для истории.", 422)
        with self.store.view() as view:
            _, branch, state = self._snapshot(view, story_id, branch_id, expected_revision)
            template = self.get("worlds", world_id, view=view)
            scenario = self.get("scenarios", scenario_id, view=view) if scenario_id else None
        if state["payload"]["world"]:
            raise AppError(
                "world_already_attached", "В этой ветви уже есть состояние мира. Оно не будет заменено.", 409
            )
        world = World.model_validate(template["payload"]).model_dump(mode="json")
        if scenario and scenario["payload"]["world_id"] != world_id:
            raise AppError("invalid_world", "Сценарий относится к другому миру.", 422)
        if scenario:
            try:
                validate_scenario(world, Scenario.model_validate(scenario["payload"]).model_dump(mode="json"))
            except ValueError as exc:
                raise AppError("invalid_world", "Сценарий несовместим со снимком мира.", 409) from exc
        starts = scenario["payload"]["starts"] if scenario else []
        selected = next((s for s in starts if s["id"] == start_id), None)
        seed = random.SystemRandom().getrandbits(63) if seed is None else seed
        if start_id and not selected:
            raise AppError("invalid_world", "Начало сценария не найдено.", 422)
        if starts and not selected:
            selected = random.Random(seed).choice(starts)
        selected = selected or {
            "id": None,
            "name": "Без сценария",
            "text": "",
            "location_id": None,
            "minutes": 0,
            "flags": {},
            "resources": {},
        }
        data = copy.deepcopy(state["payload"])
        data["world"] = {
            "format": 1,
            "template": copy.deepcopy(template),
            "scenario": copy.deepcopy(scenario),
            "start": copy.deepcopy(selected),
            "random_seed": seed,
            "clock": {"minutes": selected["minutes"]},
            "entities": {e["id"]: copy.deepcopy(e) for e in world["entities"]},
            "flags": {**world["flags"], **selected["flags"]},
            "resources": {**world["resources"], **selected["resources"]},
            "arcs": {},
            "rule_runs": {},
            "applied_events": {},
        }
        data["scene"]["opening"] = selected["text"]
        data["scene"]["location_id"] = selected["location_id"]
        data["scene"]["location"] = next(
            (e["name"] for e in world["entities"] if e["id"] == selected["location_id"]), ""
        )
        data["scene"]["time"] = self.time_text(data["world"])
        result = self.chat.update_state(story_id, data, expected_revision, branch_id)
        return {"state": result, "branch_revision": expected_revision + 1, "start": selected}

    @staticmethod
    def time_text(world):
        c = Calendar.model_validate(world["template"]["payload"]["calendar"]).display(
            world["clock"]["minutes"]
        )
        return f"{c['day']} {c['month']} {c['year']}, {c['hour']:02}:{c['minute']:02} ({c['period']})"

    def read(self, story_id, branch_id):
        _, branch, state = self.snapshot(story_id, branch_id)
        world = state["payload"]["world"]
        return {
            "world": world,
            "branch_revision": branch["revision"],
            "scene": state["payload"]["scene"],
            "calendar": Calendar.model_validate(world["template"]["payload"]["calendar"]).display(
                world["clock"]["minutes"]
            )
            if world.get("format") == 1
            else None,
        }

    def reduce(
        self,
        data,
        *,
        event_id,
        operation,
        minutes=0,
        location_id=None,
        effects=(),
        reason,
        cause_ids=(),
        source_ids=(),
        base_revision=None,
    ):
        world = data.get("world", {})
        if world.get("format") != 1:
            raise AppError("world_missing", "Сначала подключите мир к истории.", 409)
        fingerprint = digest(
            encoded(
                {
                    "operation": operation,
                    "minutes": minutes,
                    "location_id": location_id,
                    "effects": list(effects),
                    "reason": reason,
                    "cause_ids": list(cause_ids),
                    "source_ids": list(source_ids),
                }
            )
        )
        existing = world["applied_events"].get(event_id)
        if existing:
            if existing["fingerprint"] != fingerprint:
                raise AppError(
                    "event_conflict", "Этот ID события уже использован для другого изменения.", 409
                )
            return existing, False
        known_events = {e["id"] for e in data["events"] if isinstance(e, dict) and "id" in e}
        if set(cause_ids) - known_events:
            raise AppError("invalid_effect", "Причина ссылается на неизвестное событие этой ветви.", 422)
        before = {"minutes": world["clock"]["minutes"], "location_id": data["scene"].get("location_id")}
        if operation == "time_advance":
            if (
                type(minutes) is not int
                or not 1 <= minutes <= 1000000000
                or world["clock"]["minutes"] + minutes > 1000000000000
            ):
                raise AppError("invalid_effect", "Недопустимый временной пропуск.", 422)
            world["clock"]["minutes"] += minutes
        elif operation == "move":
            location = world["entities"].get(location_id)
            if not location or location["kind"] not in ("location", "region"):
                raise AppError("invalid_effect", "Локация не найдена.", 422)
            data["scene"].update(location_id=location_id, location=location["name"])
        elif operation not in ("manual", "message"):
            raise AppError("invalid_effect", "Неизвестная операция мира.", 422)
        changes = [effect(world, value) for value in effects]
        rules = [
            *world["template"]["payload"]["rules"],
            *((world.get("scenario") or {}).get("payload", {}).get("rules", [])),
        ]
        fired = []
        for rule in rules:
            previous = world["rule_runs"].get(rule["id"])
            if (
                not rule["enabled"]
                or rule["on"] not in (operation, "any")
                or previous
                and (
                    rule["once"] or world["clock"]["minutes"] - previous["minutes"] < rule["cooldown_minutes"]
                )
            ):
                continue
            validate_condition(rule["condition"])
            if condition(rule["condition"], world):
                changes.extend(effect(world, value) for value in rule["effects"])
                fired.append(rule["id"])
                world["rule_runs"][rule["id"]] = {"event_id": event_id, "minutes": world["clock"]["minutes"]}
        data["scene"]["time"] = self.time_text(world)
        receipt = {
            "id": event_id,
            "story_id": data["story_id"],
            "branch_id": data["branch_id"],
            "fingerprint": fingerprint,
            "operation": operation,
            "reason": reason,
            "source_ids": list(source_ids),
            "cause_ids": list(cause_ids),
            "base_revision": base_revision,
            "created_at": now(),
            "before": before,
            "after": {"minutes": world["clock"]["minutes"], "location_id": data["scene"].get("location_id")},
            "changes": changes,
            "rules_fired": fired,
        }
        world["applied_events"][event_id] = receipt
        data["events"].append(receipt)
        return receipt, True

    def apply(
        self,
        story_id,
        branch_id,
        expected_revision,
        *,
        event_id=None,
        operation="manual",
        minutes=0,
        location_id=None,
        effects=(),
        reason,
        cause_ids=(),
        source_ids=(),
    ):
        _, branch, state = self.snapshot(story_id, branch_id)
        event_id = identifier(event_id or str(uuid4()))
        # Duplicate receipts are checked before CAS, allowing safe retries after an acknowledged/lost response.
        existing = state["payload"].get("world", {}).get("applied_events", {}).get(event_id)
        if not existing and branch["revision"] != expected_revision:
            raise AppError("branch_conflict", "Ветвь изменилась.", 409)
        if source_ids:
            messages = {m["id"] for m in self.chat._prefix(story_id, branch_id)}
            if set(source_ids) - messages:
                raise AppError("source_missing", "Источник события отсутствует в этой ветви.", 422)
        data = copy.deepcopy(state["payload"])
        receipt, changed = self.reduce(
            data,
            event_id=event_id,
            operation=operation,
            minutes=minutes,
            location_id=location_id,
            effects=effects,
            reason=reason,
            cause_ids=cause_ids,
            source_ids=source_ids,
            base_revision=expected_revision,
        )
        if changed:
            self.chat.update_state(story_id, data, expected_revision, branch_id)
        return {
            "event": receipt,
            "already_applied": not changed,
            "branch_revision": expected_revision + 1 if changed else branch["revision"],
        }

    def message_state(self, data, message, revision):
        if data.get("world", {}).get("format") != 1:
            return None
        rules = [
            *data["world"]["template"]["payload"]["rules"],
            *((data["world"].get("scenario") or {}).get("payload", {}).get("rules", [])),
        ]
        if not any(rule["enabled"] and rule["on"] in ("message", "any") for rule in rules):
            return None
        result = copy.deepcopy(data)
        self.reduce(
            result,
            event_id=message["id"],
            operation="message",
            reason="Событие сохранения реплики",
            source_ids=[message["id"]],
            base_revision=revision,
        )
        return result

    @staticmethod
    def clone_scope(payload, mapping, branch_id):
        world = payload.get("world", {})
        if world.get("format") != 1:
            return
        # Event IDs and request fingerprints retain their original identity; scope/source links are local.
        for event in [*payload.get("events", []), *world.get("applied_events", {}).values()]:
            if not isinstance(event, dict) or "fingerprint" not in event:
                continue
            event.setdefault(
                "origin",
                {
                    "story_id": event.get("story_id"),
                    "branch_id": event.get("branch_id"),
                    "source_ids": copy.deepcopy(event.get("source_ids", [])),
                },
            )
            event["branch_id"] = branch_id
            event["source_ids"] = [mapping.get(source, source) for source in event.get("source_ids", [])]
