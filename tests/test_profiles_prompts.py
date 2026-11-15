import asyncio
import copy
import json
from uuid import uuid4

import httpx
import pytest
from taverna.core import AppError
from taverna.generation_api import GenerationParameters
from taverna.knowledge import MemoryRecord
from taverna.llm import LmStudioAdapter
from taverna.profiles import BindingWrite, ProfileService, ProfileWrite
from taverna.prompts import ContextTraces, PromptBuilder, expand
from taverna.quality import inspect_answer
from taverna.storage import JsonStore, now
from test_chat import append, setup_chat


def test_profile_partial_inheritance_and_revision(tmp_path):
    chat, sid, ids = setup_chat(tmp_path)
    service = chat.generation.profiles
    assert len(service.store.scan("presets/profiles/")[0]) == 3

    def profile(name, patch):
        return service.write(ProfileWrite(name=name, patch=patch, expected_revision=0))["id"]

    global_id = profile(
        "Общий", {"parameters": {"temperature": 0.3, "seed": 42}, "extras": {"my_setting": True}}
    )
    actor_id = profile("Персонаж", {"parameters": {"top_p": 0.8}})
    story_id = profile("История", {"parameters": {"max_tokens": 512}})
    for scope, scope_id, pid in [
        ("global", "default", global_id),
        ("character", ids[0], actor_id),
        ("story", sid, story_id),
    ]:
        service.bind(BindingWrite(scope=scope, scope_id=scope_id, profile_id=pid, expected_revision=0))
    result = service.resolve(character_id=ids[0], story_id=sid, parameters={"temperature": 0.6})
    assert result["settings"]["parameters"] == {
        "temperature": 0.6,
        "top_p": 0.8,
        "max_tokens": 512,
        "seed": 42,
    }
    assert result["settings"]["extras"] == {"my_setting": True}
    assert [row["scope"] for row in result["chain"]] == ["builtin", "global", "character", "story"]
    assert service.resolve()["settings"]["parameters"]["temperature"] == 0.3
    with pytest.raises(AppError):
        service.write(ProfileWrite(name="Конфликт", patch={}, expected_revision=0), global_id)
    restarted = ProfileService(service.store)
    assert restarted.resolve(story_id=sid)["settings"]["parameters"]["max_tokens"] == 512


@pytest.mark.parametrize(
    "patch",
    [
        {"parameters": {"temperature": 20}},
        {"parameters": {"unknown": 1}},
        {"budgets": {"memory": -1}},
        {"quality": {"max_attempts": 100}},
        {
            "blocks": [
                {
                    "id": str(uuid4()),
                    "name": "Опасное условие",
                    "source": "custom",
                    "condition": {"field": "eval", "value": '__import__("os")'},
                }
            ]
        },
    ],
)
def test_invalid_profile_rejected_without_writes(tmp_path, patch):
    service = ProfileService(JsonStore(tmp_path))
    with pytest.raises(AppError):
        service.write(ProfileWrite(name="Недопустимый", patch=patch, expected_revision=0))
    assert len(service.store.scan("presets/profiles/")[0]) == 3


def context_fixture(tmp_path):
    chat, sid, ids = setup_chat(tmp_path)
    append(chat, sid, "В таверне есть ключ.")
    state = chat.state(sid)["payload"]
    bid = chat.branch(sid)["id"]
    for name, knowers, branch in [
        ("known", [ids[0]], bid),
        ("secret", [ids[1]], bid),
        ("other", [ids[0]], str(uuid4())),
    ]:
        rid = str(uuid4())
        state["memory"][rid] = MemoryRecord(
            id=rid,
            text=name,
            knower_ids=knowers,
            story_id=sid,
            branch_id=branch,
            version=1,
            created_at=now(),
            updated_at=now(),
            provenance={"mode": "manual", "note": "Тестовый факт", "source_hashes": {}},
        ).model_dump(mode="json")
    chat.update_state(sid, state, chat.branch(sid)["revision"])
    return chat, sid, ids


def build(chat, sid, cid, resolved=None, history=None):
    return PromptBuilder().build(
        story=chat.story(sid),
        branch=chat.branch(sid),
        state=chat.state(sid),
        character_id=cid,
        history=chat._prefix(sid, chat.branch(sid)["id"]) if history is None else history,
        resolved=resolved or chat.generation.profiles.resolve(),
    )


def test_context_determinism_privacy_macros_and_budget(tmp_path):
    chat, sid, ids = context_fixture(tmp_path)
    first = build(chat, sid, ids[0])
    second = build(chat, sid, ids[0])
    assert first == second
    text = json.dumps(first["messages"], ensure_ascii=False)
    assert "known" in text and "secret" not in text and "other" not in text
    assert first["estimate"] + first["reply_reserve"] + first["safety_margin"] <= first["context_limit"]
    assert not first["exact_tokens"]
    assert expand('{{char}} {{__import__("os")}}', {"char": "Мира"}) == 'Мира {{__import__("os")}}'
    history = chat._prefix(sid, chat.branch(sid)["id"])
    hidden = copy.deepcopy(history[0])
    hidden["id"] = str(uuid4())
    hidden["text"] = "секретная реплика"
    hidden["present_ids"] = [ids[1]]
    result = build(chat, sid, ids[0], history=[hidden, *history])
    assert result["hidden_history_count"] == 1 and "секретная реплика" not in str(result["messages"])


def test_pinned_overflow_and_unpinned_pruning(tmp_path):
    chat, sid, ids = context_fixture(tmp_path)
    resolved = chat.generation.profiles.resolve()
    resolved["settings"].update(context_limit=1800, context_safety_margin=128)
    history = chat._prefix(sid, chat.branch(sid)["id"])
    long = copy.deepcopy(history[0])
    long["id"] = str(uuid4())
    long["text"] = "старый текст " * 3000
    result = build(chat, sid, ids[0], resolved, history=[long, *history])
    assert any(row["reason"] in ("context_budget", "block_budget") for row in result["excluded"])
    with pytest.raises(AppError, match="Закреплённые"):
        build(chat, sid, ids[0], resolved, history=[long])


def test_card_prompt_overrides_and_creator_notes_excluded(tmp_path):
    chat, sid, ids = setup_chat(tmp_path)
    story = chat.story(sid)
    fields = story["payload"]["character_snapshots"][ids[0]]["card"]["fields"]
    fields.update(
        creator_notes="НЕ ВКЛЮЧАТЬ",
        system_prompt="До {{original}} после {{char}}",
        post_history_instructions="Пиши кратко",
    )
    result = PromptBuilder().build(
        story=story,
        branch=chat.branch(sid),
        state=chat.state(sid),
        character_id=ids[0],
        history=[],
        resolved=chat.generation.profiles.resolve(),
    )
    text = str(result["messages"])
    assert (
        "НЕ ВКЛЮЧАТЬ" not in text
        and "До Ты участвуешь" in text
        and "после Мира" in text
        and "Пиши кратко" in text
    )


def test_context_diagnostic_opt_out_does_not_save_prompt(tmp_path):
    traces = ContextTraces(JsonStore(tmp_path))
    value = traces.save(
        {
            "messages": [{"role": "user", "content": "private"}],
            "selected": [{"content": "private"}],
            "profile": {"secret": "private"},
            "estimate": 7,
        },
        False,
    )
    assert not value["payload"]["full_context_saved"]
    assert "private" not in json.dumps(value)
    assert len(value["payload"]["request_sha256"]) == 64


def test_quality_flags_and_generation_parameters():
    settings = {"repeat_threshold": 0.9, "format": "json"}
    assert {f["code"] for f in inspect_answer("Вы: снова", ["Вы: снова"], settings)} == {
        "repetition",
        "role",
        "format",
    }
    assert inspect_answer('{"ок":true}', [], settings) == []
    assert GenerationParameters(seed=42, stop=["Конец"]).model_dump(exclude_none=True)["stop"] == ["Конец"]
    with pytest.raises(ValueError):
        GenerationParameters(stop=[""])


@pytest.mark.asyncio
async def test_quality_retry_has_bounded_attempts_and_actual_context_trace(tmp_path):
    chat, sid, ids = setup_chat(tmp_path)
    append(chat, sid, "Начни историю")
    service = chat.generation.profiles
    pid = service.write(
        ProfileWrite(
            name="JSON",
            patch={"quality": {"format": "json", "mode": "retry", "max_attempts": 2}},
            expected_revision=0,
        )
    )["id"]
    requests = []

    def transport(request):
        if request.method == "GET":
            return httpx.Response(
                200,
                json={
                    "models": [
                        {
                            "type": "llm",
                            "key": "file",
                            "loaded_instances": [{"id": "loaded", "config": {"context_length": 8192}}],
                        }
                    ]
                },
            )
        requests.append(json.loads(request.content))
        text = "не JSON" if len(requests) == 1 else '{"ответ":"Привет"}'
        data = json.dumps({"choices": [{"delta": {"content": text}}]}, ensure_ascii=False)
        return httpx.Response(200, content=f"data: {data}\n\ndata: [DONE]\n\n".encode())

    chat.generation.adapter = LmStudioAdapter(transport=httpx.MockTransport(transport))
    events = []

    async def emit(kind, data):
        events.append((kind, data))

    result = await chat.generate(
        {
            "story_id": sid,
            "branch_id": chat.branch(sid)["id"],
            "expected_revision": chat.branch(sid)["revision"],
            "character_id": ids[0],
            "model_id": "loaded",
            "endpoint": "http://127.0.0.1:1234",
            "parameters": {},
            "profile_id": pid,
        },
        asyncio.Event(),
        emit,
    )
    assert len(requests) == 2 and len(result["quality_audit"]) == 2
    assert chat.messages(sid)["total"] == 2
    trace = chat.store.get(f"presets/context-traces/{result['context_trace_id']}.json")["payload"]
    assert trace["messages"] == requests[-1]["messages"]
    assert trace["parameters"]["max_tokens"] == requests[-1]["max_tokens"]
    assert len([e for e in events if e[0] == "attempt_started"]) == 2
    assert result["quality_audit"][0]["flags"][0]["code"] == "format"
    # Same actual generator with exhausted retry rejects without appending an answer.
    requests.clear()
    bad = service.get(pid)
    bad["payload"]["patch"]["quality"]["max_attempts"] = 1
    service.write(
        ProfileWrite(name="JSON", patch=bad["payload"]["patch"], expected_revision=bad["revision"]), pid
    )
    with pytest.raises(AppError, match="отклонён"):
        await chat.generate(
            {
                "story_id": sid,
                "branch_id": chat.branch(sid)["id"],
                "expected_revision": chat.branch(sid)["revision"],
                "character_id": ids[0],
                "model_id": "loaded",
                "endpoint": "http://127.0.0.1:1234",
                "parameters": {},
                "profile_id": pid,
            },
            asyncio.Event(),
            emit,
        )
    assert chat.messages(sid)["total"] == 2 and len(requests) == 1


def test_context_conditions_order_and_pinned_fact(tmp_path):
    chat, sid, ids = context_fixture(tmp_path)
    resolved = chat.generation.profiles.resolve()
    blocks = resolved["settings"]["blocks"]
    custom = {
        "id": str(uuid4()),
        "name": "Группа",
        "source": "custom",
        "enabled": True,
        "role": "system",
        "priority": 10,
        "pinned": False,
        "template": "ГРУППОВОЙ БЛОК",
        "max_tokens": None,
        "condition": {"field": "group", "op": "equals", "value": True},
    }
    blocks.insert(0, custom)
    assert "ГРУППОВОЙ БЛОК" in str(build(chat, sid, ids[0], resolved)["messages"])
    custom["condition"]["value"] = False
    result = build(chat, sid, ids[0], resolved)
    assert "ГРУППОВОЙ БЛОК" not in str(result["messages"])
    assert any(row["reason"] == "condition_false" for row in result["excluded"])
    state = chat.state(sid)["payload"]
    next(fact for fact in state["memory"].values() if fact["text"] == "known")["pinned"] = True
    chat.update_state(sid, state, chat.branch(sid)["revision"])
    resolved["settings"]["budgets"]["memory"] = 0
    with pytest.raises(AppError, match="ручной бюджет"):
        build(chat, sid, ids[0], resolved)
    resolved["settings"]["budgets"]["memory"] = 128
    assert "known" in str(build(chat, sid, ids[0], resolved)["messages"])
    resolved["settings"]["automatic_context"] = False
    with pytest.raises(AppError, match="Ручное окно"):
        PromptBuilder().build(
            story=chat.story(sid),
            branch=chat.branch(sid),
            state=chat.state(sid),
            character_id=ids[0],
            history=[],
            resolved=resolved,
            loaded_context=4096,
        )


def test_profile_api_types_and_context_trace_access(tmp_path):
    from fastapi.testclient import TestClient
    from taverna.core import Config
    from taverna.main import create_app

    app = create_app(Config(tmp_path))
    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
        token = app.state.sessions.bootstrap()
        csrf = client.post("/api/v1/session/bootstrap", json={"token": token}).json()["csrf"]
        client.headers.update({"X-Taverna-CSRF": csrf})
        for body in [{"parameters": []}, {"role": "execute"}, {"character_id": {}}]:
            assert client.post("/api/v1/profiles/resolve", json=body).status_code == 422
        assert client.get("/api/v1/contexts/not-a-uuid").status_code == 422
        trace = app.state.generation.traces.save({"messages": [{"role": "user", "content": "Привет"}]}, True)
        assert client.get(f"/api/v1/contexts/{trace['id']}").json() == trace
        assert client.get("/api/v1/contexts/" + str(uuid4())).status_code == 404
        assert (
            client.post(
                "/api/v1/profiles",
                json={"name": "Ошибка", "patch": {"parameters": {"top_p": 0}}, "expected_revision": 0},
            ).status_code
            == 422
        )
