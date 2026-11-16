from __future__ import annotations

import asyncio
from uuid import UUID

import portalocker
from fastapi import APIRouter, Header, Query, Request
from pydantic import Field

from .core import AppError
from .plugin_actions import PluginDecision, PluginRun
from .plugin_models import PluginGrant
from .world_models import Strict


class PluginEditor(Strict):
    catalog_revision: int = Field(default=0, ge=0)
    approved_sha256: str = Field(default="", max_length=64)
    enabled: bool = False
    grants: list[PluginGrant] = Field(default_factory=list, max_length=32)
    trusted_full_access_ack: bool = False


class ConfigurePlugin(PluginEditor):
    expected_revision: int = Field(ge=1)
    catalog_revision: int = Field(ge=1)
    approved_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    confirmed: bool = False


class RemovePlugin(Strict):
    expected_revision: int = Field(ge=1)
    catalog_revision: int = Field(ge=1)
    approved_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    confirmed: bool = False


def guarded(operation, *args):
    try:
        return operation(*args)
    except portalocker.exceptions.LockException as exc:
        raise AppError("plugin_busy", "Пакет занят другой операцией. Повторите позже.", 409) from exc


async def cancel_stale(registry, queue):
    if queue is None:
        return
    rows, _ = await asyncio.to_thread(registry.store.scan, "jobs/records/")
    for row in rows:
        p = row["value"]["payload"]
        r = p.get("request", {})
        if p.get("kind") not in ("plugin_compute", "plugin_effect") or p["state"] not in (
            "queued",
            "running",
        ):
            continue
        try:
            await asyncio.to_thread(
                registry.require_current,
                r["plugin_id"],
                r["plugin_revision"],
                r["permission_epoch"],
                r["package_sha256"],
                catalog_revision=r.get("catalog_revision"),
            )
        except (AppError, KeyError):
            await queue.cancel(row["value"]["id"])


def router(registry, queue=None, service=None):
    api = APIRouter(prefix="/api/v1/plugins")

    @api.get("")
    def list_plugins():
        return registry.list()

    @api.post("/import-preview")
    async def preview(request: Request):
        return await asyncio.to_thread(guarded, registry.preview, await request.body())

    @api.post("/import")
    async def install(
        request: Request,
        expected_revision: int = Query(ge=0),
        catalog_revision: int = Query(ge=0),
        approved_sha256: str = Query(pattern=r"^[a-f0-9]{64}$"),
        confirmed: bool = False,
    ):
        result = await asyncio.to_thread(
            guarded,
            registry.install,
            await request.body(),
            expected_revision,
            catalog_revision,
            approved_sha256,
            confirmed,
        )
        await cancel_stale(registry, queue)
        return result

    @api.post("/{pid}/run")
    async def run(
        pid: UUID,
        body: PluginRun,
        idempotency_key: str = Header(alias="Idempotency-Key", min_length=1, max_length=128),
    ):
        return await service.enqueue(str(pid), body, idempotency_key)

    @api.get("/{pid}/data/{target}")
    def own_data(pid: UUID, target: str):
        return service.own_data(str(pid), target)

    @api.get("/{pid}/previews")
    def previews(pid: UUID):
        return service.previews(str(pid))

    @api.post("/previews/{preview_id}/accept")
    async def accept(preview_id: UUID, body: PluginDecision):
        return await asyncio.to_thread(
            service.decide, str(preview_id), body.expected_revision, body.confirmed
        )

    @api.post("/previews/{preview_id}/reject")
    async def reject(preview_id: UUID, body: PluginDecision):
        return await asyncio.to_thread(
            service.decide, str(preview_id), body.expected_revision, body.confirmed, False
        )

    @api.get("/{pid}/entry")
    def entry(pid: UUID):
        row = registry.get(str(pid))
        files = registry.verify(row)
        manifest = row["payload"]["manifest"]
        raw = files[manifest["entry"]]
        return {
            "filename": manifest["entry"],
            "runtime": manifest["runtime"],
            "sha256": row["payload"]["files"][manifest["entry"]],
            "bytes": len(raw),
            "text": None if manifest["runtime"] == "wasm" else raw.decode("utf-8"),
        }

    @api.get("/{pid}")
    def get(pid: UUID):
        return registry.get(str(pid))

    @api.put("/{pid}")
    async def configure(pid: UUID, body: ConfigurePlugin):
        result = await asyncio.to_thread(
            guarded,
            registry.configure,
            str(pid),
            body.expected_revision,
            body.catalog_revision,
            body.enabled,
            body.model_dump(mode="json")["grants"],
            body.trusted_full_access_ack,
            body.approved_sha256,
            body.confirmed,
        )
        await cancel_stale(registry, queue)
        return result

    @api.delete("/{pid}")
    async def remove(pid: UUID, body: RemovePlugin):
        result = await asyncio.to_thread(
            guarded,
            registry.remove,
            str(pid),
            body.expected_revision,
            body.catalog_revision,
            body.approved_sha256,
            body.confirmed,
        )
        await cancel_stale(registry, queue)
        return result

    return api
