from __future__ import annotations

import asyncio
from typing import Literal
from uuid import uuid4

from fastapi import APIRouter, Header, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field

from .chat import ChatService
from .core import AppError
from .generation_api import GenerationParameters
from .storage import Mutation, document, safe_path


class NewStory(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=200)
    character_ids: list[str] = Field(min_length=1, max_length=20)
    greeting_index: int = Field(default=0, ge=0)


class MessageRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=1)
    branch_id: str
    text: str = Field(min_length=1, max_length=262144)


class ForkRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=1)
    branch_id: str
    through_id: str | None = None
    name: str = Field(default="Новая ветвь", min_length=1, max_length=200)


class RevisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=1)
    branch_id: str


class DeleteMessage(RevisionRequest):
    confirmed: bool = False


class StoryGeneration(RevisionRequest):
    model_id: str | None = Field(default=None, min_length=1, max_length=256)
    character_id: str
    parameters: GenerationParameters = Field(default_factory=GenerationParameters)
    instruction: str = Field(default="", max_length=12000)
    profile_id: str | None = None


class GroupGeneration(StoryGeneration):
    turns: int = Field(default=2, ge=1, le=16)


class BranchSettings(RevisionRequest):
    view_mode: Literal["messenger", "literary", "novel"] | None = None
    participants: list[str] | None = None
    speaker_order: list[str] | None = None


class NamedAnchor(RevisionRequest):
    name: str = Field(min_length=1, max_length=200)
    message_id: str | None = None
    kind: Literal["chapter", "bookmark", "checkpoint"] = "bookmark"


class Regenerate(StoryGeneration):
    message_id: str
    action: Literal["regenerate", "continue", "fragment"] = "regenerate"
    fragment: str | None = Field(default=None, max_length=262144)


def router(chat: ChatService):
    router = APIRouter(prefix="/api/v1/stories")

    @router.get("")
    def stories():
        rows, errors = chat.store.scan("chats/stories/", tolerate_corrupt=True)
        return {
            "stories": sorted([r["value"] for r in rows], key=lambda v: v["updated_at"], reverse=True),
            "errors": errors,
        }

    @router.post("")
    def create(body: NewStory):
        return chat.create(body.name, body.character_ids, body.greeting_index)

    @router.get("/{story_id}")
    def story(story_id: str):
        return {"story": chat.story(story_id), "branch": chat.branch(story_id), "state": chat.state(story_id)}

    @router.get("/{story_id}/messages")
    def messages(
        story_id: str,
        branch_id: str | None = None,
        offset: int = Query(0, ge=0),
        limit: int = Query(100, ge=1, le=500),
        query: str = "",
    ):
        if not query:
            return chat.messages(story_id, branch_id, offset, limit)
        branch = chat.branch(story_id, branch_id)
        matched = [
            m for m in chat._prefix(story_id, branch["id"]) if query.casefold() in m["text"].casefold()
        ]
        return {"items": matched[offset : offset + limit], "offset": offset, "total": len(matched)}

    @router.get("/{story_id}/portraits/{character_id}")
    def portrait(story_id: str, character_id: str):
        story = chat.story(story_id)
        snapshot = story["payload"]["character_snapshots"].get(character_id)
        media_id = snapshot["card"]["portrait_id"] if snapshot else None
        asset = chat.store.get(f"media/{media_id}.json") if media_id else None
        if not asset:
            raise AppError("portrait_missing", "Портрет в этой истории отсутствует.", 404)
        path = safe_path(chat.store.root, asset["payload"]["relative_path"])
        if not path.is_file():
            raise AppError("portrait_missing", "Файл портрета отсутствует.", 404)
        return FileResponse(path, media_type="image/png")

    @router.post("/{story_id}/messages")
    async def append(story_id: str, body: MessageRequest):
        result = await asyncio.to_thread(
            chat.append,
            story_id,
            body.text,
            expected_revision=body.expected_revision,
            branch_id=body.branch_id,
        )
        if chat.after_message:
            result["memory_background"] = await chat.after_message(
                story_id, body.branch_id, result["message"]["id"]
            )
        return result

    @router.get("/{story_id}/branches")
    def branches(story_id: str):
        chat.story(story_id)
        rows, errors = chat.store.scan(f"chats/{story_id}/branches/", tolerate_corrupt=True)
        return {"branches": [r["value"] for r in rows], "errors": errors}

    @router.post("/{story_id}/branches")
    def fork(story_id: str, body: ForkRequest):
        return chat.fork(story_id, **body.model_dump())

    @router.post("/{story_id}/branches/{branch_id}/activate")
    def activate(story_id: str, branch_id: str, body: RevisionRequest):
        return chat.switch(story_id, branch_id, body.expected_revision)

    @router.patch("/{story_id}/messages/{message_id}")
    def edit(story_id: str, message_id: str, body: MessageRequest):
        return chat.fork(
            story_id,
            expected_revision=body.expected_revision,
            through_id=message_id,
            branch_id=body.branch_id,
            replacement=body.text,
            name="Редактирование сообщения",
        )

    @router.delete("/{story_id}/messages/{message_id}")
    def delete(story_id: str, message_id: str, body: DeleteMessage):
        if not body.confirmed:
            raise AppError(
                "confirmation_required",
                "Удаление создаст новую ветвь до этого сообщения. Подтвердите последствия.",
                409,
            )
        return chat.fork(
            story_id,
            expected_revision=body.expected_revision,
            through_id=message_id,
            drop_last=True,
            branch_id=body.branch_id,
            name="До удаления сообщения",
        )

    @router.patch("/{story_id}/branch")
    def settings(story_id: str, body: BranchSettings):
        story = chat.story(story_id)
        branch = chat.branch(story_id, body.branch_id)
        if story["payload"]["active_branch"] != body.branch_id:
            raise AppError("inactive_branch", "Ветвь больше не активна.", 409)
        patch = body.model_dump(exclude_none=True, exclude={"expected_revision", "branch_id"})
        participants = patch.get("participants", branch["payload"]["participants"])
        previous_order = branch["payload"]["speaker_order"]
        default_order = [cid for cid in previous_order if cid in participants] + [
            cid for cid in participants if cid not in previous_order
        ]
        order = patch.get("speaker_order", default_order)
        if len(set(participants)) != len(participants) or any(
            cid not in story["payload"]["character_snapshots"] for cid in participants
        ):
            raise AppError("invalid_participants", "Участники не соответствуют истории.", 422)
        if len(set(order)) != len(order) or set(order) != set(participants):
            raise AppError(
                "invalid_speaker_order", "Очередь должна содержать присутствующих NPC по одному разу.", 422
            )
        patch["speaker_order"] = order
        branch["payload"].update(patch)
        changes = []
        if body.participants is not None or body.speaker_order is not None:
            data = chat.state(story_id, branch["id"])["payload"]
            data["scene"].update(participants=participants, speaker_order=order)
            state = document("branch_state", data)
            branch["payload"]["state_id"] = state["id"]
            branch["refs"] = list(
                {
                    chat.state_key(story_id, state["id"]),
                    chat.state_key(story_id, branch["payload"]["initial_state_id"]),
                }
            )
            changes.append(Mutation(chat.state_key(story_id, state["id"]), state, 0))
        chat.store.transaction(
            [
                *changes,
                Mutation(chat.branch_key(story_id, branch["id"]), branch, body.expected_revision),
                Mutation(chat.story_key(story_id), story, story["revision"]),
            ]
        )
        return chat.branch(story_id)

    @router.post("/{story_id}/anchors")
    def anchor(story_id: str, body: NamedAnchor):
        story = chat.story(story_id)
        if story["payload"]["active_branch"] != body.branch_id:
            raise AppError("inactive_branch", "Ветвь больше не активна.", 409)
        branch = chat.branch(story_id, body.branch_id)
        message_id = body.message_id or branch["payload"]["head_message_id"]
        prefix = chat._prefix(story_id, branch["id"], message_id) if message_id else []
        state_id = (
            branch["payload"]["state_id"]
            if message_id == branch["payload"]["head_message_id"]
            else prefix[-1]["state_id"]
        )
        value = {
            "id": str(uuid4()),
            "name": body.name,
            "message_id": message_id,
            "state_id": state_id,
        }
        key = {"chapter": "chapters", "bookmark": "bookmarks", "checkpoint": "checkpoints"}[body.kind]
        branch["payload"][key].append(value)
        chat.store.transaction(
            [
                Mutation(chat.branch_key(story_id, branch["id"]), branch, body.expected_revision),
                Mutation(chat.story_key(story_id), story, story["revision"]),
            ]
        )
        return chat.branch(story_id)

    @router.get("/{story_id}/messages/{message_id}/position")
    def message_position(story_id: str, message_id: str, branch_id: str):
        chat.branch(story_id, branch_id)
        prefix = chat._prefix(story_id, branch_id, message_id)
        return {"index": len(prefix) - 1}

    @router.post("/{story_id}/anchors/{anchor_id}/restore")
    def restore_anchor(story_id: str, anchor_id: str, body: RevisionRequest):
        branch = chat.branch(story_id, body.branch_id)
        selected = next(
            (anchor for anchor in branch["payload"]["checkpoints"] if anchor["id"] == anchor_id), None
        )
        if selected is None:
            raise AppError("checkpoint_missing", "Контрольная точка не найдена.", 404)
        return chat.fork(
            story_id,
            expected_revision=body.expected_revision,
            branch_id=body.branch_id,
            through_id=selected["message_id"],
            name="Контрольная точка: " + selected["name"],
            restore_state_id=selected["state_id"],
            before_all=selected["message_id"] is None,
        )

    @router.post("/{story_id}/group-generations", status_code=202)
    async def group_generation(
        story_id: str, body: GroupGeneration, idempotency_key: str = Header(min_length=1, max_length=128)
    ):
        branch = await asyncio.to_thread(chat.branch, story_id, body.branch_id)
        payload = {
            **body.model_dump(),
            "parameters": body.parameters.model_dump(exclude_unset=True, exclude_none=True),
            "story_id": story_id,
            "endpoint": chat.generation.adapter.endpoint,
        }
        job = await chat.generation.queue.enqueue(
            "chat_group",
            payload,
            idempotency_key=idempotency_key,
            identity={"story_id": story_id, "request": body.model_dump()},
            scope={"story_id": story_id, "branch_id": branch["id"], "revision": body.expected_revision},
        )
        return {"generation_id": job["id"]}

    @router.post("/{story_id}/partials/{job_id}/apply")
    async def apply_partial(story_id: str, job_id: str, body: RevisionRequest):
        job = await chat.generation.queue.get(job_id)
        saved = job["payload"].get("applied_message_id")
        request = job["payload"]["request"]
        if (
            job["payload"]["kind"] not in ("chat", "chat_regenerate", "chat_group")
            or request.get("story_id") != story_id
        ):
            raise AppError("invalid_partial", "Частичный ответ не принадлежит этой истории.", 409)
        if saved:
            return {"message_id": saved, "already_applied": True}
        if job["payload"]["state"] not in ("failed", "cancelled", "interrupted"):
            raise AppError("invalid_partial", "Сначала остановите генерацию.", 409)
        target_branch = request["branch_id"]
        text = ""
        after = 0
        speaker = None
        context_metadata = {}
        while True:
            events = await chat.generation.queue.events(job_id, after)
            for event in events:
                after = event["sequence"]
                if event["type"] == "generation.attempt_started":
                    text = ""
                if event["type"] == "generation.context":
                    data = event["data"]
                    context_metadata = {
                        "model_id": data.get("model_id"),
                        "prompt_profile_id": data.get("profile_id"),
                        "context_trace_id": data.get("trace_id"),
                    }
                if event["type"] == "generation.speaker":
                    text = ""
                    speaker = event["data"]["character_id"]
                if event["type"] == "generation.turn_completed":
                    text = ""
                    speaker = None
                if event["type"] == "generation.delta":
                    text += event["data"].get("text", "")
                if event["type"] == "generation.branch_created":
                    target_branch = event["data"]["branch_id"]
            if len(events) < 100:
                break
        if target_branch != body.branch_id or not text.strip():
            raise AppError("invalid_partial", "Нет частичного текста для этой ветви.", 409)
        source = request.get("request", request)
        if source.get("action") in ("continue", "fragment"):
            originals = await asyncio.to_thread(
                chat._prefix, story_id, request["branch_id"], source["message_id"]
            )
            original = originals[-1]["text"]
            if source["action"] == "continue":
                text = original + text
            else:
                fragment = source["fragment"]
                start = original.index(fragment)
                text = original[:start] + text + original[start + len(fragment) :]
        message_id = str(uuid4())
        job["payload"]["applied_message_id"] = message_id
        result = await asyncio.to_thread(
            chat.append,
            story_id,
            text,
            expected_revision=body.expected_revision,
            branch_id=body.branch_id,
            role="assistant",
            author_id=speaker or source["character_id"],
            metadata={
                "id": message_id,
                "model_id": source.get("model_id"),
                "partial": True,
                "source_job_id": job_id,
                **context_metadata,
            },
            extra_changes=[Mutation(chat.generation.queue.key(job_id), job, job["revision"])],
        )
        return {"message_id": result["message"]["id"], "already_applied": False}

    @router.post("/{story_id}/generations", status_code=202)
    async def generate(
        story_id: str, body: StoryGeneration, idempotency_key: str = Header(min_length=1, max_length=128)
    ):
        branch = await asyncio.to_thread(chat.branch, story_id, body.branch_id)
        payload = {
            **body.model_dump(),
            "parameters": body.parameters.model_dump(exclude_unset=True, exclude_none=True),
            "story_id": story_id,
            "branch_id": branch["id"],
            "endpoint": chat.generation.adapter.endpoint,
        }
        job = await chat.generation.queue.enqueue(
            "chat",
            payload,
            idempotency_key=idempotency_key,
            scope={"story_id": story_id, "branch_id": branch["id"], "revision": body.expected_revision},
            identity={"story_id": story_id, "request": body.model_dump()},
        )
        return {"generation_id": job["id"]}

    @router.post("/{story_id}/context-preview")
    def context_preview(story_id: str, body: StoryGeneration):
        from .prompts import PromptBuilder

        story = chat.story(story_id)
        branch = chat.branch(story_id, body.branch_id)
        if branch["revision"] != body.expected_revision:
            raise AppError("branch_conflict", "Ветвь изменилась. Обновите историю.", 409)
        if body.character_id not in branch["payload"]["participants"]:
            raise AppError("speaker_absent", "Персонаж отсутствует в сцене.", 409)
        resolved = chat.generation.profiles.resolve(
            character_id=body.character_id,
            story_id=story_id,
            profile_id=body.profile_id,
            parameters=body.parameters.model_dump(exclude_unset=True, exclude_none=True),
        )
        result = PromptBuilder().build(
            story=story,
            branch=branch,
            state=chat.state(story_id, branch["id"]),
            character_id=body.character_id,
            history=chat._prefix(story_id, branch["id"]),
            resolved=resolved,
            instruction=body.instruction,
        )
        if chat.branch(story_id, branch["id"])["revision"] != body.expected_revision:
            raise AppError("branch_conflict", "Ветвь изменилась при подготовке контекста.", 409)
        return {**result, "preview": True, "loaded_window_checked": False}

    async def regeneration_handler(payload, cancel, emit):
        body = Regenerate.model_validate(payload["request"])
        story_id = payload["story_id"]
        story = await asyncio.to_thread(chat.story, story_id)
        if story["payload"]["active_branch"] != payload["branch_id"]:
            raise AppError("generation_stale", "Активная ветвь изменилась.", 409)
        originals = await asyncio.to_thread(chat._prefix, story_id, payload["branch_id"], body.message_id)
        source = originals[-1]
        if source["role"] != "assistant":
            raise AppError("invalid_regeneration", "Регенерация доступна для ответа NPC.", 422)
        prefix = suffix = ""
        instruction = body.instruction
        if body.action == "continue":
            prefix = source["text"]
            instruction += "\nПродолжи этот ответ без повторения: " + source["text"]
        elif body.action == "fragment":
            if not body.fragment or source["text"].count(body.fragment) != 1:
                raise AppError("ambiguous_fragment", "Выберите однозначный непустой фрагмент.", 422)
            start = source["text"].index(body.fragment)
            prefix = source["text"][:start]
            suffix = source["text"][start + len(body.fragment) :]
            instruction += "\nВерни только замену фрагмента без окружающего текста: " + body.fragment
        resolved = chat.generation.profiles.resolve(
            character_id=body.character_id, story_id=story_id, profile_id=body.profile_id
        )
        model_id = body.model_id or resolved["settings"]["model_id"]
        if not model_id:
            raise AppError("model_required", "Выберите загруженную модель.", 422)
        await chat.generation.adapter.require_loaded(model_id)
        if cancel.is_set():
            return {"partial": True}
        forked = await asyncio.to_thread(
            chat.fork,
            story_id,
            expected_revision=body.expected_revision,
            through_id=body.message_id,
            drop_last=True,
            name="Вариант ответа",
            branch_id=payload["branch_id"],
        )
        await emit("branch_created", {"branch_id": forked["branch"]["id"]})
        request = {
            **body.model_dump(exclude={"message_id", "action", "fragment"}),
            "model_id": model_id,
            "parameters": body.parameters.model_dump(exclude_unset=True, exclude_none=True),
            "story_id": story_id,
            "branch_id": forked["branch"]["id"],
            "endpoint": payload["endpoint"],
            "expected_revision": forked["branch"]["revision"],
            "prefix": prefix,
            "suffix": suffix,
            "instruction": instruction,
            "source_message_ids": [source["id"]],
        }
        return await chat.generate(request, cancel, emit)

    chat.generation.queue.handlers["chat_regenerate"] = regeneration_handler

    @router.post("/{story_id}/regenerate", status_code=202)
    async def regenerate(
        story_id: str, body: Regenerate, idempotency_key: str = Header(min_length=1, max_length=128)
    ):
        branch = await asyncio.to_thread(chat.branch, story_id, body.branch_id)
        payload = {
            "request": body.model_dump(exclude_unset=True, exclude_none=True),
            "story_id": story_id,
            "branch_id": branch["id"],
            "endpoint": chat.generation.adapter.endpoint,
        }
        job = await chat.generation.queue.enqueue(
            "chat_regenerate",
            payload,
            idempotency_key=idempotency_key,
            identity={"story_id": story_id, "request": body.model_dump()},
            scope={"story_id": story_id, "branch_id": branch["id"], "revision": body.expected_revision},
        )
        return {"generation_id": job["id"]}

    return router
