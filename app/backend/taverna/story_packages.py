from __future__ import annotations

import copy
import io
import re
from zipfile import ZIP_DEFLATED, BadZipFile, ZipFile

import jsonschema

from .cards import parse_json
from .characters import CHARACTER_SCHEMA
from .core import AppError
from .director_models import DirectorState, NPCState
from .knowledge import player_id
from .media import MediaService, asset_ids, media_value
from .media_models import Asset
from .media_probe import probe_media
from .profiles import DEFAULTS, merge, validate_settings
from .rpg import RpgService, rpg_value
from .storage import Mutation, atomic_write, digest, document, encoded, safe_path
from .world_models import Scenario, World
from .world_packages import validate_scenario

MAX_UNPACKED = 64 * 1024 * 1024
MAX_ARCHIVE = 16 * 1024 * 1024


class StoryPackages:
    """A complete branch/state graph, with explicit ID conflicts instead of destructive replacement."""

    def __init__(self, chat):
        self.chat, self.store = chat, chat.store

    def export(self, story_id):
        with self.store.view() as view:
            story = view.get(self.chat.story_key(story_id))
            if not story:
                raise AppError("story_missing", "История не найдена.", 404)
            keys = {self.chat.story_key(story_id)}
            keys.update(key for key in view.manifest["entries"] if key.startswith(f"chats/{story_id}/"))
            branches = [
                view.get(key) for key in sorted(keys) if key.startswith(f"chats/{story_id}/branches/")
            ]
            for branch in branches:
                keys.update(
                    key for key in view.manifest["entries"] if key.startswith(f"chats/{branch['id']}/")
                )
            docs = {key: view.get(key) for key in sorted(keys)}
            media_ids = {
                snap["card"].get("portrait_id") for snap in story["payload"]["character_snapshots"].values()
            }
            for value in docs.values():
                if value["kind"] == "branch_state":
                    media_ids.update(asset_ids(media_value(value["payload"])))
                if value["kind"] == "message_segment":
                    media_ids.update(
                        fid for message in value["payload"]["messages"] for fid in message.get("file_ids", [])
                    )
            binding_key = self.chat.generation.profiles.binding_key("story", story_id)
            binding = view.get(binding_key)
            profile_ids = {binding["payload"]["profile_id"]} if binding else set()
            if binding:
                docs[binding_key] = binding
            for value in list(docs.values()):
                if value["kind"] == "message_segment":
                    profile_ids.update(
                        message.get("prompt_profile_id") for message in value["payload"]["messages"]
                    )
            for profile_id in profile_ids - {None}:
                key = self.chat.generation.profiles.key(profile_id)
                value = view.get(key)
                if value:
                    docs[key] = value
            assets = {}
            for media_id in media_ids - {None}:
                key = f"media/{media_id}.json"
                media = view.get(key)
                if not media:
                    raise AppError(
                        "source_missing", "Медиа истории отсутствует; неполный пакет не создан.", 409
                    )
                payload = media["payload"]
                raw = safe_path(self.store.root, payload["relative_path"]).read_bytes()
                if probe_media(raw)["type"] != payload["type"]:
                    raise AppError("media_type_mismatch", "Тип медиа не соответствует содержимому.", 422)
                if digest(raw) != payload["checksum"]:
                    raise AppError("document_corrupt", "Контрольная сумма изображения не совпала.", 503)
                docs[key] = media
                assets["assets/" + digest(raw) + ".bin"] = raw
        manifest = {
            "format": "taverna-story",
            "version": 1,
            "story_id": story_id,
            "documents": [],
            "assets": {name: digest(raw) for name, raw in assets.items()},
        }
        files = dict(assets)
        for index, (key, value) in enumerate(sorted(docs.items())):
            name = f"documents/{index:06}.json"
            raw = encoded(value)
            manifest["documents"].append({"key": key, "file": name, "sha256": digest(raw)})
            files[name] = raw
        files["manifest.json"] = encoded(manifest)
        if len(files) > 10000 or sum(map(len, files.values())) > MAX_UNPACKED:
            raise AppError("package_limit", "История превышает лимит пакета64МиБ/10000 файлов.", 422)
        buffer = io.BytesIO()
        with ZipFile(buffer, "w", ZIP_DEFLATED) as archive:
            for name, raw in sorted(files.items()):
                archive.writestr(name, raw)
        result = buffer.getvalue()
        if len(result) > MAX_ARCHIVE:
            raise AppError(
                "package_limit", "Сжатый пакет превышает лимит16МиБ; непереносимый пакет не выдан.", 422
            )
        return result

    def decode(self, raw):
        try:
            if len(raw) > MAX_ARCHIVE:
                raise ValueError("size")
            with ZipFile(io.BytesIO(raw)) as archive:
                infos = archive.infolist()
                names = [info.filename for info in infos]
                if (
                    len(names) != len(set(names))
                    or len(names) > 10000
                    or sum(info.file_size for info in infos) > MAX_UNPACKED
                ):
                    raise ValueError("limits")
                if any(
                    (info.external_attr >> 16) & 0o170000 == 0o120000
                    or info.flag_bits & 1
                    or not re.fullmatch(
                        r"(?:manifest\.json|documents/\d{6}\.json|assets/[a-f0-9]{64}\.bin)", info.filename
                    )
                    for info in infos
                ):
                    raise ValueError("entry")
                manifest = parse_json(archive.read("manifest.json"))
                if (
                    manifest.get("format") != "taverna-story"
                    or manifest.get("version") != 1
                    or not isinstance(manifest.get("documents"), list)
                    or not isinstance(manifest.get("assets"), dict)
                ):
                    raise ValueError("manifest")
                expected = {"manifest.json", *manifest["assets"]}
                expected.update(entry["file"] for entry in manifest["documents"])
                if set(names) != expected:
                    raise ValueError("files")
                docs, assets = {}, {}
                for entry in manifest["documents"]:
                    if entry["key"] in docs:
                        raise ValueError("duplicate document")
                    data = archive.read(entry["file"])
                    if digest(data) != entry["sha256"]:
                        raise ValueError("checksum")
                    value = parse_json(data)
                    self.store.validate(value)
                    docs[entry["key"]] = value
                for name, checksum in manifest["assets"].items():
                    data = archive.read(name)
                    if digest(data) != checksum or name != f"assets/{checksum}.bin":
                        raise ValueError("checksum")
                    probe_media(data)
                    assets[checksum] = data
            self.validate_graph(manifest["story_id"], docs, assets)
            return {
                "story_id": manifest["story_id"],
                "sha256": digest(raw),
                "documents": docs,
                "assets": assets,
            }
        except (
            BadZipFile,
            KeyError,
            TypeError,
            ValueError,
            AttributeError,
            OSError,
            RuntimeError,
            jsonschema.ValidationError,
        ) as exc:
            raise AppError(
                "invalid_story_package",
                "Пакет истории повреждён, превышает лимит или имеет недопустимые ссылки.",
                422,
            ) from exc

    def validate_graph(self, story_id, docs, assets):
        story = docs[self.chat.story_key(story_id)]
        if story["kind"] != "story" or story["id"] != story_id:
            raise ValueError("story")
        branches = {
            v["id"]: v
            for k, v in docs.items()
            if k.startswith(f"chats/{story_id}/branches/") and v["kind"] == "branch"
        }
        if story["payload"]["active_branch"] not in branches:
            raise ValueError("active branch")
        states = {
            v["id"]: v
            for k, v in docs.items()
            if k.startswith(f"chats/{story_id}/states/") and v["kind"] == "branch_state"
        }
        for state in states.values():
            payload = state["payload"]
            actors = set(story["payload"]["character_snapshots"])
            media = media_value(payload)
            branch_scenes = {
                v["payload"]["scene"]["id"]
                for v in states.values()
                if v["payload"]["branch_id"] == payload["branch_id"]
            }
            if not set(media["compositions"]) <= branch_scenes:
                raise ValueError("media scene scope")

            def lookup_media(aid):
                value = docs.get(f"media/{aid}.json")
                if not value or value["kind"] != "media_asset":
                    raise ValueError("media reference missing")
                return Asset.model_validate(value["payload"]).model_dump(mode="json")

            MediaService.validate_scope(story, state, media, lookup_media)
            rpg = rpg_value(payload)
            RpgService.validate_sheets(rpg, actors | {player_id(story_id)})
            for actor, npc in payload["character_states"].items():
                if not isinstance(npc, dict):
                    raise ValueError("npc shape")
                if npc.get("format") is not None:
                    if actor not in actors:
                        raise ValueError("npc scope")
                    NPCState.model_validate(npc)
            if "director" in payload:
                director = DirectorState.model_validate(payload["director"]).model_dump(mode="json")
                if (
                    not set(director["policy"]["actor_entities"]) <= actors
                    or not set(director["policy"]["actor_attribute_keys"]) <= actors
                    or any(not set(arc["knower_ids"]) <= actors for arc in director["arcs"].values())
                ):
                    raise ValueError("director scope")
            for event in payload.get("events", []):
                if not isinstance(event, dict):
                    continue
                if event.get("id") is not None:
                    from uuid import UUID

                    UUID(event["id"])
                for field in ("source_ids", "cause_ids", "knower_ids"):
                    if field in event and (
                        not isinstance(event[field], list)
                        or any(not isinstance(item, str) for item in event[field])
                    ):
                        raise ValueError("event links")
            world = payload.get("world", {})
            if world.get("format") == 1:
                source = world["template"]
                self.store.validate(source)
                template = World.model_validate(source["payload"]).model_dump(mode="json")
                if (
                    source["kind"] != "world_template"
                    or type(world["clock"]["minutes"]) is not int
                    or not 0 <= world["clock"]["minutes"] <= 1000000000000
                ):
                    raise ValueError("world snapshot")
                if any(key != entity["id"] for key, entity in world["entities"].items()):
                    raise ValueError("entity identity")
                World.model_validate(
                    {
                        **template,
                        "entities": list(world["entities"].values()),
                        "flags": world["flags"],
                        "resources": world["resources"],
                    }
                )
                if not isinstance(world["arcs"], dict) or any(
                    not isinstance(arc, dict) or arc.get("status") not in ("open", "resolved")
                    for arc in world["arcs"].values()
                ):
                    raise ValueError("arcs")
                for run in world["rule_runs"].values():
                    if (
                        not isinstance(run, dict)
                        or type(run.get("minutes")) is not int
                        or not isinstance(run.get("event_id"), str)
                    ):
                        raise ValueError("rule receipt")
                for receipt in world["applied_events"].values():
                    if not isinstance(receipt, dict) or not re.fullmatch(
                        r"[a-f0-9]{64}", receipt.get("fingerprint", "")
                    ):
                        raise ValueError("event receipt")
                if world.get("scenario"):
                    self.store.validate(world["scenario"])
                    scenario = Scenario.model_validate(world["scenario"]["payload"]).model_dump(mode="json")
                    if (
                        world["scenario"]["kind"] != "scenario_template"
                        or scenario["world_id"] != source["id"]
                    ):
                        raise ValueError("scenario snapshot")
                    validate_scenario(template, scenario)
        for key, value in docs.items():
            allowed = key == self.chat.story_key(story_id) and value["kind"] == "story"
            if value["kind"] == "branch":
                allowed = (
                    key == self.chat.branch_key(story_id, value["id"])
                    and value["payload"]["story_id"] == story_id
                )
            elif value["kind"] == "branch_state":
                allowed = (
                    key == self.chat.state_key(story_id, value["id"])
                    and value["payload"]["story_id"] == story_id
                    and value["payload"]["branch_id"] in branches
                )
            elif value["kind"] == "message_segments":
                allowed = any(key == self.chat.segments._key(bid) for bid in branches)
            elif value["kind"] == "message_segment":
                allowed = any(
                    re.fullmatch(rf"chats/{bid}/messages/[a-f0-9-]{{36}}\.json", key) for bid in branches
                )
            elif value["kind"] == "generation_profile":
                allowed = key == self.chat.generation.profiles.key(value["id"])
                validate_settings(merge(DEFAULTS, value["payload"]["patch"]))
            elif value["kind"] == "profile_binding":
                payload = value["payload"]
                allowed = (
                    key == self.chat.generation.profiles.binding_key("story", story_id)
                    and payload.get("scope") == "story"
                    and payload.get("scope_id") == story_id
                    and self.chat.generation.profiles.key(payload["profile_id"]) in docs
                )
            elif value["kind"] == "media_asset":
                payload = Asset.model_validate(value["payload"]).model_dump(mode="json")
                raw_asset = assets.get(payload["checksum"])
                if raw_asset is None:
                    raise ValueError("media bytes missing")
                info = probe_media(raw_asset)
                paths = {f"media/assets/{payload['checksum']}.{info['extension']}"}
                if info["type"] == "image/png":
                    paths.add(f"media/portraits/{payload['checksum']}.png")
                allowed = (
                    key == f"media/{value['id']}.json"
                    and payload["type"] == info["type"]
                    and payload["relative_path"] in paths
                )
            if not allowed or set(value.get("refs", [])) - docs.keys():
                raise ValueError("scope or reference")
        for bid, branch in branches.items():
            for field in ("state_id", "initial_state_id"):
                if states[branch["payload"][field]]["payload"]["branch_id"] != bid:
                    raise ValueError("state scope")
            parent, seen = branch["payload"].get("parent_id"), {bid}
            while parent:
                if parent not in branches or parent in seen:
                    raise ValueError("branch cycle")
                seen.add(parent)
                parent = branches[parent]["payload"].get("parent_id")
            head = docs.get(self.chat.segments._key(bid))
            messages = []
            if head and (
                head["kind"] != "message_segments"
                or not isinstance(head["payload"].get("segments"), list)
                or type(head["payload"].get("count")) is not int
                or len(head["payload"]["segments"]) != len(set(head["payload"]["segments"]))
            ):
                raise ValueError("message index")
            for key in head["payload"]["segments"] if head else []:
                segment = docs[key]
                if segment["kind"] != "message_segment" or not key.startswith(f"chats/{bid}/messages/"):
                    raise ValueError("segment")
                for message in segment["payload"]["messages"]:
                    self.chat.message_validator.validate(message)
                    present = message.get("present_ids", [])
                    if (
                        not isinstance(present, list)
                        or any(
                            not isinstance(cid, str) or cid not in story["payload"]["character_snapshots"]
                            for cid in present
                        )
                        or message["role"] == "assistant"
                        and message["author_id"] not in story["payload"]["character_snapshots"]
                    ):
                        raise ValueError("message author or presence")
                    if (
                        message["story_id"] != story_id
                        or message["branch_id"] != bid
                        or states[message["state_id"]]["payload"]["branch_id"] != bid
                    ):
                        raise ValueError("message scope")
                    messages.append(message)
            if (
                head
                and len(messages) != head["payload"]["count"]
                or len({m["id"] for m in messages}) != len(messages)
            ):
                raise ValueError("count")
            if branch["payload"]["head_message_id"] != (messages[-1]["id"] if messages else None):
                raise ValueError("head")
        for snap in story["payload"]["character_snapshots"].values():
            jsonschema.Draft202012Validator(
                CHARACTER_SCHEMA, format_checker=jsonschema.FormatChecker()
            ).validate(snap["card"])
            portrait = snap["card"].get("portrait_id")
            if portrait and f"media/{portrait}.json" not in docs:
                raise ValueError("portrait missing")

    def preview(self, raw):
        package = self.decode(raw)
        conflicts, reused = [], []
        with self.store.view() as view:
            for key, value in package["documents"].items():
                existing = view.get(key)
                if existing:
                    (
                        reused
                        if existing["kind"] == value["kind"] and existing["payload"] == value["payload"]
                        else conflicts
                    ).append(key)
        return {
            "story_id": package["story_id"],
            "sha256": package["sha256"],
            "name": package["documents"][self.chat.story_key(package["story_id"])]["payload"]["name"],
            "document_count": len(package["documents"]),
            "asset_count": len(package["assets"]),
            "conflicts": conflicts,
            "reused": reused,
            "warnings": [
                "ID сохраняются. Отличающиеся существующие данные не заменяются; для переноса используйте отдельный каталог данных. Назначение профиля истории и доступные профили сообщений включены. Глобальные/служебные/персонажные назначения наследуются из целевой установки. Диагностические запросы, задания и черновики не входят; их ID сохраняются как происхождение."
            ],
        }

    def apply(self, raw, confirmed, expected_sha256):
        package = self.decode(raw)
        if not confirmed or package["sha256"] != expected_sha256:
            raise AppError(
                "confirmation_required", "Подтвердите неизменённый пакет после предпросмотра.", 422
            )
        changes, guards = [], {}
        with self.store.view() as view:
            for key, source in package["documents"].items():
                existing = view.get(key)
                if existing:
                    if existing["kind"] != source["kind"] or existing["payload"] != source["payload"]:
                        raise AppError(
                            "story_package_conflict",
                            "Существующие данные отличаются. Импорт не заменяет историю.",
                            409,
                            key=key,
                        )
                    guards[key] = existing["revision"]
                else:
                    value = copy.deepcopy(source)
                    changes.append(Mutation(key, value, 0))
        for value in package["documents"].values():
            if value["kind"] != "media_asset":
                continue
            payload = value["payload"]
            raw_asset = package["assets"][payload["checksum"]]
            path = safe_path(self.store.root, payload["relative_path"])
            if path.exists() and digest(path.read_bytes()) != payload["checksum"]:
                raise AppError(
                    "story_package_conflict", "Локальный медиафайл повреждён. Проверьте восстановление.", 409
                )
            if not path.exists():
                atomic_write(path, raw_asset)
        # Orphan immutable assets after a rejected CAS are harmless; no partial authoritative graph is published.
        if changes:
            metadata = {
                "story_id": package["story_id"],
                "sha256": package["sha256"],
                "source_envelopes": {
                    key: {"revision": value["revision"], "updated_at": value["updated_at"]}
                    for key, value in package["documents"].items()
                },
            }
            changes.append(
                Mutation(
                    f"chats/packages/{package['sha256']}.json",
                    document("story_import", metadata, refs=[self.chat.story_key(package["story_id"])]),
                    0,
                )
            )
            self.store.transaction(changes, guards=guards)
        return {
            "story_id": package["story_id"],
            "imported_documents": len(changes),
            "already_present": not changes,
        }
