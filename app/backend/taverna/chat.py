from __future__ import annotations

import asyncio
import copy
from uuid import UUID, uuid4

import jsonschema

from .chat_schemas import BRANCH_SCHEMA, MESSAGE_SCHEMA, STATE_SCHEMA, STORY_SCHEMA
from .core import AppError
from .events import EventBus
from .llm import LmStudioAdapter
from .segments import SegmentStore
from .storage import JsonStore, Mutation, document, now


def identifier(value: str) -> str:
    try:
        UUID(value)
    except ValueError as exc:
        raise AppError("invalid_id", "Некорректный идентификатор.") from exc
    return value


class ChatService:
    def __init__(self, store: JsonStore, generation):
        self.store, self.generation = store, generation
        self.after_message = EventBus()
        self.state_reducer = None
        self.state_cloner = None
        self.segments = SegmentStore(store)
        for kind, schema in [
            ("story", STORY_SCHEMA),
            ("branch", BRANCH_SCHEMA),
            ("branch_state", STATE_SCHEMA),
        ]:
            store.register(kind, 1, schema)
        self.message_validator = jsonschema.Draft202012Validator(
            MESSAGE_SCHEMA, format_checker=jsonschema.FormatChecker()
        )
        generation.queue.handlers["chat"] = self.generate
        generation.queue.handlers["chat_group"] = self.generate_group

    def story_key(self, story_id):
        return f"chats/stories/{identifier(story_id)}.json"

    def branch_key(self, story_id, branch_id):
        return f"chats/{identifier(story_id)}/branches/{identifier(branch_id)}.json"

    def state_key(self, story_id, state_id):
        return f"chats/{identifier(story_id)}/states/{identifier(state_id)}.json"

    def story(self, story_id):
        value = self.store.get(self.story_key(story_id))
        if value is None:
            raise AppError("story_missing", "История не найдена.", 404)
        return value

    def branch(self, story_id, branch_id=None):
        story = self.story(story_id)
        value = self.store.get(self.branch_key(story_id, branch_id or story["payload"]["active_branch"]))
        if value is None:
            raise AppError("branch_missing", "Ветвь не найдена.", 404)
        return value

    def state(self, story_id, branch_id=None):
        branch = self.branch(story_id, branch_id)
        value = self.store.get(self.state_key(story_id, branch["payload"]["state_id"]))
        if value is None:
            raise AppError("state_missing", "Состояние ветви утрачено. Восстановите контрольную копию.", 503)
        return value

    def _message(self, story_id, branch_id, role, text, author_id=None, **metadata):
        value = {
            "id": str(uuid4()),
            "story_id": story_id,
            "branch_id": branch_id,
            "role": role,
            "author_id": author_id,
            "text": text,
            "created_at": now(),
            "version": 1,
            "model_id": None,
            "prompt_profile_id": None,
            "source_message_ids": [],
            "event_ids": [],
            "file_ids": [],
            **metadata,
        }
        try:
            self.message_validator.validate(value)
        except jsonschema.ValidationError as exc:
            raise AppError("invalid_message", "Сообщение не соответствует схеме.", 422) from exc
        return value

    def _prepare_create(
        self, name: str, character_ids: list[str], greeting_index=0, *, include_greeting=True
    ):
        if not name.strip() or not character_ids or len(set(character_ids)) != len(character_ids):
            raise AppError("invalid_story", "Укажите название и разных персонажей.", 422)
        snapshots = {}
        for cid in character_ids:
            card = self.store.get(f"characters/{identifier(cid)}.json")
            if card is None:
                raise AppError("character_missing", "Участник истории не найден.", 404)
            snapshots[cid] = {"revision": card["revision"], "card": card["payload"]}
        story = document(
            "story",
            {
                "name": name,
                "character_snapshots": snapshots,
                "active_branch": None,
                "world_id": None,
                "scenario_id": None,
            },
        )
        sid = story["id"]
        branch = document(
            "branch",
            {
                "story_id": sid,
                "name": "Основная ветвь",
                "parent_id": None,
                "fork_message_id": None,
                "state_id": None,
                "head_message_id": None,
                "participants": list(character_ids),
                "speaker_order": list(character_ids),
                "view_mode": "messenger",
                "chapters": [],
                "bookmarks": [],
                "checkpoints": [],
            },
        )
        bid = branch["id"]
        state = document(
            "branch_state",
            {
                "story_id": sid,
                "branch_id": bid,
                "memory": {},
                "relationships": {},
                "world": {},
                "rpg": {},
                "character_states": {cid: {} for cid in character_ids},
                "scene": {
                    "id": str(uuid4()),
                    "name": "Начальная сцена",
                    "participants": list(character_ids),
                    "speaker_order": list(character_ids),
                    "location_id": None,
                },
                "events": [],
            },
        )
        branch["payload"]["state_id"] = state["id"]
        branch["payload"]["initial_state_id"] = state["id"]
        branch["refs"] = [self.state_key(sid, state["id"])]
        story["payload"]["active_branch"] = bid
        story["refs"] = [self.branch_key(sid, bid)]
        changes = [
            Mutation(self.story_key(sid), story, 0),
            Mutation(self.branch_key(sid, bid), branch, 0),
            Mutation(self.state_key(sid, state["id"]), state, 0),
        ]
        first = snapshots[character_ids[0]]["card"]["fields"]
        greetings = [first.get("first_mes", ""), *first.get("alternate_greetings", [])]
        if not 0 <= greeting_index < len(greetings):
            raise AppError("invalid_greeting", "Приветствие не найдено.", 422)
        if include_greeting and greetings[greeting_index]:
            message = self._message(
                sid,
                bid,
                "assistant",
                greetings[greeting_index],
                character_ids[0],
                state_id=state["id"],
                present_ids=list(character_ids),
            )
            changes += self.segments.plan_append(bid, [message])
            branch["payload"]["head_message_id"] = message["id"]
        return story, branch, state, changes

    def create(self, name: str, character_ids: list[str], greeting_index=0):
        story, branch, state, changes = self._prepare_create(name, character_ids, greeting_index)
        self.store.transaction(changes)
        return self.story(story["id"])

    def append(
        self,
        story_id,
        text,
        *,
        expected_revision,
        role="user",
        author_id=None,
        metadata=None,
        branch_id=None,
        extra_changes=(),
    ):
        if not text.strip():
            raise AppError("empty_message", "Сообщение не может быть пустым.", 422)
        story = self.story(story_id)
        branch = self.branch(story_id, branch_id)
        bid = branch["id"]
        if bid != story["payload"]["active_branch"]:
            raise AppError("inactive_branch", "Ветвь больше не активна. Результат сохранён отдельно.", 409)
        if branch["revision"] != expected_revision:
            raise AppError("branch_conflict", "Ветвь изменилась. Обновите историю.", 409)
        if role == "assistant" and author_id not in branch["payload"]["participants"]:
            raise AppError("speaker_absent", "Персонаж отсутствует в сцене.", 409)
        if role not in ("user", "assistant"):
            raise AppError("invalid_author", "Недопустимый автор сообщения.", 422)
        if role == "user" and author_id is not None:
            raise AppError("invalid_author", "Пользователь пишет от своего имени.", 422)
        message = self._message(
            story_id,
            bid,
            role,
            text,
            author_id,
            state_id=branch["payload"]["state_id"],
            present_ids=list(branch["payload"]["participants"]),
            **(metadata or {}),
        )
        previous_state_id = branch["payload"]["state_id"]
        state_changes = []
        if self.state_reducer:
            state = self.state(story_id, bid)
            reduced = self.state_reducer(state["payload"], message, expected_revision)
            if reduced is not None:
                updated = document("branch_state", reduced)
                branch["payload"]["state_id"] = updated["id"]
                branch["refs"] = list(
                    {
                        self.state_key(story_id, updated["id"]),
                        self.state_key(story_id, branch["payload"]["initial_state_id"]),
                    }
                )
                message["state_id"] = updated["id"]
                message["event_ids"] = list(dict.fromkeys([*message["event_ids"], message["id"]]))
                state_changes.append(Mutation(self.state_key(story_id, updated["id"]), updated, 0))
        branch["payload"]["head_message_id"] = message["id"]
        changes = [
            *state_changes,
            *self.segments.plan_append(bid, [message], seal_tail_state=previous_state_id),
        ]
        changes += [
            Mutation(self.branch_key(story_id, bid), branch, expected_revision),
            Mutation(self.story_key(story_id), story, story["revision"]),
        ]
        self.store.transaction([*changes, *extra_changes])
        return {"message": message, "branch": self.branch(story_id, bid)}

    def messages(self, story_id, branch_id=None, offset=0, limit=100):
        branch = self.branch(story_id, branch_id)
        return self.segments.page(branch["id"], offset, limit)

    def _prefix(self, story_id, branch_id, through_id=None):
        messages = []
        found = through_id is None
        # One consistent snapshot; repeated page() calls reread all preceding segments.
        with self.store.view() as view:
            head = view.get(self.segments._key(branch_id))
            for key in head["payload"]["segments"] if head else []:
                for message in view.get(key)["payload"]["messages"]:
                    messages.append(message)
                    if message["id"] == through_id:
                        return messages
        if not found:
            raise AppError("message_missing", "Сообщение не найдено в этой ветви.", 404)
        return messages

    def fork(
        self,
        story_id,
        *,
        expected_revision,
        through_id=None,
        name="Новая ветвь",
        replacement=None,
        drop_last=False,
        branch_id=None,
        restore_state_id=None,
        before_all=False,
    ):
        story = self.story(story_id)
        parent = self.branch(story_id, branch_id)
        if parent["id"] != story["payload"]["active_branch"]:
            raise AppError("inactive_branch", "Ветвь больше не активна.", 409)
        if parent["revision"] != expected_revision:
            raise AppError("branch_conflict", "Ветвь изменилась.", 409)
        originals = self._prefix(story_id, parent["id"], through_id)
        if before_all:
            originals = []
        if drop_last and originals:
            originals = originals[:-1]
        if replacement is not None and not originals:
            raise AppError("message_missing", "Нет сообщения для редактирования.", 404)
        branch = document("branch", copy.deepcopy(parent["payload"]))
        bid = branch["id"]
        branch["payload"].update(
            name=name,
            parent_id=parent["id"],
            fork_message_id=through_id,
            head_message_id=None,
            chapters=[],
            bookmarks=[],
            checkpoints=[],
        )
        copies = []
        mapping = {}
        for original in originals:
            cloned = {
                **copy.deepcopy(original),
                "id": str(uuid4()),
                "branch_id": bid,
                "source_message_ids": list(
                    dict.fromkeys([original["id"], *original.get("source_message_ids", [])])
                ),
            }
            mapping[original["id"]] = cloned["id"]
            copies.append(cloned)
        if replacement is not None:
            copies[-1]["text"] = replacement
            copies[-1]["version"] += 1
        changes = []
        cloned_states = {}
        state_payloads = {}

        def clone_state(state_id):
            if state_id in cloned_states:
                return cloned_states[state_id]
            original_state = self.store.get(self.state_key(story_id, state_id))
            if original_state is None:
                raise AppError("state_missing", "Снимок состояния утрачен.", 503)
            payload = copy.deepcopy(original_state["payload"])
            payload["branch_id"] = bid
            for fact in [*payload.get("memory", {}).values(), *payload.get("relationships", {}).values()]:
                if isinstance(fact, dict):
                    fact["source_ids"] = [
                        mapping.get(source, source) for source in fact.get("source_ids", [])
                    ]
                    fact["branch_id"] = bid
                    if isinstance(fact.get("provenance"), dict):
                        fact["provenance"]["source_hashes"] = {
                            mapping.get(source, source): checksum
                            for source, checksum in fact["provenance"].get("source_hashes", {}).items()
                        }
            if self.state_cloner:
                self.state_cloner(payload, mapping, bid)
            state = document("branch_state", payload)
            changes.append(Mutation(self.state_key(story_id, state["id"]), state, 0))
            state_payloads[state["id"]] = state["payload"]
            cloned_states[state_id] = state["id"]
            return state["id"]

        initial_id = parent["payload"]["initial_state_id"]
        initial_clone = clone_state(initial_id)
        previous_state_id = initial_id
        head_state_id = initial_clone
        for index, (original, cloned) in enumerate(zip(originals, copies, strict=True)):
            state_id = (
                parent["payload"]["state_id"]
                if original["id"] == parent["payload"]["head_message_id"]
                else original["state_id"]
            )
            # An edited message cannot inherit consequences of its old contents.
            if replacement is not None and index == len(copies) - 1:
                state_id = previous_state_id
            head_state_id = clone_state(state_id)
            cloned["state_id"] = head_state_id
            previous_state_id = state_id
        if restore_state_id is not None:
            head_state_id = clone_state(restore_state_id)
            if copies:
                copies[-1]["state_id"] = head_state_id
        for collection in ("chapters", "bookmarks", "checkpoints"):
            for anchor in parent["payload"][collection]:
                source_message = anchor["message_id"]
                if source_message is not None and source_message not in mapping:
                    continue
                if replacement is not None and originals and source_message == originals[-1]["id"]:
                    continue
                cloned_anchor = {
                    **copy.deepcopy(anchor),
                    "id": str(uuid4()),
                    "source_anchor_id": anchor["id"],
                    "message_id": mapping.get(source_message),
                    "state_id": clone_state(anchor["state_id"]),
                }
                branch["payload"][collection].append(cloned_anchor)
        branch["payload"].update(
            state_id=head_state_id,
            initial_state_id=initial_clone,
            head_message_id=copies[-1]["id"] if copies else None,
            participants=copy.deepcopy(state_payloads[head_state_id]["scene"]["participants"]),
            speaker_order=copy.deepcopy(state_payloads[head_state_id]["scene"]["speaker_order"]),
        )
        branch["refs"] = list(
            {self.state_key(story_id, initial_clone), self.state_key(story_id, head_state_id)}
        )
        if copies:
            changes += self.segments.plan_append(bid, copies)
        story["payload"]["active_branch"] = bid
        story["refs"] = list(dict.fromkeys([*story.get("refs", []), self.branch_key(story_id, bid)]))
        changes += [
            Mutation(self.branch_key(story_id, parent["id"]), parent, expected_revision),
            Mutation(self.branch_key(story_id, bid), branch, 0),
            Mutation(self.story_key(story_id), story, story["revision"]),
        ]
        self.store.transaction(changes)
        return {"story": self.story(story_id), "branch": self.branch(story_id, bid)}

    def switch(self, story_id, branch_id, expected_revision):
        story = self.story(story_id)
        self.branch(story_id, branch_id)
        story["payload"]["active_branch"] = branch_id
        self.store.transaction([Mutation(self.story_key(story_id), story, expected_revision)])
        return self.story(story_id)

    def update_state(
        self, story_id, data, expected_revision, branch_id=None, *, extra_changes=(), guards=None
    ):
        story = self.story(story_id)
        branch = self.branch(story_id, branch_id)
        if (
            branch["id"] != story["payload"]["active_branch"]
            or data.get("branch_id") != branch["id"]
            or data.get("story_id") != story_id
        ):
            raise AppError(
                "state_scope_conflict", "Состояние принадлежит другой ветви. Обновите данные.", 409
            )
        if branch["revision"] != expected_revision:
            raise AppError("branch_conflict", "Состояние ветви изменилось.", 409)
        state = document(
            "branch_state", {**copy.deepcopy(data), "story_id": story_id, "branch_id": branch["id"]}
        )
        branch["payload"]["state_id"] = state["id"]
        scene = state["payload"]["scene"]
        branch["payload"]["participants"] = copy.deepcopy(scene["participants"])
        branch["payload"]["speaker_order"] = copy.deepcopy(scene["speaker_order"])
        branch["refs"] = list(
            {
                self.state_key(story_id, state["id"]),
                self.state_key(story_id, branch["payload"]["initial_state_id"]),
            }
        )
        changes = [
            Mutation(self.state_key(story_id, state["id"]), state, 0),
            Mutation(self.branch_key(story_id, branch["id"]), branch, expected_revision),
            Mutation(self.story_key(story_id), story, story["revision"]),
        ]
        self.store.transaction([*changes, *extra_changes], guards=guards)
        return self.state(story_id)

    async def generate(self, payload, cancel, emit):
        story = await asyncio.to_thread(self.story, payload["story_id"])
        branch = await asyncio.to_thread(self.branch, payload["story_id"], payload["branch_id"])
        if (
            branch["revision"] != payload["expected_revision"]
            or story["payload"]["active_branch"] != branch["id"]
        ):
            raise AppError("generation_stale", "История изменилась до начала генерации.", 409)
        cid = payload["character_id"]
        if cid not in branch["payload"]["participants"]:
            raise AppError("speaker_absent", "Персонаж отсутствует в сцене.", 409)
        from .quality import inspect_answer

        adapter = LmStudioAdapter(payload["endpoint"], transport=self.generation.adapter.transport)
        resolved = await asyncio.to_thread(
            self.generation.profiles.resolve,
            character_id=cid,
            story_id=story["id"],
            profile_id=payload.get("profile_id"),
            parameters=payload.get("parameters", {}),
        )
        model_id = payload.get("model_id") or resolved["settings"]["model_id"]
        if not model_id:
            raise AppError("model_required", "Выберите загруженную модель или назначьте её профилю.", 422)
        model = await adapter.require_loaded(model_id)
        history = await asyncio.to_thread(self._prefix, story["id"], branch["id"])
        state = await asyncio.to_thread(self.state, story["id"], branch["id"])
        from .prompts import PromptBuilder

        builder = PromptBuilder()
        quality = resolved["settings"]["quality"]
        audit = []
        instruction = payload.get("instruction", "")
        previous = [m["text"] for m in history if m.get("author_id") == cid]
        for attempt in range(1, (quality["max_attempts"] if quality["mode"] == "retry" else 1) + 1):
            context = builder.build(
                story=story,
                branch=branch,
                state=state,
                character_id=cid,
                history=history,
                resolved=resolved,
                loaded_context=model["config"].get("context_length"),
                instruction=instruction,
            )
            context.update(model_id=model_id, parameters=resolved["settings"]["parameters"])
            if (await asyncio.to_thread(self.branch, story["id"], branch["id"]))["revision"] != payload[
                "expected_revision"
            ]:
                raise AppError("generation_stale", "История изменилась при сборке контекста.", 409)
            trace = await asyncio.to_thread(
                self.generation.traces.save, context, resolved["settings"]["save_context"]
            )
            await emit(
                "context",
                {
                    "trace_id": trace["id"],
                    "estimate": context["estimate"],
                    "exact_tokens": False,
                    "show_reasoning": resolved["settings"]["show_reasoning"],
                    "model_id": model_id,
                    "profile_id": resolved["profile_id"],
                },
            )
            await emit("attempt_started", {"attempt": attempt})
            text, reasoning, metrics = "", "", {}
            async for kind, event in adapter.stream(
                model_id,
                context["messages"],
                resolved["settings"]["parameters"],
                cancel,
                estimator=resolved["settings"]["estimator"],
            ):
                if kind == "delta":
                    text += event["text"]
                if kind == "reasoning":
                    reasoning += event["text"]
                if kind == "metrics":
                    metrics = event
                await emit(kind, event)
            flags = inspect_answer(text, previous, quality)
            audit.append({"attempt": attempt, "flags": flags, "trace_id": trace["id"]})
            await emit("quality", {"attempt": attempt, "flags": flags, "mode": quality["mode"]})
            if not flags or quality["mode"] == "warn":
                break
            if quality["mode"] == "retry" and not any(flag["code"] in quality["retry_on"] for flag in flags):
                break
            if quality["mode"] == "reject" or attempt == quality["max_attempts"]:
                raise AppError(
                    "answer_quality_rejected",
                    "Ответ отклонён проверкой качества. Попытки сохранены в журнале генерации; их можно просмотреть.",
                    409,
                )
            instruction = (
                payload.get("instruction", "")
                + "\nИсправь ответ: "
                + " ".join(flag["message_ru"] for flag in flags)
            )
        if cancel.is_set():
            return {"text": text, "partial": True}
        answer = payload.get("prefix", "") + text + payload.get("suffix", "")
        saved = await asyncio.to_thread(
            self.append,
            payload["story_id"],
            answer,
            expected_revision=payload["expected_revision"],
            role="assistant",
            author_id=cid,
            branch_id=branch["id"],
            metadata={
                "model_id": model_id,
                "prompt_profile_id": resolved["profile_id"],
                "context_trace_id": trace["id"],
                "quality_audit": audit,
                "source_message_ids": payload.get("source_message_ids", []),
            },
        )
        if self.after_message:
            await self.after_message(story["id"], branch["id"], saved["message"]["id"], model_id)
        return {
            "text": answer,
            "reasoning": reasoning,
            "metrics": metrics,
            "message_id": saved["message"]["id"],
            "context_trace_id": trace["id"],
            "quality_audit": audit,
            "branch_id": branch["id"],
            "branch_revision": saved["branch"]["revision"],
        }

    async def generate_group(self, payload, cancel, emit):
        branch = await asyncio.to_thread(self.branch, payload["story_id"], payload["branch_id"])
        order = list(branch["payload"]["speaker_order"])
        if not order:
            raise AppError("no_speakers", "В сцене нет присутствующих NPC.", 409)
        if payload["character_id"] not in order:
            raise AppError("speaker_absent", "Начальный говорящий отсутствует.", 409)
        start = order.index(payload["character_id"])
        current_revision = payload["expected_revision"]
        results = []
        for turn in range(payload["turns"]):
            if cancel.is_set():
                break
            cid = order[(start + turn) % len(order)]
            await emit("speaker", {"character_id": cid, "turn": turn + 1})
            result = await self.generate(
                {**payload, "character_id": cid, "expected_revision": current_revision}, cancel, emit
            )
            if result.get("partial"):
                break
            current_revision = result["branch_revision"]
            results.append(result)
            await emit(
                "turn_completed", {"message_id": result["message_id"], "character_id": cid, "turn": turn + 1}
            )
        return {"turns": results, "branch_revision": current_revision, "branch_id": branch["id"]}
