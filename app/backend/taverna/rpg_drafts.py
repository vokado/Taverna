from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter
from pydantic import Field

from .draft_shapes import relaxed
from .rpg_models import RpgSettings
from .storage import Mutation, document
from .world_models import Strict

DraftSettings = relaxed(RpgSettings)


class Editor(Strict):
    settings: DraftSettings
    reason: str = Field(max_length=2000)
    seed: str = Field(max_length=128)


class Draft(Strict):
    expected_revision: int = Field(ge=0)
    template_id: UUID | None = None
    source_revision: int = Field(ge=1)
    raw: str = Field(max_length=1024 * 1024)
    payload: Editor


def router(service):
    api = APIRouter(prefix="/api/v1/stories/{story_id}/rpg/drafts/{branch_id}")

    def prefix(sid, bid):
        service.chat.branch(str(sid), str(bid))
        return f"config/rpg-drafts/{sid}/{bid}/"

    @api.get("")
    def read(story_id: UUID, branch_id: UUID):
        rows, errors = service.store.scan(prefix(story_id, branch_id), tolerate_corrupt=True)
        return {"items": [row["value"] for row in rows], "errors": errors}

    @api.put("/{draft_id}")
    def write(story_id: UUID, branch_id: UUID, draft_id: UUID, body: Draft):
        key = prefix(story_id, branch_id) + str(draft_id) + ".json"
        value = service.store.get(key) or document(
            "rpg_draft",
            {},
            entity_id=str(draft_id),
            refs=[service.chat.branch_key(str(story_id), str(branch_id))],
        )
        value["payload"] = body.model_dump(mode="json", exclude={"expected_revision"})
        service.store.transaction([Mutation(key, value, body.expected_revision)])
        return service.store.get(key)

    @api.delete("/{draft_id}")
    def delete(story_id: UUID, branch_id: UUID, draft_id: UUID, expected_revision: int):
        service.store.transaction(
            [Mutation(prefix(story_id, branch_id) + str(draft_id) + ".json", None, expected_revision)]
        )
        return {"deleted": True}

    @api.post("/validate")
    def validate(story_id: UUID, branch_id: UUID, body: RpgSettings):
        return body.model_dump(mode="json")

    return api
