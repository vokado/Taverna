from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, Request
from fastapi.responses import FileResponse, Response
from pydantic import Field

from .core import AppError
from .media_models import AssetMetadata, Category, MediaState
from .world_models import Strict


class Metadata(AssetMetadata):
    expected_revision: int = Field(ge=1)


class SceneWrite(Strict):
    branch_id: UUID
    expected_revision: int = Field(ge=1)
    media: MediaState
    reason: str = Field(min_length=1, max_length=2000)


def router(service):
    api = APIRouter(prefix="/api/v1")

    @api.get("/media")
    def media(
        query: str = Query("", max_length=2000),
        tag: str | None = None,
        category: Category | None = None,
        archived: bool = False,
        offset: int = Query(0, ge=0),
        limit: int = Query(50, ge=1, le=500),
    ):
        return service.list(query, tag, category, archived, offset, limit)

    @api.post("/media/import")
    async def import_media(
        request: Request,
        name: str = Query(min_length=1, max_length=300),
        category: Category = "illustration",
        tags: Annotated[list[str] | None, Query()] = None,
        confirmed: bool = False,
    ):
        if not confirmed:
            raise AppError("confirmation_required", "Подтвердите импорт собственного медиафайла.", 422)
        import asyncio

        return await asyncio.to_thread(service.import_raw, await request.body(), name, category, tags or [])

    @api.get("/media/{asset_id}/metadata")
    def metadata(asset_id: UUID):
        return service.get(str(asset_id))

    @api.get("/media/{asset_id}/file")
    def file(asset_id: UUID):
        value, path = service.checked_file(str(asset_id))
        return FileResponse(
            path,
            media_type=value["payload"]["type"],
            headers={"Cache-Control": "private, max-age=3600"},
        )

    @api.get("/media/{asset_id}/thumbnail")
    def thumbnail(asset_id: UUID):
        return Response(
            service.thumbnail(str(asset_id)),
            media_type="image/png",
            headers={"Cache-Control": "private, max-age=3600"},
        )

    @api.patch("/media/{asset_id}")
    def update(asset_id: UUID, body: Metadata):
        return service.update(
            str(asset_id), body.expected_revision, body.model_dump(exclude={"expected_revision"})
        )

    @api.get("/stories/{story_id}/media")
    def scene(story_id: UUID, branch_id: UUID):
        return service.read_scene(str(story_id), str(branch_id))

    @api.get("/stories/{story_id}/media/stage")
    def stage(story_id: UUID, branch_id: UUID):
        result = service.read_scene(str(story_id), str(branch_id))
        return {k: v for k, v in result.items() if k != "media"}

    @api.put("/stories/{story_id}/media")
    def save_scene(story_id: UUID, body: SceneWrite):
        return service.save_scene(
            str(story_id), str(body.branch_id), body.expected_revision, body.media, body.reason
        )

    return api
