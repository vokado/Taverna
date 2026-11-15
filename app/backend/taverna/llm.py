from __future__ import annotations

import asyncio
import ipaddress
import json
import math
import time
from urllib.parse import urlsplit

import httpx

from .core import AppError


def local_endpoint(url: str) -> str:
    parsed = urlsplit(url)
    try:
        address = ipaddress.ip_address(parsed.hostname or "")
        port = parsed.port
    except ValueError as exc:
        raise AppError("invalid_endpoint", "Укажите числовой loopback-адрес локального сервера.") from exc
    if (
        parsed.scheme != "http"
        or not address.is_loopback
        or parsed.username
        or parsed.password
        or parsed.path not in ("", "/")
        or parsed.query
        or parsed.fragment
        or port is None
    ):
        raise AppError("invalid_endpoint", "Разрешён только локальный HTTP loopback-адрес с портом.")
    return url.rstrip("/")


class LmStudioAdapter:
    """No model lifecycle endpoints. Every inference rechecks native loaded instances."""

    def __init__(self, endpoint="http://127.0.0.1:1234", *, transport=None, timeout=120):
        self.endpoint = local_endpoint(endpoint)
        self.transport = transport
        self.timeout = timeout

    def client(self):
        return httpx.AsyncClient(
            base_url=self.endpoint,
            trust_env=False,
            follow_redirects=False,
            transport=self.transport,
            timeout=httpx.Timeout(self.timeout, connect=3),
        )

    async def loaded_models(self) -> list[dict]:
        try:
            async with self.client() as client:
                response = await client.get("/api/v1/models")
                if response.status_code != 200:
                    raise AppError(
                        "loaded_status_unavailable",
                        "LM Studio не предоставляет подтверждённый список загруженных экземпляров. Обновите LM Studio; модели управляются вручную.",
                        409,
                    )
                data = response.json()
                models = data.get("models")
                if not isinstance(models, list):
                    raise AppError(
                        "loaded_status_unavailable", "Формат каталога LM Studio не поддерживается.", 409
                    )
                result = []
                for model in models:
                    if not isinstance(model, dict):
                        raise ValueError("model catalog entry")
                    if model.get("type") not in ("llm", "embedding"):
                        continue
                    instances = model.get("loaded_instances", [])
                    if not isinstance(instances, list):
                        raise ValueError("loaded instances")
                    for instance in instances:
                        if (
                            isinstance(instance, dict)
                            and isinstance(instance.get("id"), str)
                            and instance["id"]
                        ):
                            if not isinstance(instance.get("config", {}), dict):
                                raise ValueError("instance config")
                            result.append(
                                {
                                    "id": instance["id"],
                                    "key": model.get("key"),
                                    "name": model.get("display_name", model.get("key", instance["id"])),
                                    "type": model["type"],
                                    "config": instance.get("config", {}),
                                    # Advanced features are unknown until explicitly verified for this API/model.
                                    "capabilities": {
                                        "stream": model["type"] == "llm",
                                        "reasoning": "unknown",
                                        "structured_output": "unknown",
                                        "tool_calling": "unknown",
                                        "embeddings": model["type"] == "embedding",
                                    },
                                }
                            )
                return result
        except (httpx.HTTPError, ValueError, TypeError) as exc:
            raise AppError(
                "model_unavailable",
                "LM Studio недоступен или вернул повреждённый каталог. Запустите сервер и загрузите модель вручную.",
                503,
            ) from exc

    async def require_loaded(self, model_id: str, kind="llm") -> dict:
        candidates = await self.loaded_models()
        for model in candidates:
            if model["id"] == model_id and model["type"] == kind:
                return model
        raise AppError(
            "model_not_loaded", "Экземпляр модели не загружен. Загрузите его в LM Studio вручную.", 409
        )

    async def stream(
        self,
        model_id: str,
        messages: list[dict],
        parameters: dict,
        cancel: asyncio.Event,
        *,
        estimator="balanced",
    ):
        for attempt in range(2):
            emitted = False
            try:
                async for event in self._stream_once(
                    model_id, messages, parameters, cancel, estimator=estimator
                ):
                    emitted = True
                    yield event
                return
            except AppError as exc:
                if exc.code != "generation_connection_failed" or emitted or attempt == 1 or cancel.is_set():
                    raise
                await asyncio.sleep(0.2)

    async def _stream_once(
        self,
        model_id: str,
        messages: list[dict],
        parameters: dict,
        cancel: asyncio.Event,
        *,
        estimator="balanced",
    ):
        model = await self.require_loaded(model_id)
        allowed = {
            "temperature",
            "top_p",
            "max_tokens",
            "frequency_penalty",
            "presence_penalty",
            "seed",
            "stop",
        }
        if set(parameters) - allowed:
            raise AppError(
                "unsupported_parameter", "Параметр не включён в проверенный контракт Chat Completions.", 422
            )
        context = model["config"].get("context_length")
        from .tokens import estimate_messages

        estimate = estimate_messages(messages, estimator)
        if isinstance(context, int) and estimate + parameters.get("max_tokens", 1024) > context:
            raise AppError(
                "context_overflow",
                "Оценка контекста превышает доступное окно модели. Сократите запрос или резерв ответа.",
                409,
            )
        started, first, usage, tokens, final_seen = time.perf_counter(), None, {}, 0, False
        try:
            async with self.client() as client:
                async with client.stream(
                    "POST",
                    "/v1/chat/completions",
                    json={"model": model_id, "messages": messages, "stream": True, **parameters},
                ) as response:
                    if response.status_code != 200:
                        raise AppError(
                            "generation_rejected",
                            "LM Studio отклонил запрос. Проверьте загруженную модель и параметры.",
                            502,
                            http_status=response.status_code,
                        )
                    data_lines = []
                    async for line in response.aiter_lines():
                        if cancel.is_set():
                            return
                        if line.startswith("data:"):
                            data_lines.append(line[5:].lstrip())
                        elif not line and data_lines:
                            data = "\n".join(data_lines)
                            data_lines.clear()
                            if data == "[DONE]":
                                final_seen = True
                                break
                            try:
                                item = json.loads(data)
                                if "error" in item:
                                    raise AppError(
                                        "generation_failed", "Модель вернула ошибку в потоке.", 502
                                    )
                                usage = item.get("usage") or usage
                                for choice in item.get("choices", []):
                                    delta = choice.get("delta", {})
                                    if choice.get("finish_reason"):
                                        final_seen = True
                                    reasoning = delta.get("reasoning_content") or delta.get("reasoning")
                                    if reasoning:
                                        yield "reasoning", {"text": reasoning}
                                    if delta.get("content"):
                                        first = first or time.perf_counter()
                                        tokens += 1
                                        yield "delta", {"text": delta["content"]}
                            except (ValueError, TypeError, KeyError) as exc:
                                raise AppError(
                                    "invalid_stream", "Поток модели содержит повреждённые данные.", 502
                                ) from exc
                    if not final_seen and not cancel.is_set():
                        raise AppError(
                            "generation_interrupted",
                            "Поток оборвался до завершения. Частичный ответ сохранён.",
                            502,
                        )
            elapsed = time.perf_counter() - started
            completion_tokens = usage.get("completion_tokens")
            yield (
                "metrics",
                {
                    "ttft_seconds": first - started if first else None,
                    "duration_seconds": elapsed,
                    "usage": usage,
                    "tokens_per_second": completion_tokens / elapsed
                    if completion_tokens and elapsed
                    else None,
                    "stream_chunks": tokens,
                    "prompt_token_estimate": estimate,
                    "estimate_method": estimator,
                    "exact_tokens": False,
                },
            )
        except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
            raise AppError(
                "generation_connection_failed", "Не удалось установить соединение с моделью.", 503
            ) from exc
        except httpx.TimeoutException as exc:
            raise AppError(
                "generation_timeout", "Истекло время ожидания модели. Частичный ответ сохранён.", 504
            ) from exc
        except httpx.HTTPError as exc:
            raise AppError(
                "generation_interrupted", "Соединение с моделью прервано. Частичный ответ сохранён.", 502
            ) from exc

    async def embeddings(self, model_id: str, texts: list[str]) -> list[list[float]]:
        await self.require_loaded(model_id, "embedding")
        try:
            async with self.client() as client:
                response = await client.post("/v1/embeddings", json={"model": model_id, "input": texts})
                response.raise_for_status()
                rows = sorted(response.json()["data"], key=lambda row: row["index"])
                if len(rows) != len(texts) or [row["index"] for row in rows] != list(range(len(texts))):
                    raise ValueError("length")
                vectors = [row["embedding"] for row in rows]
                if any(
                    not isinstance(vector, list)
                    or not 1 <= len(vector) <= 65536
                    or not all(type(x) in (int, float) and math.isfinite(x) for x in vector)
                    for vector in vectors
                ):
                    raise ValueError("vector")
                if len({len(vector) for vector in vectors}) > 1:
                    raise ValueError("dimensions")
                return vectors
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
            raise AppError(
                "embeddings_unavailable", "Смысловой поиск недоступен. Текстовый поиск сохраняется.", 503
            ) from exc
