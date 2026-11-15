import asyncio
import copy
import json
from uuid import uuid4

import httpx
import pytest
from taverna.core import AppError
from taverna.knowledge import KnowledgePolicy, RecordInput
from taverna.llm import LmStudioAdapter
from taverna.memory import MemoryService
from taverna.memory_ai import MemoryAI
from taverna.memory_index import MemoryIndex, VectorCache
from taverna.memory_io import MemoryPackages
from taverna.memory_search import MemorySearch
from taverna.prompts import PromptBuilder
from taverna.storage import JsonStore
from test_chat import append, setup_chat
from test_generation import terminal


def setup_memory(tmp_path):
    chat, sid, ids = setup_chat(tmp_path)
    service = MemoryService(chat)
    return service, chat, sid, ids


def save(service, sid, ids, text="Факт", **kwargs):
    return service.save(
        sid,
        service.chat.branch(sid)["id"],
        service.chat.branch(sid)["revision"],
        RecordInput(text=text, knower_ids=[ids[0]], **kwargs),
    )["record"]


def test_six_levels_versions_relationships_and_conflicts(tmp_path):
    service, chat, sid, ids = setup_memory(tmp_path)
    rows = []
    for level in ("operational", "episodic", "personal", "relationship", "world", "plot"):
        extra = {"relation": {"parties": ids, "metrics": {"доверие": 25}}} if level == "relationship" else {}
        rows.append(save(service, sid, ids, level, level=level, **extra))
    assert len(service.list(sid, chat.branch(sid)["id"])["records"]) == 6
    first = save(service, sid, ids, "Дверь закрыта", claim_key="дверь/состояние")
    with pytest.raises(AppError, match="несовпадающие"):
        save(service, sid, ids, "Дверь открыта", claim_key="дверь/состояние")
    result = service.save(
        sid,
        chat.branch(sid)["id"],
        chat.branch(sid)["revision"],
        RecordInput(text="Дверь открыта", knower_ids=[ids[0]], claim_key="дверь/состояние"),
        conflict_policy="supersede",
    )
    state = chat.state(sid)["payload"]
    assert state["memory"][first["id"]]["status"] == "superseded"
    assert state["memory"][first["id"]]["version"] == 2
    assert state["memory"][first["id"]]["history"][0]["previous"]["text"] == "Дверь закрыта"
    with pytest.raises(AppError, match="Версия"):
        service.save(
            sid,
            chat.branch(sid)["id"],
            chat.branch(sid)["revision"],
            RecordInput(text="Новая версия", knower_ids=[ids[0]]),
            record_id=result["record"]["id"],
            expected_version=0,
        )
    with pytest.raises(ValueError):
        RecordInput(
            level="relationship",
            text="Неверные показатели",
            knower_ids=[ids[0]],
            relation={"parties": ids, "metrics": {"доверие": 101}},
        )
    assert chat.store.integrity()["ok"]


def test_secret_summary_relationship_search_and_source_version_boundary(tmp_path):
    service, chat, sid, ids = setup_memory(tmp_path)
    source = append(chat, sid, "Только Мира знает пароль")
    fact = save(service, sid, ids, "Пароль — Звезда", nature="secret", source_ids=[source["id"]])
    summary = save(
        service,
        sid,
        ids,
        "Сводка о Звезде",
        nature="summary",
        level="episodic",
        source_fact_versions={fact["id"]: 1},
    )
    with pytest.raises(AppError, match="недоступный"):
        service.save(
            sid,
            chat.branch(sid)["id"],
            chat.branch(sid)["revision"],
            RecordInput(
                text="Сводка для Орна",
                knower_ids=[ids[1]],
                nature="summary",
                source_fact_versions={fact["id"]: 1},
            ),
        )
    # Adversarial imported/persisted scope labels do not override dependency checks.
    data = copy.deepcopy(chat.state(sid)["payload"])
    leaked = copy.deepcopy(data["memory"][summary["id"]])
    leaked["id"] = str(uuid4())
    leaked["knower_ids"] = [ids[1]]
    data["memory"][leaked["id"]] = leaked
    chat.update_state(sid, data, chat.branch(sid)["revision"])
    snap = service.snapshot(sid, chat.branch(sid)["id"])
    index = MemoryIndex(service)
    assert not index.search(snap, "Звезд", ids[1])["results"]
    assert len(index.search(snap, "Звезд", ids[0])["results"]) == 2
    context = PromptBuilder().build(
        story=chat.story(sid),
        branch=chat.branch(sid),
        state=chat.state(sid),
        character_id=ids[1],
        history=list(snap["messages"].values()),
        resolved=chat.generation.profiles.resolve(),
    )
    assert "Сводка о Звезде" not in str(context["messages"]) and "Пароль — Звезда" not in str(
        context["messages"]
    )
    updated = service.save(
        sid,
        chat.branch(sid)["id"],
        chat.branch(sid)["revision"],
        RecordInput(text="Пароль сменился", knower_ids=[ids[0]], nature="secret", source_ids=[source["id"]]),
        record_id=fact["id"],
        expected_version=1,
    )["record"]
    assert updated["version"] == 2
    snap = service.snapshot(sid, chat.branch(sid)["id"])
    assert service.policy(snap).reason(snap["records"][summary["id"]], ids[0]) == "source_fact_changed"


def test_fork_remaps_sources_and_relationship_scopes_without_future_leak(tmp_path):
    service, chat, sid, ids = setup_memory(tmp_path)
    message = append(chat, sid, "Встреча")
    fact = save(service, sid, ids, "Знакомство", source_ids=[message["id"]])
    relation = save(
        service,
        sid,
        ids,
        "Доверяет Мире",
        level="relationship",
        relation={"parties": ids, "metrics": {"доверие": 15}},
        source_ids=[message["id"]],
    )
    append(chat, sid, "После встречи")
    forked = chat.fork(sid, expected_revision=chat.branch(sid)["revision"], name="Другая линия")
    bid = forked["branch"]["id"]
    snap = service.snapshot(sid, bid)
    assert snap["records"][fact["id"]]["branch_id"] == bid
    assert snap["records"][relation["id"]]["branch_id"] == bid
    assert service.policy(snap).reason(snap["records"][fact["id"]], ids[0]) is None
    assert service.policy(snap).reason(snap["records"][relation["id"]], ids[0]) is None
    assert message["id"] not in snap["records"][fact["id"]]["provenance"]["source_hashes"]
    edited = chat.fork(
        sid,
        expected_revision=chat.branch(sid)["revision"],
        through_id=chat.messages(sid)["items"][0]["id"],
        replacement="Другая встреча",
    )
    assert edited["branch"]["payload"]["state_id"]
    assert not chat.state(sid)["payload"]["memory"]


def test_forgetting_archives_only_old_unpinned_and_keeps_sources(tmp_path):
    service, chat, sid, ids = setup_memory(tmp_path)
    source = append(chat, sid, "Старое событие")
    low = save(service, sid, ids, "Малозначительное", source_ids=[source["id"]], salience=0.1)
    pinned = save(service, sid, ids, "Важное", source_ids=[source["id"]], salience=0.1, pinned=True)
    append(chat, sid, "Новое событие")
    result = service.archive(sid, chat.branch(sid)["id"], chat.branch(sid)["revision"], min_age_messages=1)
    assert result["archived_ids"] == [low["id"]]
    state = chat.state(sid)["payload"]
    assert state["memory"][pinned["id"]]["status"] == "active"
    assert chat.messages(sid)["items"][0]["text"] == "Старое событие"
    assert state["memory"][low["id"]]["history"][0]["previous"]["status"] == "active"


def test_memory_package_roundtrip_external_sources_and_idempotence(tmp_path):
    service, chat, sid, ids = setup_memory(tmp_path)
    source = append(chat, sid, "Общее наблюдение")
    fact = save(
        service,
        sid,
        ids,
        "Мира увидела ключ",
        source_ids=[source["id"]],
        extras={"неизвестное": {"поле": True}},
    )
    save(service, sid, ids, "Сводка встречи", nature="summary", source_fact_versions={fact["id"]: 1})
    packages = MemoryPackages(service)
    package = packages.export(sid, chat.branch(sid)["id"])
    text = json.dumps(package, ensure_ascii=False)
    target = chat.create("Принимающая история", ids)["id"]
    bid = chat.branch(target)["id"]
    with pytest.raises(AppError, match="Сопоставьте исходные"):
        packages.preview(target, bid, text, actor_map={}, source_map={})
    preview = packages.preview(target, bid, text, actor_map={}, source_map={}, allow_external_sources=True)
    assert preview["warnings"] and not chat.state(target)["payload"]["memory"]
    result = packages.apply(
        target,
        bid,
        chat.branch(target)["revision"],
        text,
        actor_map={},
        source_map={},
        allow_external_sources=True,
        confirmed=True,
    )
    assert len(result["record_ids"]) == 2
    imported = chat.state(target)["payload"]["memory"]
    imported_fact = next(r for r in imported.values() if r["text"] == "Мира увидела ключ")
    assert imported_fact["extras"]["неизвестное"] == {"поле": True}
    assert imported_fact["source_ids"] == [] and imported_fact["extras"]["import_origin"]["external_sources"]
    assert packages.apply(
        target,
        bid,
        chat.branch(target)["revision"],
        text,
        actor_map={},
        source_map={},
        allow_external_sources=True,
        confirmed=True,
    )["already_imported"]
    assert len(chat.state(target)["payload"]["memory"]) == 2
    bad = copy.deepcopy(package)
    bad["sha256"] = "0" * 64
    with pytest.raises(AppError):
        packages.preview(
            target, bid, json.dumps(bad), actor_map={}, source_map={}, allow_external_sources=True
        )
    assert chat.store.integrity()["ok"]


@pytest.mark.asyncio
async def test_vector_cache_deduplicates_validates_and_invalidates_model(tmp_path):
    service, chat, sid, ids = setup_memory(tmp_path)
    calls = []
    model_key = ["one"]

    def transport(request):
        if request.method == "GET":
            return httpx.Response(
                200,
                json={
                    "models": [
                        {
                            "type": "embedding",
                            "key": model_key[0],
                            "loaded_instances": [{"id": "embedding", "config": {}}],
                        }
                    ]
                },
            )
        data = json.loads(request.content)
        calls.append(data)
        return httpx.Response(
            200,
            json={
                "data": [
                    {"index": i, "embedding": [float(len(text)), 1.0]} for i, text in enumerate(data["input"])
                ]
            },
        )

    adapter = LmStudioAdapter(transport=httpx.MockTransport(transport))
    cache = VectorCache(chat.store, adapter)
    vectors, first = await cache.vectors("embedding", ["ключ", "ключ", "дверь"], asyncio.Event())
    assert len(vectors) == 3 and len(calls) == 1 and len(calls[0]["input"]) == 2
    again, second = await cache.vectors("embedding", ["ключ"], asyncio.Event())
    assert second == first and again[0] == vectors[0] and len(calls) == 1
    model_key[0] = "two"
    _, third = await cache.vectors("embedding", ["ключ"], asyncio.Event())
    assert third != first and len(calls) == 2
    invalid = LmStudioAdapter(
        transport=httpx.MockTransport(
            lambda request: (
                httpx.Response(200, json={"data": [{"index": 0, "embedding": [float("nan")]}]})
                if request.method == "POST"
                else transport(request)
            )
        )
    )
    with pytest.raises(AppError, match="Смысловой поиск"):
        await invalid.embeddings("embedding", ["текст"])


@pytest.mark.asyncio
async def test_hybrid_filters_before_embeddings_and_falls_back(tmp_path):
    service, chat, sid, ids = setup_memory(tmp_path)
    save(service, sid, ids, "публичный ключ")
    service.save(
        sid,
        chat.branch(sid)["id"],
        chat.branch(sid)["revision"],
        RecordInput(text="СЕКРЕТ ДРУГОГО NPC", knower_ids=[ids[1]]),
    )
    requests = []

    def transport(request):
        if request.method == "GET":
            return httpx.Response(
                200,
                json={
                    "models": [
                        {
                            "type": "embedding",
                            "key": "emb",
                            "loaded_instances": [{"id": "embedding", "config": {}}],
                        }
                    ]
                },
            )
        data = json.loads(request.content)
        requests.extend(data["input"])
        return httpx.Response(
            200,
            json={
                "data": [
                    {"index": i, "embedding": [1.0, float(len(text))]} for i, text in enumerate(data["input"])
                ]
            },
        )

    chat.generation.adapter = LmStudioAdapter(transport=httpx.MockTransport(transport))
    search = MemorySearch(service)
    payload = {
        "story_id": sid,
        "branch_id": chat.branch(sid)["id"],
        "expected_revision": chat.branch(sid)["revision"],
        "query": "ключ",
        "character_id": ids[0],
        "limit": 50,
        "embedding_model_id": "embedding",
        "endpoint": "http://127.0.0.1:1234",
    }

    async def emit(kind, data):
        pass

    result = await search.run(payload, asyncio.Event(), emit)
    assert result["mode"] == "hybrid" and len(result["results"]) == 1
    assert not any("СЕКРЕТ" in text for text in requests)
    payload["embedding_model_id"] = "not-loaded"
    fallback = await search.run(payload, asyncio.Event(), emit)
    assert (
        fallback["mode"] == "text"
        and len(fallback["results"]) == 1
        and fallback["embedding_error"] == "model_not_loaded"
    )


@pytest.mark.asyncio
async def test_real_adapter_extraction_proposal_review_no_direct_write(tmp_path):
    service, chat, sid, ids = setup_memory(tmp_path)
    source = append(chat, sid, "Мира увидела ключ")

    def transport(request):
        if request.method == "GET":
            return httpx.Response(
                200,
                json={
                    "models": [
                        {
                            "type": "llm",
                            "key": "llm",
                            "loaded_instances": [{"id": "loaded", "config": {"context_length": 8192}}],
                        }
                    ]
                },
            )
        data = json.loads(request.content)
        assert "Мира увидела ключ" in str(data["messages"])
        response = json.dumps(
            {"facts": [{"text": "Мира знает о ключе", "source_ids": [source["id"]]}]}, ensure_ascii=False
        )
        event = json.dumps({"choices": [{"delta": {"content": response}}]}, ensure_ascii=False)
        return httpx.Response(200, content=f"data: {event}\n\ndata: [DONE]\n\n".encode())

    chat.generation.adapter = LmStudioAdapter(transport=httpx.MockTransport(transport))
    ai = MemoryAI(service)
    request = {
        "branch_id": chat.branch(sid)["id"],
        "expected_revision": chat.branch(sid)["revision"],
        "character_id": ids[0],
        "model_id": "loaded",
        "operation": "extract",
    }
    queued = await ai.enqueue(sid, request, "memory-test")
    job = await terminal(chat.generation.queue, queued["generation_id"])
    assert job["payload"]["state"] == "completed", job["payload"]
    assert not chat.state(sid)["payload"]["memory"]
    proposal = chat.store.get(ai.key(sid, request["branch_id"], queued["proposal_id"]))
    result = ai.review(
        sid,
        request["branch_id"],
        queued["proposal_id"],
        expected_revision=chat.branch(sid)["revision"],
        proposal_revision=proposal["revision"],
        accept=True,
        confirmed=True,
    )
    assert len(result["record_ids"]) == 1
    record = chat.state(sid)["payload"]["memory"][result["record_ids"][0]]
    assert record["provenance"]["mode"] == "model" and record["knower_ids"] == [ids[0]]
    assert ai.review(
        sid,
        request["branch_id"],
        queued["proposal_id"],
        expected_revision=chat.branch(sid)["revision"],
        proposal_revision=proposal["revision"],
        accept=True,
        confirmed=True,
    )["already_reviewed"]
    assert (await ai.enqueue(sid, request, "memory-test"))["generation_id"] == queued["generation_id"]
    await chat.generation.queue.close()


@pytest.mark.asyncio
async def test_foreground_dialog_preempts_background_without_replay(tmp_path):
    service, chat, sid, ids = setup_memory(tmp_path)
    queue = chat.generation.queue
    entered = asyncio.Event()
    order = []

    async def background(payload, cancel, emit):
        entered.set()
        await asyncio.Event().wait()
        order.append("background")

    async def foreground(payload, cancel, emit):
        order.append("foreground")
        return {"ok": True}

    queue.handlers["memory_extract"] = background
    queue.handlers["dialog_test"] = foreground
    bg = await queue.enqueue("memory_extract", {}, priority=10, idempotency_key="bg")
    await entered.wait()
    fg = await queue.enqueue("dialog_test", {}, priority=0, idempotency_key="fg")
    assert (await terminal(queue, fg["id"]))["payload"]["state"] == "completed"
    assert (await queue.get(bg["id"]))["payload"]["state"] == "cancelled" and order == ["foreground"]
    assert (await queue.events(bg["id"]))[-1]["data"]["reason"] == "foreground_priority"
    await queue.close()


def test_player_relationship_package_maps_story_role(tmp_path):
    from taverna.knowledge import player_id

    service, chat, sid, ids = setup_memory(tmp_path)
    relation = save(
        service,
        sid,
        ids,
        "Мира доверяет игроку",
        level="relationship",
        relation={"parties": [ids[0], player_id(sid)], "metrics": {"доверие": 40}},
    )
    package = MemoryPackages(service).export(sid, chat.branch(sid)["id"])
    target = chat.create("Другая история", ids)["id"]
    bid = chat.branch(target)["id"]
    result = MemoryPackages(service).apply(
        target,
        bid,
        chat.branch(target)["revision"],
        json.dumps(package),
        actor_map={},
        source_map={},
        allow_external_sources=False,
        confirmed=True,
    )
    imported = chat.state(target)["payload"]["relationships"][result["record_ids"][0]]
    assert imported["relation"]["parties"] == [ids[0], player_id(target)]
    assert relation["relation"]["parties"] != imported["relation"]["parties"]


def test_index_cache_corruption_rebuilds_and_source_search_survives(tmp_path):
    service, chat, sid, ids = setup_memory(tmp_path)
    save(service, sid, ids, "Нужный ключ")
    index = MemoryIndex(service)
    snap = service.snapshot(sid, chat.branch(sid)["id"])
    head = index.rebuild(snap)
    with chat.store.view() as view:
        obj = view.manifest["entries"][head["payload"]["shards"][0]]["object"]
    (chat.store.data / obj).write_text("повреждение", encoding="utf-8")
    assert index.search(snap, "ключ", ids[0])["total"] == 1
    repaired = index.rebuild(snap)
    assert repaired["payload"]["entry_count"] == 1 and chat.store.integrity()["ok"]
    with chat.store.view() as view:
        obj = view.manifest["entries"][index.key(snap["branch"]["id"])]["object"]
    (chat.store.data / obj).write_text("{}", encoding="utf-8")
    index.rebuild(snap)
    assert index.search(snap, "ключ", ids[0])["total"] == 1 and chat.store.integrity()["ok"]


def test_index_rebuild_10000_messages_json_only(tmp_path):
    from taverna.chat_import import ChatImport, import_chat

    service, chat, sid, ids = setup_memory(tmp_path)
    rows = [
        {"name": "Игрок", "is_user": True, "mes": f"Текст сообщения {i}", "send_date": "2026-01-01T00:00:00Z"}
        for i in range(10000)
    ]
    imported = import_chat(
        chat,
        ChatImport(
            format="json", text=json.dumps(rows), name="Индекс 10k", character_ids=ids, confirmed=True
        ),
    )
    sid = imported["story"]["id"]
    bid = chat.branch(sid)["id"]
    snap = service.snapshot(sid, bid)
    index = MemoryIndex(service)
    head = index.rebuild(snap)
    assert head["payload"]["entry_count"] == 10000 and len(head["payload"]["shards"]) == 40
    found = index.search(snap, "9999", ids[0], include_messages=True)
    assert found["total"] == 1 and found["results"][0]["text"] == "Текст сообщения 9999"
    assert chat.messages(sid)["total"] == 10000
    assert not list(tmp_path.rglob("*.sqlite*"))
    assert chat.store.integrity()["ok"]


def test_api_memory_draft_cas_and_invalid_editor_json(tmp_path):
    from fastapi.testclient import TestClient
    from taverna.core import Config
    from taverna.main import create_app

    app = create_app(Config(tmp_path))
    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
        csrf = client.post(
            "/api/v1/session/bootstrap", json={"token": app.state.sessions.bootstrap()}
        ).json()["csrf"]
        client.headers.update({"X-Taverna-CSRF": csrf})
        from taverna.cards import new_card

        actor = client.post(
            "/api/v1/characters", json={"expected_revision": 0, "character": new_card("Мира")}
        ).json()["id"]
        sid = client.post("/api/v1/stories", json={"name": "Черновики", "character_ids": [actor]}).json()[
            "id"
        ]
        branch = app.state.chat.branch(sid)
        bid = branch["id"]
        base = f"/api/v1/stories/{sid}/memories"
        draft_id = str(uuid4())
        payload = {
            "draft_id": draft_id,
            "branch_id": bid,
            "expected_revision": 0,
            "record_id": None,
            "source_revision": branch["revision"],
            "form": {"text": "", "knower_ids": [actor]},
            "raw": "{не закончено",
            "applied_raw": "{}",
            "reason": "Черновик",
        }
        response = client.put(base + "/draft", json=payload)
        assert response.status_code == 200 and response.json()["revision"] == 1
        assert (
            client.get(base + "/draft", params={"branch_id": bid}).json()["draft"]["payload"]["raw"]
            == "{не закончено"
        )
        assert client.put(base + "/draft", json=payload).status_code == 409
        assert (
            app.state.chat.branch(sid)["revision"] == branch["revision"]
            and not app.state.chat.state(sid)["payload"]["memory"]
        )
        assert (
            client.post(
                base + "/validate", json={"record": {"text": "ok", "knower_ids": "invalid"}}
            ).status_code
            == 422
        )
        assert (
            client.post(
                base + "/draft-validate", json={"record": {"text": "", "knower_ids": [actor]}}
            ).status_code
            == 200
        )
        assert (
            client.post("/api/v1/profiles/validate", json={"patch": {"blocks": "invalid"}}).status_code == 422
        )
        assert (
            client.request(
                "DELETE",
                base + "/draft",
                json={"draft_id": draft_id, "branch_id": bid, "expected_revision": 1},
            ).status_code
            == 200
        )
        assert client.get(base + "/draft", params={"branch_id": bid}).json()["draft"] is None


@pytest.mark.asyncio
async def test_stale_extraction_does_not_write_proposal_or_state(tmp_path):
    service, chat, sid, ids = setup_memory(tmp_path)
    message = append(chat, sid, "Первое сообщение")
    ai = MemoryAI(service)
    old = chat.branch(sid)
    append(chat, sid, "Новое сообщение")

    async def emit(kind, data):
        pass

    with pytest.raises(AppError, match="изменилась"):
        await ai.run(
            {
                "story_id": sid,
                "branch_id": old["id"],
                "expected_revision": old["revision"],
                "character_id": ids[0],
                "source_ids": [message["id"]],
            },
            asyncio.Event(),
            emit,
        )
    assert not chat.store.scan("memory/proposals/")[0] and not chat.state(sid)["payload"]["memory"]


def test_npc_projection_omits_private_previous_versions_and_message_alternatives(tmp_path):
    service, chat, sid, ids = setup_memory(tmp_path)
    source = chat.append(
        sid,
        "Общий текст",
        expected_revision=chat.branch(sid)["revision"],
        metadata={"import_metadata": {"swipes": ["СКРЫТАЯ АЛЬТЕРНАТИВА"]}},
    )["message"]
    old = save(service, sid, ids, "ПРЕЖНЯЯ ТАЙНА", extras={"private_note": "СЕКРЕТ РАСШИРЕНИЯ"})
    service.save(
        sid,
        chat.branch(sid)["id"],
        chat.branch(sid)["revision"],
        RecordInput(text="Общедоступная новая запись", knower_ids=ids, source_ids=[source["id"]]),
        record_id=old["id"],
        expected_version=1,
        reason="СЕКРЕТНОЕ ОБОСНОВАНИЕ",
    )
    snap = service.snapshot(sid, chat.branch(sid)["id"])
    found = MemoryIndex(service).search(snap, "Общ", ids[1], include_messages=True)
    output = json.dumps(found, ensure_ascii=False)
    assert (
        "ПРЕЖНЯЯ ТАЙНА" not in output
        and "СЕКРЕТНОЕ ОБОСНОВАНИЕ" not in output
        and "СКРЫТАЯ АЛЬТЕРНАТИВА" not in output
        and "СЕКРЕТ РАСШИРЕНИЯ" not in output
    )
    projected = next(row["record"] for row in found["results"] if row["kind"] == "record")
    assert projected["knower_ids"] == [ids[1]] and projected["projected"]
    assert "history" not in projected and "provenance" not in projected and "extras" not in projected
    assert service.timeline(sid, chat.branch(sid)["id"], ids[1])["anchors"] == {}
    assert "ПРЕЖНЯЯ ТАЙНА" in json.dumps(service.list(sid, chat.branch(sid)["id"]), ensure_ascii=False)


def test_pinned_summary_protects_sources_and_relationship_episode_scope(tmp_path):
    service, chat, sid, ids = setup_memory(tmp_path)
    first = append(chat, sid, "Значимый эпизод")
    episode = save(
        service, sid, ids, "Мира запомнила помощь", level="episodic", salience=0.1, source_ids=[first["id"]]
    )
    summary = save(
        service,
        sid,
        ids,
        "Закреплённая сводка",
        nature="summary",
        pinned=True,
        source_fact_versions={episode["id"]: 1},
    )
    relation = save(
        service,
        sid,
        ids,
        "Доверяет после помощи",
        level="relationship",
        pinned=True,
        relation={"parties": ids, "episode_ids": [episode["id"]], "metrics": {"доверие": 10}},
    )
    assert relation["source_fact_versions"] == {episode["id"]: 1}
    append(chat, sid, "Следующий эпизод")
    assert (
        service.archive(sid, chat.branch(sid)["id"], chat.branch(sid)["revision"], min_age_messages=1)[
            "archived_ids"
        ]
        == []
    )
    snap = service.snapshot(sid, chat.branch(sid)["id"])
    assert service.policy(snap).reason(snap["records"][summary["id"]], ids[0]) is None
    with pytest.raises(AppError, match="недоступный"):
        service.save(
            sid,
            chat.branch(sid)["id"],
            chat.branch(sid)["revision"],
            RecordInput(
                level="relationship",
                text="Орн не наблюдал эпизод",
                knower_ids=[ids[1]],
                relation={"parties": ids, "episode_ids": [episode["id"]]},
            ),
        )
    profile = chat.generation.profiles.resolve()
    profile["settings"]["budgets"]["relationships"] = 0
    with pytest.raises(AppError, match="ручной бюджет"):
        PromptBuilder().build(
            story=chat.story(sid),
            branch=chat.branch(sid),
            state=chat.state(sid),
            character_id=ids[0],
            history=list(snap["messages"].values()),
            resolved=profile,
        )


def test_dependency_graph_deeper_than_python_recursion_limit_and_cycle(tmp_path):
    from taverna.knowledge import MemoryRecord
    from taverna.storage import now

    service, chat, sid, ids = setup_memory(tmp_path)
    bid = chat.branch(sid)["id"]
    records = {}
    previous = None
    for i in range(1100):
        rid = str(uuid4())
        record = MemoryRecord(
            id=rid,
            story_id=sid,
            branch_id=bid,
            version=1,
            text=f"Узел {i}",
            knower_ids=[ids[0]],
            source_fact_versions={previous: 1} if previous else {},
            created_at=now(),
            updated_at=now(),
            provenance={"mode": "manual", "note": "Тест графа", "source_hashes": {}},
        ).model_dump(mode="json")
        records[rid] = record
        previous = rid
    policy = KnowledgePolicy(sid, bid, records, {})
    assert policy.reason(records[previous], ids[0]) is None
    assert len(policy.visible(ids[0])) == 1100
    first = next(iter(records))
    records[first]["source_fact_versions"] = {previous: 1}
    assert KnowledgePolicy(sid, bid, records, {}).reason(records[previous], ids[0])


@pytest.mark.asyncio
async def test_memory_service_profiles_budget_and_automatic_proposals(tmp_path):
    from taverna.profiles import BindingWrite, ProfileWrite

    service, chat, sid, ids = setup_memory(tmp_path)
    calls = []
    profiles = chat.generation.profiles
    profile = profiles.write(
        ProfileWrite(
            name="Память с малым окном",
            patch={
                "model_id": "loaded",
                "context_limit": 2048,
                "parameters": {"max_tokens": 128, "temperature": 0.25},
            },
            expected_revision=0,
        )
    )
    profiles.bind(
        BindingWrite(scope="service", scope_id="memory", profile_id=profile["id"], expected_revision=0)
    )
    # Two complete long sources; automatic selection may drop older sources, not invent a summary.
    append(chat, sid, "А" * 3000)
    last = append(chat, sid, "Мира увидела ключ")

    def transport(request):
        if request.method == "GET":
            return httpx.Response(
                200,
                json={
                    "models": [
                        {
                            "key": "key",
                            "type": "llm",
                            "loaded_instances": [{"id": "loaded", "config": {"context_length": 2048}}],
                        }
                    ]
                },
            )
        body = json.loads(request.content)
        calls.append(body)
        assert body["temperature"] == 0.25 and body["max_tokens"] == 128
        assert "А" * 3000 not in str(body["messages"])
        answer = json.dumps(
            {"facts": [{"text": "Известен ключ", "source_ids": [last["id"]]}]}, ensure_ascii=False
        )
        event = json.dumps({"choices": [{"delta": {"content": answer}}]}, ensure_ascii=False)
        return httpx.Response(200, content=f"data: {event}\n\ndata: [DONE]\n\n".encode())

    chat.generation.adapter = LmStudioAdapter(transport=httpx.MockTransport(transport))
    ai = MemoryAI(service)
    state = chat.state(sid)["payload"]
    state["memory_policy"] = {"auto_extract": True, "every_messages": 1, "auto_archive": False}
    chat.update_state(sid, state, chat.branch(sid)["revision"])
    queued = await ai.on_message(sid, chat.branch(sid)["id"], last["id"])
    assert queued["status"] == "queued" and len(queued["jobs"]) == 2
    for job in queued["jobs"]:
        assert (await terminal(chat.generation.queue, job["generation_id"]))["payload"][
            "state"
        ] == "completed"
    assert len(calls) == 2 and not chat.state(sid)["payload"]["memory"]
    proposals = chat.store.scan(f"memory/proposals/{sid}/")[0]
    assert all(row["value"]["payload"]["excluded_sources"] for row in proposals)
    await chat.generation.queue.close()


@pytest.mark.asyncio
async def test_explicit_vector_cache_reset_recomputes_same_model(tmp_path):
    service, chat, sid, ids = setup_memory(tmp_path)
    calls = []

    def transport(request):
        if request.method == "GET":
            return httpx.Response(
                200,
                json={
                    "models": [
                        {
                            "key": "same",
                            "type": "embedding",
                            "loaded_instances": [{"id": "embedding", "config": {}}],
                        }
                    ]
                },
            )
        calls.append(request)
        return httpx.Response(200, json={"data": [{"index": 0, "embedding": [1.0, 2.0]}]})

    chat.generation.adapter = LmStudioAdapter(transport=httpx.MockTransport(transport))
    cache = VectorCache(chat.store, chat.generation.adapter)
    _, before = await cache.vectors("embedding", ["текст"], asyncio.Event())
    search = MemorySearch(service)

    async def emit(kind, data):
        pass

    result = await search.rebuild(
        {
            "story_id": sid,
            "branch_id": chat.branch(sid)["id"],
            "expected_revision": chat.branch(sid)["revision"],
            "operation": "reset_vectors",
        },
        asyncio.Event(),
        emit,
    )
    _, after = await cache.vectors("embedding", ["текст"], asyncio.Event())
    assert result["invalidated"] and before != after and len(calls) == 2


@pytest.mark.asyncio
async def test_reserved_queue_space_keeps_background_from_blocking_dialog(tmp_path):
    from taverna.jobs import JobQueue

    queue = JobQueue(JsonStore(tmp_path), capacity=4)
    entered = asyncio.Event()
    release = asyncio.Event()

    async def blocking(payload, cancel, emit):
        entered.set()
        await release.wait()
        return {}

    async def done(payload, cancel, emit):
        return {}

    queue.handlers["dialog"] = blocking
    queue.handlers["memory_extract"] = done
    await queue.enqueue("dialog", {}, idempotency_key="block")
    await entered.wait()
    for n in range(3):
        await queue.enqueue("memory_extract", {}, priority=10, idempotency_key=f"background-{n}")
    with pytest.raises(AppError, match="Очередь заполнена"):
        await queue.enqueue("memory_extract", {}, priority=10, idempotency_key="overflow-bg")
    foreground = await queue.enqueue("dialog", {}, priority=0, idempotency_key="foreground")
    release.set()
    assert (await terminal(queue, foreground["id"]))["payload"]["state"] == "completed"
    await queue.close()
