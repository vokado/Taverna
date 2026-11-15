from __future__ import annotations

import copy
from uuid import uuid4

from .core import AppError
from .knowledge import player_id
from .rpg_models import ActorSheet, RpgAction, RpgSettings, RpgState, WorldRpgPolicy
from .rpg_random import SeededRandom
from .storage import digest, encoded, now

ACTION_MODULE = {
    "set_attribute": "stats",
    "set_health": "stats",
    "set_capacity": "inventory",
    "set_skill": "skills",
    "adjust_item": "inventory",
    "transfer_item": "inventory",
    "adjust_currency": "economy",
    "transfer_currency": "economy",
    "trade": "economy",
    "roll": "dice",
    "check": "skills",
    "start_conflict": "conflicts",
    "attack": "conflicts",
    "end_conflict": "conflicts",
    "random_event": "random_events",
}


def rpg_value(payload):
    raw = payload.get("rpg", {})
    if not isinstance(raw, dict):
        raise AppError("unsupported_schema", "Некорректное состояние RPG.", 409)
    try:
        return RpgState.model_validate(raw).model_dump(mode="json")
    except ValueError as exc:
        raise AppError("unsupported_schema", "Версия или структура RPG не поддерживается.", 409) from exc


def rpg_prompt(state, actor):
    rpg = rpg_value(state["payload"])
    enabled = rpg["settings"]["enabled"]
    if not any(enabled.values()):
        return ""
    sheet = rpg["actors"].get(actor, {})
    own = {}
    fields = {
        "inventory": ["inventory", "capacity_grams"],
        "stats": ["attributes", "health", "max_health"],
        "skills": ["skills"],
        "economy": ["balances"],
    }
    for module, keys in fields.items():
        if enabled.get(module):
            own.update({key: sheet[key] for key in keys if key in sheet})
    if enabled.get("conflicts"):
        own["conflicts"] = [
            {k: c[k] for k in ("id", "round", "status")} | {"your_turn": c["turn"] == actor}
            for c in rpg["conflicts"].values()
            if actor in c["participants"] and c["status"] == "active"
        ]
        if own["conflicts"]:
            own.update({k: sheet[k] for k in ("health", "max_health") if k in sheet})
    if enabled.get("random_events"):
        own["recent_events"] = [
            e["result"]["outcome"]["text"]
            for e in rpg["audit"]
            if e["kind"] == "random_event" and e.get("actor_id") == actor
        ][-3:]
    if not own:
        return ""
    return (
        "Подтверждённые игровые параметры только этого NPC (изменяет backend, не модель): "
        + encoded(own).decode()
    )


class RpgService:
    def __init__(self, worlds):
        self.worlds, self.chat, self.store = worlds, worlds.chat, worlds.store

    def read(self, sid, bid):
        story, branch, state = self.worlds.snapshot(sid, bid)
        return {
            "rpg": rpg_value(state["payload"]),
            "branch_revision": branch["revision"],
            "player_id": player_id(sid),
            "actors": {
                player_id(sid): "Пользователь",
                **{
                    k: v["card"]["fields"]["name"] for k, v in story["payload"]["character_snapshots"].items()
                },
            },
        }

    @staticmethod
    def policies(data, settings):
        extras = data["world"].get("template", {}).get("payload", {}).get("extras", {})
        if "rpg_policy" not in extras:
            return [settings["world_policy"]]
        raw = extras["rpg_policy"]
        try:
            template = WorldRpgPolicy.model_validate(raw).model_dump(mode="json")
        except ValueError as exc:
            raise AppError(
                "invalid_world_rpg_policy", "Политика RPG прикреплённого мира некорректна.", 422
            ) from exc
        return [settings["world_policy"], template]

    @staticmethod
    def validate_sheets(rpg, actors):
        settings = rpg["settings"]
        if not set(rpg["actors"]) <= actors:
            raise AppError("rpg_scope", "Параметры RPG принадлежат неизвестному участнику.", 422)
        for cid, conflict in rpg["conflicts"].items():
            ids = conflict["participants"]
            if (
                cid != conflict["id"]
                or len(set(ids)) != len(ids)
                or set(conflict["order"]) != set(ids)
                or len(conflict["order"]) != len(ids)
                or conflict["turn"] not in ids
                or not set(ids) <= set(rpg["actors"])
            ):
                raise AppError(
                    "rpg_scope", "Конфликт содержит недопустимый состав или порядок участников.", 422
                )
        if any(r.get("actor_id") and r["actor_id"] not in actors for r in rpg["audit"]):
            raise AppError("rpg_scope", "Журнал RPG относится к неизвестному участнику.", 422)
        for sheet in rpg["actors"].values():
            ActorSheet.model_validate(sheet)
            if not set(sheet["inventory"]) <= set(settings["items"]) or not set(sheet["balances"]) <= set(
                settings["currencies"]
            ):
                raise AppError(
                    "rpg_catalog_missing",
                    "Нельзя удалить предмет/валюту, на которые ссылаются счета участников.",
                    422,
                )
            weight = sum(settings["items"][k]["weight_grams"] * n for k, n in sheet["inventory"].items())
            if sheet["capacity_grams"] is not None and weight > sheet["capacity_grams"]:
                raise AppError("inventory_capacity", "Превышена грузоподъёмность участника.", 409)

    def commit(self, story, branch, data):
        self.chat.update_state(
            story["id"],
            data,
            branch["revision"],
            branch["id"],
            guards={self.chat.story_key(story["id"]): story["revision"]},
        )

    def configure(self, sid, bid, revision, settings, reason, seed=None):
        story, branch, state = self.worlds.snapshot(sid, bid, revision)
        data = copy.deepcopy(state["payload"])
        rpg = rpg_value(data)
        settings = RpgSettings.model_validate(settings).model_dump(mode="json")
        actors = set(story["payload"]["character_snapshots"]) | {player_id(sid)}
        local = settings["world_policy"]
        if not set(local["blocked_actor_ids"]) <= actors or any(
            eid not in data["world"].get("entities", {})
            or data["world"]["entities"][eid]["kind"] != "location"
            for eid in local["allowed_locations"]
        ):
            raise AppError("rpg_scope", "Политика ссылается на неизвестного участника или локацию.", 422)
        policies = self.policies(data, settings)
        for module, enabled in settings["enabled"].items():
            if enabled and any(module not in p["allowed_modules"] for p in policies):
                raise AppError("rpg_world_denied", "Мировая политика запрещает включение этого модуля.", 409)
        previous = rpg["settings"]
        rpg["settings"] = settings
        self.validate_sheets(rpg, set(story["payload"]["character_snapshots"]) | {player_id(sid)})
        receipt = {
            "id": str(uuid4()),
            "kind": "settings",
            "before": previous,
            "after": settings,
            "reason": reason,
            "created_at": now(),
            "branch_id": bid,
        }
        if seed is not None:
            receipt.update(previous_seed=rpg["seed"], previous_counter=rpg["counter"], seed=seed)
            rpg.update(seed=seed, counter=0)
        rpg["audit"].append(receipt)
        data["rpg"] = rpg
        self.commit(story, branch, data)
        return {"branch_revision": revision + 1, "receipt": receipt}

    def apply(self, sid, body):
        a = (
            body.model_dump(mode="json")
            if isinstance(body, RpgAction)
            else RpgAction.model_validate(body).model_dump(mode="json")
        )
        relevant = {
            "target_id": {"trade", "transfer_item", "transfer_currency", "attack"},
            "participants": {"start_conflict"},
            "conflict_id": {"attack", "end_conflict"},
            "key": {
                "set_attribute",
                "set_skill",
                "adjust_item",
                "transfer_item",
                "adjust_currency",
                "transfer_currency",
                "trade",
                "random_event",
            },
            "amount": {
                "set_attribute",
                "set_skill",
                "set_health",
                "set_capacity",
                "adjust_item",
                "adjust_currency",
            },
            "quantity": {"transfer_item", "transfer_currency", "trade"},
            "currency": {"trade"},
            "price": {"trade"},
            "attribute": {"check", "attack"},
            "skill": {"check", "attack"},
            "difficulty": {"check"},
            "dice": {"roll", "attack"},
            "max_health": {"set_health"},
        }
        for key, kinds in relevant.items():
            if a["kind"] not in kinds:
                field = RpgAction.model_fields[key]
                default = field.default_factory() if field.default_factory else field.default
                a[key] = default.model_dump(mode="json") if hasattr(default, "model_dump") else default
        if not a["confirmed"]:
            raise AppError("confirmation_required", "Подтвердите игровое действие.", 422)
        bid = a["branch_id"]
        story, branch, state = self.worlds.snapshot(sid, bid)
        data = copy.deepcopy(state["payload"])
        rpg = rpg_value(data)
        fingerprint = digest(
            encoded({k: v for k, v in a.items() if k not in ("expected_revision", "branch_id")})
        )
        old = next((r for r in rpg["audit"] if r["id"] == a["event_id"]), None)
        if old:
            if old.get("fingerprint") != fingerprint:
                raise AppError("event_conflict", "Этот event_id уже использован с другими параметрами.", 409)
            return {"already_applied": True, "receipt": old, "current_revision": branch["revision"]}
        if branch["revision"] != a["expected_revision"]:
            raise AppError("branch_conflict", "Ветвь изменилась до игрового действия.", 409)
        actors = set(story["payload"]["character_snapshots"]) | {player_id(sid)}
        requested = {a["actor_id"]} | ({a["target_id"]} if a["target_id"] else set()) | set(a["participants"])
        if not requested <= actors:
            raise AppError("rpg_scope", "Участник отсутствует в этой истории.", 422)
        settings = rpg["settings"]
        module = ACTION_MODULE[a["kind"]]
        self.require(settings, module)
        if a["kind"] == "trade":
            self.require(settings, "inventory")
        present = set(data["scene"]["participants"]) | {player_id(sid)}
        required = {module}
        if a["kind"] == "trade":
            required.add("inventory")
        if a["kind"] in ("attack", "start_conflict") and settings["enabled"].get("stats"):
            required.add("stats")
        if a["kind"] in ("check", "attack") and a["attribute"]:
            required.add("stats")
        if a["kind"] == "attack" and a["skill"]:
            required.add("skills")
        if a["kind"] == "random_event":
            self.catalog(settings, "tables", a["key"])
            for outcome in settings["tables"][a["key"]]["outcomes"]:
                required.update(
                    {"item": "inventory", "currency": "economy", "health": "stats", "attribute": "stats"}[
                        reward["kind"]
                    ]
                    for reward in outcome["rewards"]
                )
        for required_module in required:
            self.require(settings, required_module)
        for policy in self.policies(data, settings):
            if not required <= set(policy["allowed_modules"]):
                raise AppError("rpg_world_denied", "Мир запрещает эту игровую механику.", 409)
            if requested & set(policy["blocked_actor_ids"]):
                raise AppError("rpg_world_denied", "Мир запрещает действия выбранного участника.", 409)
            if not policy["allow_offscene"] and not requested <= present:
                raise AppError("speaker_absent", "Игровое действие требует присутствия участников.", 409)
            if (
                policy["allowed_locations"]
                and data["scene"].get("location_id") not in policy["allowed_locations"]
            ):
                raise AppError("rpg_world_denied", "Игровое действие недоступно в этой локации.", 409)
            flags = data["world"].get("flags", {})
            if any(
                key not in flags or flags[key] != value for key, value in policy["required_flags"].items()
            ):
                raise AppError("rpg_world_denied", "Не выполнены условия мировых флагов.", 409)
        for actor in requested:
            rpg["actors"].setdefault(actor, ActorSheet().model_dump(mode="json"))
        before = {k: copy.deepcopy(rpg["actors"][k]) for k in requested}
        rng = SeededRandom(rpg)
        counter = rpg["counter"]
        result = self.reduce(rpg, a, rng)
        try:
            self.validate_sheets(rpg, actors)
        except ValueError as exc:
            raise AppError(
                "invalid_rpg_parameter", "Результат выходит за допустимые пределы игровых параметров.", 422
            ) from exc
        receipt = {
            "id": a["event_id"],
            "kind": a["kind"],
            "actor_id": a["actor_id"],
            "branch_id": bid,
            "fingerprint": fingerprint,
            "input": a,
            "before": before,
            "after": {k: copy.deepcopy(rpg["actors"][k]) for k in requested},
            "seed": rpg["seed"],
            "counter_before": counter,
            "counter_after": rpg["counter"],
            "draws": rng.draws,
            "result": result,
            "reason": a["reason"],
            "created_at": now(),
        }
        rpg["audit"].append(receipt)
        data["rpg"] = RpgState.model_validate(rpg).model_dump(mode="json")
        data["events"].append(
            {
                "id": a["event_id"],
                "kind": "rpg_" + a["kind"],
                "story_id": sid,
                "branch_id": bid,
                "knower_ids": sorted(requested - {player_id(sid)}),
                "source_ids": [],
                "cause_ids": [],
                "created_at": receipt["created_at"],
            }
        )
        self.commit(story, branch, data)
        return {"already_applied": False, "receipt": receipt, "branch_revision": branch["revision"] + 1}

    @staticmethod
    def require(settings, module):
        if not settings["enabled"].get(module):
            raise AppError("rpg_module_disabled", "Сначала включите требуемый игровой модуль.", 409)

    @staticmethod
    def balance(values, key, delta):
        result = values.get(key, 0) + delta
        if not 0 <= result <= 10**12:
            raise AppError(
                "insufficient_balance", "Недостаточно остатка или превышен допустимый предел.", 409
            )
        values[key] = result

    @staticmethod
    def catalog(settings, kind, key):
        if key not in settings[kind]:
            raise AppError("rpg_catalog_missing", "Предмет, валюта или таблица не найдены в настройках.", 422)

    def reduce(self, rpg, a, rng):
        s, actor = rpg["settings"], rpg["actors"][a["actor_id"]]
        kind = a["kind"]
        target = rpg["actors"].get(a["target_id"])
        if kind in ("transfer_item", "transfer_currency", "trade", "attack") and (
            not target or a["actor_id"] == a["target_id"]
        ):
            raise AppError("rpg_target_required", "Выберите другого участника-получателя/цель.", 422)
        if (
            kind in ("check", "attack", "trade", "transfer_item", "transfer_currency")
            and actor["health"] == 0
            and (s["enabled"].get("stats") or s["enabled"].get("conflicts"))
        ):
            raise AppError(
                "rpg_actor_incapacitated",
                "Участник с нулевым здоровьем не может выполнить это действие.",
                409,
            )
        if kind in ("set_attribute", "set_skill"):
            if not a["key"].strip() or not -1000 <= a["amount"] <= 1000:
                raise AppError("invalid_rpg_parameter", "Задайте имя и параметр в пределах -1000–1000.", 422)
            actor["attributes" if kind == "set_attribute" else "skills"][a["key"]] = a["amount"]
        elif kind == "set_health":
            actor["health"] = a["amount"]
            if a.get("max_health") is not None:
                actor["max_health"] = a["max_health"]
        elif kind == "set_capacity":
            actor["capacity_grams"] = a["amount"] if a["amount"] else None
        elif kind in ("adjust_item", "transfer_item"):
            self.catalog(s, "items", a["key"])
            self.balance(
                actor["inventory"], a["key"], a["amount"] if kind == "adjust_item" else -a["quantity"]
            )
            if target is not None and kind == "transfer_item":
                self.balance(target["inventory"], a["key"], a["quantity"])
        elif kind in ("adjust_currency", "transfer_currency"):
            self.catalog(s, "currencies", a["key"])
            self.balance(
                actor["balances"], a["key"], a["amount"] if kind == "adjust_currency" else -a["quantity"]
            )
            if target is not None and kind == "transfer_currency":
                self.balance(target["balances"], a["key"], a["quantity"])
        elif kind == "trade":
            self.catalog(s, "items", a["key"])
            self.catalog(s, "currencies", a["currency"])
            # Actor buys from target; all four ledger changes publish atomically.
            cost = a["quantity"] * a["price"]
            self.balance(actor["balances"], a["currency"], -cost)
            self.balance(target["balances"], a["currency"], cost)
            self.balance(target["inventory"], a["key"], -a["quantity"])
            self.balance(actor["inventory"], a["key"], a["quantity"])
            return {"quantity": a["quantity"], "total_price": cost}
        elif kind == "roll":
            return rng.dice(a["dice"])
        elif kind == "check":
            attribute = self.attribute(s, actor, a["attribute"])
            skill = self.skill(actor, a["skill"])
            rolled = rng.dice({"count": 1, "sides": 20, "modifier": attribute + skill})
            return {**rolled, "difficulty": a["difficulty"], "success": rolled["total"] >= a["difficulty"]}
        elif kind == "start_conflict":
            ids = a["participants"]
            if len(ids) < 2 or len(set(ids)) != len(ids) or a["actor_id"] not in ids:
                raise AppError(
                    "invalid_conflict", "Задайте минимум двух разных участников, включая инициатора.", 422
                )
            if any(
                c["status"] == "active" and set(c["participants"]) & set(ids)
                for c in rpg["conflicts"].values()
            ):
                raise AppError("conflict_active", "Участник уже занят в активном конфликте.", 409)
            if any(rpg["actors"][i]["health"] == 0 for i in ids):
                raise AppError(
                    "rpg_actor_incapacitated", "Участник с нулевым здоровьем не начинает конфликт.", 409
                )
            initiatives = {
                i: rng.below(20)
                + 1
                + (rpg["actors"][i]["attributes"].get("agility", 0) if s["enabled"].get("stats") else 0)
                for i in ids
            }
            order = sorted(ids, key=lambda i: (-initiatives[i], i))
            cid = a["event_id"]
            rpg["conflicts"][cid] = {
                "id": cid,
                "participants": ids,
                "order": order,
                "turn": order[0],
                "round": 1,
                "status": "active",
            }
            return {"conflict_id": cid, "initiatives": initiatives, "order": order}
        elif kind in ("attack", "end_conflict"):
            conflict = rpg["conflicts"].get(a["conflict_id"])
            if (
                not conflict
                or conflict["status"] != "active"
                or a["actor_id"] not in conflict["participants"]
            ):
                raise AppError("invalid_conflict", "Активный конфликт участника не найден.", 409)
            if kind == "end_conflict":
                conflict["status"] = "ended"
                return {"conflict_id": conflict["id"], "status": "ended"}
            if (
                conflict["turn"] != a["actor_id"]
                or a["target_id"] not in conflict["participants"]
                or target["health"] == 0
            ):
                raise AppError(
                    "invalid_conflict_turn", "Сейчас ход другого участника или цель недоступна.", 409
                )
            modifier = self.attribute(s, actor, a["attribute"])
            if a["skill"]:
                self.require(s, "skills")
                modifier += self.skill(actor, a["skill"])
            hit = rng.dice({"count": 1, "sides": 20, "modifier": modifier})
            defense = target["attributes"].get("defense", 10) if s["enabled"].get("stats") else 10
            damage = rng.dice(a["dice"]) if hit["total"] >= defense else None
            loss = min(target["health"], max(0, damage["total"])) if damage else 0
            target["health"] = max(0, target["health"] - loss)
            alive = [i for i in conflict["order"] if rpg["actors"][i]["health"] > 0]
            if len(alive) <= 1:
                conflict["status"] = "ended"
            else:
                position = conflict["order"].index(a["actor_id"])
                following = next((i for i in conflict["order"][position + 1 :] if i in alive), None)
                if following is None:
                    following = alive[0]
                    conflict["round"] += 1
                conflict["turn"] = following
            return {
                "hit": hit,
                "defense": defense,
                "damage": damage,
                "health_lost": loss,
                "conflict": copy.deepcopy(conflict),
            }
        elif kind == "random_event":
            self.catalog(s, "tables", a["key"])
            table = s["tables"][a["key"]]
            ticket = rng.below(sum(o["weight"] for o in table["outcomes"]))
            for outcome in table["outcomes"]:
                ticket -= outcome["weight"]
                if ticket < 0:
                    for reward in outcome["rewards"]:
                        module = {
                            "item": "inventory",
                            "currency": "economy",
                            "health": "stats",
                            "attribute": "stats",
                        }[reward["kind"]]
                        self.require(s, module)
                        if reward["kind"] in ("item", "currency"):
                            self.balance(
                                actor["inventory" if reward["kind"] == "item" else "balances"],
                                reward["key"],
                                reward["amount"],
                            )
                        elif reward["kind"] == "health":
                            actor["health"] = min(
                                actor["max_health"], max(0, actor["health"] + reward["amount"])
                            )
                        else:
                            actor["attributes"][reward["key"]] = (
                                actor["attributes"].get(reward["key"], 0) + reward["amount"]
                            )
                    return {"table": a["key"], "outcome": copy.deepcopy(outcome)}
        return {"updated": True, "manual_correction": kind.startswith("set_") or kind.startswith("adjust_")}

    @staticmethod
    def attribute(settings, actor, key):
        if not key:
            return 0
        RpgService.require(settings, "stats")
        if key not in actor["attributes"]:
            raise AppError("rpg_parameter_missing", "Характеристика участника не задана.", 422)
        return actor["attributes"][key]

    @staticmethod
    def skill(actor, key):
        if not key:
            return 0
        if key not in actor["skills"]:
            raise AppError("rpg_parameter_missing", "Навык участника не задан.", 422)
        return actor["skills"][key]
