import copy
from uuid import uuid4

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field

from .chat import identifier
from .core import AppError
from .storage import Mutation, digest, document, encoded


class EventTransfer(BaseModel):
    model_config = ConfigDict(extra="forbid")
    operation_id: str
    branch_id: str
    expected_revision: int = Field(ge=1)
    source_story_id: str
    source_branch_id: str
    source_revision: int = Field(ge=1)
    event_ids: list[str] = Field(min_length=1, max_length=1000)
    confirmed: bool = False


def transfer(chat, story_id, body: EventTransfer):
    operation_id = identifier(body.operation_id)
    target = chat.branch(story_id, body.branch_id)
    source = chat.branch(body.source_story_id, body.source_branch_id)
    if target["id"] == source["id"]:
        raise AppError("same_scope", "Источник и получатель должны быть разными ветвями.", 422)
    fingerprint = digest(encoded({"target_story": story_id, **body.model_dump(exclude={"confirmed"})}))
    key = f"config/operations/{operation_id}.json"
    existing = chat.store.get(key)
    if existing:
        if existing["payload"]["fingerprint"] != fingerprint:
            raise AppError("idempotency_conflict", "Операция уже использована с другими параметрами.", 409)
        return {"transferred": existing["payload"]["event_ids"], "already_applied": True}
    if source["revision"] != body.source_revision or target["revision"] != body.expected_revision:
        raise AppError("branch_conflict", "Источник или получатель изменился. Повторите предпросмотр.", 409)
    data = chat.state(body.source_story_id, body.source_branch_id)["payload"]
    events = {
        event["id"]: event
        for event in data["events"]
        if isinstance(event, dict) and isinstance(event.get("id"), str)
    }
    if len(set(body.event_ids)) != len(body.event_ids) or any(
        event_id not in events for event_id in body.event_ids
    ):
        raise AppError("event_missing", "Выбранное событие отсутствует или повторяется.", 422)
    selected = [events[event_id] for event_id in body.event_ids]
    for event in selected:
        identifier(event["id"])
        if not isinstance(event.get("knower_ids", []), list):
            raise AppError("invalid_event", "Список осведомлённых NPC в событии повреждён.", 422)
    warning = "Переносятся только записи событий. Последствия мира не применяются автоматически, память и отношения NPC не переносятся и не становятся общими знаниями."
    if not body.confirmed:
        return {"events": selected, "warning": warning, "saved": False}
    target_data = chat.state(story_id, body.branch_id)["payload"]
    target_characters = set(chat.story(story_id)["payload"]["character_snapshots"])
    result = []
    for event in selected:
        cloned = {
            **copy.deepcopy(event),
            "id": str(uuid4()),
            "story_id": story_id,
            "branch_id": body.branch_id,
            "status": "imported_record",
            "origin": {
                "story_id": body.source_story_id,
                "branch_id": body.source_branch_id,
                "event_id": event["id"],
            },
        }
        cloned["knower_ids"] = [npc for npc in event.get("knower_ids", []) if npc in target_characters]
        cloned["origin_knower_ids"] = copy.deepcopy(event.get("knower_ids", []))
        target_data["events"].append(cloned)
        result.append(cloned["id"])
    log = document(
        "event_transfer",
        {
            "fingerprint": fingerprint,
            "event_ids": result,
            "source": body.model_dump(exclude={"confirmed"}),
            "warning": warning,
        },
        entity_id=operation_id,
    )
    chat.update_state(
        story_id,
        target_data,
        body.expected_revision,
        body.branch_id,
        extra_changes=[Mutation(key, log, 0)],
        guards={chat.branch_key(body.source_story_id, body.source_branch_id): body.source_revision},
    )
    return {"transferred": result, "already_applied": False, "warning": warning}


def router(chat):
    router = APIRouter(prefix="/api/v1")

    @router.get("/stories/{story_id}/transfer-events")
    def events(story_id: str, branch_id: str):
        branch = chat.branch(story_id, branch_id)
        return {
            "events": chat.state(story_id, branch_id)["payload"]["events"],
            "revision": branch["revision"],
        }

    @router.post("/stories/{story_id}/transfer-events")
    def transfer_events(story_id: str, body: EventTransfer):
        return transfer(chat, story_id, body)

    return router
