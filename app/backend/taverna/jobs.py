from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Awaitable, Callable
from uuid import UUID

from .core import AppError
from .storage import JsonStore, Mutation, document, encoded

TERMINAL = {"completed", "failed", "cancelled", "interrupted"}
JobHandler = Callable[[dict, asyncio.Event, Callable[[str, dict], Awaitable[None]]], Awaitable[dict]]


class JobQueue:
    """One bounded priority queue shared by generation and later maintenance handlers."""

    def __init__(self, store: JsonStore, capacity=64):
        self.store, self.capacity = store, capacity
        self.queue = asyncio.PriorityQueue(maxsize=capacity)
        self.handlers: dict[str, JobHandler] = {}
        self.cancellations: dict[str, asyncio.Event] = {}
        self.worker = None
        self.guard = asyncio.Lock()
        self.sequence = 0
        self.closed = False
        self.active = None
        self.foreground_pending = 0
        self.preemptible_kinds = {"memory_extract", "memory_search", "memory_index", "memory_maintenance"}
        self.preempted = set()

    @staticmethod
    def key(job_id: str):
        try:
            UUID(job_id)
        except ValueError as exc:
            raise AppError("invalid_id", "Некорректный идентификатор задачи.") from exc
        return f"jobs/records/{job_id}.json"

    async def start(self):
        if self.closed:
            raise AppError("job_unavailable", "Очередь завершает работу.", 409)
        if self.worker is not None and not self.worker.done():
            return
        if self.worker is not None:
            # Observe a failed worker and discard its in-memory queue. Durable pending jobs
            # are interrupted below; replaying stale in-memory entries would duplicate effects.
            if not self.worker.cancelled():
                self.worker.exception()
            self.queue = asyncio.PriorityQueue(maxsize=self.capacity)
            self.cancellations.clear()
            self.active = None
            self.foreground_pending = 0
            self.preempted.clear()
            self.worker = None
        rows, _ = await asyncio.to_thread(self.store.scan, "jobs/records/")
        for row in rows:
            value = row["value"]
            if value["payload"]["state"] not in TERMINAL:
                value["payload"]["state"] = "interrupted"
                value["payload"]["error"] = {
                    "code": "application_restarted",
                    "message_ru": "Задача прервана перезапуском. Сохранённый частичный результат доступен; автоматический повтор не выполняется.",
                }
                sequence = value["payload"].get("event_count", 0) + 1
                value["payload"]["event_count"] = sequence
                event = document(
                    "job_event",
                    {
                        "event_id": f"{value['id']}:{sequence}",
                        "generation_id": value["id"],
                        "sequence": sequence,
                        "type": "generation.interrupted",
                        "data": value["payload"]["error"],
                    },
                )
                await asyncio.to_thread(
                    self.store.transaction,
                    [
                        Mutation(row["key"], value, value["revision"]),
                        Mutation(f"jobs/events/{value['id']}/{sequence:08}.json", event, 0),
                    ],
                )
        self.worker = asyncio.create_task(self._work())

    async def close(self):
        if self.closed:
            return
        self.closed = True
        for event in self.cancellations.values():
            event.set()
        if self.worker:
            self.worker.cancel()
            try:
                await self.worker
            except asyncio.CancelledError:
                pass
            except AppError:
                pass
        for job_id in list(self.cancellations):
            try:
                job = await self.get(job_id)
                if job["payload"]["state"] not in TERMINAL:
                    await self.emit(
                        job_id,
                        "interrupted",
                        {"message_ru": "Приложение завершает работу."},
                        state="interrupted",
                    )
            except AppError:
                pass
        self.cancellations.clear()

    async def enqueue(
        self, kind: str, payload: dict, *, priority=0, idempotency_key: str, scope=None, identity=None
    ) -> dict:
        if kind not in self.handlers or self.closed:
            raise AppError("job_unavailable", "Обработчик задачи недоступен.", 409)
        if not 1 <= len(idempotency_key) <= 128:
            raise AppError("invalid_idempotency_key", "Некорректный ключ повторного запроса.")
        async with self.guard:
            await self.start()
            key = "jobs/keys/" + hashlib.sha256(idempotency_key.encode()).hexdigest() + ".json"
            fingerprint = hashlib.sha256(
                encoded(
                    {"kind": kind, "identity": identity}
                    if identity is not None
                    else {"kind": kind, "payload": payload, "scope": scope}
                )
            ).hexdigest()
            existing = await asyncio.to_thread(self.store.get, key)
            if existing:
                if existing["payload"]["fingerprint"] != fingerprint:
                    raise AppError(
                        "idempotency_conflict", "Этот ключ уже использован для другого запроса.", 409
                    )
                return await self.get(existing["payload"]["job_id"])
            background_limit = max(1, self.queue.maxsize - min(8, self.queue.maxsize // 4))
            if self.queue.full() or (
                kind in self.preemptible_kinds and self.queue.qsize() >= background_limit
            ):
                raise AppError("queue_full", "Очередь заполнена. Дождитесь завершения текущих задач.", 429)
            job = document(
                "job",
                {
                    "kind": kind,
                    "request": payload,
                    "scope": scope,
                    "priority": priority,
                    "state": "queued",
                    "event_count": 0,
                    "result": None,
                    "error": None,
                },
            )
            record = document(
                "idempotency_key",
                {"fingerprint": fingerprint, "job_id": job["id"]},
                refs=[self.key(job["id"])],
            )
            await asyncio.to_thread(
                self.store.transaction, [Mutation(self.key(job["id"]), job, 0), Mutation(key, record, 0)]
            )
            self.cancellations[job["id"]] = asyncio.Event()
            self.sequence += 1
            self.queue.put_nowait((priority, self.sequence, job["id"]))
            if priority == 0:
                self.foreground_pending += 1
                if self.active and self.active[2] in self.preemptible_kinds:
                    active_cancel = self.cancellations.get(self.active[0])
                    if active_cancel:
                        self.preempted.add(self.active[0])
                        active_cancel.set()
            return await self.get(job["id"])

    async def get(self, job_id: str) -> dict:
        value = await asyncio.to_thread(self.store.get, self.key(job_id))
        if value is None:
            raise AppError("job_not_found", "Задача не найдена.", 404)
        return value

    async def emit(self, job_id: str, event_type: str, payload: dict, *, state=None, result=None, error=None):
        async with self.guard:
            job = await self.get(job_id)
            sequence = job["payload"]["event_count"] + 1
            event = document(
                "job_event",
                {
                    "event_id": f"{job_id}:{sequence}",
                    "generation_id": job_id,
                    "sequence": sequence,
                    "type": f"generation.{event_type}",
                    "data": payload,
                },
            )
            job["payload"]["event_count"] = sequence
            if state:
                job["payload"].update(state=state, result=result, error=error)
            await asyncio.to_thread(
                self.store.transaction,
                [
                    Mutation(self.key(job_id), job, job["revision"]),
                    Mutation(f"jobs/events/{job_id}/{sequence:08}.json", event, 0),
                ],
            )

    async def events(self, job_id: str, after=0, limit=100) -> list[dict]:
        job = await self.get(job_id)
        if after < 0 or not 1 <= limit <= 200:
            raise AppError("invalid_page", "Некорректная страница событий.")
        result = []
        for n in range(after + 1, min(after + limit, job["payload"]["event_count"]) + 1):
            event = await asyncio.to_thread(self.store.get, f"jobs/events/{job_id}/{n:08}.json")
            result.append(event["payload"])
        return result

    async def cancel(self, job_id: str):
        job = await self.get(job_id)
        if job["payload"]["state"] not in TERMINAL:
            event = self.cancellations.get(job_id)
            if event:
                event.set()
        return await self.get(job_id)

    async def _work(self):
        current = None
        try:
            while True:
                priority, _, current = await self.queue.get()
                if priority == 0:
                    self.foreground_pending = max(0, self.foreground_pending - 1)
                job = await self.get(current)
                self.active = (current, priority, job["payload"]["kind"])
                cancel = self.cancellations[current]
                if job["payload"]["kind"] in self.preemptible_kinds and self.foreground_pending:
                    self.preempted.add(current)
                    cancel.set()
                if cancel.is_set():
                    await self.emit(
                        current,
                        "cancelled",
                        {"reason": "foreground_priority"} if current in self.preempted else {},
                        state="cancelled",
                    )
                else:
                    await self.emit(current, "started", {}, state="running")

                    async def emit(kind, payload, job_id=current):
                        await self.emit(job_id, kind, payload)

                    try:
                        # Cancellation must interrupt a blocked socket read, not wait for the next token.
                        handler = asyncio.create_task(
                            self.handlers[job["payload"]["kind"]](job["payload"]["request"], cancel, emit)
                        )
                        waiter = asyncio.create_task(cancel.wait())
                        try:
                            done, _ = await asyncio.wait(
                                {handler, waiter}, return_when=asyncio.FIRST_COMPLETED
                            )
                            if waiter in done and cancel.is_set():
                                handler.cancel()
                                await asyncio.gather(handler, return_exceptions=True)
                                await self.emit(
                                    current,
                                    "cancelled",
                                    {"reason": "foreground_priority"} if current in self.preempted else {},
                                    state="cancelled",
                                )
                            else:
                                result = await handler
                                await self.emit(current, "completed", {}, state="completed", result=result)
                        finally:
                            waiter.cancel()
                            if not handler.done():
                                handler.cancel()
                            await asyncio.gather(handler, waiter, return_exceptions=True)
                    except AppError as exc:
                        error = {"code": exc.code, "message_ru": exc.message}
                        await self.emit(current, "failed", error, state="failed", error=error)
                    except Exception:
                        error = {
                            "code": "job_failed",
                            "message_ru": "Ошибка задачи. Частичный результат сохранён.",
                        }
                        await self.emit(current, "failed", error, state="failed", error=error)
                self.cancellations.pop(current, None)
                self.preempted.discard(current)
                self.active = None
                self.queue.task_done()
                current = None
        except asyncio.CancelledError:
            if current:
                await self.emit(current, "interrupted", {}, state="interrupted")
            raise

    async def sse(self, job_id: str, after=0):
        while True:
            if not self.closed and (self.worker is None or self.worker.done()):
                async with self.guard:
                    await self.start()
            events = await self.events(job_id, after)
            for event in events:
                after = event["sequence"]
                yield f"id: {event['event_id']}\nevent: {event['type']}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"
            job = await self.get(job_id)
            if job["payload"]["state"] in TERMINAL and after >= job["payload"]["event_count"]:
                break
            if self.closed and not events:
                break
            if not events:
                yield ": ожидание\n\n"
                await asyncio.sleep(0.2)
