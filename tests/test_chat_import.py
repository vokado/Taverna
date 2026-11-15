import json

import pytest
from taverna.cards import new_card
from taverna.characters import CharacterRequest, CharacterService
from taverna.chat import ChatService
from taverna.chat_import import ChatImport, import_chat
from taverna.core import AppError
from taverna.generation_api import GenerationService
from taverna.storage import JsonStore


def setup(tmp_path):
    store = JsonStore(tmp_path)
    generation = GenerationService(store)
    characters = CharacterService(store, generation)
    ids = [
        characters.save(CharacterRequest(expected_revision=0, character=new_card(name)))["id"]
        for name in ["Мира", "Орн"]
    ]
    return ChatService(store, generation), ids


def test_jsonl_preview_then_atomic_import_preserves_unknown_fields(tmp_path):
    chat, ids = setup(tmp_path)
    raw = [
        {"user_name": "Игрок", "character_name": "Мира", "chat_metadata": {"vendor": {"x": 1}}},
        {
            "name": "Игрок",
            "is_user": True,
            "mes": "Привет",
            "send_date": "2025-01-01T12:00:00+05:00",
            "extra": {"vendor": True},
        },
        {
            "name": "Мира",
            "is_user": False,
            "mes": "Ответ",
            "send_date": "unknown format",
            "swipes": ["Ответ", "Вариант"],
            "unknown_extension": {"x": [1, 2]},
        },
    ]
    body = ChatImport(
        format="jsonl",
        text="\n".join(json.dumps(row, ensure_ascii=False) for row in raw),
        name="Импорт",
        character_ids=ids,
    )
    preview = import_chat(chat, body)
    assert preview["saved"] is False and preview["message_count"] == 2
    assert chat.store.scan("chats/stories/")[0] == []
    body.confirmed = True
    imported = import_chat(chat, body)
    messages = chat.messages(imported["story"]["id"])["items"]
    assert messages[0]["created_at"] == "2025-01-01T07:00:00+00:00"
    assert messages[1]["imported_metadata"]["unknown_extension"] == {"x": [1, 2]}
    assert imported["story"]["payload"]["import_metadata"]["metadata"] == raw[0]
    assert messages[1]["author_id"] == ids[0]
    assert chat.store.integrity()["ok"]


def test_unknown_author_requires_explicit_mapping_and_invalid_rows_do_not_write(tmp_path):
    chat, ids = setup(tmp_path)
    body = ChatImport(
        format="json",
        text=json.dumps([{"mes": "Текст", "is_user": False, "name": "Неизвестный"}]),
        name="Импорт",
        character_ids=ids,
    )
    assert import_chat(chat, body)["unresolved_authors"] == ["Неизвестный"]
    body.confirmed = True
    with pytest.raises(AppError):
        import_chat(chat, body)
    assert chat.store.scan("chats/stories/")[0] == []
    body.author_map = {"Неизвестный": ids[1]}
    imported = import_chat(chat, body)
    assert chat.messages(imported["story"]["id"])["items"][0]["author_id"] == ids[1]
    body.text = '[{"mes":"bad","mes":"overwritten","is_user":true}]'
    with pytest.raises(AppError):
        import_chat(chat, body)
