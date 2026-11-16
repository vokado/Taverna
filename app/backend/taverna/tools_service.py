from __future__ import annotations

import re
from uuid import uuid4

from .core import AppError
from .rpg_random import SeededRandom
from .storage import Mutation, document
from .tool_models import ToolEntry


class ToolService:
    def __init__(self, materials, manuscripts):
        self.materials, self.manuscripts, self.store = materials, manuscripts, materials.store
        self.store.register("tool_entry", 1, ToolEntry.model_json_schema())

    @staticmethod
    def key(tid):
        return f"manuscripts/tools/{tid}.json"

    def get(self, tid):
        value = self.store.get(self.key(tid))
        if not value or value["kind"] != "tool_entry":
            raise AppError("tool_missing", "Запись инструмента не найдена.", 404)
        return value

    def list(self, query="", kind=None, archived=False):
        rows, errors = self.store.scan("manuscripts/tools/", tolerate_corrupt=True)
        return {
            "items": [
                r["value"]
                for r in rows
                if (not kind or r["value"]["payload"]["kind"] == kind)
                and (archived or not r["value"]["payload"]["archived"])
                and query.casefold()
                in (r["value"]["payload"]["title"] + "\n" + r["value"]["payload"]["text"]).casefold()
            ],
            "errors": errors,
        }

    def plan(self, tid, expected, payload):
        data = ToolEntry.model_validate(payload).model_dump(mode="json")
        value = self.store.get(self.key(tid)) or document("tool_entry", {}, entity_id=tid)
        if value["kind"] != "tool_entry":
            raise AppError("tool_conflict", "ID занят другим документом.", 409)
        value["payload"] = data
        guards = self.materials.reference_guards(data["references"])
        return [Mutation(self.key(tid), value, expected)], guards

    def save(self, tid, expected, payload):
        changes, guards = self.plan(tid, expected, payload)
        self.store.transaction(changes, guards=guards)
        return self.get(tid)

    def names(self, tid, request):
        value = self.get(tid)
        if value["revision"] != request.expected_revision:
            raise AppError("tool_conflict", "Генератор изменился.", 409)
        gen = value["payload"]["generator"]
        if not gen or value["payload"]["archived"]:
            raise AppError("invalid_tool", "Выберите действующий генератор имён.", 422)
        state = {"seed": request.seed, "counter": 0}
        random = SeededRandom(state)
        result = [
            "".join(gen[key][random.below(len(gen[key]))] for key in ("prefixes", "middles", "suffixes"))
            for _ in range(request.count)
        ]
        run = document(
            "tool_run",
            {
                "tool_id": tid,
                "tool_revision": value["revision"],
                "kind": "name_generator",
                "seed": request.seed,
                "algorithm": "sha256-counter-v1",
                "draws": random.draws,
                "names": result,
            },
        )
        self.store.transaction(
            [Mutation("manuscripts/tool-runs/" + run["id"] + ".json", run, 0)],
            guards={self.key(tid): value["revision"]},
        )
        return run

    def preview(self, tid, request):
        result, guards = self.preview_plan(tid, request)
        self.store.transaction(
            [Mutation("manuscripts/tool-previews/" + result["id"] + ".json", result, 0)],
            guards=guards,
        )
        return result

    def preview_plan(self, tid, request):
        """Render without publishing; plugin plans use the same validated template."""
        value = self.get(tid)
        data = value["payload"]
        template = data["template"]
        if value["revision"] != request.expected_revision:
            raise AppError("tool_conflict", "Шаблон изменился.", 409)
        if not template or data["archived"]:
            raise AppError("invalid_tool", "Выберите действующий шаблон.", 422)
        if set(request.inputs) != set(template["variables"]) or any(
            len(v) > 16000 for v in request.inputs.values()
        ):
            raise AppError("template_inputs", "Заполните все объявленные переменные, до16000символов.", 422)
        text = re.sub(r"\{\{\s*([^{}]+?)\s*\}\}", lambda m: request.inputs[m[1]], template["text"])
        if len(text) > 256 * 1024:
            raise AppError("template_limit", "Результат шаблона слишком велик.", 413)
        permission = "notes.write" if template["action"] == "note" else "manuscripts.write"
        book = None
        if template["action"] == "chapter":
            if not request.manuscript_id or not request.manuscript_revision:
                raise AppError("manuscript_required", "Выберите рукопись и её редакцию.", 422)
            book = self.manuscripts.get(str(request.manuscript_id))
            if book["revision"] != request.manuscript_revision:
                raise AppError("manuscript_conflict", "Рукопись изменилась.", 409)
        result = document(
            "tool_preview",
            {
                "status": "pending",
                "tool_id": tid,
                "tool_revision": value["revision"],
                "required_permission": permission,
                "permission_granted": permission in template["grants"],
                "action": template["action"],
                "text": text,
                "title": request.chapter_title if book else data["title"],
                "manuscript_id": book["id"] if book else None,
                "manuscript_revision": book["revision"] if book else None,
                "references": data["references"],
                "result_id": str(uuid4()),
            },
        )
        guards = {self.key(tid): value["revision"]}
        if book:
            guards[self.manuscripts.key(book["id"])] = book["revision"]
        return result, guards

    def execute(self, pid, revision, confirmed):
        if not confirmed:
            raise AppError("confirmation_required", "Подтвердите действие из предпросмотра и его права.", 422)
        key = "manuscripts/tool-previews/" + pid + ".json"
        value = self.store.get(key)
        if not value or value["kind"] != "tool_preview":
            raise AppError("preview_missing", "Предпросмотр не найден.", 404)
        p = value["payload"]
        if p["status"] != "pending":
            raise AppError("preview_used", "Действие уже выполнено.", 409)
        changes, guards, result_id = self.plan_preview_effects(p)
        p["status"] = "executed"
        changes.append(Mutation(key, value, revision))
        self.store.transaction(changes, guards=guards)
        return {"preview": self.store.get(key), "result_id": result_id}

    def plan_preview_effects(self, p):
        """Validate current nested permission and prepare atomic internal effects."""
        tool = self.get(p["tool_id"])
        if (
            tool["revision"] != p["tool_revision"]
            or tool["payload"]["archived"]
            or p["required_permission"] not in tool["payload"]["template"]["grants"]
        ):
            raise AppError(
                "permission_revoked",
                "Шаблон изменён или разрешение отозвано. Создайте новый предпросмотр.",
                403,
            )
        guards = {self.key(tool["id"]): tool["revision"]}
        if p["action"] == "note":
            note = ToolEntry(
                kind="note", title=p["title"], text=p["text"], references=p["references"]
            ).model_dump(mode="json")
            result = document("tool_entry", note, entity_id=p["result_id"])
            changes = [Mutation(self.key(result["id"]), result, 0)]
            guards.update(self.materials.reference_guards(p["references"]))
        else:
            result = self.manuscripts.get(p["manuscript_id"])
            result["payload"]["chapters"].append(
                {"id": p["result_id"], "title": p["title"], "text": p["text"], "links": [], "media": []}
            )
            changes, more = self.manuscripts.plan(
                result["id"],
                p["manuscript_revision"],
                result["payload"],
                "Пользовательский шаблон " + tool["id"],
            )
            guards.update(more)
        for change in changes:
            guards[change.key] = change.expected_revision
        return changes, guards, result["id"]
