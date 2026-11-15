from __future__ import annotations

import asyncio
import time
from uuid import NAMESPACE_URL, uuid5

from .core import AppError
from .image_engine import TERMINAL_IMAGE, ComfyAdapter
from .image_models import ImageLease, ImageRequest, ImageSettings
from .storage import Mutation, document, now

LEASE_KEY = "config/image-lease.json"
SETTINGS_KEY = "config/image-engine.json"
HEAVY_KINDS = {
    "llm",
    "chat",
    "chat_group",
    "chat_regenerate",
    "director",
    "director_batch",
    "director_turn",
    "memory_extract",
    "world_assistant",
    "image_generate",
}


class ImageService:
    def __init__(self, media, *, transport=None):
        self.media, self.chat, self.store = media, media.chat, media.store
        self.generation = self.chat.generation
        self.transport = transport
        self.store.register("image_engine_settings", 1, ImageSettings.model_json_schema())
        self.store.register("image_lease", 1, ImageLease.model_json_schema())
        self.generation.queue.handlers["image_generate"] = self.run
        self.generation.queue.preemptible_kinds.add("image_generate")
        self.generation.queue.resource_guard = self.resource_guard
        self.generation.inference_guard = self.inference_guard

    def settings(self):
        value = self.store.get(SETTINGS_KEY)
        return value or {"revision": 0, "payload": ImageSettings().model_dump(mode="json")}

    def lease(self):
        return self.store.get(LEASE_KEY)

    @staticmethod
    def busy(lease):
        return lease and lease["payload"]["state"] in ("running", "uncertain")

    def save_settings(self, settings, expected_revision):
        lease = self.lease()
        if self.busy(lease):
            raise AppError(
                "image_resource_busy", "Сначала остановите или согласуйте внешнюю задачу изображения.", 409
            )
        data = ImageSettings.model_validate(settings).model_dump(mode="json")
        value = self.store.get(SETTINGS_KEY) or document("image_engine_settings", {})
        value["payload"] = data
        self.store.transaction(
            [Mutation(SETTINGS_KEY, value, expected_revision)],
            guards={LEASE_KEY: lease["revision"] if lease else 0},
        )
        return self.settings()

    def recent_jobs(self, limit=20):
        rows, errors = self.store.scan("jobs/records/", tolerate_corrupt=True)
        rows = [
            r["value"]
            for r in rows
            if r["value"]["kind"] == "job" and r["value"]["payload"]["kind"] == "image_generate"
        ]
        rows.sort(key=lambda r: (r["created_at"], r["id"]), reverse=True)
        return {
            "items": [
                {
                    "id": r["id"],
                    "state": r["payload"]["state"],
                    "name": r["payload"]["request"]["request"]["name"],
                    "result": r["payload"]["result"],
                    "error": r["payload"]["error"],
                }
                for r in rows[:limit]
            ],
            "errors": errors,
        }

    def job(self, job_id):
        key = self.generation.queue.key(job_id)
        with self.store.view() as view:
            value = view.get(key)
            if not value or value["payload"].get("kind") != "image_generate":
                raise AppError("job_not_found", "Задача изображения не найдена.", 404)
            lease = view.get(LEASE_KEY)
            if lease and lease["payload"]["job_id"] != job_id:
                lease = None
            return {"generation_id": job_id, **value["payload"], "lease": lease}

    async def inference_guard(self, payload):
        await self.resource_guard("llm", payload)

    async def resource_guard(self, kind, payload):
        if kind not in HEAVY_KINDS and not (kind == "memory_search" and payload.get("embedding_model_id")):
            return
        lease = await asyncio.to_thread(self.lease)
        if self.busy(lease):
            raise AppError(
                "image_resource_busy",
                "Статус внешней GPU-задачи не подтверждён. Откройте генератор изображений: проверьте/остановите собственную задачу перед новым запросом.",
                409,
            )

    def adapter(self, endpoint):
        return ComfyAdapter(endpoint, transport=self.transport)

    async def capabilities(self):
        config = await asyncio.to_thread(self.settings)
        capabilities = await self.adapter(config["payload"]["endpoint"]).capabilities()
        return {**capabilities, "lm_loaded_instances": await self.loaded_lm()}

    async def loaded_lm(self):
        try:
            async with asyncio.timeout(3):
                return sorted(m["id"] for m in await self.generation.adapter.loaded_models())
        except (AppError, TimeoutError):
            return None

    def update_lease(self, prompt_id, patch):
        value = self.lease()
        if not value or value["payload"]["prompt_id"] != prompt_id:
            raise AppError("image_lease_conflict", "Ресурсная запись относится к другой внешней задаче.", 409)
        value["payload"] = {**value["payload"], **patch, "updated_at": now()}
        self.store.transaction([Mutation(LEASE_KEY, value, value["revision"])])
        return self.lease()

    def acquire(self, payload, devices, lm_before, cancel):
        current = self.lease()
        if self.busy(current):
            raise AppError("image_resource_busy", "Внешняя задача уже владеет GPU-слотом.", 409)
        stamp = now()
        value = current or document("image_lease", {})
        value["payload"] = ImageLease(
            state="running",
            endpoint=payload["endpoint"],
            prompt_id=payload["prompt_id"],
            job_id=payload["job_id"],
            request=ImageRequest.model_validate(payload["request"]),
            created_at=stamp,
            updated_at=stamp,
            lm_before=lm_before,
            devices_before=devices,
        ).model_dump(mode="json")
        self.store.transaction(
            [Mutation(LEASE_KEY, value, current["revision"] if current else 0)],
            guards={SETTINGS_KEY: payload["request"]["settings_revision"]},
            before_commit=lambda: self.chat.cancellation_guard(cancel),
        )

    async def enqueue(self, body, idempotency_key):
        body = body if isinstance(body, ImageRequest) else ImageRequest.model_validate(body)
        if not body.confirmed:
            raise AppError(
                "confirmation_required", "Подтвердите генерацию изображения на локальном движке.", 422
            )
        config = await asyncio.to_thread(self.settings)
        if not config["payload"]["enabled"]:
            raise AppError("image_disabled", "Сначала включите внешний генератор в его настройках.", 409)
        if body.settings_revision != config["revision"]:
            raise AppError("image_settings_conflict", "Настройки генератора изменились.", 409)
        if body.width * body.height > config["payload"]["max_pixels"]:
            raise AppError("image_resource_limit", "Разрешение превышает выбранный предел пикселей.", 422)
        if body.story_id:
            await asyncio.to_thread(
                self.media.worlds.snapshot, str(body.story_id), str(body.branch_id), body.expected_revision
            )
        # The identifiers must be stable across an idempotent enqueue retry.
        prompt_id = str(uuid5(NAMESPACE_URL, "taverna/image/" + config["id"] + "/" + idempotency_key))
        payload = {
            "prompt_id": prompt_id,
            "job_id": prompt_id,
            "endpoint": config["payload"]["endpoint"],
            "settings": config["payload"],
            "request": body.model_dump(mode="json"),
        }
        # Actual queue job ID is filled in by the worker, before claiming a lease.
        job = await self.generation.queue.enqueue(
            "image_generate", payload, priority=20, idempotency_key=idempotency_key
        )
        return job

    async def stop_external(self, adapter, prompt_id, wait_seconds):
        before = await adapter.job(prompt_id)
        if before and before["status"] in TERMINAL_IMAGE:
            return before
        dispatched = await adapter.cancel(prompt_id)
        deadline = time.monotonic() + wait_seconds
        while time.monotonic() < deadline:
            status = await adapter.job(prompt_id)
            if status and status["status"] in TERMINAL_IMAGE:
                return status
            # ComfyUI removes pending jobs without creating a history row.
            # A missing running/unknown job is never accepted as cancellation.
            if status is None and before and before["status"] == "pending" and dispatched["cancelled"]:
                return {
                    "id": prompt_id,
                    "status": "cancelled",
                    "evidence": "pending_seen_then_addressed_dequeue",
                }
            await asyncio.sleep(0.2)
        raise AppError(
            "image_cancel_uncertain",
            "Адресная отмена отправлена, но завершение внешней задачи ещё не подтверждено.",
            409,
        )

    async def uncertain(self, prompt_id, code):
        try:
            await asyncio.to_thread(self.update_lease, prompt_id, {"state": "uncertain", "error_code": code})
        except AppError:
            pass

    async def recover_cancel(self, adapter, prompt_id, wait_seconds):
        try:
            status = await self.stop_external(adapter, prompt_id, wait_seconds)
            await asyncio.to_thread(
                self.update_lease, prompt_id, {"state": "terminal", "external_status": status["status"]}
            )
            return status
        except AppError as exc:
            await self.uncertain(prompt_id, exc.code)
            return None

    async def save_output(self, adapter, lease, before_commit=None):
        p = lease["payload"]
        request = p["request"]
        pid = p["prompt_id"]
        raw, info = await adapter.output(pid)
        if info["width"] != request["width"] or info["height"] != request["height"]:
            raise AppError("image_output_invalid", "Размеры изображения не соответствуют запросу.", 502)
        aid = str(uuid5(NAMESPACE_URL, "taverna/image-result/" + p["endpoint"] + "/" + pid))
        provenance = {
            "source": "comfyui-modern-v1",
            "external_prompt_id": pid,
            "queue_job_id": p["job_id"],
            "endpoint": p["endpoint"],
            "request": request,
            "lm_before": p["lm_before"],
            "lm_after": p["lm_after"],
        }
        asset = await asyncio.to_thread(
            self.media.import_raw,
            raw,
            request["name"],
            request["category"],
            request["tags"],
            provenance,
            aid,
            before_commit,
        )
        await asyncio.to_thread(self.update_lease, pid, {"result_asset_id": asset["id"]})
        return asset

    async def run(self, payload, cancel, emit):
        pid = payload["prompt_id"]
        settings = payload["settings"]
        request = ImageRequest.model_validate(payload["request"]).model_dump(mode="json")
        if self.generation.queue.active:
            payload = {**payload, "job_id": self.generation.queue.active[0]}
        if request["story_id"]:
            await asyncio.to_thread(
                self.media.worlds.snapshot,
                request["story_id"],
                request["branch_id"],
                request["expected_revision"],
            )
        config = await asyncio.to_thread(self.settings)
        if config["revision"] != request["settings_revision"] or not config["payload"]["enabled"]:
            raise AppError("image_settings_conflict", "Настройки изменились до запуска изображения.", 409)
        adapter = self.adapter(payload["endpoint"])
        capabilities = await adapter.capabilities()
        devices = capabilities["devices"]
        threshold = settings["minimum_free_vram_mib"] * 1024 * 1024
        primary = devices[0] if devices else {}
        if threshold and primary.get("type") == "cuda":
            if "vram_free" not in primary:
                raise AppError(
                    "image_resource_unknown", "Движок не сообщил свободную VRAM; порог нельзя проверить.", 409
                )
            if primary["vram_free"] < threshold:
                raise AppError(
                    "image_vram_low", "Свободной VRAM меньше заданного порога. LM Studio не выгружается.", 409
                )
        graph = adapter.graph(request, capabilities, pid)
        lm_before = await self.loaded_lm()
        self.chat.cancellation_guard(cancel)
        claim = asyncio.create_task(asyncio.to_thread(self.acquire, payload, devices, lm_before, cancel))
        try:
            await asyncio.shield(claim)
        except asyncio.CancelledError:
            try:
                await asyncio.shield(claim)
            except AppError:
                raise asyncio.CancelledError from None
            await asyncio.to_thread(
                self.update_lease, pid, {"state": "terminal", "external_status": "not_submitted"}
            )
            raise
        submitted = False
        external_started = False
        terminal = False
        try:
            await emit(
                "image_resources",
                {
                    "devices": devices,
                    "lm_loaded_instances": lm_before,
                    "lm_lifecycle_changed_by_app": False,
                    "hardware_verified": False,
                },
            )
            await emit("image_external_id", {"prompt_id": pid, "endpoint": adapter.endpoint})
            # Shield only submission acknowledgement. Cancelling its socket is not
            # evidence that the external server did not accept the workflow.
            external_started = True
            submission = asyncio.create_task(adapter.submit(pid, graph))
            try:
                await asyncio.shield(submission)
            except asyncio.CancelledError:
                try:
                    await asyncio.shield(submission)
                    submitted = True
                    await asyncio.to_thread(self.update_lease, pid, {"acknowledged": True})
                except AppError:
                    await self.uncertain(pid, "image_submission_unknown")
                raise
            submitted = True
            await asyncio.to_thread(self.update_lease, pid, {"acknowledged": True})
            deadline = time.monotonic() + settings["timeout_seconds"]
            last = None
            while time.monotonic() < deadline:
                self.chat.cancellation_guard(cancel)
                status = await adapter.job(pid)
                if status is None:
                    raise AppError(
                        "image_external_missing",
                        "Принятая внешняя задача не найдена. Требуется проверка состояния.",
                        409,
                    )
                if status["status"] != last:
                    last = status["status"]
                    await emit("image_status", {"external_status": last})
                if last in TERMINAL_IMAGE:
                    terminal = True
                    finish = asyncio.create_task(
                        asyncio.to_thread(
                            self.update_lease,
                            pid,
                            {"state": "terminal", "external_status": last, "error_code": None},
                        )
                    )
                    try:
                        await asyncio.shield(finish)
                    except asyncio.CancelledError:
                        await asyncio.shield(finish)
                        raise
                    lm_after = await self.loaded_lm()
                    await asyncio.to_thread(self.update_lease, pid, {"lm_after": lm_after})
                    if last != "completed":
                        err = status.get("execution_error") or {}
                        kind = (
                            str(err.get("exception_type", "")) + " " + str(err.get("exception_message", ""))
                        )
                        code = (
                            "image_oom"
                            if "outofmemory" in kind.casefold().replace(" ", "")
                            else "image_engine_failed"
                        )
                        raise AppError(
                            code,
                            "Генератор сообщил нехватку памяти. LM Studio не выгружался."
                            if code == "image_oom"
                            else "Внешняя задача остановлена или завершилась ошибкой.",
                            502,
                        )
                    self.chat.cancellation_guard(cancel)
                    lease = await asyncio.to_thread(self.lease)
                    asset = await self.save_output(
                        adapter, lease, before_commit=lambda: self.chat.cancellation_guard(cancel)
                    )
                    source_current = True
                    if request["story_id"]:
                        try:
                            await asyncio.to_thread(
                                self.media.worlds.snapshot,
                                request["story_id"],
                                request["branch_id"],
                                request["expected_revision"],
                            )
                        except AppError:
                            source_current = False
                    await emit(
                        "image_saved",
                        {
                            "asset_id": asset["id"],
                            "source_current": source_current,
                            "automatic_branch_write": False,
                        },
                    )
                    return {
                        "asset_id": asset["id"],
                        "prompt_id": pid,
                        "source_current": source_current,
                        "lm_before": lm_before,
                        "lm_after": lm_after,
                        "lm_instances_changed": None
                        if lm_before is None or lm_after is None
                        else lm_before != lm_after,
                    }
                await asyncio.sleep(0.3)
            raise AppError(
                "image_generation_timeout",
                "Истёк лимит ожидания изображения; отправляется адресная отмена.",
                504,
            )
        except asyncio.CancelledError:
            if not terminal:
                if not external_started:
                    await asyncio.shield(
                        asyncio.to_thread(
                            self.update_lease, pid, {"state": "terminal", "external_status": "not_submitted"}
                        )
                    )
                elif submitted:
                    await asyncio.shield(self.recover_cancel(adapter, pid, settings["cancel_wait_seconds"]))
                else:
                    await asyncio.shield(self.uncertain(pid, "image_submission_unknown"))
            raise
        except AppError as exc:
            if not terminal:
                if not external_started:
                    await asyncio.to_thread(
                        self.update_lease, pid, {"state": "terminal", "external_status": "not_submitted"}
                    )
                elif (
                    not submitted
                    and exc.code == "image_engine_rejected"
                    and exc.details.get("http_status") in (400, 401, 403, 405, 422)
                ):
                    await asyncio.to_thread(
                        self.update_lease, pid, {"state": "terminal", "external_status": "rejected"}
                    )
                elif submitted:
                    await self.recover_cancel(adapter, pid, settings["cancel_wait_seconds"])
                else:
                    await self.uncertain(pid, "image_submission_unknown")
            raise

    def manual_release(self, expected_revision, reason, confirmed):
        lease = self.lease()
        if not confirmed or not reason.strip():
            raise AppError(
                "confirmation_required",
                "Подтвердите ручную остановку движка и укажите основание снятия блокировки.",
                422,
            )
        if not lease:
            raise AppError("image_external_missing", "Ресурсная запись отсутствует.", 404)
        if self.generation.queue.active and self.generation.queue.active[0] == lease["payload"]["job_id"]:
            raise AppError("image_resource_busy", "Сначала остановите задачу в общей очереди.", 409)
        lease["payload"].update(state="manually_released", manual_reason=reason, updated_at=now())
        self.store.transaction([Mutation(LEASE_KEY, lease, expected_revision)])
        return self.lease()

    async def reconcile(self, cancel_external=False):
        lease = await asyncio.to_thread(self.lease)
        if not lease:
            return {"lease": None}
        p = lease["payload"]
        adapter = self.adapter(p["endpoint"])
        status = await (
            self.stop_external(adapter, p["prompt_id"], 10)
            if cancel_external
            else adapter.job(p["prompt_id"])
        )
        if status is None:
            await self.uncertain(p["prompt_id"], "image_external_missing")
            raise AppError(
                "image_external_missing",
                "Внешняя задача не найдена; отсутствие ответа не доказывает остановку GPU. Проверьте движок вручную.",
                409,
            )
        if status["status"] in TERMINAL_IMAGE:
            lease = await asyncio.to_thread(
                self.update_lease,
                p["prompt_id"],
                {
                    "state": "terminal",
                    "external_status": status["status"],
                    "lm_after": await self.loaded_lm(),
                },
            )
            if status["status"] == "completed" and not lease["payload"]["result_asset_id"]:
                await self.save_output(adapter, lease)
        return {"lease": await asyncio.to_thread(self.lease), "external_status": status["status"]}
