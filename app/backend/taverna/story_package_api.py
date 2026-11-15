import asyncio
from uuid import UUID

from fastapi import APIRouter, Header, Request
from fastapi.responses import Response

from .story_packages import StoryPackages


def router(chat):
    packages = StoryPackages(chat)
    router = APIRouter()

    @router.post("/api/v1/stories/{story_id}/export-package")
    def export(story_id: UUID):
        return Response(
            packages.export(str(story_id)),
            media_type="application/zip",
            headers={"Content-Disposition": 'attachment; filename="taverna-story.zip"'},
        )

    @router.post(
        "/api/v1/story-packages/preview",
        openapi_extra={
            "requestBody": {
                "required": True,
                "content": {"application/octet-stream": {"schema": {"type": "string", "format": "binary"}}},
            }
        },
    )
    async def preview(request: Request):
        raw = await request.body()
        return await asyncio.to_thread(packages.preview, raw)

    @router.post(
        "/api/v1/story-packages/import",
        openapi_extra={
            "requestBody": {
                "required": True,
                "content": {"application/octet-stream": {"schema": {"type": "string", "format": "binary"}}},
            }
        },
    )
    async def apply(
        request: Request,
        confirmed: bool = Header(default=False, alias="X-Confirm-Import"),
        sha256: str = Header(alias="X-Package-SHA256", pattern="^[a-f0-9]{64}$"),
    ):
        raw = await request.body()
        return await asyncio.to_thread(packages.apply, raw, confirmed, sha256)

    return router
