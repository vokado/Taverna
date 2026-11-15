from __future__ import annotations

from fastapi import APIRouter, Header
from fastapi.responses import JSONResponse
from pydantic import Field

from .core import AppError
from .image_models import ImageRequest, ImageSettings
from .world_models import Strict


class ConfigureImage(Strict):
    expected_revision: int = Field(ge=0)
    settings: ImageSettings


class ReconcileImage(Strict):
    cancel_external: bool = False
    confirmed: bool = False


class ReleaseImage(Strict):
    expected_revision: int = Field(ge=1)
    reason: str = Field(min_length=1, max_length=2000)
    confirmed: bool = False


def router(service):
    api = APIRouter(prefix="/api/v1/images")

    @api.get("/settings")
    def settings():
        return {"config": service.settings(), "lease": service.lease()}

    @api.get("/jobs")
    def jobs():
        return service.recent_jobs()

    @api.get("/jobs/{job_id}")
    def image_job(job_id: str):
        return service.job(job_id)

    @api.put("/settings")
    def configure(body: ConfigureImage):
        return service.save_settings(body.settings, body.expected_revision)

    @api.get("/capabilities")
    async def capabilities():
        return await service.capabilities()

    @api.post("/generate")
    async def generate(body: ImageRequest, idempotency_key: str = Header(alias="Idempotency-Key")):
        job = await service.enqueue(body, idempotency_key)
        return JSONResponse({"generation_id": job["id"], "state": job["payload"]["state"]}, status_code=202)

    @api.post("/lease/reconcile")
    async def reconcile(body: ReconcileImage):
        if body.cancel_external and not body.confirmed:
            raise AppError(
                "confirmation_required", "Подтвердите адресную остановку собственной внешней задачи.", 422
            )
        return await service.reconcile(body.cancel_external)

    @api.post("/lease/manual-release")
    def release(body: ReleaseImage):
        return service.manual_release(body.expected_revision, body.reason, body.confirmed)

    return api
