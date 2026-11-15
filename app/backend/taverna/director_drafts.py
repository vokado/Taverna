from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter
from pydantic import ConfigDict, Field, create_model

from .core import AppError
from .director_models import DirectorPolicy, Emotion, Mode
from .storage import Mutation, document
from .world_models import Strict


def shape_model(name, source):
    # Drafts retain out-of-range numbers; publication still uses the full domain validators.
    return create_model(
        name,
        __config__=ConfigDict(extra="forbid", allow_inf_nan=False),
        **{
            key: (
                value.annotation,
                Field(default_factory=value.default_factory) if value.default_factory else value.default,
            )
            for key, value in source.model_fields.items()
        },
    )


PolicyDraft = shape_model("DirectorPolicyDraft", DirectorPolicy)
EmotionDraft = shape_model("DirectorEmotionDraft", Emotion)


class NPCDraft(Strict):
    mode: Mode = "fixed"
    emotions_enabled: bool = True
    relationships_enabled: bool = True
    emotion: EmotionDraft = Field(default_factory=EmotionDraft)
    traits: dict[str, float] = Field(default_factory=dict)
    adaptations: dict[str, float] = Field(default_factory=dict)


class Bundle(Strict):
    actor_id: UUID
    policy: PolicyDraft
    npc: NPCDraft
    reason: str = Field(max_length=2000)
    dirty_policy: bool = False
    dirty_npc: bool = False
    dirty_raw: bool = False


class DraftWrite(Strict):
    expected_revision: int = Field(ge=0)
    template_id: UUID | None = None
    source_revision: int = Field(ge=1)
    raw: str = Field(max_length=1024 * 1024)
    payload: Bundle


def router(service):
    router = APIRouter(prefix="/api/v1/stories/{story_id}/director/drafts/{branch_id}")

    def prefix(story_id, branch_id):
        service.chat.branch(str(story_id), str(branch_id))
        return f"config/director-drafts/{story_id}/{branch_id}/"

    @router.get("")
    def read(story_id: UUID, branch_id: UUID):
        rows, errors = service.store.scan(prefix(story_id, branch_id), tolerate_corrupt=True)
        return {"items": [row["value"] for row in rows], "errors": errors}

    @router.put("/{draft_id}")
    def write(story_id: UUID, branch_id: UUID, draft_id: UUID, body: DraftWrite):
        key = prefix(story_id, branch_id) + str(draft_id) + ".json"
        story = service.chat.story(str(story_id))
        if (
            str(body.payload.actor_id) not in story["payload"]["character_snapshots"]
            or body.template_id != body.payload.actor_id
        ):
            raise AppError("knowledge_scope", "Черновик относится к другому NPC.", 422)
        current = service.store.get(key)
        value = current or document(
            "director_draft",
            {},
            entity_id=str(draft_id),
            refs=[service.chat.branch_key(str(story_id), str(branch_id))],
        )
        value["payload"] = body.model_dump(mode="json", exclude={"expected_revision"})
        service.store.transaction([Mutation(key, value, body.expected_revision)])
        return service.store.get(key)

    @router.delete("/{draft_id}")
    def delete(story_id: UUID, branch_id: UUID, draft_id: UUID, expected_revision: int):
        service.store.transaction(
            [Mutation(prefix(story_id, branch_id) + str(draft_id) + ".json", None, expected_revision)]
        )
        return {"deleted": True}

    return router
