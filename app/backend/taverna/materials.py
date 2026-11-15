from __future__ import annotations

from .core import AppError
from .library_models import Material
from .material_probe import inspect_document
from .storage import Mutation, atomic_write, digest, document, safe_path


class MaterialService:
    def __init__(self, media):
        self.media, self.store, self.chat, self.worlds = media, media.store, media.chat, media.worlds
        self.store.register("material", 1, Material.model_json_schema())

    @staticmethod
    def key(mid):
        return f"media/materials/{mid}.json"

    def get(self, mid):
        value = self.store.get(self.key(mid))
        if not value or value["kind"] != "material":
            raise AppError("material_missing", "Материал не найден.", 404)
        return value

    def reference_guards(self, refs):
        guards = {}
        for reference in refs:
            r = reference.model_dump(mode="json") if hasattr(reference, "model_dump") else reference
            if r.get("world_id"):
                world = self.worlds.get("worlds", r["world_id"])
                guards[self.worlds.key("worlds", world["id"])] = world["revision"]
            if not r.get("story_id"):
                continue
            story = self.chat.story(r["story_id"])
            guards[self.chat.story_key(story["id"])] = story["revision"]
            if not r.get("branch_id"):
                continue
            branch = self.chat.branch(story["id"], r["branch_id"])
            guards[self.chat.branch_key(story["id"], branch["id"])] = branch["revision"]
            state = self.chat.state(story["id"], branch["id"])
            if r.get("event_id") and r["event_id"] not in {e["id"] for e in state["payload"]["events"]}:
                raise AppError("event_missing", "Событие отсутствует в выбранной ветви.", 422)
            messages = None
            if r.get("message_id") or r.get("scene_id"):
                messages = self.chat._prefix(story["id"], branch["id"])
            if r.get("message_id") and r["message_id"] not in {m["id"] for m in messages}:
                raise AppError("message_missing", "Сообщение отсутствует в выбранной ветви.", 422)
            if r.get("scene_id") and r["scene_id"] not in {
                state["payload"]["scene"]["id"],
                *(m.get("scene_id") for m in messages),
            }:
                raise AppError("scene_missing", "Сцена отсутствует в выбранной ветви.", 422)
        return guards

    def import_raw(self, raw, filename, metadata):
        info = inspect_document(raw, filename)
        checksum = digest(raw)
        path = f"library/documents/{checksum}.{info['extension']}"
        payload = Material(
            **metadata,
            filename=filename,
            relative_path=path,
            checksum=checksum,
            mime=info["mime"],
            text=info["text"],
            probe=info["probe"],
        ).model_dump(mode="json")
        guards = self.reference_guards(payload["references"])
        target = safe_path(self.store.root, path)
        if target.exists():
            if digest(target.read_bytes()) != checksum:
                raise AppError("document_corrupt", "Существующий файл повреждён.", 503)
        else:
            atomic_write(target, raw)
        value = document("material", payload)
        self.store.transaction([Mutation(self.key(value["id"]), value, 0)], guards=guards)
        return self.get(value["id"])

    def update(self, mid, revision, metadata):
        value = self.get(mid)
        value["payload"] = Material.model_validate({**value["payload"], **metadata}).model_dump(mode="json")
        guards = self.reference_guards(value["payload"]["references"])
        self.store.transaction([Mutation(self.key(mid), value, revision)], guards=guards)
        return self.get(mid)

    def list(self, query="", archived=False):
        rows, errors = self.store.scan("media/materials/", tolerate_corrupt=True)
        return {
            "items": [
                r["value"]
                for r in rows
                if (archived or not r["value"]["payload"]["archived"])
                and query.casefold()
                in "\n".join(
                    [
                        r["value"]["payload"]["name"],
                        r["value"]["payload"]["description"],
                        r["value"]["payload"]["text"],
                        *r["value"]["payload"]["tags"],
                    ]
                ).casefold()
            ],
            "errors": errors,
        }

    def checked_file(self, mid):
        value = self.get(mid)
        data = value["payload"]
        if (
            data["relative_path"]
            != f"library/documents/{data['checksum']}.{data['filename'].rsplit('.', 1)[-1].lower()}"
        ):
            raise AppError("invalid_path", "Недопустимый путь материала.", 422)
        path = safe_path(self.store.root, data["relative_path"])
        if not path.is_file() or digest(path.read_bytes()) != data["checksum"]:
            raise AppError("document_corrupt", "Файл материала отсутствует или повреждён.", 503)
        return value, path
