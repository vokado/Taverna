from typing import Literal
from uuid import UUID

from fastapi import APIRouter
from pydantic import Field

from .draft_shapes import relaxed
from .library_models import MaterialMetadata
from .storage import Mutation, document
from .tool_models import ToolEntry
from .world_models import Strict

Kind = Literal["material", "tool"]


class OwnerDraft(Strict):
    expected_revision: int = Field(ge=0)
    template_id: UUID | None = None
    source_revision: int = Field(ge=0)
    raw: str = Field(max_length=2 * 1024 * 1024)
    payload: dict


def router(store):
    api = APIRouter(prefix="/api/v1/owner-drafts")
    shapes = {"material": relaxed(MaterialMetadata), "tool": relaxed(ToolEntry)}

    @api.get("/{kind}")
    def read(kind: Kind):
        rows, errors = store.scan(f"config/owner-drafts/{kind}/", tolerate_corrupt=True)
        return {"items": [r["value"] for r in rows], "errors": errors}

    @api.put("/{kind}/{did}")
    def write(kind: Kind, did: UUID, body: OwnerDraft):
        from .core import AppError

        try:
            payload = shapes[kind].model_validate(body.payload).model_dump(mode="json")
        except ValueError as exc:
            raise AppError(
                "invalid_draft_shape", "Структура черновика не соответствует редактору.", 422
            ) from exc
        key = f"config/owner-drafts/{kind}/{did}.json"
        value = store.get(key) or document(kind + "_draft", {}, entity_id=str(did))
        value["payload"] = {**body.model_dump(mode="json", exclude={"expected_revision"}), "payload": payload}
        store.transaction([Mutation(key, value, body.expected_revision)])
        return store.get(key)

    @api.delete("/{kind}/{did}")
    def delete(kind: Kind, did: UUID, expected_revision: int):
        store.transaction([Mutation(f"config/owner-drafts/{kind}/{did}.json", None, expected_revision)])
        return {"deleted": True}

    return api
