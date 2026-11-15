from __future__ import annotations

import asyncio
from uuid import uuid4

from .core import AppError
from .knowledge import relevance
from .llm import LmStudioAdapter
from .memory_index import MemoryIndex, VectorCache, cosine
from .storage import Mutation, document


class MemorySearch:
    def __init__(self, service):
        self.service, self.store, self.generation = service, service.store, service.generation
        self.index = MemoryIndex(service)
        self.generation.queue.handlers["memory_search"] = self.run
        self.generation.queue.handlers["memory_index"] = self.rebuild

    async def rebuild(self, payload, cancel, emit):
        snap = await asyncio.to_thread(self.service.snapshot, payload["story_id"], payload["branch_id"])
        if snap["branch"]["revision"] != payload["expected_revision"]:
            raise AppError("branch_conflict", "Ветвь изменилась до перестроения индекса.", 409)
        if cancel.is_set():
            return {"cancelled": True}
        if payload.get("operation") == "reset_vectors":
            key = "config/embedding-cache-epoch.json"
            old = self.store.get(key)
            value = old or document("embedding_cache_epoch", {})
            value["payload"] = {"nonce": str(uuid4())}
            await asyncio.to_thread(
                self.store.transaction, [Mutation(key, value, old["revision"] if old else 0)]
            )
            return {
                "invalidated": True,
                "message_ru": "Кеш смыслового поиска сброшен. При следующем поиске векторы будут вычислены заново.",
            }
        head = await asyncio.to_thread(self.index.rebuild, snap)
        return head["payload"]

    async def run(self, payload, cancel, emit):
        snap = await asyncio.to_thread(self.service.snapshot, payload["story_id"], payload["branch_id"])
        if snap["branch"]["revision"] != payload["expected_revision"]:
            raise AppError("branch_conflict", "Ветвь изменилась до поиска.", 409)
        filters = {
            key: payload.get(key)
            for key in ("level", "nature", "subject_id", "source_id", "location_id", "time_from", "time_to")
        }
        text = await asyncio.to_thread(
            self.index.search,
            snap,
            payload["query"],
            payload.get("character_id"),
            include_messages=payload.get("include_messages", False),
            limit=payload["limit"],
            **filters,
        )
        model_id = payload.get("embedding_model_id")
        if not model_id:
            return text
        # Establish the knowledge boundary before any vector operation or query comparison.
        records = self.service.search_records(snap, "", payload.get("character_id"), **filters)
        candidates = [
            {
                "kind": "record",
                "id": row["record"]["id"],
                "text": row["record"]["text"],
                "record": row["record"],
                "message": None,
            }
            for row in records
        ]
        if payload.get("include_messages") and not any(v is not None for v in filters.values()):
            actor = payload.get("character_id")
            candidates.extend(
                {
                    "kind": "message",
                    "id": mid,
                    "text": m["text"],
                    "record": None,
                    "message": self.service.policy(snap).project_message(m, actor) if actor else m,
                }
                for mid, m in snap["messages"].items()
                if actor is None or actor in m.get("present_ids", []) or m.get("author_id") == actor
            )
        if not candidates:
            return {
                **text,
                "embedding_available": False,
                "message_ru": "Нет доступных источников для смыслового поиска.",
            }
        try:
            adapter = LmStudioAdapter(payload["endpoint"], transport=self.generation.adapter.transport)
            cache = VectorCache(self.store, adapter)
            vectors, fingerprint = await cache.vectors(
                model_id, [payload["query"], *[c["text"] for c in candidates]], cancel
            )
            lexical = {row["id"]: relevance(row["text"], payload["query"]) for row in candidates}
            for row, vector in zip(candidates, vectors[1:], strict=True):
                semantic = cosine(vectors[0], vector)
                row.update(
                    semantic_score=semantic,
                    text_score=lexical.get(row["id"], 0),
                    score=0.65 * ((semantic + 1) / 2) + 0.35 * lexical.get(row["id"], 0),
                )
            candidates.sort(key=lambda row: (-row["score"], row["id"]))
            if (
                self.service.chat.branch(payload["story_id"], payload["branch_id"])["revision"]
                != payload["expected_revision"]
            ):
                raise AppError("branch_conflict", "Ветвь изменилась во время смыслового поиска.", 409)
            return {
                "results": candidates[: payload["limit"]],
                "total": len(candidates),
                "mode": "hybrid",
                "embedding_available": True,
                "model_fingerprint": fingerprint,
            }
        except AppError as exc:
            if exc.code in ("branch_conflict", "search_cancelled"):
                raise
            return {
                **text,
                "message_ru": "Смысловой поиск недоступен. Показаны результаты текстового поиска.",
                "embedding_error": exc.code,
            }
