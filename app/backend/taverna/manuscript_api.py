from __future__ import annotations

import asyncio
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Header, Query
from fastapi.responses import JSONResponse, Response
from pydantic import Field

from .book_export import BookExporter
from .core import AppError
from .draft_shapes import relaxed
from .manuscript_models import BookAI, BookWrite, Manuscript
from .storage import Mutation, document
from .world_models import Strict


class RestoreBook(Strict):
    expected_revision: int = Field(ge=1)
    number: int = Field(ge=1)
    confirmed: bool = False


class ReviewBook(Strict):
    proposal_revision: int = Field(ge=1)
    accept: bool
    confirmed: bool = False


class BookDraft(Strict):
    expected_revision: int = Field(ge=0)
    template_id: UUID | None = None
    source_revision: int = Field(ge=0)
    raw: str = Field(max_length=4 * 1024 * 1024)
    payload: relaxed(Manuscript)


def router(service):
    api = APIRouter(prefix="/api/v1/manuscripts")
    exporter = BookExporter(service)

    @api.get("")
    def list_books(
        query: str = Query("", max_length=2000),
        offset: int = Query(0, ge=0),
        limit: int = Query(50, ge=1, le=500),
    ):
        return service.list(query, offset, limit)

    @api.get("/drafts")
    def drafts():
        rows, errors = service.store.scan("manuscripts/drafts/", tolerate_corrupt=True)
        return {"items": [r["value"] for r in rows], "errors": errors}

    @api.put("/drafts/{did}")
    def draft(did: UUID, body: BookDraft):
        key = f"manuscripts/drafts/{did}.json"
        value = service.store.get(key) or document("manuscript_draft", {}, entity_id=str(did))
        value["payload"] = body.model_dump(mode="json", exclude={"expected_revision"})
        service.store.transaction([Mutation(key, value, body.expected_revision)])
        return service.store.get(key)

    @api.delete("/drafts/{did}")
    def delete_draft(did: UUID, expected_revision: int):
        service.store.transaction([Mutation(f"manuscripts/drafts/{did}.json", None, expected_revision)])
        return {"deleted": True}

    @api.get("/proposals")
    def proposals():
        rows, errors = service.store.scan("manuscripts/proposals/", tolerate_corrupt=True)
        return {"items": [r["value"] for r in rows], "errors": errors}

    @api.post("/proposals/{pid}/review")
    def review(pid: UUID, body: ReviewBook):
        return service.review(str(pid), body.proposal_revision, body.accept, body.confirmed)

    @api.post("/assistant")
    async def assistant(body: BookAI, idempotency_key: str = Header(alias="Idempotency-Key")):
        request = body.model_dump(mode="json")
        await asyncio.to_thread(service.source, request)
        payload = {"request": request, "endpoint": service.generation.adapter.endpoint}
        job = await service.generation.queue.enqueue(
            "manuscript_ai", payload, priority=20, idempotency_key=idempotency_key
        )
        return JSONResponse({"generation_id": job["id"], "state": job["payload"]["state"]}, status_code=202)

    @api.get("/{mid}")
    def read(mid: UUID):
        return service.get(str(mid))

    @api.put("/{mid}")
    def save(mid: UUID, body: BookWrite):
        return service.save(
            str(mid), body.expected_revision, body.payload.model_dump(mode="json"), body.reason
        )

    @api.get("/{mid}/history")
    def history(mid: UUID):
        return service.history(str(mid))

    @api.post("/{mid}/restore")
    def restore(mid: UUID, body: RestoreBook):
        if not body.confirmed:
            raise AppError("confirmation_required", "Подтвердите восстановление в новую версию.", 422)
        return service.restore(str(mid), body.number, body.expected_revision)

    @api.get("/{mid}/export")
    def export(
        mid: UUID, format: Literal["txt", "markdown", "html", "epub"], expected_revision: int = Query(ge=1)
    ):
        raw, mime, name = exporter.export(str(mid), format, expected_revision)
        return Response(
            raw, media_type=mime, headers={"Content-Disposition": f'attachment; filename="{name}"'}
        )

    return api
