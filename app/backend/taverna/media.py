from __future__ import annotations

import copy
import io
from uuid import uuid4

from PIL import Image, ImageOps

from .core import AppError
from .media_models import Asset, MediaState
from .media_probe import probe_media
from .npc_context import npc_value
from .storage import Mutation, atomic_write, digest, document, now, safe_path


def media_value(payload):
    try:
        return MediaState.model_validate(payload.get("media", {})).model_dump(mode="json")
    except ValueError as exc:
        raise AppError(
            "unsupported_schema", "Состояние медиа не соответствует поддерживаемой версии.", 409
        ) from exc


def asset_ids(media):
    ids = set()
    snapshots = [media]
    for entry in media["history"]:
        for key in ("before", "after"):
            try:
                snapshots.append(
                    MediaState.model_validate({**entry[key], "history": []}).model_dump(mode="json")
                )
            except (ValueError, KeyError, TypeError) as exc:
                raise AppError("invalid_media_history", "История медиа повреждена.", 422) from exc
    for snap in snapshots:
        ids.update(v["asset_id"] for v in snap["variants"])
        ids.update(b["asset_id"] for b in snap["bindings"])
        for scene in snap["compositions"].values():
            ids.update(layer["asset_id"] for layer in scene["layers"])
            ids.update(track["asset_id"] for track in (scene["music"], scene["ambient"]) if track["asset_id"])
    return ids


class MediaService:
    def __init__(self, worlds):
        self.worlds, self.chat, self.store = worlds, worlds.chat, worlds.store
        # Optional metadata fields keep existing v1 PNG card portraits compatible.
        self.store.register("media_asset", 1, Asset.model_json_schema())
        self.chat.state_cloners["media"] = self.clone_scope

    @staticmethod
    def key(asset_id):
        return f"media/{asset_id}.json"

    def get(self, asset_id):
        value = self.store.get(self.key(asset_id))
        if not value or value["kind"] != "media_asset":
            raise AppError("media_missing", "Медиаресурс не найден.", 404)
        value["payload"] = Asset.model_validate(value["payload"]).model_dump(mode="json")
        if not value["payload"]["relative_path"].startswith("media/"):
            raise AppError("invalid_path", "Медиафайл должен находиться в медиакаталоге.", 422)
        return value

    def import_raw(self, raw, name, category, tags=(), provenance=None):
        info = probe_media(raw)
        checksum = digest(raw)
        try:
            payload = Asset(
                relative_path=f"media/assets/{checksum}.{info['extension']}",
                checksum=checksum,
                type=info["type"],
                name=name,
                category=category,
                tags=list(tags),
                info=info,
                provenance=provenance or {},
            ).model_dump(mode="json")
        except ValueError as exc:
            raise AppError(
                "invalid_media_metadata", "Категория/название/теги не соответствуют ресурсу.", 422
            ) from exc

        path = safe_path(self.store.root, payload["relative_path"])
        if path.exists():
            if digest(path.read_bytes()) != checksum:
                raise AppError(
                    "document_corrupt", "Существующий медиафайл повреждён; импорт не перезаписал его.", 503
                )
        else:
            atomic_write(path, raw)
        value = document("media_asset", payload)
        self.store.transaction([Mutation(self.key(value["id"]), value, 0)])
        return self.get(value["id"])

    def update(self, asset_id, expected_revision, metadata):
        value = self.get(asset_id)
        # Binary content/path/checksum/provenance are immutable through metadata editing.
        try:
            value["payload"] = Asset.model_validate({**value["payload"], **metadata}).model_dump(mode="json")
        except ValueError as exc:
            raise AppError(
                "invalid_media_metadata", "Категория/название/теги не соответствуют ресурсу.", 422
            ) from exc
        self.store.transaction([Mutation(self.key(asset_id), value, expected_revision)])
        return self.get(asset_id)

    def list(self, query="", tag=None, category=None, archived=False, offset=0, limit=50):
        rows, errors = self.store.scan("media/", tolerate_corrupt=True)
        found = []
        for row in rows:
            value = row["value"]
            if value["kind"] != "media_asset":
                continue
            try:
                payload = Asset.model_validate(value["payload"]).model_dump(mode="json")
            except ValueError:
                errors.append(row["key"])
                continue
            text = "\n".join([payload["name"], payload["description"], *payload["tags"]]).casefold()
            if (
                (payload["archived"] and not archived)
                or (query.casefold() not in text)
                or (tag and tag not in payload["tags"])
                or (category and payload["category"] != category)
            ):
                continue
            found.append({**value, "payload": payload})
        return {
            "items": sorted(found, key=lambda v: (v["updated_at"], v["id"]), reverse=True)[
                offset : offset + limit
            ],
            "total": len(found),
            "offset": offset,
            "errors": errors,
        }

    def checked_file(self, asset_id):
        value = self.get(asset_id)
        path = safe_path(self.store.root, value["payload"]["relative_path"])
        try:
            raw = path.read_bytes()
            if digest(raw) != value["payload"]["checksum"]:
                raise ValueError("checksum")
        except (OSError, ValueError) as exc:
            raise AppError("document_corrupt", "Медиафайл повреждён или отсутствует.", 503) from exc
        return value, path

    def thumbnail(self, asset_id):
        value, path = self.checked_file(asset_id)
        if not value["payload"]["type"].startswith("image/"):
            raise AppError("media_type_mismatch", "Миниатюра доступна для изображения.", 422)
        with Image.open(path) as image:
            image.seek(0)
            image = ImageOps.exif_transpose(image).convert("RGBA")
            image.thumbnail((512, 512))
            out = io.BytesIO()
            image.save(out, format="PNG")
            return out.getvalue()

    @staticmethod
    def validate_scope(story, state, media, lookup):
        actors = set(story["payload"]["character_snapshots"])
        scene_ids = {state["payload"]["scene"]["id"]}
        # Past compositions remain attached to their own scene UUID in this branch.
        scene_ids.update(media["compositions"])
        events = {e.get("id") for e in state["payload"]["events"] if isinstance(e, dict)}
        world_id = state["payload"]["world"].get("template", {}).get("id")
        for binding in media["bindings"]:
            expected = {"character": actors, "scene": scene_ids, "event": events, "world": {world_id}}[
                binding["kind"]
            ]
            if binding["target_id"] not in expected:
                raise AppError(
                    "media_scope", "Привязка относится к неизвестному NPC/сцене/событию/миру.", 422
                )
        for variant in media["variants"]:
            if variant["character_id"] not in actors or not lookup(variant["asset_id"])["type"].startswith(
                "image/"
            ):
                raise AppError("media_scope", "Вариант требует NPC этой истории и изображение.", 422)
        for scene in media["compositions"].values():
            for layer in scene["layers"]:
                if (layer["character_id"] and layer["character_id"] not in actors) or not lookup(
                    layer["asset_id"]
                )["type"].startswith("image/"):
                    raise AppError("media_scope", "Слой требует изображение и NPC этой истории.", 422)
            for track in (scene["music"], scene["ambient"]):
                if track["asset_id"] and not lookup(track["asset_id"])["type"].startswith("audio/"):
                    raise AppError("media_type_mismatch", "Звуковая дорожка требует аудиофайл.", 422)
        for aid in asset_ids(media):
            lookup(aid)

    def read_scene(self, sid, bid):
        story, branch, state = self.worlds.snapshot(sid, bid)
        media = media_value(state["payload"])
        scene_id = state["payload"]["scene"]["id"]
        from .media_models import Composition

        composition = copy.deepcopy(
            media["compositions"].get(scene_id) or Composition().model_dump(mode="json")
        )
        resolved = []
        for layer in composition["layers"]:
            value = copy.deepcopy(layer)
            actor = layer["character_id"]
            if actor:
                if actor not in state["payload"]["scene"]["participants"]:
                    value["enabled"] = False
                npc = npc_value(story, state, actor)
                emotion = (
                    npc["emotion"]["label"]
                    if layer["emotion"] == "auto" and npc["emotions_enabled"]
                    else layer["emotion"]
                )
                candidates = [
                    v
                    for v in media["variants"]
                    if v["character_id"] == actor
                    and all(
                        not v[k] or v[k] == desired
                        for k, desired in [
                            ("costume", layer["costume"]),
                            ("pose", layer["pose"]),
                            ("emotion", emotion),
                        ]
                    )
                ]
                if candidates:
                    chosen = max(
                        candidates,
                        key=lambda v: (
                            sum(bool(v[k]) for k in ("costume", "pose", "emotion")),
                            v["priority"],
                            v["id"],
                        ),
                    )
                    value["asset_id"] = chosen["asset_id"]
                    value["variant_id"] = chosen["id"]
            resolved.append(value)
        return {
            "media": media,
            "composition": composition,
            "resolved_layers": resolved,
            "scene_id": scene_id,
            "branch_revision": branch["revision"],
        }

    def save_scene(self, sid, bid, revision, media, reason):
        story, branch, state = self.worlds.snapshot(sid, bid, revision)
        data = copy.deepcopy(state["payload"])
        media = MediaState.model_validate(media).model_dump(mode="json")
        current = media_value(data)
        if not set(media["compositions"]) <= set(current["compositions"]) | {data["scene"]["id"]}:
            raise AppError("media_scope", "Новая композиция должна относиться к текущей сцене.", 422)
        if media["history"] != current["history"]:
            raise AppError(
                "media_history_readonly", "История изменения сцены редактируется только backend.", 422
            )
        cache = {}

        def lookup(aid):
            if aid not in cache:
                cache[aid] = self.get(aid)
            return cache[aid]["payload"]

        self.validate_scope(story, state, media, lookup)
        # Asset metadata cannot concurrently change type/archive while binding is published.
        guards = {
            self.chat.story_key(sid): story["revision"],
            **{self.key(k): v["revision"] for k, v in cache.items()},
        }
        media["history"].append(
            {
                "id": str(uuid4()),
                "reason": reason,
                "created_at": now(),
                "branch_id": bid,
                "before": {k: v for k, v in current.items() if k != "history"},
                "after": {k: v for k, v in media.items() if k != "history"},
            }
        )
        data["media"] = media
        self.chat.update_state(sid, data, revision, bid, guards=guards)
        return {"branch_revision": revision + 1}

    @staticmethod
    def clone_scope(data, mapping, branch_id, source_branch_id=None):
        media = data.get("media")
        if not media:
            return
        for binding in media.get("bindings", []):
            if binding["kind"] == "event":
                binding["target_id"] = mapping.get(binding["target_id"], binding["target_id"])
