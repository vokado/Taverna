from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Query

from .tool_models import NameRun, TemplatePreview, ToolExecute, ToolWrite


def router(service):
    api = APIRouter(prefix="/api/v1/tools")

    @api.get("")
    def list(
        query: str = Query("", max_length=2000),
        kind: Literal["note", "bookmark", "dictionary", "name_generator", "template"] | None = None,
        archived: bool = False,
    ):
        return service.list(query, kind, archived)

    @api.get("/previews")
    def previews():
        rows, errors = service.store.scan("manuscripts/tool-previews/", tolerate_corrupt=True)
        return {"items": [r["value"] for r in rows], "errors": errors}

    @api.post("/previews/{pid}/execute")
    def execute(pid: UUID, body: ToolExecute):
        return service.execute(str(pid), body.expected_revision, body.confirmed)

    @api.put("/{tid}")
    def save(tid: UUID, body: ToolWrite):
        return service.save(str(tid), body.expected_revision, body.payload.model_dump(mode="json"))

    @api.get("/{tid}")
    def get(tid: UUID):
        return service.get(str(tid))

    @api.post("/{tid}/names")
    def names(tid: UUID, body: NameRun):
        return service.names(str(tid), body)

    @api.post("/{tid}/preview")
    def preview(tid: UUID, body: TemplatePreview):
        return service.preview(str(tid), body)

    return api
