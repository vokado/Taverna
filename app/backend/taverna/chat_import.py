from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field

from .chat import ChatService, identifier
from .core import AppError
from .storage import now


class ChatImport(BaseModel):
    model_config = ConfigDict(extra="forbid")
    format: Literal["jsonl", "json"]
    text: str = Field(max_length=8 * 1024 * 1024)
    name: str = Field(min_length=1, max_length=200)
    character_ids: list[str] = Field(min_length=1, max_length=20)
    author_map: dict[str, str] = Field(default_factory=dict)
    confirmed: bool = False


def parse_chat(body: ChatImport):
    try:

        def unique(pairs):
            value = {}
            for key, item in pairs:
                if key in value:
                    raise ValueError("duplicate key")
                value[key] = item
            return value

        if body.format == "jsonl":
            rows = [
                json.loads(line, object_pairs_hook=unique) for line in body.text.splitlines() if line.strip()
            ]
            metadata = rows.pop(0) if rows and isinstance(rows[0], dict) and "mes" not in rows[0] else {}
        else:
            parsed = json.loads(body.text, object_pairs_hook=unique)
            rows = parsed if isinstance(parsed, list) else parsed["messages"]
            metadata = (
                {}
                if isinstance(parsed, list)
                else {key: value for key, value in parsed.items() if key != "messages"}
            )
        if not isinstance(rows, list) or not 0 < len(rows) <= 10000 or not isinstance(metadata, dict):
            raise ValueError("format")
        for row in rows:
            if (
                not isinstance(row, dict)
                or not isinstance(row.get("mes"), str)
                or type(row.get("is_user")) is not bool
            ):
                raise ValueError("message")
        # Reject non-finite or pathological nesting before any durable writes.
        json.dumps([metadata, rows], allow_nan=False)
        return rows, metadata
    except (ValueError, KeyError, TypeError, RecursionError) as exc:
        raise AppError(
            "invalid_chat_import",
            "Поддерживается JSONL SillyTavern или JSON-массив сообщений с mes и is_user. Файл повреждён или формат не распознан.",
            422,
        ) from exc


def original_time(value):
    try:
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return datetime.fromtimestamp(
                value / 1000 if abs(value) > 100_000_000_000 else value, timezone.utc
            ).isoformat()
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is not None:
            return parsed.astimezone(timezone.utc).isoformat()
    except (ValueError, TypeError, OSError, OverflowError):
        pass
    return None


def import_chat(chat: ChatService, body: ChatImport):
    rows, metadata = parse_chat(body)
    snapshots = {}
    for cid in body.character_ids:
        card = chat.store.get(f"characters/{identifier(cid)}.json")
        if card is None:
            raise AppError("character_missing", "Выбранный персонаж не найден.", 404)
        snapshots[cid] = card["payload"]
    by_name = {}
    for cid, card in snapshots.items():
        by_name.setdefault(str(card["fields"]["name"]), []).append(cid)
    authors = {}
    unresolved = set()
    warnings = []
    ambiguous_dates = 0
    for index, row in enumerate(rows):
        if row["is_user"]:
            continue
        name = str(row.get("name", "[Автор без имени]"))
        matches = by_name.get(name, [])
        cid = body.author_map.get(name, matches[0] if len(matches) == 1 else None)
        if cid is None and len(snapshots) == 1:
            cid = next(iter(snapshots))
        if cid not in snapshots:
            unresolved.add(name)
        authors[index] = cid
    for row in rows:
        if original_time(row.get("send_date")) is None:
            ambiguous_dates += 1
    if ambiguous_dates:
        warnings.append(
            f"Даты без распознанного часового пояса: {ambiguous_dates}. Исходные даты сохранятся в метаданных; UTC created_at будет временем импорта."
        )
    if any("swipes" in row for row in rows):
        warnings.append(
            "Исходные swipes сохраняются в imported_metadata. Автоматическое превращение всех импортированных вариантов в дерево ветвей не выполняется."
        )
    if any(row.get("is_system") for row in rows):
        warnings.append(
            "Импортированные системные записи считаются сюжетным текстом, не инструкциями backend."
        )
    preview = {
        "message_count": len(rows),
        "metadata": metadata,
        "unresolved_authors": sorted(unresolved),
        "warnings": warnings,
        "messages": [
            {
                "name": row.get("name"),
                "role": "user" if row["is_user"] else "assistant",
                "text": row["mes"],
                "author_id": authors.get(i),
            }
            for i, row in enumerate(rows[:20])
        ],
        "saved": False,
    }
    if not body.confirmed:
        return preview
    if unresolved:
        raise AppError(
            "unmapped_authors",
            "Сопоставьте всех авторов NPC выбранным персонажам.",
            422,
            authors=sorted(unresolved),
        )
    story, branch, state, changes = chat._prepare_create(
        body.name, body.character_ids, include_greeting=False
    )
    story["payload"]["import_metadata"] = {
        "source": "SillyTavern",
        "format": body.format,
        "metadata": metadata,
        "warnings": warnings,
        "author_map": body.author_map,
    }
    messages = []
    for index, row in enumerate(rows):
        if not row["mes"].strip():
            # Empty imported rows are retained as text records with an explicit marker, not silently dropped.
            raise AppError(
                "empty_imported_message",
                "В исходном файле есть пустое сообщение. Исправьте его перед импортом.",
                422,
                index=index,
            )
        timestamp = original_time(row.get("send_date")) or now()
        message = chat._message(
            story["id"],
            branch["id"],
            "user" if row["is_user"] else "assistant",
            row["mes"],
            None if row["is_user"] else authors[index],
            created_at=timestamp,
            state_id=state["id"],
            imported_metadata=row,
            present_ids=list(body.character_ids),
        )
        messages.append(message)
    changes += chat.segments.plan_append(branch["id"], messages)
    branch["payload"]["head_message_id"] = messages[-1]["id"]
    chat.store.transaction(changes)
    return {"story": chat.story(story["id"]), "saved": True, "warnings": warnings}


def router(chat):
    router = APIRouter(prefix="/api/v1")

    @router.post("/chat-import")
    def import_history(body: ChatImport):
        return import_chat(chat, body)

    return router
