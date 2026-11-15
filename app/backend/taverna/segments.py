from __future__ import annotations

from uuid import UUID, uuid4

from .core import AppError
from .storage import JsonStore, Mutation, document, encoded


class SegmentStore:
    """Immutable-on-publication segments; only the bounded tail changes on append."""

    def __init__(self, store: JsonStore, max_messages=128, max_bytes=1024 * 1024):
        self.store, self.max_messages, self.max_bytes = store, max_messages, max_bytes

    def _key(self, story_id: str):
        try:
            UUID(story_id)
        except ValueError as exc:
            raise AppError("invalid_id", "Некорректный идентификатор истории.") from exc
        return f"chats/{story_id}/segments.json"

    def append(self, story_id: str, messages: list[dict]):
        changes = self.plan_append(story_id, messages)
        self.store.transaction(changes)
        return self.store.get(self._key(story_id))

    def plan_append(self, story_id: str, messages: list[dict], *, seal_tail_state=None) -> list[Mutation]:
        key = self._key(story_id)
        if not messages:
            raise AppError("empty_messages", "Нет сообщений для добавления.")
        # Validate UUID identities and message size before any writes.
        ids = []
        for message in messages:
            try:
                UUID(message["id"])
            except (KeyError, ValueError, TypeError) as exc:
                raise AppError("invalid_message", "У сообщения нет корректного UUID.") from exc
            if len(encoded(message)) > self.max_bytes - 1024:
                raise AppError("message_too_large", "Сообщение превышает лимит сегмента.", 413)
            ids.append(message["id"])
        if len(set(ids)) != len(ids):
            raise AppError("duplicate_message", "Повторный идентификатор сообщения.", 409)
        head = self.store.get(key)
        if head is None:
            head = document("message_segments", {"segments": [], "count": 0})
            expected = 0
        else:
            expected = head["revision"]
        # IDs are checked against all published segments without loading them into UI/DOM.
        segments = head["payload"]["segments"]
        for segment_key in segments:
            segment = self.store.get(segment_key)
            if any(m["id"] in ids for m in segment["payload"]["messages"]):
                raise AppError("duplicate_message", "Сообщение уже сохранено.", 409)
        changes = []
        tail_key = segments[-1] if segments else None
        tail = self.store.get(tail_key) if tail_key else None
        tail_expected = tail["revision"] if tail else 0
        if tail is not None and seal_tail_state is not None:
            tail["payload"]["messages"][-1]["state_id"] = seal_tail_state
        for message in messages:
            if (
                tail is None
                or len(tail["payload"]["messages"]) >= self.max_messages
                or len(encoded(tail)) + len(encoded(message)) > self.max_bytes
            ):
                if tail is not None:
                    changes.append(Mutation(tail_key, tail, tail_expected))
                tail_key = f"chats/{story_id}/messages/{uuid4()}.json"
                tail = document("message_segment", {"messages": []})
                tail_expected = 0
                segments.append(tail_key)
            tail["payload"]["messages"].append(message)
        changes.append(Mutation(tail_key, tail, tail_expected))
        head["payload"]["count"] += len(messages)
        head["refs"] = list(segments)
        changes.append(Mutation(key, head, expected))
        return changes

    def page(self, story_id: str, offset=0, limit=100) -> dict:
        if offset < 0 or not 1 <= limit <= 500:
            raise AppError("invalid_page", "Некорректные параметры страницы.")
        with self.store.view() as view:
            head = view.get(self._key(story_id))
            if not head:
                return {"items": [], "total": 0, "offset": offset}
            total = head["payload"]["count"]
            if offset >= total:
                return {"items": [], "total": total, "offset": offset}
            items = []
            end = min(total, offset + limit)
            keys = head["payload"]["segments"]
            # Near the tail, count backwards from the published total. Segment sizes
            # vary with byte limits, so never infer positions from max_messages.
            reverse = offset > total // 2
            cursor = total if reverse else 0
            for key in reversed(keys) if reverse else keys:
                messages = view.get(key)["payload"]["messages"]
                start = cursor - len(messages) if reverse else cursor
                stop = start + len(messages)
                if stop > offset and start < end:
                    part = messages[max(0, offset - start) : min(len(messages), end - start)]
                    items = part + items if reverse else items + part
                cursor = start if reverse else stop
                if (reverse and start <= offset) or (not reverse and stop >= end):
                    break
            return {"items": items, "total": total, "offset": offset}
