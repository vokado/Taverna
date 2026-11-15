from __future__ import annotations

import copy
from uuid import NAMESPACE_URL, uuid4, uuid5

from .chat import identifier
from .core import AppError
from .director_models import DirectorAction, DirectorPolicy, DirectorProposal, NPCState
from .knowledge import RecordInput, player_id, text_hash
from .npc_context import actor_scene_id, derived_reason, npc_value
from .storage import Mutation, digest, document, encoded, now


class DirectorService:
    def __init__(self, memory, worlds):
        self.memory, self.worlds = memory, worlds
        self.chat, self.store, self.generation = memory.chat, memory.store, memory.generation
        self.chat.state_cloners["director"] = self.clone_scope
        self.chat.message_state_hooks["director"] = self.message_state
        self.worlds.after_reduce = self.reconcile_presence
        self.store.register("director_proposal", 1, DirectorProposal.model_json_schema())

    @staticmethod
    def director(data):
        value = data.setdefault("director", {"format": 1, "policy": {}, "arcs": {}, "audit": []})
        if value.get("format") != 1:
            raise AppError("unsupported_schema", "Неизвестная версия состояния режиссёра.", 409)
        return value

    def snapshot(self, story_id, branch_id, expected_revision=None):
        snap = self.memory.snapshot(story_id, branch_id)
        if snap["story"]["payload"]["active_branch"] != branch_id:
            raise AppError("inactive_branch", "Ветвь больше не активна.", 409)
        if expected_revision is not None and snap["branch"]["revision"] != expected_revision:
            raise AppError("branch_conflict", "Ветвь изменилась.", 409)
        return snap

    @staticmethod
    def npc(snap, actor):
        return npc_value(snap["story"], snap["state"], actor)

    @staticmethod
    def policy(snap):
        raw = snap["state"]["payload"].get("director", {})
        return DirectorPolicy.model_validate(raw.get("policy", {})).model_dump(mode="json")

    def read(self, story_id, branch_id):
        snap = self.snapshot(story_id, branch_id)
        return {
            "policy": self.policy(snap),
            "characters": {
                actor: self.npc(snap, actor) for actor in snap["story"]["payload"]["character_snapshots"]
            },
            "director": copy.deepcopy(snap["state"]["payload"].get("director", {})),
            "branch_revision": snap["branch"]["revision"],
        }

    def write_policy(self, story_id, branch_id, expected_revision, policy, reason):
        snap = self.snapshot(story_id, branch_id, expected_revision)
        policy = DirectorPolicy.model_validate(policy).model_dump(mode="json")
        actors = set(snap["story"]["payload"]["character_snapshots"])
        entities = snap["state"]["payload"]["world"].get("entities", {})
        if (
            not set(policy["actor_entities"]) <= actors
            or not set(policy["actor_attribute_keys"]) <= actors
            or any(
                eid not in entities or entities[eid]["kind"] not in ("person", "faction")
                for eid in policy["actor_entities"].values()
            )
            or len(set(policy["actor_entities"].values())) != len(policy["actor_entities"])
        ):
            raise AppError(
                "actor_binding_required", "Свяжите разных NPC с существующими персонами/фракциями мира.", 422
            )
        if policy["profile_id"]:
            self.generation.profiles.get(policy["profile_id"])
        data = copy.deepcopy(snap["state"]["payload"])
        director = self.director(data)
        previous = copy.deepcopy(director["policy"])
        director["policy"] = policy
        director["audit"].append(
            {
                "id": str(uuid4()),
                "kind": "manual_policy",
                "before": previous,
                "after": policy,
                "reason": reason,
                "created_at": now(),
            }
        )
        self.commit(snap, data)
        return {"policy": policy, "branch_revision": expected_revision + 1}

    def write_npc(self, story_id, branch_id, actor, expected_revision, patch, reason):
        snap = self.snapshot(story_id, branch_id, expected_revision)
        previous = self.npc(snap, actor)
        if set(patch) - {
            "mode",
            "emotions_enabled",
            "relationships_enabled",
            "emotion",
            "traits",
            "adaptations",
        }:
            raise AppError("invalid_npc_state", "Изменять историю или служебные поля напрямую нельзя.", 422)
        try:
            npc = NPCState.model_validate({**previous, **patch}).model_dump(mode="json")
        except ValueError as exc:
            raise AppError("invalid_npc_state", "Некорректное состояние NPC.", 422) from exc
        if "adaptations" in patch:
            npc["adaptation_scene_id"] = actor_scene_id(snap["story"], snap["state"], actor)
        self.history(npc, previous, reason, "manual", [])
        data = copy.deepcopy(snap["state"]["payload"])
        data["character_states"][actor] = npc
        self.director(data)["audit"].append(
            {
                "id": str(uuid4()),
                "kind": "manual_npc",
                "actor_id": actor,
                "reason": reason,
                "created_at": now(),
            }
        )
        self.commit(snap, data)
        return {"character": npc, "branch_revision": expected_revision + 1}

    @staticmethod
    def history(npc, before, reason, mode, sources, provenance=None):
        npc["history"].append(
            {
                "id": str(uuid4()),
                "created_at": now(),
                "reason": reason,
                "mode": mode,
                "source_ids": list(sources),
                "provenance": copy.deepcopy(provenance or {}),
                "before": {k: copy.deepcopy(v) for k, v in before.items() if k not in ("history", "extras")},
                "after": {k: copy.deepcopy(v) for k, v in npc.items() if k not in ("history", "extras")},
            }
        )

    def commit(self, snap, data, extra_changes=(), before_commit=None):
        return self.chat.update_state(
            snap["story"]["id"],
            data,
            snap["branch"]["revision"],
            snap["branch"]["id"],
            extra_changes=extra_changes,
            before_commit=before_commit,
            guards={self.chat.story_key(snap["story"]["id"]): snap["story"]["revision"]},
        )

    def known_locations(self, snap, actor):
        world = snap["state"]["payload"]["world"]
        policy = self.policy(snap)
        entity = world.get("entities", {}).get(policy["actor_entities"].get(actor), {})
        known = {entity.get("location_id")}
        for record in self.memory.policy(snap).visible(actor):
            known.add(record.get("location_id"))
        for entry in world.get("template", {}).get("payload", {}).get("lore", []):
            if entry["enabled"] and (entry["public"] or actor in entry["knower_ids"]):
                known.update(entry["entity_ids"])
        return known - {None, ""}

    def sources(self, snap, actor, action):
        action = action.model_dump(mode="json")
        hashes = {}
        for mid in action["source_ids"]:
            message = snap["messages"].get(mid)
            if (
                not message
                or actor not in message.get("present_ids", [])
                and message.get("author_id") != actor
            ):
                raise AppError("knowledge_scope", "Режиссёр сослался на ненаблюдавшуюся реплику.", 422)
            hashes[mid] = text_hash(message["text"])
        knowledge = self.memory.policy(snap)
        for fid, version in action["source_fact_versions"].items():
            record = snap["records"].get(fid)
            if not record or record["version"] != version or knowledge.reason(record, actor):
                raise AppError("knowledge_scope", "Исходный факт недоступен или изменился.", 422)
        return hashes

    def reduce_actions(self, snap, actor, actions, proposal_id):
        data = copy.deepcopy(snap["state"]["payload"])
        npc, policy = self.npc(snap, actor), self.policy(snap)
        scene_id = actor_scene_id(snap["story"], snap["state"], actor)
        if actor not in snap["branch"]["payload"]["participants"] and not policy["offscene"]:
            raise AppError("director_disabled", "Действия отсутствующих NPC отключены.", 409)
        director = self.director(data)
        working = {
            **snap,
            "state": {**snap["state"], "payload": data},
            "records": copy.deepcopy(snap["records"]),
        }
        world_effects, receipts, all_sources = [], [], set()
        deltas = {}
        for raw in actions:
            action = DirectorAction.model_validate(raw)
            source_hashes = self.sources(snap, actor, action)
            all_sources.update(source_hashes)
            before = copy.deepcopy(npc)
            derived = {
                "source_ids": list(source_hashes),
                "source_hashes": source_hashes,
                "source_fact_versions": {str(k): v for k, v in action.source_fact_versions.items()},
                "source_card_sha256": digest(encoded(snap["story"]["payload"]["character_snapshots"][actor]))
                if action.source_card
                else None,
            }
            result = {"kind": action.kind}
            if action.kind == "emotion":
                if not npc["emotions_enabled"]:
                    raise AppError("emotions_disabled", "Автоматические эмоции отключены для NPC.", 409)
                npc["emotion"] = action.emotion.model_dump(mode="json")
            elif action.kind in ("adaptation", "evolution"):
                required = "flexible" if action.kind == "adaptation" else "developing"
                if npc["mode"] != required:
                    raise AppError(
                        "npc_mode_conflict", "Изменение не разрешено текущим режимом характера.", 409
                    )
                deltas[action.trait] = deltas.get(action.trait, 0) + abs(action.delta)
                if deltas[action.trait] > 0.1 + 1e-9:
                    raise AppError(
                        "invalid_director_proposal",
                        "Суммарный сдвиг тенденции превышает0.1 за предложение.",
                        422,
                    )
                values = npc["adaptations" if action.kind == "adaptation" else "traits"]
                if action.kind == "adaptation" and npc["adaptation_scene_id"] != scene_id:
                    npc["adaptations"] = values = {}
                npc["adaptation_scene_id"] = (
                    scene_id if action.kind == "adaptation" else npc["adaptation_scene_id"]
                )
                values[action.trait] = round(max(-1, min(1, values.get(action.trait, 0) + action.delta)), 6)
            elif action.kind in ("relationship", "knowledge"):
                value = action.record.model_dump(mode="json")
                if (
                    set(value["knower_ids"]) != {actor}
                    or value["source_ids"] != list(source_hashes)
                    or value["source_fact_versions"]
                    != {str(k): v for k, v in action.source_fact_versions.items()}
                ):
                    raise AppError(
                        "knowledge_scope", "Знание и его источники должны принадлежать выбранному NPC.", 422
                    )
                if value["status"] != "active" or value["pinned"]:
                    raise AppError(
                        "invalid_director_proposal",
                        "Модель не архивирует записи и не назначает закрепление.",
                        422,
                    )
                if action.kind == "relationship":
                    seen = {actor}
                    for message in snap["messages"].values():
                        if actor in message.get("present_ids", []) or message.get("author_id") == actor:
                            seen.update(message.get("present_ids", []))
                            if message["role"] == "user":
                                seen.add(player_id(snap["story"]["id"]))
                    for known in self.memory.policy(snap).visible(actor):
                        seen.update(known["subject_ids"])
                        seen.update((known.get("relation") or {}).get("parties", []))
                    if not set((value.get("relation") or {}).get("parties", [])) <= seen:
                        raise AppError("knowledge_scope", "Участник отношения ещё неизвестен NPC.", 422)
                    if not npc["relationships_enabled"]:
                        raise AppError("relationships_disabled", "Автоматические отношения отключены.", 409)
                    if value["level"] != "relationship" or actor not in (value.get("relation") or {}).get(
                        "parties", []
                    ):
                        raise AppError(
                            "invalid_director_proposal", "Отношение должно включать выбранного NPC.", 422
                        )
                elif value["level"] == "relationship":
                    raise AppError(
                        "invalid_director_proposal", "Изменяйте отношения отдельным типом действия.", 422
                    )
                record_id = str(action.record_id) if action.record_id else None
                old = working["records"].get(record_id) if record_id else None
                if old and (
                    self.memory.policy(working).reason(old, actor)
                    or old["version"] != action.expected_record_version
                ):
                    raise AppError(
                        "knowledge_scope", "Изменяемая запись недоступна или имеет другую версию.", 422
                    )
                if old and old["pinned"]:
                    raise AppError("memory_conflict", "Закреплённую запись изменяет владелец вручную.", 409)
                if action.kind == "relationship":
                    previous = (old.get("relation") or {}).get("metrics", {}) if old else {}
                    if old and set(value["relation"]["parties"]) != set(old["relation"]["parties"]):
                        raise AppError(
                            "memory_conflict", "Участники существующего отношения не меняются моделью.", 409
                        )
                    if any(
                        abs(metric - previous.get(name, 0)) > 10
                        for name, metric in value["relation"]["metrics"].items()
                    ) or set(previous) - set(value["relation"]["metrics"]):
                        raise AppError(
                            "invalid_director_proposal",
                            "Изменение показателей отношений превышает предел10 или удаляет показатели.",
                            422,
                        )
                value["extras"]["director_proposal_id"] = proposal_id
                if action.source_card:
                    value["extras"]["source_card"] = {
                        "character_id": actor,
                        "revision": snap["story"]["payload"]["character_snapshots"][actor]["revision"],
                    }
                record = self.memory._prepare(
                    working,
                    RecordInput.model_validate(value),
                    record_id=record_id,
                    expected_version=action.expected_record_version,
                    reason=action.reason,
                    mode="model",
                    source_hashes=source_hashes,
                )
                if self.memory.conflicts(working["records"], record):
                    raise AppError(
                        "memory_conflict",
                        "Режиссёр предложил противоречащую запись. Разрешите её вручную.",
                        409,
                    )
                data["relationships" if record["level"] == "relationship" else "memory"][record["id"]] = (
                    record
                )
                working["records"][record["id"]] = record
                result["record_id"] = record["id"]
            elif action.kind == "world_action":
                effect = action.effect.model_dump(mode="json")
                bound = policy["actor_entities"].get(actor)
                if (
                    not bound
                    or effect["entity_id"] != bound
                    or effect["op"] not in ("move_entity", "set_attribute")
                ):
                    raise AppError(
                        "actor_binding_required",
                        "NPC может менять только явно связанную собственную сущность.",
                        422,
                    )
                if effect["op"] == "move_entity" and effect["value"] not in self.known_locations(snap, actor):
                    raise AppError("knowledge_scope", "Целевая локация неизвестна NPC.", 422)
                if effect["op"] == "set_attribute" and effect["key"] not in policy[
                    "actor_attribute_keys"
                ].get(actor, []):
                    raise AppError(
                        "actor_binding_required", "Изменение этого атрибута не разрешено владельцем.", 422
                    )
                if effect["op"] == "move_entity":
                    world = data["world"]
                    current_location = world["entities"][bound].get("location_id")
                    if current_location != effect["value"] and not any(
                        link["kind"] in ("path", "road", "route", "путь", "дорога", "маршрут")
                        and {link["source_id"], link["target_id"]} == {current_location, effect["value"]}
                        for link in world["template"]["payload"]["links"]
                    ):
                        raise AppError(
                            "world_state_conflict", "Между локациями нет разрешённого маршрута.", 409
                        )
                world_effects.append(effect)
            elif action.kind == "initiative":
                npc["intentions"] = [
                    *npc["intentions"][-99:],
                    {
                        "id": str(uuid4()),
                        "text": action.intent,
                        **derived,
                        "proposal_id": proposal_id,
                        "scene_id": scene_id,
                        "status": "proposed_action",
                    },
                ]
            elif action.kind == "arc":
                arc_id = str(action.arc_id) if action.arc_id else str(uuid4())
                old = director["arcs"].get(arc_id)
                if action.resolve_arc and (
                    not old
                    or actor not in old["knower_ids"]
                    or derived_reason(
                        old,
                        actor,
                        self.memory.policy(snap),
                        digest(encoded(snap["story"]["payload"]["character_snapshots"][actor])),
                    )
                    is not None
                ):
                    raise AppError("knowledge_scope", "Линия отсутствует или неизвестна выбранному NPC.", 422)
                if old and (not action.resolve_arc or old["status"] != "open"):
                    raise AppError(
                        "world_state_conflict", "Сюжетная линия уже существует или разрешена.", 409
                    )
                director["arcs"][arc_id] = {
                    "id": arc_id,
                    "description": old["description"] if old else action.intent,
                    "resolution": action.intent if action.resolve_arc else None,
                    "status": "resolved" if action.resolve_arc else "open",
                    "knower_ids": [actor],
                    **derived,
                    "proposal_id": proposal_id,
                }
                result["arc_id"] = arc_id
            if npc != before:
                self.history(
                    npc,
                    before,
                    action.reason,
                    "model",
                    source_hashes,
                    {"proposal_id": proposal_id, **derived},
                )
            receipts.append(result)
        if world_effects:
            self.worlds.reduce(
                data,
                event_id=str(uuid5(NAMESPACE_URL, f"taverna/director-world/{proposal_id}")),
                operation="manual",
                effects=world_effects,
                reason="Подтверждено действие NPC",
                source_ids=sorted(all_sources),
                base_revision=snap["branch"]["revision"],
            )
        data["character_states"][actor] = NPCState.model_validate(npc).model_dump(mode="json")
        event = {
            "id": proposal_id,
            "story_id": snap["story"]["id"],
            "branch_id": snap["branch"]["id"],
            "kind": "director_applied",
            "actor_id": actor,
            "knower_ids": [actor],
            "source_ids": sorted(all_sources),
            "actions": receipts,
            "created_at": now(),
        }
        director["audit"].append(event)
        data["events"].append(event)
        return data, receipts

    def proposal_key(self, story_id, branch_id, proposal_id):
        return (
            f"events/director/{identifier(story_id)}/{identifier(branch_id)}/{identifier(proposal_id)}.json"
        )

    def propose(self, snap, actor, actions, *, proposal_id=None, notes="", trace_id=None):
        proposal_id = proposal_id or str(uuid4())
        self.npc(snap, actor)
        policy = self.policy(snap)
        if not policy["enabled"]:
            raise AppError("director_disabled", "Режиссёр отключён для этой ветви.", 409)
        if len(actions) > policy["max_actions"]:
            raise AppError("invalid_director_proposal", "Предложено слишком много действий.", 422)
        parsed = [DirectorAction.model_validate(action).model_dump(mode="json") for action in actions]
        error = None
        try:
            self.reduce_actions(snap, actor, parsed, proposal_id)
        except AppError as exc:
            error = {"code": exc.code, "message_ru": exc.message}
        hashes = {
            str(mid): text_hash(snap["messages"][str(mid)]["text"])
            for action in parsed
            for mid in action["source_ids"]
            if str(mid) in snap["messages"]
        }
        proposal = document(
            "director_proposal",
            {
                "story_id": snap["story"]["id"],
                "branch_id": snap["branch"]["id"],
                "character_id": actor,
                "base_revision": snap["branch"]["revision"],
                "status": "invalid" if error else "pending",
                "actions": parsed,
                "notes": notes,
                "error": error,
                "source_hashes": hashes,
                "context_trace_id": trace_id,
                "policy_sha256": digest(encoded(policy)),
                "base_npc_sha256": digest(encoded(self.npc(snap, actor))),
                "base_entity_sha256": digest(
                    encoded(
                        snap["state"]["payload"]["world"]
                        .get("entities", {})
                        .get(policy["actor_entities"].get(actor))
                    )
                )
                if any(action["kind"] == "world_action" for action in parsed)
                else None,
            },
            entity_id=proposal_id,
        )
        key = self.proposal_key(snap["story"]["id"], snap["branch"]["id"], proposal_id)
        self.store.transaction(
            [Mutation(key, proposal, 0)],
            guards={
                self.chat.branch_key(snap["story"]["id"], snap["branch"]["id"]): snap["branch"]["revision"],
                self.chat.story_key(snap["story"]["id"]): snap["story"]["revision"],
            },
        )
        return self.store.get(key)

    def review(
        self,
        story_id,
        branch_id,
        proposal_id,
        *,
        expected_revision,
        proposal_revision,
        accept,
        confirmed=False,
        automatic=False,
        rebase=False,
        before_commit=None,
    ):
        if not confirmed:
            raise AppError("confirmation_required", "Подтвердите решение режиссёра.", 422)
        key = self.proposal_key(story_id, branch_id, proposal_id)
        proposal = self.store.get(key)
        if not proposal:
            raise AppError("proposal_missing", "Предложение режиссёра не найдено.", 404)
        if proposal["payload"]["status"] != "pending":
            return {"proposal": proposal, "already_reviewed": True}
        if proposal["revision"] != proposal_revision:
            raise AppError("storage_conflict", "Предложение изменилось.", 409)
        if not accept:
            proposal["payload"]["status"] = "rejected"
            self.store.transaction([Mutation(key, proposal, proposal_revision)])
            return {"proposal": self.store.get(key), "already_reviewed": False}
        snap = self.snapshot(story_id, branch_id, expected_revision)
        if proposal["payload"]["base_revision"] != expected_revision:
            if automatic or not rebase:
                raise AppError(
                    "generation_stale",
                    "Предложение относится к старой ревизии. Проверьте источники и явно подтвердите повторную привязку.",
                    409,
                )
            actor = proposal["payload"]["character_id"]
            if digest(encoded(self.npc(snap, actor))) != proposal["payload"]["base_npc_sha256"]:
                raise AppError(
                    "proposal_rebase_conflict",
                    "Состояние NPC изменилось после предложения. Создайте новый анализ либо исправьте состояние вручную.",
                    409,
                )
            original_entity = proposal["payload"].get("base_entity_sha256")
            current_policy = self.policy(snap)
            if original_entity is not None and original_entity != digest(
                encoded(
                    snap["state"]["payload"]["world"]
                    .get("entities", {})
                    .get(current_policy["actor_entities"].get(actor))
                )
            ):
                raise AppError("proposal_rebase_conflict", "Сущность NPC изменилась после предложения.", 409)
            proposal["payload"]["rebased_to_revision"] = expected_revision
        policy = self.policy(snap)
        if (
            not policy["enabled"]
            or automatic
            and (not policy["auto_apply"] or digest(encoded(policy)) != proposal["payload"]["policy_sha256"])
        ):
            raise AppError(
                "director_disabled", "Автоматическое применение не разрешено текущей политикой.", 409
            )
        for mid, checksum in proposal["payload"]["source_hashes"].items():
            if mid not in snap["messages"] or text_hash(snap["messages"][mid]["text"]) != checksum:
                raise AppError("source_changed", "Источник предложения изменился.", 409)
        if not proposal["payload"]["actions"]:
            proposal["payload"].update(status="accepted", automatic=automatic, receipts=[])
            self.store.transaction(
                [Mutation(key, proposal, proposal_revision)],
                guards={
                    self.chat.branch_key(story_id, branch_id): expected_revision,
                    self.chat.story_key(story_id): snap["story"]["revision"],
                },
            )
            return {
                "proposal": self.store.get(key),
                "branch_revision": expected_revision,
                "already_reviewed": False,
            }
        data, receipts = self.reduce_actions(
            snap, proposal["payload"]["character_id"], proposal["payload"]["actions"], proposal_id
        )
        proposal["payload"].update(status="accepted", automatic=automatic, receipts=receipts)
        self.commit(snap, data, [Mutation(key, proposal, proposal_revision)], before_commit=before_commit)
        return {
            "proposal": self.store.get(key),
            "branch_revision": expected_revision + 1,
            "already_reviewed": False,
        }

    @staticmethod
    def clone_scope(payload, mapping, branch_id, source_branch_id=None):
        director = payload.get("director", {})
        if director.get("format") != 1:
            return
        for event in [
            *director.get("audit", []),
            *(
                e
                for e in payload.get("events", [])
                if isinstance(e, dict) and e.get("kind") in ("director_applied", "initiative_turn_generated")
            ),
        ]:
            event.setdefault(
                "origin",
                {
                    "story_id": payload["story_id"],
                    "branch_id": event.get("branch_id", source_branch_id),
                    "source_ids": copy.deepcopy(event.get("source_ids", [])),
                },
            )
            event["branch_id"] = branch_id
            event["source_ids"] = [mapping.get(sid, sid) for sid in event.get("source_ids", [])]
        for arc in director.get("arcs", {}).values():
            arc["source_ids"] = [mapping.get(sid, sid) for sid in arc.get("source_ids", [])]
            arc["source_hashes"] = {
                mapping.get(sid, sid): checksum for sid, checksum in arc.get("source_hashes", {}).items()
            }
        for actor, npc in payload.get("character_states", {}).items():
            if not isinstance(npc, dict) or npc.get("format") != 1:
                continue
            old_scene = str(
                uuid5(NAMESPACE_URL, f"taverna/offscene/{payload['story_id']}/{source_branch_id}/{actor}")
            )
            new_scene = str(
                uuid5(NAMESPACE_URL, f"taverna/offscene/{payload['story_id']}/{branch_id}/{actor}")
            )
            if npc["adaptation_scene_id"] == old_scene:
                npc["adaptation_scene_id"] = new_scene
            for intent in npc["intentions"]:
                intent["source_ids"] = [mapping.get(sid, sid) for sid in intent.get("source_ids", [])]
                intent["source_hashes"] = {
                    mapping.get(sid, sid): checksum
                    for sid, checksum in intent.get("source_hashes", {}).items()
                }
                if intent.get("scene_id") == old_scene:
                    intent["scene_id"] = new_scene

    def intention(self, snap, actor, intention_id):
        if actor not in snap["branch"]["payload"]["participants"]:
            raise AppError("speaker_absent", "NPC отсутствует в сцене.", 409)
        npc = self.npc(snap, actor)
        intent = next((value for value in npc["intentions"] if value["id"] == intention_id), None)
        if (
            not intent
            or intent["status"] != "proposed_action"
            or intent["scene_id"] != snap["state"]["payload"]["scene"]["id"]
            or derived_reason(
                intent,
                actor,
                self.memory.policy(snap),
                digest(encoded(snap["story"]["payload"]["character_snapshots"][actor])),
            )
            is not None
        ):
            raise AppError(
                "initiative_invalid",
                "Намерение отсутствует, устарело, потеряло доступные источники или уже получило реплику.",
                409,
            )
        return intent

    def message_state(self, data, message, revision):
        intention_id = message.get("director_intention_id")
        if not intention_id:
            return None
        actor = message.get("author_id")
        snap = self.snapshot(data["story_id"], data["branch_id"], revision)
        self.intention(snap, actor, intention_id)
        if message["role"] != "assistant" or actor not in data["scene"]["participants"]:
            raise AppError(
                "initiative_invalid", "Инициатива может создать только реплику присутствующего NPC.", 422
            )
        result = copy.deepcopy(data)
        npc = result["character_states"].get(actor, {})
        intent = next((intent for intent in npc.get("intentions", []) if intent["id"] == intention_id), None)
        if not intent or intent["status"] != "proposed_action" or intent["scene_id"] != data["scene"]["id"]:
            raise AppError(
                "initiative_invalid", "Намерение отсутствует, устарело или уже получило реплику.", 409
            )
        intent.update(status="turn_generated", result_message_id=message["id"])
        event = {
            "id": str(uuid5(NAMESPACE_URL, "taverna/initiative-event/" + message["id"])),
            "story_id": data["story_id"],
            "branch_id": data["branch_id"],
            "kind": "initiative_turn_generated",
            "actor_id": actor,
            "intention_id": intention_id,
            "source_ids": [message["id"]],
            "created_at": now(),
        }
        DirectorService.director(result)["audit"].append(event)
        result["events"].append(event)
        return result

    @staticmethod
    def reconcile_presence(data, operation, before, changes):
        scene = data["scene"]
        location = scene.get("location_id")
        if not location:
            return
        bindings = data.get("director", {}).get("policy", {}).get("actor_entities", {})
        entities = data["world"]["entities"]
        for actor in list(scene["participants"]):
            entity_id = bindings.get(actor)
            entity = entities.get(entity_id)
            if not entity:
                continue
            old = entity.get("location_id")
            if operation == "move" and old in (None, before["location_id"]):
                entity["location_id"] = location
                changes.append(
                    {"op": "party_travel", "entity_id": entity_id, "before": old, "after": location}
                )
            elif old is not None and old != location:
                scene["participants"].remove(actor)
                scene["speaker_order"] = [cid for cid in scene["speaker_order"] if cid != actor]
                changes.append({"op": "presence_left", "actor_id": actor, "before": location, "after": old})
