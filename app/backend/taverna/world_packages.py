from __future__ import annotations

import copy
from uuid import NAMESPACE_URL, uuid5

from .cards import parse_json
from .core import AppError
from .storage import Mutation, digest, document, encoded
from .world_models import Scenario, World


def validate_scenario(world, scenario):
    entities = {e["id"]: e for e in world["entities"]}
    if any(
        s["location_id"]
        and (
            s["location_id"] not in entities
            or entities[s["location_id"]]["kind"] not in ("region", "location")
        )
        for s in scenario["starts"]
    ):
        raise ValueError("Начальная локация отсутствует")
    World.model_validate({**world, "rules": [*world["rules"], *scenario["rules"]]})
    for start in scenario["starts"]:
        World.model_validate(
            {
                **world,
                "flags": {**world["flags"], **start["flags"]},
                "resources": {**world["resources"], **start["resources"]},
            }
        )


class WorldPackages:
    def __init__(self, service):
        self.service, self.store = service, service.store

    def export(self, world_id):
        with self.store.view() as view:
            world = self.service.get("worlds", world_id, view=view)
            rows = [view.get(key) for key in sorted(view.manifest["entries"]) if key.startswith("scenarios/")]
            scenarios = [
                r for r in rows if r["kind"] == "scenario_template" and r["payload"]["world_id"] == world_id
            ]
        data = {"world": world, "scenarios": scenarios}
        return {"format": "taverna-world", "version": 1, "sha256": digest(encoded(data)), "data": data}

    def preview(self, text):
        try:
            package = parse_json(text.encode("utf-8"))
            if (
                package.get("format") != "taverna-world"
                or package.get("version") != 1
                or digest(encoded(package["data"])) != package["sha256"]
            ):
                raise ValueError("package")
            source = package["data"]
            self.store.validate(source["world"])
            if (
                source["world"]["kind"] != "world_template"
                or not isinstance(source["scenarios"], list)
                or len(source["scenarios"]) > 100
            ):
                raise ValueError("kind")
            world = World.model_validate(source["world"]["payload"]).model_dump(mode="json")
            scenarios = []
            for value in source["scenarios"]:
                self.store.validate(value)
                if value["kind"] != "scenario_template":
                    raise ValueError("kind")
                data = Scenario.model_validate(value["payload"]).model_dump(mode="json")
                if data["world_id"] != source["world"]["id"]:
                    raise ValueError("world")
                validate_scenario(world, data)
                scenarios.append({"id": value["id"], "payload": data})
            if len({s["id"] for s in scenarios}) != len(scenarios):
                raise ValueError("duplicates")
            target = str(uuid5(NAMESPACE_URL, "taverna/world-package/" + package["sha256"]))
            return {
                "sha256": package["sha256"],
                "source_world_id": source["world"]["id"],
                "world_id": target,
                "world": world,
                "scenarios": scenarios,
                "warnings": [
                    "Пакет создаст отдельные шаблоны. Явные знания NPC сохраняют исходные ID; назначьте их персонажам целевой истории в редакторе."
                ],
            }
        except (ValueError, TypeError, KeyError, AttributeError) as exc:
            raise AppError(
                "invalid_world_package", "Пакет мира повреждён или содержит недопустимые ссылки.", 422
            ) from exc

    def apply(self, text, confirmed):
        preview = self.preview(text)
        if not confirmed:
            raise AppError("confirmation_required", "Подтвердите импорт пакета после предпросмотра.", 422)
        ledger_key = "worlds/imports/" + preview["sha256"] + ".json"
        existing = self.store.get(ledger_key)
        if existing:
            return {**existing["payload"], "already_imported": True}
        data = copy.deepcopy(preview["world"])
        data["extras"]["import_origin"] = {
            "sha256": preview["sha256"],
            "world_id": preview["source_world_id"],
        }
        world = document("world_template", data, entity_id=preview["world_id"])
        changes = [Mutation(self.service.key("worlds", world["id"]), world, 0)]
        scenario_ids = []
        for source in preview["scenarios"]:
            data = copy.deepcopy(source["payload"])
            data["world_id"] = world["id"]
            data["extras"]["import_origin"] = {"sha256": preview["sha256"], "scenario_id": source["id"]}
            target_id = str(uuid5(NAMESPACE_URL, f"taverna/world-package/{preview['sha256']}/{source['id']}"))
            scenario = document(
                "scenario_template", data, entity_id=target_id, refs=[self.service.key("worlds", world["id"])]
            )
            changes.append(Mutation(self.service.key("scenarios", scenario["id"]), scenario, 0))
            scenario_ids.append(target_id)
        result = {"world_id": world["id"], "scenario_ids": scenario_ids, "sha256": preview["sha256"]}
        changes.append(Mutation(ledger_key, document("world_import", result), 0))
        with self.store.view() as view:
            changes.append(self.service.catalog_change(view))
        self.store.transaction(changes)
        return {**result, "already_imported": False}
