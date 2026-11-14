from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import APIRouter, Header, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from .core import AppError
from .jobs import JobQueue
from .llm import LmStudioAdapter, local_endpoint
from .storage import Mutation, document


class ConnectionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    endpoint: str = Field(max_length=200)
    expected_revision: int = Field(ge=0)


class ChatMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: Literal["system", "user", "assistant"]
    content: str = Field(max_length=262144)


class GenerationParameters(BaseModel):
    model_config = ConfigDict(extra="forbid")
    max_tokens: int = Field(default=1024, ge=1, le=32768)
    temperature: float = Field(default=0.8, ge=0, le=2)
    top_p: float = Field(default=1, gt=0, le=1)
    frequency_penalty: float = Field(default=0, ge=-2, le=2)
    presence_penalty: float = Field(default=0, ge=-2, le=2)


class GenerationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    model_id: str = Field(min_length=1, max_length=256)
    messages: list[ChatMessage] = Field(min_length=1, max_length=1000)
    parameters: GenerationParameters = Field(default_factory=GenerationParameters)


class GenerationService:
    def __init__(self, store, adapter=None):
        self.store = store
        connection = store.get("config/lmstudio.json")
        self.adapter = adapter or LmStudioAdapter(
            connection["payload"]["endpoint"] if connection else "http://127.0.0.1:1234"
        )
        self.queue = JobQueue(store)
        self.queue.handlers["llm"] = self.run

    async def run(self, payload, cancel, emit):
        text, reasoning, metrics = "", "", {}
        adapter = LmStudioAdapter(
            payload.get("endpoint", self.adapter.endpoint), transport=self.adapter.transport
        )
        async for kind, event in adapter.stream(
            payload["model_id"], payload["messages"], payload["parameters"], cancel
        ):
            if kind == "delta":
                text += event["text"]
            if kind == "reasoning":
                reasoning += event["text"]
            if kind == "metrics":
                metrics = event
            await emit(kind, event)
        return {"text": text, "reasoning": reasoning, "metrics": metrics, "model_id": payload["model_id"]}

    @asynccontextmanager
    async def lifespan(self, app):
        await self.queue.start()
        try:
            yield
        finally:
            await self.queue.close()

    def router(self):
        router = APIRouter(prefix="/api/v1")

        @router.get("/models/connection")
        def connection():
            value = self.store.get("config/lmstudio.json")
            return {"endpoint": self.adapter.endpoint, "revision": value["revision"] if value else 0}

        @router.put("/models/connection")
        async def set_connection(body: ConnectionRequest):
            endpoint = local_endpoint(body.endpoint)
            current = await asyncio.to_thread(self.store.get, "config/lmstudio.json")
            value = current or document("llm_connection", {})
            value["payload"] = {"endpoint": endpoint}
            await asyncio.to_thread(
                self.store.transaction, [Mutation("config/lmstudio.json", value, body.expected_revision)]
            )
            self.adapter = LmStudioAdapter(endpoint)
            return {"endpoint": endpoint, "revision": body.expected_revision + 1}

        @router.get("/models")
        async def models():
            return {"models": await self.adapter.loaded_models()}

        @router.get("/models/status")
        async def model_status():
            try:
                loaded = await self.adapter.loaded_models()
                return {
                    "available": any(m["type"] == "llm" for m in loaded),
                    "models": loaded,
                    "message_ru": "Подключение доступно."
                    if loaded
                    else "Загрузите модель в LM Studio вручную.",
                }
            except AppError as exc:
                return {"available": False, "models": [], "code": exc.code, "message_ru": exc.message}

        @router.post("/generations", status_code=202)
        async def generate(
            body: GenerationRequest, idempotency_key: str = Header(min_length=1, max_length=128)
        ):
            adapter = self.adapter
            payload = {**body.model_dump(), "endpoint": adapter.endpoint}
            job = await self.queue.enqueue("llm", payload, idempotency_key=idempotency_key)
            return {"generation_id": job["id"], "state": job["payload"]["state"]}

        @router.get("/generations/{job_id}")
        async def generation(job_id: str):
            value = await self.queue.get(job_id)
            return {"generation_id": job_id, **value["payload"]}

        @router.get("/generations/{job_id}/events")
        async def events(job_id: str, after: int = Query(0, ge=0)):
            return {"events": await self.queue.events(job_id, after)}

        @router.get("/generations/{job_id}/stream")
        async def stream(job_id: str, request: Request, after: int = Query(0, ge=0)):
            await self.queue.get(job_id)
            last = request.headers.get("Last-Event-ID")
            if last:
                try:
                    event_job, sequence = last.rsplit(":", 1)
                    if event_job != job_id or int(sequence) < 0:
                        raise ValueError("job")
                    after = max(after, int(sequence))
                except ValueError as exc:
                    raise AppError("invalid_event_id", "Некорректный идентификатор события.") from exc
            return StreamingResponse(
                self.queue.sse(job_id, after),
                media_type="text/event-stream",
                headers={"X-Accel-Buffering": "no"},
            )

        @router.post("/generations/{job_id}/cancel")
        async def cancel(job_id: str):
            value = await self.queue.cancel(job_id)
            return {
                "generation_id": job_id,
                "state": value["payload"]["state"],
                "message_ru": "Запрос остановки принят.",
            }

        @router.get("/jobs")
        async def jobs():
            rows, errors = await asyncio.to_thread(self.store.scan, "jobs/records/", tolerate_corrupt=True)
            return {
                "jobs": [
                    {
                        "id": r["value"]["id"],
                        "state": r["value"]["payload"]["state"],
                        "kind": r["value"]["payload"]["kind"],
                    }
                    for r in rows
                ],
                "errors": errors,
            }

        return router
