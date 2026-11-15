from uuid import UUID

from fastapi import APIRouter, Query, Request
from fastapi.responses import FileResponse
from pydantic import Field

from .core import AppError
from .library_models import MaterialMetadata


class MaterialWrite(MaterialMetadata):
    expected_revision: int = Field(ge=1)


def router(service):
    api = APIRouter(prefix="/api/v1/library")

    @api.get("")
    def list(query: str = Query("", max_length=2000), archived: bool = False):
        return service.list(query, archived)

    @api.post("/import")
    async def create(
        request: Request,
        filename: str = Query(min_length=1, max_length=250),
        name: str = Query(min_length=1, max_length=300),
        confirmed: bool = False,
    ):
        if not confirmed:
            raise AppError("confirmation_required", "Подтвердите импорт документа.", 422)
        import asyncio

        return await asyncio.to_thread(service.import_raw, await request.body(), filename, {"name": name})

    @api.get("/{mid}")
    def read(mid: UUID):
        return service.get(str(mid))

    @api.patch("/{mid}")
    def update(mid: UUID, body: MaterialWrite):
        return service.update(
            str(mid), body.expected_revision, body.model_dump(mode="json", exclude={"expected_revision"})
        )

    @api.get("/{mid}/file")
    def file(mid: UUID):
        value, path = service.checked_file(str(mid))
        return FileResponse(
            path,
            media_type=value["payload"]["mime"],
            filename=value["payload"]["filename"],
            content_disposition_type="attachment",
        )

    return api
