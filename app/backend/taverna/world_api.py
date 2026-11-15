from __future__ import annotations

import asyncio
import json
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Header
from fastapi.responses import JSONResponse, Response
from pydantic import Field, model_validator

from .cards import parse_json
from .core import AppError
from .storage import Mutation, document, encoded
from .world_drafts import DraftPayload
from .world_lore import preview_lore
from .world_models import Effect, Scenario, Strict, World
from .world_packages import WorldPackages

Kind = Literal["worlds", "scenarios"]


class TemplateWrite(Strict):
    expected_revision: int = Field(ge=0)
    payload: dict


class Attach(Strict):
    branch_id: UUID
    expected_revision: int = Field(ge=1)
    world_id: UUID
    scenario_id: UUID | None = None
    start_id: UUID | None = None
    seed: int | None = Field(default=None, ge=0, le=9223372036854775807)
    confirmed: bool = False


class WorldEvent(Strict):
    branch_id: UUID
    expected_revision: int = Field(ge=1)
    event_id: UUID
    operation: Literal["manual", "time_advance", "move"] = "manual"
    minutes: int = Field(default=0, ge=0, le=1000000000)
    location_id: UUID | None = None
    effects: list[Effect] = Field(default_factory=list, max_length=64)
    reason: str = Field(min_length=1, max_length=2000)
    cause_ids: list[UUID] = Field(default_factory=list, max_length=100)
    source_ids: list[UUID] = Field(default_factory=list, max_length=100)
    confirmed: bool = False

    @model_validator(mode="after")
    def parameters(self):
        if self.operation == "time_advance" and (self.minutes < 1 or self.location_id is not None):
            raise ValueError("Пропуск времени требует положительные минуты без перемещения")
        if self.operation == "move" and (self.location_id is None or self.minutes):
            raise ValueError("Перемещение требует локацию, без временного пропуска")
        if self.operation == "manual" and (self.minutes or self.location_id is not None):
            raise ValueError("Ручные последствия задаются в effects")
        return self


class LoreImport(Strict):
    text: str = Field(max_length=12 * 1024 * 1024)
    public: bool = False
    expected_revision: int = Field(default=0, ge=0)
    confirmed: bool = False


class PackageImport(Strict):
    text: str = Field(max_length=12 * 1024 * 1024)
    confirmed: bool = False


class WorldAssistant(Strict):
    kind: Kind = "worlds"
    instruction: str = Field(min_length=1, max_length=16000)
    world_id: UUID | None = None
    template_id: UUID | None = None
    expected_revision: int = Field(default=0, ge=0)
    model_id: str | None = Field(default=None, min_length=1, max_length=256)
    profile_id: UUID | None = None


class WorldAI:
    def __init__(self, service):
        self.service, self.generation, self.store = service, service.generation, service.store
        self.generation.queue.handlers["world_assistant"] = self.run
        self.generation.queue.preemptible_kinds.add("world_assistant")

    async def run(self, payload, cancel, emit):
        kind = payload["kind"]
        original = None
        if payload.get("template_id"):
            original = await asyncio.to_thread(self.service.get, kind, payload["template_id"])
            if original["revision"] != payload["expected_revision"]:
                raise AppError("world_state_conflict", "Шаблон изменился до запуска помощника.", 409)
        world = (
            await asyncio.to_thread(self.service.get, "worlds", payload["world_id"])
            if payload.get("world_id")
            else None
        )
        if kind == "scenarios" and not world:
            raise AppError("world_missing", "Выберите мир для сценария.", 422)
        model = World if kind == "worlds" else Scenario
        messages = [
            {
                "role": "system",
                "content": "Создай или расширь шаблон мира/сценария. Не меняй требования пользователя и не пиши от лица игрока. Верни только JSON по схеме: "
                + json.dumps(model.model_json_schema(), ensure_ascii=False),
            },
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "instruction": payload["instruction"],
                        "original": original["payload"] if original else None,
                        "world": world,
                    },
                    ensure_ascii=False,
                ),
            },
        ]
        result = await self.generation.run(
            {
                "messages": messages,
                "model_id": payload.get("model_id"),
                "profile_id": payload.get("profile_id"),
                "parameters": {},
                "service_role": "world",
                "endpoint": payload["endpoint"],
            },
            cancel,
            emit,
        )
        if cancel.is_set():
            return {"partial": True}
        text = result["text"].strip()
        if text.startswith("```json") and text.endswith("```"):
            text = text[7:-3].strip()
        try:
            data = self.service.validate(kind, parse_json(text.encode("utf-8")))
        except (ValueError, AppError) as exc:
            raise AppError(
                "invalid_world_proposal",
                "Помощник вернул недопустимый шаблон; исходные данные сохранены.",
                422,
            ) from exc
        if kind == "scenarios" and data["world_id"] != payload["world_id"]:
            raise AppError("invalid_world_proposal", "Помощник изменил выбранный мир.", 422)
        proposal = document(
            "world_proposal",
            {
                "status": "pending",
                "kind": kind,
                "template_id": payload.get("template_id"),
                "base_revision": payload["expected_revision"],
                "payload": data,
                "context_trace_id": result["context_trace_id"],
            },
        )
        guards = {self.service.key(kind, original["id"]): original["revision"]} if original else {}
        if world:
            guards[self.service.key("worlds", world["id"])] = world["revision"]
        self.store.transaction(
            [Mutation("worlds/proposals/" + proposal["id"] + ".json", proposal, 0)], guards=guards
        )
        return {"proposal": proposal}


class ProposalReview(Strict):
    proposal_revision: int = Field(ge=1)
    expected_revision: int = Field(ge=0)
    accept: bool
    confirmed: bool = False
    payload: dict | None = None


class TemplateDraft(Strict):
    expected_revision: int = Field(ge=0)
    template_id: UUID | None = None
    source_revision: int = Field(ge=0)
    raw: str = Field(max_length=2 * 1024 * 1024)
    payload: dict


def router(service):
    router = APIRouter()
    packages = WorldPackages(service)
    service.ai = WorldAI(service)

    @router.get("/api/v1/template-drafts/{kind}")
    def drafts(kind: Kind):
        rows, errors = service.store.scan(f"config/template-drafts/{kind}/", tolerate_corrupt=True)
        return {"items": [r["value"] for r in rows], "errors": errors}

    @router.put("/api/v1/template-drafts/{kind}/{draft_id}")
    def draft_write(kind: Kind, draft_id: UUID, body: TemplateDraft):
        try:
            DraftPayload.model_validate(body.payload)
        except ValueError as exc:
            raise AppError(
                "invalid_world_draft",
                "Форма черновика имеет недопустимые типы. Незавершённый JSON храните в поле raw.",
                422,
            ) from exc
        key = f"config/template-drafts/{kind}/{draft_id}.json"
        current = service.store.get(key)
        value = current or document("template_draft", {}, entity_id=str(draft_id))
        value["payload"] = body.model_dump(mode="json", exclude={"expected_revision"})
        service.store.transaction([Mutation(key, value, body.expected_revision)])
        return service.store.get(key)

    @router.delete("/api/v1/template-drafts/{kind}/{draft_id}")
    def draft_delete(kind: Kind, draft_id: UUID, expected_revision: int):
        key = f"config/template-drafts/{kind}/{draft_id}.json"
        service.store.transaction([Mutation(key, None, expected_revision)])
        return {"deleted": True}

    @router.get("/api/v1/templates/{kind}")
    def templates(kind: Kind):
        return service.list(kind)

    @router.post("/api/v1/templates/{kind}/validate")
    def validate(kind: Kind, body: TemplateWrite):
        return {"payload": service.validate(kind, body.payload)}

    @router.post("/api/v1/templates/{kind}")
    def create(kind: Kind, body: TemplateWrite):
        return service.save(kind, body.payload, body.expected_revision)

    @router.get("/api/v1/templates/{kind}/{entity_id}")
    def read(kind: Kind, entity_id: UUID):
        return service.get(kind, str(entity_id))

    @router.put("/api/v1/templates/{kind}/{entity_id}")
    def save(kind: Kind, entity_id: UUID, body: TemplateWrite):
        return service.save(kind, body.payload, body.expected_revision, str(entity_id))

    @router.post("/api/v1/world-assistant")
    async def assistant(body: WorldAssistant, idempotency_key: str = Header(alias="Idempotency-Key")):
        job = await service.generation.queue.enqueue(
            "world_assistant",
            {**body.model_dump(mode="json"), "endpoint": service.generation.adapter.endpoint},
            priority=10,
            idempotency_key=idempotency_key,
        )
        return JSONResponse({"generation_id": job["id"]}, status_code=202)

    @router.get("/api/v1/world-proposals")
    def proposals():
        rows, corrupt = service.store.scan("worlds/proposals/", tolerate_corrupt=True)
        return {"items": [r["value"] for r in rows], "corrupt": corrupt}

    @router.post("/api/v1/world-proposals/{proposal_id}/review")
    def review(proposal_id: UUID, body: ProposalReview):
        if not body.confirmed:
            raise AppError("confirmation_required", "Подтвердите решение по предложению.", 422)
        key = "worlds/proposals/" + str(proposal_id) + ".json"
        proposal = service.store.get(key)
        if not proposal:
            raise AppError("proposal_missing", "Предложение не найдено.", 404)
        if proposal["payload"]["status"] != "pending":
            return {"proposal": proposal, "already_reviewed": True}
        changes, guards = [], {}
        if body.accept:
            if body.expected_revision != proposal["payload"]["base_revision"]:
                raise AppError(
                    "world_state_conflict", "Проверьте предложение относительно исходной версии шаблона.", 409
                )
            value, changes, guards = service.plan_save(
                proposal["payload"]["kind"],
                body.payload if body.payload is not None else proposal["payload"]["payload"],
                body.expected_revision,
                proposal["payload"]["template_id"],
            )
            proposal["payload"].update(status="accepted", applied_template_id=value["id"])
        else:
            proposal["payload"]["status"] = "rejected"
        service.store.transaction([*changes, Mutation(key, proposal, body.proposal_revision)], guards=guards)
        return {"proposal": service.store.get(key), "already_reviewed": False}

    @router.post("/api/v1/lorebooks/preview")
    def lore_preview(body: LoreImport):
        return preview_lore(body.text, public=body.public)

    @router.post("/api/v1/templates/worlds/{world_id}/lore-import")
    def lore_confirm(world_id: UUID, body: LoreImport):
        preview = preview_lore(body.text, public=body.public)
        if not body.confirmed:
            raise AppError("confirmation_required", "Подтвердите импорт лорбука после предпросмотра.", 422)
        world = service.get("worlds", str(world_id))
        existing = {entry["id"] for entry in world["payload"]["lore"]}
        world["payload"]["lore"].extend(e for e in preview["entries"] if e["id"] not in existing)
        world["payload"]["extras"].setdefault("lore_imports", {})[preview["sha256"]] = preview["original"]
        return {
            "world": service.save("worlds", world["payload"], body.expected_revision, str(world_id)),
            "warnings": preview["warnings"],
        }

    @router.post("/api/v1/templates/worlds/{world_id}/export")
    def export(world_id: UUID):
        return Response(
            encoded(packages.export(str(world_id))),
            media_type="application/json",
            headers={"Content-Disposition": 'attachment; filename="taverna-world.json"'},
        )

    @router.post("/api/v1/world-packages/preview")
    def preview(body: PackageImport):
        return packages.preview(body.text)

    @router.post("/api/v1/world-packages/import")
    def import_package(body: PackageImport):
        return packages.apply(body.text, body.confirmed)

    @router.get("/api/v1/stories/{story_id}/world")
    def state(story_id: UUID, branch_id: UUID):
        return service.read(str(story_id), str(branch_id))

    @router.post("/api/v1/stories/{story_id}/world/attach")
    def attach(story_id: UUID, body: Attach):
        data = body.model_dump(mode="json")
        return service.attach(str(story_id), **data)

    @router.post("/api/v1/stories/{story_id}/world/events")
    def event(story_id: UUID, body: WorldEvent):
        if not body.confirmed:
            raise AppError("confirmation_required", "Подтвердите изменение мира.", 422)
        data = body.model_dump(mode="json", exclude={"confirmed"})
        return service.apply(str(story_id), **data)

    return router
