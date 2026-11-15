from __future__ import annotations

import asyncio
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Header
from fastapi.responses import JSONResponse
from pydantic import Field

from .core import AppError
from .director_ai import DirectorAI
from .director_models import DirectorPolicy, Emotion, Mode, NPCState, Trait
from .world_models import Strict


class Scope(Strict):
    branch_id: UUID
    expected_revision: int = Field(ge=1)


class PolicyWrite(Scope):
    policy: DirectorPolicy
    reason: str = Field(min_length=1, max_length=2000)


class NPCPatch(Strict):
    mode: Mode | None = None
    emotions_enabled: bool | None = None
    relationships_enabled: bool | None = None
    emotion: Emotion | None = None
    traits: dict[Trait, float] | None = None
    adaptations: dict[Trait, float] | None = None


class NPCWrite(Scope):
    patch: NPCPatch
    reason: str = Field(min_length=1, max_length=2000)


class EditorValidation(Strict):
    policy: DirectorPolicy
    npc: NPCPatch


class Analyze(Scope):
    character_id: UUID
    model_id: str | None = Field(default=None, min_length=1, max_length=256)
    profile_id: UUID | None = None


class Review(Scope):
    proposal_revision: int = Field(ge=1)
    accept: bool
    confirmed: bool = False
    rebase: bool = False


class Dismiss(Scope):
    character_id: UUID
    target_id: UUID
    kind: Literal["intention", "arc"]
    reason: str = Field(min_length=1, max_length=2000)


class Initiative(Analyze):
    intention_id: UUID
    confirmed: bool = False


def router(service):
    ai = DirectorAI(service)
    service.ai = ai
    router = APIRouter(prefix="/api/v1/stories/{story_id}/director")

    @router.get("")
    def read(story_id: UUID, branch_id: UUID):
        return service.read(str(story_id), str(branch_id))

    @router.post("/validate")
    def validate(story_id: UUID, body: EditorValidation):
        try:
            npc = NPCState.model_validate(
                {**NPCState().model_dump(mode="json"), **body.npc.model_dump(mode="json", exclude_unset=True)}
            )
        except ValueError as exc:
            raise AppError(
                "invalid_npc_state", "Состояние NPC выходит за допустимые типы или пределы.", 422
            ) from exc
        return {
            "policy": body.policy.model_dump(mode="json"),
            "npc": {
                key: value
                for key, value in npc.model_dump(mode="json").items()
                if key in NPCPatch.model_fields
            },
        }

    @router.put("/policy")
    def policy(story_id: UUID, body: PolicyWrite):
        return service.write_policy(
            str(story_id), str(body.branch_id), body.expected_revision, body.policy, body.reason
        )

    @router.put("/npcs/{actor_id}")
    def npc(story_id: UUID, actor_id: UUID, body: NPCWrite):
        return service.write_npc(
            str(story_id),
            str(body.branch_id),
            str(actor_id),
            body.expected_revision,
            body.patch.model_dump(mode="json", exclude_unset=True),
            body.reason,
        )

    @router.post("/analyze")
    async def analyze(story_id: UUID, body: Analyze, idempotency_key: str = Header(alias="Idempotency-Key")):
        snap = await asyncio.to_thread(service.memory.scheduling_view, str(story_id), str(body.branch_id))
        if str(body.character_id) not in snap["story"]["payload"]["character_snapshots"]:
            raise AppError("knowledge_scope", "NPC отсутствует в истории.", 422)
        if not service.policy(snap)["enabled"]:
            raise AppError("director_disabled", "Сначала включите режиссёра для этой ветви.", 409)
        payload = {
            "story_id": str(story_id),
            **body.model_dump(mode="json"),
            "endpoint": service.generation.adapter.endpoint,
        }
        job = await service.generation.queue.enqueue(
            "director",
            payload,
            priority=10,
            idempotency_key=idempotency_key,
            scope={
                "story_id": str(story_id),
                "branch_id": str(body.branch_id),
                "revision": body.expected_revision,
            },
        )
        return JSONResponse({"generation_id": job["id"]}, status_code=202)

    @router.get("/proposals")
    def proposals(story_id: UUID, branch_id: UUID):
        service.chat.branch(str(story_id), str(branch_id))
        rows, errors = service.store.scan(f"events/director/{story_id}/{branch_id}/", tolerate_corrupt=True)
        return {"items": [row["value"] for row in rows], "errors": errors}

    @router.post("/proposals/{proposal_id}/review")
    def review(story_id: UUID, proposal_id: UUID, body: Review):
        return service.review(
            str(story_id),
            str(body.branch_id),
            str(proposal_id),
            expected_revision=body.expected_revision,
            proposal_revision=body.proposal_revision,
            accept=body.accept,
            confirmed=body.confirmed,
            rebase=body.rebase,
        )

    @router.post("/dismiss")
    def dismiss(story_id: UUID, body: Dismiss):
        return service.dismiss(
            str(story_id),
            str(body.branch_id),
            body.expected_revision,
            str(body.character_id),
            str(body.target_id),
            body.kind,
            body.reason,
        )

    @router.post("/initiative")
    async def initiative(
        story_id: UUID, body: Initiative, idempotency_key: str = Header(alias="Idempotency-Key")
    ):
        if not body.confirmed:
            raise AppError("confirmation_required", "Подтвердите отдельную реплику NPC.", 422)
        snap = await asyncio.to_thread(
            service.snapshot, str(story_id), str(body.branch_id), body.expected_revision
        )
        actor = str(body.character_id)
        service.intention(snap, actor, str(body.intention_id))
        resolved = await asyncio.to_thread(
            service.generation.profiles.resolve,
            story_id=str(story_id),
            character_id=actor,
            profile_id=str(body.profile_id) if body.profile_id else None,
        )
        model_id = body.model_id or resolved["settings"]["model_id"] or service.policy(snap)["model_id"]
        payload = {
            "story_id": str(story_id),
            **body.model_dump(mode="json", exclude={"confirmed"}),
            "endpoint": service.generation.adapter.endpoint,
            "model_id": model_id,
            "skip_director": True,
            "director_initiative": True,
            "parameters": {},
        }
        job = await service.generation.queue.enqueue(
            "director_turn",
            payload,
            priority=0,
            idempotency_key=idempotency_key,
            scope={
                "story_id": str(story_id),
                "branch_id": str(body.branch_id),
                "revision": body.expected_revision,
            },
        )
        return JSONResponse({"generation_id": job["id"]}, status_code=202)

    return router
