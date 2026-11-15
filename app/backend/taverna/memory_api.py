from __future__ import annotations

import copy
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Header
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from .core import AppError
from .knowledge import DraftRecordInput, Level, MemoryRecord, Nature, RecordInput
from .memory_ai import MemoryAI, MemorySettings
from .memory_io import MemoryPackages
from .memory_search import MemorySearch
from .storage import Mutation, document


class Scope(BaseModel):
    model_config = ConfigDict(extra="forbid")
    branch_id: UUID
    expected_revision: int = Field(ge=1)


class RecordWrite(Scope):
    expected_version: int = Field(default=0, ge=0)
    record: RecordInput
    reason: str = Field(min_length=1, max_length=2000)
    conflict_policy: Literal["keep_both", "supersede"] | None = None


class RecordValidation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    record: RecordInput


class DraftValidation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    record: DraftRecordInput


class RecordDelete(Scope):
    expected_version: int = Field(ge=1)
    confirmed: bool = False
    reason: str = Field(default="Удалено владельцем", min_length=1, max_length=2000)


class SearchRequest(Scope):
    character_id: UUID | None = None
    query: str = Field(default="", max_length=4000)
    level: Level | None = None
    nature: Nature | None = None
    subject_id: UUID | None = None
    source_id: UUID | None = None
    location_id: str | None = Field(default=None, max_length=200)
    time_from: str | None = Field(default=None, max_length=200)
    time_to: str | None = Field(default=None, max_length=200)
    include_messages: bool = False
    embedding_model_id: str | None = Field(default=None, min_length=1, max_length=256)
    limit: int = Field(default=50, ge=1, le=200)


class ExtractRequest(Scope):
    character_id: UUID
    model_id: str | None = Field(default=None, min_length=1, max_length=256)
    profile_id: UUID | None = None
    source_ids: list[UUID] = Field(default_factory=list, max_length=100)
    source_fact_ids: list[UUID] = Field(default_factory=list, max_length=100)
    operation: Literal["extract", "summarize"] = "extract"


class ProposalReview(Scope):
    proposal_revision: int = Field(ge=1)
    accept: bool
    confirmed: bool = False
    rebase: bool = False
    conflict_policy: Literal["keep_both"] | None = None
    records: list[RecordInput] | None = Field(default=None, max_length=32)


class PolicyWrite(Scope):
    settings: MemorySettings


class ArchiveRequest(Scope):
    min_salience: float = Field(default=0.2, ge=0, le=1)
    min_age_messages: int = Field(default=100, ge=0, le=1000000)


class ConflictResolution(Scope):
    record_id: UUID
    expected_version: int = Field(ge=1)
    decision: Literal["keep_both", "supersede"]
    reason: str = Field(min_length=1, max_length=2000)


class ImportRequest(Scope):
    text: str = Field(max_length=12 * 1024 * 1024)
    actor_map: dict[UUID, UUID] = Field(default_factory=dict)
    source_map: dict[UUID, UUID] = Field(default_factory=dict)
    allow_external_sources: bool = False
    confirmed: bool = False
    conflict_policy: Literal["keep_both"] | None = None


class DraftWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")
    draft_id: UUID
    branch_id: UUID
    expected_revision: int = Field(ge=0)
    record_id: UUID | None = None
    record_version: int = Field(default=0, ge=0)
    source_revision: int = Field(ge=1)
    form: dict
    policy_draft: dict | None = None
    raw: str = Field(max_length=524288)
    applied_raw: str = Field(default="", max_length=524288)
    reason: str = Field(min_length=1, max_length=2000)
    conflict_policy: Literal["keep_both", "supersede"] | None = None


class DraftDelete(BaseModel):
    model_config = ConfigDict(extra="forbid")
    draft_id: UUID
    branch_id: UUID
    expected_revision: int = Field(ge=0)


def router(service):
    ai = MemoryAI(service)
    search = MemorySearch(service)
    packages = MemoryPackages(service)
    service.ai, service.search, service.packages = ai, search, packages
    service.chat.after_message.subscribe("memory", ai.on_message)
    router = APIRouter(prefix="/api/v1/stories/{story_id}/memories")

    def draft_key(story_id, branch_id, draft_id):
        from .chat import identifier

        service.chat.branch(story_id, branch_id)
        return f"config/memory-drafts/{story_id}/{branch_id}/{identifier(draft_id)}.json"

    @router.get("/draft")
    def draft(story_id: str, branch_id: str):
        service.chat.branch(story_id, branch_id)
        # Retain any single-draft records from the early internal stage-08 working format.
        legacy_key = f"config/memory-drafts/{story_id}/{branch_id}.json"
        legacy = service.store.get(legacy_key)
        if legacy:
            target = draft_key(story_id, branch_id, legacy["id"])
            if service.store.get(target) is None:
                service.store.transaction(
                    [Mutation(target, legacy, 0), Mutation(legacy_key, None, legacy["revision"])]
                )
        rows, errors = service.store.scan(
            f"config/memory-drafts/{story_id}/{branch_id}/", tolerate_corrupt=True
        )
        values = sorted(
            [row["value"] for row in rows], key=lambda value: (value["updated_at"], value["id"]), reverse=True
        )
        value = values[0] if values else None
        return {
            "draft": value,
            "drafts": values,
            "revision": value["revision"] if value else 0,
            "errors": errors,
        }

    @router.put("/draft")
    def save_draft(story_id: str, body: DraftWrite):
        key = draft_key(story_id, str(body.branch_id), str(body.draft_id))
        value = service.store.get(key) or document(
            "memory_draft",
            {},
            entity_id=str(body.draft_id),
            refs=[service.chat.branch_key(story_id, str(body.branch_id))],
        )
        value["payload"] = body.model_dump(
            mode="json", exclude={"expected_revision", "branch_id", "draft_id"}
        )
        service.store.transaction([Mutation(key, value, body.expected_revision)])
        return {"draft": service.store.get(key), "revision": body.expected_revision + 1}

    @router.delete("/draft")
    def delete_draft(story_id: str, body: DraftDelete):
        service.store.transaction(
            [
                Mutation(
                    draft_key(story_id, str(body.branch_id), str(body.draft_id)), None, body.expected_revision
                )
            ]
        )
        return {"deleted": True}

    @router.get("")
    def records(story_id: str, branch_id: str, character_id: str | None = None):
        return service.list(story_id, branch_id, character_id)

    @router.post("")
    def create(story_id: str, body: RecordWrite):
        return service.save(
            story_id,
            str(body.branch_id),
            body.expected_revision,
            body.record,
            expected_version=body.expected_version,
            reason=body.reason,
            conflict_policy=body.conflict_policy,
        )

    @router.post("/validate")
    def validate_record(story_id: str, body: RecordValidation):
        return body.record.model_dump(mode="json")

    @router.post("/draft-validate")
    def validate_draft(story_id: str, body: DraftValidation):
        return body.record.model_dump(mode="json")

    @router.post("/search", status_code=202)
    async def find(
        story_id: str, body: SearchRequest, idempotency_key: str = Header(min_length=1, max_length=128)
    ):
        payload = {
            **body.model_dump(mode="json"),
            "story_id": story_id,
            "endpoint": service.generation.adapter.endpoint,
        }
        job = await service.generation.queue.enqueue(
            "memory_search",
            payload,
            priority=5,
            idempotency_key=idempotency_key,
            scope={
                "story_id": story_id,
                "branch_id": str(body.branch_id),
                "revision": body.expected_revision,
            },
        )
        return {"generation_id": job["id"]}

    @router.post("/index", status_code=202)
    async def index(story_id: str, body: Scope, idempotency_key: str = Header(min_length=1, max_length=128)):
        payload = {**body.model_dump(mode="json"), "story_id": story_id}
        job = await service.generation.queue.enqueue(
            "memory_index",
            payload,
            priority=10,
            idempotency_key=idempotency_key,
            scope={
                "story_id": story_id,
                "branch_id": str(body.branch_id),
                "revision": body.expected_revision,
            },
        )
        return {"generation_id": job["id"]}

    @router.post("/semantic-cache/reset", status_code=202)
    async def reset_vectors(
        story_id: str, body: Scope, idempotency_key: str = Header(min_length=1, max_length=128)
    ):
        payload = {**body.model_dump(mode="json"), "story_id": story_id, "operation": "reset_vectors"}
        job = await service.generation.queue.enqueue(
            "memory_index",
            payload,
            priority=10,
            idempotency_key=idempotency_key,
            scope={
                "story_id": story_id,
                "branch_id": str(body.branch_id),
                "revision": body.expected_revision,
            },
        )
        return {"generation_id": job["id"]}

    @router.get("/settings")
    def policy(story_id: str, branch_id: str):
        snap = service.scheduling_view(story_id, branch_id)
        settings = MemorySettings.model_validate(snap["state"]["payload"].get("memory_policy", {}))
        resolved = service.generation.profiles.resolve(story_id=story_id, role="memory")
        return {
            "settings": settings.model_dump(),
            "branch_revision": snap["branch"]["revision"],
            "assigned_model": settings.model_id or resolved["settings"]["model_id"],
            "message_ru": "Модель для автоматического извлечения задаётся в политике или служебном профиле памяти; также используется выбранная модель текущего ответа NPC.",
        }

    @router.put("/settings")
    def set_policy(story_id: str, body: PolicyWrite):
        snap = service.snapshot(story_id, str(body.branch_id))
        data = copy.deepcopy(snap["state"]["payload"])
        data["memory_policy"] = body.settings.model_dump()
        state = service.chat.update_state(story_id, data, body.expected_revision, str(body.branch_id))
        return {
            "state_id": state["id"],
            "branch_revision": service.chat.branch(story_id, str(body.branch_id))["revision"],
        }

    @router.post("/archive")
    def archive(story_id: str, body: ArchiveRequest):
        return service.archive(
            story_id,
            str(body.branch_id),
            body.expected_revision,
            min_salience=body.min_salience,
            min_age_messages=body.min_age_messages,
        )

    @router.get("/timeline")
    def timeline(story_id: str, branch_id: str, character_id: str | None = None):
        return service.timeline(story_id, branch_id, character_id)

    @router.get("/conflicts")
    def conflicts(story_id: str, branch_id: str):
        snap = service.snapshot(story_id, branch_id)
        pairs = []
        seen = set()
        for record in snap["records"].values():
            try:
                MemoryRecord.model_validate(record)
            except ValueError:
                continue
            for other in service.conflicts(snap["records"], record):
                pair = tuple(sorted([record["id"], other["id"]]))
                if pair in seen:
                    continue
                if any(
                    decision.get("decision") == "keep_both"
                    and all(
                        decision.get("versions", {}).get(row["id"]) == row["version"]
                        for row in (record, other)
                    )
                    for decision in snap["state"]["payload"].get("memory_conflict_decisions", [])
                ):
                    continue
                seen.add(pair)
                pairs.append({"records": [record, other], "claim_key": record["claim_key"]})
        return {"conflicts": pairs, "branch_revision": snap["branch"]["revision"]}

    @router.post("/conflicts/resolve")
    def resolve_conflict(story_id: str, body: ConflictResolution):
        snap = service.snapshot(story_id, str(body.branch_id))
        record = snap["records"].get(str(body.record_id))
        if not record:
            raise AppError("memory_missing", "Запись не найдена.", 404)
        value = RecordInput.model_validate({key: record[key] for key in RecordInput.model_fields})
        return service.save(
            story_id,
            str(body.branch_id),
            body.expected_revision,
            value,
            record_id=str(body.record_id),
            expected_version=body.expected_version,
            reason=body.reason,
            conflict_policy=body.decision,
        )

    @router.post("/extract", status_code=202)
    async def extract(
        story_id: str, body: ExtractRequest, idempotency_key: str = Header(min_length=1, max_length=128)
    ):
        return await ai.enqueue(story_id, body.model_dump(mode="json"), idempotency_key)

    @router.get("/proposals")
    def proposals(story_id: str, branch_id: str):
        service.chat.branch(story_id, branch_id)
        rows, errors = service.store.scan(f"memory/proposals/{story_id}/{branch_id}/", tolerate_corrupt=True)
        return {"proposals": [row["value"] for row in rows], "errors": errors}

    @router.post("/proposals/{proposal_id}/review")
    def review(story_id: str, proposal_id: str, body: ProposalReview):
        data = body.model_dump(mode="json")
        branch_id = data.pop("branch_id")
        return ai.review(story_id, branch_id, proposal_id, **data)

    @router.post("/export")
    def export(story_id: str, body: Scope):
        package = packages.export(story_id, str(body.branch_id))
        if service.chat.branch(story_id, str(body.branch_id))["revision"] != body.expected_revision:
            raise AppError("branch_conflict", "Ветвь изменилась при экспорте.", 409)
        return JSONResponse(
            package, headers={"Content-Disposition": 'attachment; filename="Taverna-memory.json"'}
        )

    @router.post("/import-preview")
    def import_preview(story_id: str, body: ImportRequest):
        data = body.model_dump(mode="json")
        return packages.preview(
            story_id,
            str(body.branch_id),
            body.text,
            actor_map=data["actor_map"],
            source_map=data["source_map"],
            allow_external_sources=body.allow_external_sources,
        )

    @router.post("/import")
    def import_package(story_id: str, body: ImportRequest):
        data = body.model_dump(mode="json")
        data.pop("branch_id")
        data.pop("expected_revision")
        return packages.apply(story_id, str(body.branch_id), body.expected_revision, **data)

    @router.get("/{record_id}/sources")
    def sources(story_id: str, record_id: str, branch_id: str, character_id: str | None = None):
        snap = service.snapshot(story_id, branch_id)
        record = snap["records"].get(record_id)
        if not record:
            raise AppError("memory_missing", "Запись памяти не найдена.", 404)
        if character_id and service.policy(snap).reason(record, character_id):
            raise AppError("knowledge_scope", "Запись недоступна выбранному NPC.", 403)
        return {
            "sources": [
                {
                    "id": sid,
                    "message": service.policy(snap).project_message(snap["messages"][sid], character_id)
                    if character_id and sid in snap["messages"]
                    else snap["messages"].get(sid)
                    if character_id is None
                    else None,
                }
                for sid in record.get("source_ids", [])
            ],
            "fact_dependencies": record.get("source_fact_versions", {}),
            "history": record.get("history", []) if character_id is None else [],
            "history_omitted": character_id is not None,
        }

    @router.get("/{record_id}")
    def read_record(story_id: str, record_id: str, branch_id: str, character_id: str | None = None):
        snap = service.snapshot(story_id, branch_id, include_messages=bool(character_id))
        record = snap["records"].get(record_id)
        if record is None:
            raise AppError("memory_missing", "Запись памяти не найдена.", 404)
        try:
            MemoryRecord.model_validate(record)
        except ValueError as exc:
            raise AppError(
                "invalid_memory", "Запись прежнего формата требует ручного просмотра.", 422
            ) from exc
        if character_id:
            record = service.policy(snap).project(record, character_id)
            if record is None:
                raise AppError("knowledge_scope", "Запись недоступна выбранному NPC.", 403)
        return {"record": record, "branch_revision": snap["branch"]["revision"]}

    @router.patch("/{record_id}")
    def update(story_id: str, record_id: str, body: RecordWrite):
        return service.save(
            story_id,
            str(body.branch_id),
            body.expected_revision,
            body.record,
            expected_version=body.expected_version,
            record_id=record_id,
            reason=body.reason,
            conflict_policy=body.conflict_policy,
        )

    @router.delete("/{record_id}")
    def delete(story_id: str, record_id: str, body: RecordDelete):
        if not body.confirmed:
            raise AppError("confirmation_required", "Подтвердите удаление записи памяти.", 422)
        snap = service.snapshot(story_id, str(body.branch_id))
        old = snap["records"].get(record_id)
        if old is None:
            raise AppError("memory_missing", "Запись памяти не найдена.", 404)
        value = RecordInput.model_validate(
            {k: v for k, v in old.items() if k in RecordInput.model_fields}
        ).model_copy(update={"status": "deleted"})
        return service.save(
            story_id,
            str(body.branch_id),
            body.expected_revision,
            value,
            expected_version=body.expected_version,
            record_id=record_id,
            reason=body.reason,
        )

    return router
