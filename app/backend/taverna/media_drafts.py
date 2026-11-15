from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter
from pydantic import Field

from .draft_shapes import relaxed
from .media_models import AssetMetadata, MediaState
from .storage import Mutation, document
from .world_models import Strict

DraftMetadata = relaxed(AssetMetadata)
DraftScene = relaxed(MediaState)


class LibraryDraft(Strict):
    expected_revision: int = Field(ge=0)
    template_id: UUID
    source_revision: int = Field(ge=1)
    raw: str = Field(max_length=1024 * 1024)
    payload: DraftMetadata


class SceneEditor(Strict):
    media: DraftScene
    reason: str = Field(max_length=2000)


class SceneDraft(Strict):
    expected_revision: int = Field(ge=0)
    template_id: UUID | None = None
    source_revision: int = Field(ge=1)
    raw: str = Field(max_length=1024 * 1024)
    payload: SceneEditor


def router(service):
    api = APIRouter(prefix="/api/v1")

    def write(key, draft_id, body, refs=()):
        current = service.store.get(key) or document(
            "media_draft", {}, entity_id=str(draft_id), refs=list(refs)
        )
        current["payload"] = body.model_dump(mode="json", exclude={"expected_revision"})
        service.store.transaction([Mutation(key, current, body.expected_revision)])
        return service.store.get(key)

    @api.get("/media/drafts")
    def library_read():
        rows, errors = service.store.scan("config/media-drafts/", tolerate_corrupt=True)
        return {"items": [r["value"] for r in rows], "errors": errors}

    @api.put("/media/drafts/{draft_id}")
    def library_write(draft_id: UUID, body: LibraryDraft):
        service.get(str(body.template_id))
        return write(
            f"config/media-drafts/{draft_id}.json", draft_id, body, [service.key(str(body.template_id))]
        )

    @api.delete("/media/drafts/{draft_id}")
    def library_delete(draft_id: UUID, expected_revision: int):
        service.store.transaction([Mutation(f"config/media-drafts/{draft_id}.json", None, expected_revision)])
        return {"deleted": True}

    @api.post("/media/drafts/validate")
    def validate_metadata(body: AssetMetadata):
        return body.model_dump(mode="json")

    def scene_prefix(sid, bid):
        service.chat.branch(str(sid), str(bid))
        return f"config/scene-drafts/{sid}/{bid}/"

    @api.get("/stories/{story_id}/media/drafts/{branch_id}")
    def scene_read(story_id: UUID, branch_id: UUID):
        rows, errors = service.store.scan(scene_prefix(story_id, branch_id), tolerate_corrupt=True)
        return {"items": [r["value"] for r in rows], "errors": errors}

    @api.put("/stories/{story_id}/media/drafts/{branch_id}/{draft_id}")
    def scene_write(story_id: UUID, branch_id: UUID, draft_id: UUID, body: SceneDraft):
        return write(
            scene_prefix(story_id, branch_id) + str(draft_id) + ".json",
            draft_id,
            body,
            [service.chat.branch_key(str(story_id), str(branch_id))],
        )

    @api.delete("/stories/{story_id}/media/drafts/{branch_id}/{draft_id}")
    def scene_delete(story_id: UUID, branch_id: UUID, draft_id: UUID, expected_revision: int):
        service.store.transaction(
            [Mutation(scene_prefix(story_id, branch_id) + str(draft_id) + ".json", None, expected_revision)]
        )
        return {"deleted": True}

    @api.post("/stories/{story_id}/media/drafts/{branch_id}/validate")
    def validate_scene(story_id: UUID, branch_id: UUID, body: MediaState):
        return body.model_dump(mode="json")

    return api
