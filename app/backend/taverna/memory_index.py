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
                    "format_version": 2,
                    "state_id": snap["state"]["id"],
                    "records": [
                        (key, value.get("version"), text_hash(str(value.get("text", ""))))
                        for key, value in snap["records"].items()
                        if isinstance(value, dict)
                    ],
                    "messages": [
                        (m["id"], m["version"], text_hash(m["text"])) for m in snap["messages"].values()
                    ],
                }
            )
        )

    def source_entries(self, snap, cached=None):
        cached = cached or {}
        entries = []

        def entry(kind, source):
            checksum = text_hash(source["text"])
            old = cached.get((kind, source["id"]))
            return {
                "id": source["id"],
                "kind": kind,
                "text_sha256": checksum,
                "tokens": old["tokens"]
                if old and old.get("text_sha256") == checksum
                else sorted(words(source["text"])),
            }

        for record in snap["records"].values():
            try:
                MemoryRecord.model_validate(record)
            except ValueError:
                continue
            entries.append(entry("record", record))
        entries.extend(entry("message", message) for message in snap["messages"].values())
        return entries

    def rebuild(self, snap):
        fingerprint = self.fingerprint(snap)
        key = self.key(snap["branch"]["id"])
        prefix = key.removesuffix("head.json")
        guard = {
            self.service.chat.branch_key(snap["story"]["id"], snap["branch"]["id"]): snap["branch"][
                "revision"
            ]
        }
        # Replacing a corrupt immutable object requires discarding its derived
        # logical entry first. Source documents and other branch caches stay intact.
        for _attempt in range(2):
            existing, cached, corrupt = {}, {}, set()
            with self.store.view() as view:
                owned = {
                    k: entry["revision"]
                    for k, entry in view.manifest["entries"].items()
                    if k.startswith(prefix)
                }
                try:
                    old = view.get(key)
                except AppError:
                    corrupt.add(key)
                    old = None
                if old and (
                    old["kind"] != "memory_index" or not isinstance(old["payload"].get("shards"), list)
                ):
                    corrupt.add(key)
                    old = None
                shard_keys = list(old["payload"].get("shards", [])) if old else []
                shard_keys.extend(
                    k
                    for k in owned
                    if k.removeprefix(prefix).startswith(("messages-", "records-")) and k not in shard_keys
                )
                for shard_key in shard_keys:
                    if not isinstance(shard_key, str) or not shard_key.startswith(prefix) or shard_key == key:
                        corrupt.add(key)
                        continue
                    try:
                        shard = view.get(shard_key)
                        if not shard or shard["kind"] != "memory_index_shard":
                            corrupt.update((key, shard_key))
                            continue
                        existing[shard_key] = shard
                        for item in shard["payload"]["entries"]:
                            if (
                                item["kind"] not in ("record", "message")
                                or not isinstance(item["tokens"], list)
                                or any(not isinstance(token, str) for token in item["tokens"])
                            ):
                                raise ValueError("invalid cached token entry")
                            cached[item["kind"], item["id"]] = item
                    except (AppError, KeyError, TypeError, ValueError):
                        corrupt.update((key, shard_key))
            if not corrupt:
                break
            self.store.transaction([Mutation(k, None, owned[k]) for k in corrupt if k in owned], guards=guard)
        else:
            raise AppError(
                "index_rebuild_conflict",
                "Производный индекс меняется или повреждён; используется поиск по источникам.",
                409,
            )
        entries = self.source_entries(snap, cached)
        changes, shards = [], []
        # Separate record/message runs prevent a new fact from shifting every
        # history shard. Existing tokens and unchanged shard objects are reused.
        for kind, label in (("record", "records"), ("message", "messages")):
            group = [e for e in entries if e["kind"] == kind]
            for start in range(0, len(group), 256):
                shard_key = f"{prefix}{label}-{start // 256:06}.json"
                payload = {"entries": group[start : start + 256]}
                previous = existing.get(shard_key)
                if previous is None and shard_key in owned:
                    # A shard left by an interrupted old rebuild can still be
                    # replaced safely, but its identity must be retained.
                    previous = self.store.get(shard_key)
                if previous is None or previous["payload"] != payload:
                    value = previous or document("memory_index_shard", payload)
                    value["payload"] = payload
                    changes.append(Mutation(shard_key, value, owned.get(shard_key, 0)))
                shards.append(shard_key)
        changes.extend(
            Mutation(k, None, revision) for k, revision in owned.items() if k != key and k not in shards
        )
        payload = {
            "story_id": snap["story"]["id"],
            "branch_id": snap["branch"]["id"],
            "fingerprint": fingerprint,
            "shards": shards,
            "entry_count": len(entries),
            "format_version": 2,
        }
        if not changes and old and old["payload"] == payload and old["refs"] == shards:
            return old
        head = old or document("memory_index", payload)
        head["payload"], head["refs"] = payload, shards
        changes.append(Mutation(key, head, owned.get(key, 0)))
        self.store.transaction(changes, guards=guard)
        return self.store.get(key)

    def entries(self, snap):
        fingerprint = self.fingerprint(snap)
        try:
            for attempt in range(2):
                # Head and shards must belong to the same storage generation.
                with self.store.view() as view:
                    head = view.get(self.key(snap["branch"]["id"]))
                    if head and head["payload"]["fingerprint"] == fingerprint:
                        result = []
                        for key in head["payload"]["shards"]:
                            if not key.startswith(self.key(snap["branch"]["id"]).removesuffix("head.json")):
                                raise AppError(
                                    "invalid_index", "Индекс ссылается за пределы своей ветви.", 409
                                )
                            items = view.get(key)["payload"]["entries"]
                            if not isinstance(items, list):
                                raise AppError(
                                    "invalid_index", "Структура производного индекса повреждена.", 409
                                )
                            for item in items:
                                if (
                                    not isinstance(item, dict)
                                    or item.get("kind") not in ("record", "message")
                                    or not isinstance(item.get("id"), str)
                                    or not isinstance(item.get("tokens"), list)
                                    or any(not isinstance(token, str) for token in item["tokens"])
                                ):
                                    raise AppError(
                                        "invalid_index",
                                        "Производные токены повреждены; используется источник.",
                                        409,
                                    )
                            result.extend(items)
                        if len(result) != head["payload"].get("entry_count"):
                            raise AppError(
                                "invalid_index", "Количество производных записей не совпадает.", 409
                            )
                        return result
                if attempt == 0:
                    self.rebuild(snap)
            return self.source_entries(snap)
        except (AppError, KeyError, TypeError):
            # A derived cache cannot disable source-based, access-filtered search.
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
