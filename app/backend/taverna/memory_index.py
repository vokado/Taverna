from __future__ import annotations

import copy
import math

from .core import AppError
from .knowledge import MemoryRecord, text_hash, words
from .storage import Mutation, digest, document, encoded


def discard_derived(store, keys):
    changes = []
    with store.view() as view:
        for key in keys:
            if not key.startswith("indexes/"):
                raise AppError("invalid_index", "Можно пересоздавать только производные индексы.", 422)
            entry = view.manifest["entries"].get(key)
            if entry:
                changes.append(Mutation(key, None, entry["revision"]))
    if changes:
        store.transaction(changes)


class MemoryIndex:
    """Rebuildable scoped JSON shards. Cached knowledge labels never grant access."""

    def __init__(self, service):
        self.service, self.store = service, service.store

    def key(self, branch_id):
        return f"indexes/memory/{branch_id}/head.json"

    def fingerprint(self, snap):
        return digest(
            encoded(
                {
                    "format_version": 1,
                    "state_id": snap["state"]["id"],
                    "messages": [
                        (m["id"], m["version"], text_hash(m["text"])) for m in snap["messages"].values()
                    ],
                }
            )
        )

    def source_entries(self, snap):
        entries = []
        for record in snap["records"].values():
            try:
                MemoryRecord.model_validate(record)
            except ValueError:
                continue
            entries.append({"id": record["id"], "kind": "record", "tokens": sorted(words(record["text"]))})
        entries.extend(
            {"id": m["id"], "kind": "message", "tokens": sorted(words(m["text"]))}
            for m in snap["messages"].values()
        )
        return entries

    def rebuild(self, snap):
        fingerprint = self.fingerprint(snap)
        key = self.key(snap["branch"]["id"])
        try:
            old = self.store.get(key)
        except AppError:
            discard_derived(self.store, [key])
            old = None
        entries = self.source_entries(snap)
        changes = []
        shards = []
        for start in range(0, len(entries), 256):
            shard_key = f"indexes/memory/{snap['branch']['id']}/{fingerprint}/shard-{start // 256:06}.json"
            try:
                existing = self.store.get(shard_key)
            except AppError:
                discard_derived(self.store, [key, shard_key])
                old = None
                existing = None
            if existing is None:
                changes.append(
                    Mutation(
                        shard_key,
                        document("memory_index_shard", {"entries": entries[start : start + 256]}),
                        0,
                    )
                )
            shards.append(shard_key)
        head = old or document("memory_index", {})
        head["payload"] = {
            "story_id": snap["story"]["id"],
            "branch_id": snap["branch"]["id"],
            "fingerprint": fingerprint,
            "shards": shards,
            "entry_count": len(entries),
            "format_version": 1,
        }
        head["refs"] = shards
        changes.append(Mutation(key, head, old["revision"] if old else 0))
        self.store.transaction(
            changes,
            guards={
                self.service.chat.branch_key(snap["story"]["id"], snap["branch"]["id"]): snap["branch"][
                    "revision"
                ]
            },
        )
        return self.store.get(key)

    def entries(self, snap):
        try:
            head = self.store.get(self.key(snap["branch"]["id"]))
            if head is None or head["payload"]["fingerprint"] != self.fingerprint(snap):
                head = self.rebuild(snap)
            result = []
            with self.store.view() as view:
                for key in head["payload"]["shards"]:
                    result.extend(view.get(key)["payload"]["entries"])
            return result
        except (AppError, KeyError, TypeError):
            # A derived cache cannot disable source-based text search.
            return self.source_entries(snap)

    def search(self, snap, query, actor=None, *, include_messages=False, limit=50, **filters):
        # First establish allowed IDs, then inspect cached tokens. Never query secrets first.
        allowed = self.service.search_records(snap, "", actor, **filters)
        records = {row["record"]["id"]: row["record"] for row in allowed}
        messages = {
            mid: m
            for mid, m in snap["messages"].items()
            if include_messages
            and (actor is None or actor in m.get("present_ids", []) or actor == m.get("author_id"))
        }
        if any(value is not None for value in filters.values()):
            messages = {}
        if actor:
            policy = self.service.policy(snap)
            messages = {mid: policy.project_message(message, actor) for mid, message in messages.items()}
        terms = words(query)
        results = []
        for entry in self.entries(snap):
            source = records.get(entry["id"]) if entry["kind"] == "record" else messages.get(entry["id"])
            if source is None:
                continue
            score = (
                sum(any(term in token for token in entry["tokens"]) for term in terms) / len(terms)
                if terms
                else 0
            )
            if terms and score == 0:
                continue
            results.append(
                {
                    "kind": entry["kind"],
                    "id": entry["id"],
                    "text": source["text"],
                    "score": score,
                    "record": source if entry["kind"] == "record" else None,
                    "message": source if entry["kind"] == "message" else None,
                }
            )
        results.sort(key=lambda r: (-r["score"], r["id"]))
        return {
            "results": results[:limit],
            "total": len(results),
            "mode": "text",
            "embedding_available": False,
            "source_fingerprint": self.fingerprint(snap),
        }


class VectorCache:
    def __init__(self, store, adapter):
        self.store, self.adapter = store, adapter

    async def vectors(self, model_id, texts, cancel):
        model = await self.adapter.require_loaded(model_id, "embedding")
        fingerprint = digest(
            encoded(
                {
                    "cache_epoch": (self.store.get("config/embedding-cache-epoch.json") or {})
                    .get("payload", {})
                    .get("nonce", "initial"),
                    "endpoint": self.adapter.endpoint,
                    "id": model["id"],
                    "key": model["key"],
                    "config": model["config"],
                }
            )
        )
        result = []
        for offset in range(0, len(texts), 16):
            if cancel.is_set():
                raise AppError("search_cancelled", "Смысловой поиск остановлен.", 409)
            batch = texts[offset : offset + 16]
            unique = list(dict.fromkeys(batch))
            values = []
            missing = []
            for text in unique:
                key = f"indexes/embeddings/{fingerprint}/{text_hash(text)}.json"
                try:
                    old = self.store.get(key)
                    if old and (
                        old["payload"].get("model_fingerprint") != fingerprint
                        or old["payload"].get("text_sha256") != text_hash(text)
                        or not valid_vector(old["payload"].get("vector"))
                    ):
                        raise AppError("invalid_vector_cache", "Кеш вектора повреждён.", 503)
                except AppError:
                    discard_derived(self.store, [key])
                    old = None
                values.append((key, old))
                if old is None:
                    missing.append(text)
            computed = iter(await self.adapter.embeddings(model_id, missing) if missing else [])
            changes = []
            batch_vectors = {}
            for text, (key, old) in zip(unique, values, strict=True):
                if old:
                    vector = copy.deepcopy(old["payload"]["vector"])
                else:
                    vector = next(computed)
                    value = document(
                        "embedding_cache",
                        {"model_fingerprint": fingerprint, "text_sha256": text_hash(text), "vector": vector},
                    )
                    changes.append(Mutation(key, value, 0))
                if not valid_vector(vector):
                    raise AppError("embeddings_unavailable", "Модель вернула недопустимый вектор.", 503)
                batch_vectors[text] = vector
            if changes:
                self.store.transaction(changes)
            result.extend(batch_vectors[text] for text in batch)
        if len({len(v) for v in result}) > 1:
            raise AppError("embeddings_unavailable", "Размерность векторов изменилась.", 503)
        return result, fingerprint


def valid_vector(vector):
    return (
        isinstance(vector, list)
        and 1 <= len(vector) <= 65536
        and all(type(x) in (int, float) and math.isfinite(x) for x in vector)
    )


def cosine(left, right):
    if len(left) != len(right):
        raise AppError("embeddings_unavailable", "Размерности векторов не совпадают.", 503)
    norm = math.sqrt(sum(x * x for x in left)) * math.sqrt(sum(x * x for x in right))
    return sum(x * y for x, y in zip(left, right, strict=True)) / norm if norm else 0
