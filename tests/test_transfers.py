from uuid import uuid4

import pytest
from taverna.cards import new_card
from taverna.characters import CharacterRequest, CharacterService
from taverna.chat import ChatService
from taverna.core import AppError
from taverna.generation_api import GenerationService
from taverna.storage import JsonStore, Mutation, document
from taverna.transfers import EventTransfer, transfer


def test_explicit_event_transfer_is_previewed_idempotent_and_does_not_spread_knowledge(tmp_path):
    store = JsonStore(tmp_path)
    generation = GenerationService(store)
    characters = CharacterService(store, generation)
    cid = characters.save(CharacterRequest(expected_revision=0, character=new_card("Мира")))["id"]
    chat = ChatService(store, generation)
    source = chat.create("Источник", [cid])["id"]
    target = chat.create("Получатель", [cid])["id"]
    source_state = chat.state(source)["payload"]
    event_id = str(uuid4())
    source_state["events"].append(
        {"id": event_id, "name": "Событие", "effects": {"coins": 100}, "knower_ids": [cid]}
    )
    source_state["memory"]["secret"] = {"text": "Не переносить тайну автоматически"}
    chat.update_state(source, source_state, chat.branch(source)["revision"])
    sb = chat.branch(source)
    tb = chat.branch(target)
    body = EventTransfer(
        operation_id=str(uuid4()),
        branch_id=tb["id"],
        expected_revision=tb["revision"],
        source_story_id=source,
        source_branch_id=sb["id"],
        source_revision=sb["revision"],
        event_ids=[event_id],
    )
    preview = transfer(chat, target, body)
    assert preview["saved"] is False and len(preview["events"]) == 1
    assert chat.state(target)["payload"]["events"] == []
    body.confirmed = True
    first = transfer(chat, target, body)
    second = transfer(chat, target, body)
    assert first["transferred"] == second["transferred"]
    result = chat.state(target)["payload"]
    assert len(result["events"]) == 1
    assert result["events"][0]["status"] == "imported_record"
    assert result["events"][0]["origin"]["event_id"] == event_id
    assert result["memory"] == {} and result["relationships"] == {} and result["world"] == {}
    assert chat.branch(source)["revision"] == sb["revision"]


def test_read_guard_prevents_writing_from_changed_source(tmp_path):
    store = JsonStore(tmp_path)
    source = document("test", {"n": 1})
    store.transaction([Mutation("config/source.json", source, 0)])
    source["payload"]["n"] = 2
    store.transaction([Mutation("config/source.json", source, 1)])
    with pytest.raises(AppError) as error:
        store.transaction(
            [Mutation("config/target.json", document("test", {}), 0)], guards={"config/source.json": 1}
        )
    assert error.value.code == "storage_conflict"
    assert store.get("config/target.json") is None
