from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter
from pydantic import Field

from .draft_shapes import relaxed
from .image_models import ImageRequest, ImageSettings
from .storage import Mutation, document
from .world_models import Strict

DraftRequest = relaxed(ImageRequest)
DraftSettings = relaxed(ImageSettings)


class ImageEditor(Strict):
    settings: DraftSettings
    request: DraftRequest
    dirty_settings: bool = False


class ImageDraft(Strict):
    expected_revision: int = Field(ge=0)
    template_id: UUID | None = None
    source_revision: int = Field(ge=0)
    raw: str = Field(max_length=1024 * 1024)
    payload: ImageEditor


def router(service):
    api = APIRouter(prefix="/api/v1/images/drafts")
    prefix = "config/image-drafts/"

    @api.get("")
    def read():
        rows, errors = service.store.scan(prefix, tolerate_corrupt=True)
        return {"items": [r["value"] for r in rows], "errors": errors}

    @api.put("/{draft_id}")
    def write(draft_id: UUID, body: ImageDraft):
        key = prefix + str(draft_id) + ".json"
        value = service.store.get(key) or document("image_draft", {}, entity_id=str(draft_id))
        value["payload"] = body.model_dump(mode="json", exclude={"expected_revision"})
        service.store.transaction([Mutation(key, value, body.expected_revision)])
        return service.store.get(key)

    @api.delete("/{draft_id}")
    def delete(draft_id: UUID, expected_revision: int):
        service.store.transaction([Mutation(prefix + str(draft_id) + ".json", None, expected_revision)])
        return {"deleted": True}

    return api
