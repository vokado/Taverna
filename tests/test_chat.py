import asyncio
import json
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from taverna.cards import new_card
from taverna.characters import CharacterRequest, CharacterService
from taverna.chat import ChatService
from taverna.core import AppError, Config
from taverna.generation_api import GenerationService
from taverna.llm import LmStudioAdapter
from taverna.main import create_app
from taverna.storage import JsonStore


def setup_chat(tmp_path, greeting=""):
    store = JsonStore(tmp_path)
    generation = GenerationService(store)
    chars = CharacterService(store, generation)
    ids = []
    for name in ["Мира", "Орн"]:
        payload = new_card(name)
        payload["fields"]["first_mes"] = greeting
        ids.append(chars.save(CharacterRequest(expected_revision=0, character=payload))["id"])
    chat = ChatService(store, generation)
    story = chat.create("Независимая история", ids)
    return chat, story["id"], ids


def append(chat, sid, text, role="user", author_id=None):
    return chat.append(
        sid, text, role=role, author_id=author_id, expected_revision=chat.branch(sid)["revision"]
    )["message"]


def test_character_snapshot_independence_and_message_metadata(tmp_path):
    chat, sid, ids = setup_chat(tmp_path)
    other = chat.create("Другое прохождение", ids)["id"]
    first = append(chat, sid, "Пишу от собственного имени")
    assert first["author_id"] is None and first["role"] == "user"
    assert first["model_id"] is None and first["event_ids"] == []
    assert chat.messages(other)["total"] == 0
    CharacterService(chat.store, chat.generation).save(
        CharacterRequest(expected_revision=1, character=new_card("Новое базовое имя")), ids[0]
    )
    assert chat.story(sid)["payload"]["character_snapshots"][ids[0]]["card"]["fields"]["name"] == "Мира"
    assert chat.story(other)["payload"]["character_snapshots"][ids[0]]["card"]["fields"]["name"] == "Мира"


def test_fork_past_and_nested_fork_do_not_leak_future_state(tmp_path):
    chat, sid, ids = setup_chat(tmp_path)
    original_branch = chat.branch(sid)["id"]
    first = append(chat, sid, "Первый эпизод")
    state = chat.state(sid)["payload"]
    state["memory"]["fact-first"] = {"source_ids": [first["id"]], "text": "Первое знание"}
    chat.update_state(sid, state, chat.branch(sid)["revision"])
    second = append(chat, sid, "Второй эпизод")
    state = chat.state(sid)["payload"]
    state["memory"]["future"] = {"source_ids": [second["id"]], "text": "Тайна будущего"}
    state["world"]["coins"] = 100
    chat.update_state(sid, state, chat.branch(sid)["revision"])
    chat.fork(sid, expected_revision=chat.branch(sid)["revision"], name="Полная копия")
    assert "future" in chat.state(sid)["payload"]["memory"]
    copied_first = chat.messages(sid)["items"][0]
    chat.fork(
        sid, expected_revision=chat.branch(sid)["revision"], through_id=copied_first["id"], name="Прошлое"
    )
    assert "future" not in chat.state(sid)["payload"]["memory"]
    assert chat.state(sid)["payload"]["world"] == {}
    assert chat.state(sid)["payload"]["memory"]["fact-first"]["source_ids"] == [
        chat.messages(sid)["items"][0]["id"]
    ]
    chat.switch(sid, original_branch, chat.story(sid)["revision"])
    assert "future" in chat.state(sid)["payload"]["memory"]
    assert [m["id"] for m in chat.messages(sid)["items"]] == [first["id"], second["id"]]
    assert chat.store.integrity()["ok"]


def test_edit_delete_preserve_original_and_reset_derived_state(tmp_path):
    chat, sid, ids = setup_chat(tmp_path)
    original = chat.branch(sid)["id"]
    message = append(chat, sid, "Старый факт")
    state = chat.state(sid)["payload"]
    state["memory"]["derived"] = {"source_ids": [message["id"]], "text": "Из старого текста"}
    chat.update_state(sid, state, chat.branch(sid)["revision"])
    chat.fork(
        sid,
        expected_revision=chat.branch(sid)["revision"],
        through_id=message["id"],
        replacement="Новый факт",
    )
    assert chat.messages(sid)["items"][0]["text"] == "Новый факт"
    assert chat.state(sid)["payload"]["memory"] == {}
    chat.switch(sid, original, chat.story(sid)["revision"])
    assert chat.messages(sid)["items"][0]["text"] == "Старый факт"
    chat.fork(sid, expected_revision=chat.branch(sid)["revision"], through_id=message["id"], drop_last=True)
    assert chat.messages(sid)["total"] == 0 and chat.state(sid)["payload"]["memory"] == {}


def test_presence_and_stale_revision_are_enforced(tmp_path):
    chat, sid, ids = setup_chat(tmp_path)
    branch = chat.branch(sid)
    append(chat, sid, "Сообщение")
    with pytest.raises(AppError):
        chat.append(sid, "Поздняя правка", expected_revision=branch["revision"])
    with pytest.raises(AppError):
        append(chat, sid, "Подмена пользователя", "user", ids[0])
    with pytest.raises(AppError):
        append(chat, sid, "Неизвестный NPC", "assistant", str(uuid4()))


def protocol_adapter(calls):
    def transport(request):
        calls.append((request.method, request.url.path))
        if request.method == "GET":
            return httpx.Response(
                200,
                json={
                    "models": [
                        {
                            "key": "disk",
                            "type": "llm",
                            "loaded_instances": [{"id": "loaded", "config": {"context_length": 8192}}],
                        }
                    ]
                },
            )
        return httpx.Response(
            200,
            text="data: "
            + json.dumps({"choices": [{"delta": {"content": "Новый ответ"}}]})
            + "\n\ndata: [DONE]\n\n",
        )

    return LmStudioAdapter(transport=httpx.MockTransport(transport))


def test_regeneration_same_request_does_not_fork_twice(tmp_path):
    app = create_app(Config(tmp_path))
    calls = []
    app.state.generation.adapter = protocol_adapter(calls)
    chat = app.state.chat
    character = CharacterService(chat.store, chat.generation).save(
        CharacterRequest(expected_revision=0, character=new_card("Герой"))
    )
    sid = chat.create("История", [character["id"]])["id"]
    old = append(chat, sid, "Старый ответ", "assistant", character["id"])
    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
        csrf = client.post(
            "/api/v1/session/bootstrap", json={"token": app.state.sessions.bootstrap()}
        ).json()["csrf"]
        headers = {"X-Taverna-CSRF": csrf, "Idempotency-Key": "regenerate-once"}
        body = {
            "expected_revision": chat.branch(sid)["revision"],
            "branch_id": chat.branch(sid)["id"],
            "message_id": old["id"],
            "model_id": "loaded",
            "character_id": character["id"],
        }
        first = client.post(f"/api/v1/stories/{sid}/regenerate", json=body, headers=headers)
        assert first.status_code == 202
        job_id = first.json()["generation_id"]
        stream = client.get(f"/api/v1/generations/{job_id}/stream")
        assert "generation.completed" in stream.text, stream.text
        repeated = client.post(f"/api/v1/stories/{sid}/regenerate", json=body, headers=headers)
        assert repeated.json()["generation_id"] == job_id
        assert len(client.get(f"/api/v1/stories/{sid}/branches").json()["branches"]) == 2
        assert chat.messages(sid)["items"][-1]["text"] == "Новый ответ"
        assert len([path for method, path in calls if method == "POST"]) == 1


@pytest.mark.asyncio
async def test_late_llm_result_cannot_overwrite_changed_branch(tmp_path):
    chat, sid, ids = setup_chat(tmp_path)
    entered, release = asyncio.Event(), asyncio.Event()

    class Delayed(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b'data: {"choices":[{"delta":{"content":"partial"}}]}\n\n'
            entered.set()
            await release.wait()
            yield b"data: [DONE]\n\n"

    def transport(request):
        if request.method == "GET":
            return httpx.Response(
                200,
                json={
                    "models": [
                        {
                            "key": "disk",
                            "type": "llm",
                            "loaded_instances": [{"id": "loaded", "config": {"context_length": 8192}}],
                        }
                    ]
                },
            )
        return httpx.Response(200, stream=Delayed())

    chat.generation.adapter = LmStudioAdapter(transport=httpx.MockTransport(transport))
    branch = chat.branch(sid)
    events = []

    async def emit(kind, event):
        events.append((kind, event))

    task = asyncio.create_task(
        chat.generate(
            {
                "story_id": sid,
                "branch_id": branch["id"],
                "expected_revision": branch["revision"],
                "model_id": "loaded",
                "character_id": ids[0],
                "endpoint": "http://127.0.0.1:1234",
                "parameters": {"max_tokens": 32},
            },
            asyncio.Event(),
            emit,
        )
    )
    await entered.wait()
    append(chat, sid, "Новая реплика пользователя")
    release.set()
    with pytest.raises(AppError) as failure:
        await task
    assert failure.value.code == "branch_conflict"
    assert [m["text"] for m in chat.messages(sid)["items"]] == ["Новая реплика пользователя"]
    assert any(kind == "delta" for kind, event in events)


def api_setup(tmp_path):
    app = create_app(Config(tmp_path))
    service = CharacterService(app.state.store, app.state.generation)
    ids = [
        service.save(CharacterRequest(expected_revision=0, character=new_card(name)))["id"]
        for name in ["Мира", "Орн"]
    ]
    sid = app.state.chat.create("Группа", ids)["id"]
    return app, sid, ids


def authenticate(app, client):
    csrf = client.post("/api/v1/session/bootstrap", json={"token": app.state.sessions.bootstrap()}).json()[
        "csrf"
    ]
    return {"X-Taverna-CSRF": csrf}


def test_group_npcs_speak_in_order_and_view_switch_preserves_order(tmp_path):
    app, sid, ids = api_setup(tmp_path)
    calls = []
    app.state.generation.adapter = protocol_adapter(calls)
    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
        headers = authenticate(app, client)
        branch = app.state.chat.branch(sid)

        def change(**patch):
            b = app.state.chat.branch(sid)
            response = client.patch(
                f"/api/v1/stories/{sid}/branch",
                json={"expected_revision": b["revision"], "branch_id": b["id"], **patch},
                headers=headers,
            )
            assert response.status_code == 200, response.text
            return response.json()

        change(speaker_order=list(reversed(ids)))
        branch = change(view_mode="literary")
        assert branch["payload"]["speaker_order"] == list(reversed(ids))
        request = {
            "expected_revision": branch["revision"],
            "branch_id": branch["id"],
            "model_id": "loaded",
            "character_id": ids[1],
            "turns": 2,
            "parameters": {"max_tokens": 32},
        }
        response = client.post(
            f"/api/v1/stories/{sid}/group-generations",
            json=request,
            headers={**headers, "Idempotency-Key": "group"},
        )
        assert response.status_code == 202, response.text
        job = response.json()["generation_id"]
        stream = client.get(f"/api/v1/generations/{job}/stream")
        assert "generation.completed" in stream.text, stream.text
        messages = app.state.chat.messages(sid)["items"]
        assert [m["author_id"] for m in messages] == list(reversed(ids))
        assert all(m["role"] == "assistant" for m in messages)
        assert len([path for method, path in calls if method == "POST"]) == 2


def test_checkpoint_restore_uses_named_snapshot_and_preserves_current_line(tmp_path):
    app, sid, ids = api_setup(tmp_path)
    chat = app.state.chat
    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
        headers = authenticate(app, client)
        append(chat, sid, "До события")
        state = chat.state(sid)["payload"]
        state["world"]["coins"] = 10
        chat.update_state(sid, state, chat.branch(sid)["revision"])
        branch = chat.branch(sid)
        original = branch["id"]
        response = client.post(
            f"/api/v1/stories/{sid}/anchors",
            json={
                "name": "Безопасная точка",
                "kind": "checkpoint",
                "branch_id": branch["id"],
                "expected_revision": branch["revision"],
            },
            headers=headers,
        )
        assert response.status_code == 200, response.text
        anchor = response.json()["payload"]["checkpoints"][0]
        append(chat, sid, "После события")
        state = chat.state(sid)["payload"]
        state["world"]["coins"] = 999
        chat.update_state(sid, state, chat.branch(sid)["revision"])
        branch = chat.branch(sid)
        response = client.post(
            f"/api/v1/stories/{sid}/anchors/{anchor['id']}/restore",
            json={"expected_revision": branch["revision"], "branch_id": branch["id"]},
            headers=headers,
        )
        assert response.status_code == 200, response.text
        assert chat.state(sid)["payload"]["world"]["coins"] == 10
        assert len(chat.messages(sid)["items"]) == 1
        chat.switch(sid, original, chat.story(sid)["revision"])
        assert chat.state(sid)["payload"]["world"]["coins"] == 999
        assert len(chat.messages(sid)["items"]) == 2


def test_explicit_partial_apply_is_atomic_and_idempotent(tmp_path):
    app, sid, ids = api_setup(tmp_path)
    chat = app.state.chat

    def transport(request):
        if request.method == "GET":
            return httpx.Response(
                200,
                json={
                    "models": [
                        {
                            "type": "llm",
                            "key": "disk",
                            "loaded_instances": [{"id": "loaded", "config": {"context_length": 8192}}],
                        }
                    ]
                },
            )
        return httpx.Response(200, text='data: {"choices":[{"delta":{"content":"partial"}}]}\n\n')

    app.state.generation.adapter = LmStudioAdapter(transport=httpx.MockTransport(transport))
    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
        headers = authenticate(app, client)
        branch = chat.branch(sid)
        body = {
            "expected_revision": branch["revision"],
            "branch_id": branch["id"],
            "model_id": "loaded",
            "character_id": ids[0],
        }
        response = client.post(
            f"/api/v1/stories/{sid}/generations", json=body, headers={**headers, "Idempotency-Key": "partial"}
        )
        assert response.status_code == 202, response.text
        job = response.json()["generation_id"]
        assert "generation.failed" in client.get(f"/api/v1/generations/{job}/stream").text
        assert chat.messages(sid)["total"] == 0
        revision = {"expected_revision": branch["revision"], "branch_id": branch["id"]}
        first = client.post(f"/api/v1/stories/{sid}/partials/{job}/apply", json=revision, headers=headers)
        assert first.status_code == 200, first.text
        repeated = client.post(f"/api/v1/stories/{sid}/partials/{job}/apply", json=revision, headers=headers)
        assert repeated.json()["message_id"] == first.json()["message_id"]
        assert chat.messages(sid)["total"] == 1
        assert chat.messages(sid)["items"][0]["partial"] is True


def test_cross_branch_state_and_message_are_rejected_even_if_revision_matches(tmp_path):
    chat, sid, ids = setup_chat(tmp_path)
    original = chat.branch(sid)["id"]
    state = chat.state(sid)["payload"]
    chat.fork(sid, expected_revision=chat.branch(sid)["revision"])
    with pytest.raises(AppError) as error:
        chat.update_state(sid, state, chat.branch(sid)["revision"])
    assert error.value.code == "state_scope_conflict"
    with pytest.raises(AppError) as error:
        chat.append(
            sid,
            "Нельзя в неактивную",
            branch_id=original,
            expected_revision=chat.branch(sid, original)["revision"],
        )
    assert error.value.code == "inactive_branch"
