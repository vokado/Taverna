import copy
from uuid import uuid4

import pytest
from taverna.core import AppError
from taverna.director import DirectorService
from taverna.director_models import DirectorAction
from taverna.memory import MemoryService
from taverna.worlds import WorldService
from test_chat import append, setup_chat


def setup_director(tmp_path):
    chat, sid, ids = setup_chat(tmp_path)
    memory = MemoryService(chat)
    service = DirectorService(memory, WorldService(chat))
    service.write_policy(
        sid, chat.branch(sid)["id"], chat.branch(sid)["revision"], {"enabled": True}, "Включён для проверки"
    )
    return service, chat, sid, ids


def proposal(service, chat, sid, actor, actions):
    return service.propose(service.snapshot(sid, chat.branch(sid)["id"]), actor, actions)


def accept(service, chat, sid, value, **extra):
    return service.review(
        sid,
        chat.branch(sid)["id"],
        value["id"],
        expected_revision=chat.branch(sid)["revision"],
        proposal_revision=value["revision"],
        accept=True,
        confirmed=True,
        **extra,
    )


def test_model_emotion_proposal_not_state_write_until_review_and_disable(tmp_path):
    service, chat, sid, ids = setup_director(tmp_path)
    message = append(chat, sid, "Герои нашли потерянную вещь")
    before = chat.state(sid)
    before_revision = chat.branch(sid)["revision"]
    value = proposal(
        service,
        chat,
        sid,
        ids[0],
        [
            {
                "kind": "emotion",
                "reason": "Найден важный предмет",
                "source_ids": [message["id"]],
                "emotion": {"label": "радость", "intensity": 0.7},
            }
        ],
    )
    assert value["payload"]["status"] == "pending" and chat.state(sid) == before
    result = accept(service, chat, sid, value)
    npc = service.read(sid, chat.branch(sid)["id"])["characters"][ids[0]]
    assert npc["emotion"]["label"] == "радость" and len(npc["history"]) == 1
    assert result["branch_revision"] == before_revision + 1 == chat.branch(sid)["revision"]
    assert accept(service, chat, sid, value)["already_reviewed"]
    service.write_npc(
        sid,
        chat.branch(sid)["id"],
        ids[0],
        chat.branch(sid)["revision"],
        {"emotions_enabled": False, "emotion": {"label": "спокойствие"}},
        "Ручной режим",
    )
    rejected = proposal(
        service,
        chat,
        sid,
        ids[0],
        [{"kind": "emotion", "reason": "Новая оценка", "source_card": True, "emotion": {"label": "гнев"}}],
    )
    assert (
        rejected["payload"]["status"] == "invalid"
        and rejected["payload"]["error"]["code"] == "emotions_disabled"
    )
    assert (
        service.read(sid, chat.branch(sid)["id"])["characters"][ids[0]]["emotion"]["label"] == "спокойствие"
    )


def test_character_modes_fixed_flexible_developing_and_atomic_reject(tmp_path):
    service, chat, sid, ids = setup_director(tmp_path)
    action = {
        "kind": "evolution",
        "trait": "courage",
        "delta": 0.1,
        "source_card": True,
        "reason": "Изменение после опыта",
    }
    value = proposal(service, chat, sid, ids[0], [action])
    assert (
        value["payload"]["status"] == "invalid" and value["payload"]["error"]["code"] == "npc_mode_conflict"
    )
    service.write_npc(
        sid,
        chat.branch(sid)["id"],
        ids[0],
        chat.branch(sid)["revision"],
        {"mode": "flexible"},
        "Разрешена адаптация",
    )
    value = proposal(service, chat, sid, ids[0], [{**action, "kind": "adaptation"}])
    accept(service, chat, sid, value)
    npc = service.read(sid, chat.branch(sid)["id"])["characters"][ids[0]]
    assert npc["adaptations"]["courage"] == 0.1 and not npc["traits"]
    service.write_npc(
        sid,
        chat.branch(sid)["id"],
        ids[0],
        chat.branch(sid)["revision"],
        {"mode": "developing"},
        "Разрешено развитие",
    )
    value = proposal(service, chat, sid, ids[0], [action])
    accept(service, chat, sid, value)
    assert service.read(sid, chat.branch(sid)["id"])["characters"][ids[0]]["traits"]["courage"] == 0.1
    before = chat.state(sid)
    invalid = proposal(
        service,
        chat,
        sid,
        ids[0],
        [
            {"kind": "emotion", "source_card": True, "reason": "Оценка", "emotion": {"label": "интерес"}},
            {**action, "kind": "adaptation"},
        ],
    )
    assert invalid["payload"]["status"] == "invalid" and chat.state(sid) == before
    with pytest.raises(ValueError):
        DirectorAction.model_validate({**action, "delta": 1})
    assert chat.store.integrity()["ok"]


def test_stale_proposals_sources_privacy_and_no_user_operations(tmp_path):
    service, chat, sid, ids = setup_director(tmp_path)
    visible = append(chat, sid, "Присутствуют оба NPC")
    value = proposal(
        service,
        chat,
        sid,
        ids[0],
        [
            {
                "kind": "initiative",
                "intent": "Предложить помощь",
                "reason": "Возможность развития",
                "source_ids": [visible["id"]],
            }
        ],
    )
    append(chat, sid, "Новый эпизод")
    with pytest.raises(AppError, match="старой ревизии"):
        accept(service, chat, sid, value)
    assert not service.read(sid, chat.branch(sid)["id"])["characters"][ids[0]]["intentions"]
    for kind in ("user_message", "user_action", "set_mode", "run_command"):
        with pytest.raises(ValueError):
            DirectorAction.model_validate({"kind": kind, "reason": "Недопустимо", "source_card": True})
    private = copy.deepcopy(visible)
    private["id"], private["present_ids"] = str(uuid4()), [ids[0]]
    snap = service.snapshot(sid, chat.branch(sid)["id"])
    snap["messages"][private["id"]] = private
    rejected = service.propose(
        snap,
        ids[1],
        [
            {
                "kind": "emotion",
                "reason": "Чужая тайна",
                "source_ids": [private["id"]],
                "emotion": {"label": "страх"},
            }
        ],
    )
    assert (
        rejected["payload"]["status"] == "invalid"
        and rejected["payload"]["error"]["code"] == "knowledge_scope"
    )


def test_empty_proposal_does_not_change_branch_and_auto_requires_product_policy(tmp_path):
    service, chat, sid, ids = setup_director(tmp_path)
    value = proposal(service, chat, sid, ids[0], [])
    revision = chat.branch(sid)["revision"]
    with pytest.raises(AppError, match="Автоматическое"):
        accept(service, chat, sid, value, automatic=True)
    result = accept(service, chat, sid, value)
    assert result["branch_revision"] == revision and chat.branch(sid)["revision"] == revision


@pytest.mark.asyncio
async def test_preparation_uses_real_adapter_namespace_and_never_mixes_plan_with_chat(tmp_path):
    import json

    import httpx
    from taverna.director_ai import DirectorAI
    from taverna.llm import LmStudioAdapter
    from test_generation import terminal

    service, chat, sid, ids = setup_director(tmp_path)
    append(chat, sid, "Здравствуйте")
    requests = []

    def transport(request):
        assert request.url.path in ("/api/v1/models", "/v1/chat/completions")
        if request.method == "GET":
            return httpx.Response(
                200,
                json={
                    "models": [
                        {
                            "type": "llm",
                            "key": "llm",
                            "loaded_instances": [{"id": "loaded", "config": {"context_length": 32768}}],
                        }
                    ]
                },
            )
        body = json.loads(request.content)
        requests.append(body)
        planning = '"actions"' in body["messages"][0]["content"]
        text = (
            json.dumps(
                {
                    "actions": [
                        {
                            "kind": "emotion",
                            "reason": "Приветствие",
                            "source_card": True,
                            "emotion": {"label": "интерес", "confidence": 0.6},
                        }
                    ],
                    "notes": "СЛУЖЕБНЫЙ ПЛАН НЕ ПРОЗА",
                },
                ensure_ascii=False,
            )
            if planning
            else "Мира: Здравствуйте, путник."
        )
        chunk = json.dumps({"choices": [{"delta": {"content": text}}]}, ensure_ascii=False)
        return httpx.Response(200, content=f"data: {chunk}\n\ndata: [DONE]\n\n".encode())

    chat.generation.adapter = LmStudioAdapter(transport=httpx.MockTransport(transport))
    DirectorAI(service)
    base = chat.branch(sid)["revision"]
    request = {
        "story_id": sid,
        "branch_id": chat.branch(sid)["id"],
        "expected_revision": base,
        "character_id": ids[0],
        "model_id": "loaded",
        "parameters": {},
        "endpoint": chat.generation.adapter.endpoint,
    }
    job = await chat.generation.queue.enqueue("chat", request, idempotency_key="director-pre-chat")
    result = await terminal(chat.generation.queue, job["id"])
    assert result["payload"]["state"] == "completed", result["payload"]
    assert result["payload"]["result"]["text"] == "Мира: Здравствуйте, путник."
    assert len(requests) == 2 and "СЛУЖЕБНЫЙ ПЛАН" not in str(requests[1]["messages"])
    assert chat.branch(sid)["revision"] == base + 1
    assert chat.messages(sid)["items"][-1]["text"] == result["payload"]["result"]["text"]
    rows, _ = chat.store.scan(f"events/director/{sid}/")
    assert rows[0]["value"]["payload"]["status"] == "pending"
    assert (
        service.read(sid, chat.branch(sid)["id"])["characters"][ids[0]]["emotion"]["label"] == "спокойствие"
    )
    events = []
    after = 0
    while after < result["payload"]["event_count"]:
        page = await chat.generation.queue.events(job["id"], after)
        events.extend(page)
        after += len(page)
    assert any(event["type"] == "generation.director_delta" for event in events)
    await chat.generation.queue.close()


@pytest.mark.asyncio
async def test_director_auto_apply_tracks_own_revision_and_timeout_preserves_dialog(tmp_path):
    import asyncio
    import json

    import httpx
    from taverna.director_ai import DirectorAI
    from taverna.llm import LmStudioAdapter
    from test_generation import terminal

    for timeout in (False, True):
        service, chat, sid, ids = setup_director(tmp_path / str(timeout))
        service.write_policy(
            sid,
            chat.branch(sid)["id"],
            chat.branch(sid)["revision"],
            {"enabled": True, "auto_apply": True, "pre_timeout_seconds": 1 if timeout else 15},
            "Настройка испытания",
        )
        append(chat, sid, "Начало разговора")

        async def transport(request, simulate_timeout=timeout):
            if request.method == "GET":
                return httpx.Response(
                    200,
                    json={
                        "models": [
                            {
                                "type": "llm",
                                "key": "llm",
                                "loaded_instances": [{"id": "loaded", "config": {"context_length": 32768}}],
                            }
                        ]
                    },
                )
            body = json.loads(request.content)
            planning = '"actions"' in body["messages"][0]["content"]
            if planning and simulate_timeout:
                await asyncio.sleep(10)
            text = (
                json.dumps(
                    {
                        "actions": [
                            {
                                "kind": "emotion",
                                "source_card": True,
                                "reason": "Оценка приветствия",
                                "emotion": {"label": "радость"},
                            }
                        ]
                    }
                )
                if planning
                else "Мира приветствует путника."
            )
            if not planning and not simulate_timeout:
                assert "радость" in str(body["messages"])
            chunk = json.dumps({"choices": [{"delta": {"content": text}}]})
            return httpx.Response(200, content=f"data: {chunk}\n\ndata: [DONE]\n\n".encode())

        chat.generation.adapter = LmStudioAdapter(transport=httpx.MockTransport(transport))
        DirectorAI(service)
        base = chat.branch(sid)["revision"]
        payload = {
            "story_id": sid,
            "branch_id": chat.branch(sid)["id"],
            "expected_revision": base,
            "character_id": ids[0],
            "model_id": "loaded",
            "parameters": {},
            "endpoint": chat.generation.adapter.endpoint,
        }
        queued = await chat.generation.queue.enqueue(
            "chat", payload, idempotency_key="auto-pre-" + str(timeout)
        )
        result = await terminal(chat.generation.queue, queued["id"])
        assert result["payload"]["state"] == "completed", result["payload"]
        assert result["payload"]["result"]["text"] == "Мира приветствует путника."
        assert chat.branch(sid)["revision"] == base + (1 if timeout else 2)
        assert chat.messages(sid)["total"] == 2
        await chat.generation.queue.close()


@pytest.mark.asyncio
async def test_offscene_context_does_not_expose_current_scene_or_other_actor(tmp_path):
    import httpx
    from taverna.director_ai import DirectorAI
    from taverna.llm import LmStudioAdapter

    service, chat, sid, ids = setup_director(tmp_path)
    data = chat.state(sid)["payload"]
    data["scene"].update(
        participants=[ids[0]],
        speaker_order=[ids[0]],
        name="СКРЫТАЯ КОМНАТА",
        opening="НЕИЗВЕСТНОЕ НАЧАЛО",
        location="СЕКРЕТНОЕ МЕСТО",
    )
    chat.update_state(sid, data, chat.branch(sid)["revision"])
    append(chat, sid, "ПАРОЛЬ ДЛЯ МИРЫ")
    chat.generation.adapter = LmStudioAdapter(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                json={
                    "models": [
                        {
                            "type": "llm",
                            "key": "llm",
                            "loaded_instances": [{"id": "loaded", "config": {"context_length": 32768}}],
                        }
                    ]
                },
            )
        )
    )
    ai = DirectorAI(service)
    snap = service.snapshot(sid, chat.branch(sid)["id"])
    result = await ai.context(
        snap, ids[1], service.policy(snap), "loaded", None, chat.generation.adapter.endpoint
    )
    text = str(result["messages"])
    assert all(
        secret not in text
        for secret in ("СКРЫТАЯ КОМНАТА", "НЕИЗВЕСТНОЕ НАЧАЛО", "СЕКРЕТНОЕ МЕСТО", "ПАРОЛЬ ДЛЯ МИРЫ")
    )
    assert ids[0] not in text and not result["source_selection"]["selected_message_ids"]


def test_derived_intention_and_arc_lose_access_after_source_fact_changes(tmp_path):
    from taverna.knowledge import RecordInput
    from taverna.npc_context import npc_prompt

    service, chat, sid, ids = setup_director(tmp_path)
    fact = service.memory.save(
        sid,
        chat.branch(sid)["id"],
        chat.branch(sid)["revision"],
        RecordInput(text="Пароль — Тихий Сокол", nature="secret", knower_ids=[ids[0]]),
    )["record"]
    actions = [
        {
            "kind": kind,
            "reason": "Известный факт",
            "intent": "Проверить тайну Тихого Сокола",
            "source_fact_versions": {fact["id"]: 1},
        }
        for kind in ("initiative", "arc")
    ]
    value = proposal(service, chat, sid, ids[0], actions)
    accept(service, chat, sid, value)
    snap = service.snapshot(sid, chat.branch(sid)["id"])
    assert "Тихого Сокола" in npc_prompt(snap["story"], snap["state"], ids[0], service.memory.policy(snap))
    assert "Тихого Сокола" not in npc_prompt(
        snap["story"], snap["state"], ids[1], service.memory.policy(snap)
    )
    intent_id = service.npc(snap, ids[0])["intentions"][0]["id"]
    service.memory.save(
        sid,
        chat.branch(sid)["id"],
        chat.branch(sid)["revision"],
        RecordInput(text=fact["text"], nature="secret", knower_ids=[ids[1]]),
        record_id=fact["id"],
        expected_version=1,
        reason="Пересмотр доступа",
    )
    snap = service.snapshot(sid, chat.branch(sid)["id"])
    assert "Тихого Сокола" not in npc_prompt(
        snap["story"], snap["state"], ids[0], service.memory.policy(snap)
    )
    with pytest.raises(AppError, match="доступные источники"):
        service.intention(snap, ids[0], intent_id)


def test_manual_rebase_requires_unchanged_sources_and_npc_and_fork_maps_provenance(tmp_path):
    service, chat, sid, ids = setup_director(tmp_path)
    first = append(chat, sid, "Просьба о помощи")
    value = proposal(
        service,
        chat,
        sid,
        ids[0],
        [
            {
                "kind": "initiative",
                "reason": "Возможность ответить",
                "intent": "Предложить помощь",
                "source_ids": [first["id"]],
            }
        ],
    )
    append(chat, sid, "Подтверждаю просьбу")
    accept(service, chat, sid, value, rebase=True)
    original = chat.branch(sid)["id"]
    chat.fork(sid, expected_revision=chat.branch(sid)["revision"])
    snap = service.snapshot(sid, chat.branch(sid)["id"])
    intention = service.npc(snap, ids[0])["intentions"][0]
    assert intention["source_ids"] == [chat.messages(sid)["items"][0]["id"]]
    assert set(intention["source_hashes"]) == set(intention["source_ids"])
    assert service.intention(snap, ids[0], intention["id"])
    assert snap["state"]["payload"]["director"]["audit"][-1]["origin"]["branch_id"] == original
    changed = proposal(
        service,
        chat,
        sid,
        ids[0],
        [{"kind": "emotion", "reason": "Суждение", "source_card": True, "emotion": {"label": "радость"}}],
    )
    service.write_npc(
        sid,
        chat.branch(sid)["id"],
        ids[0],
        chat.branch(sid)["revision"],
        {"emotion": {"label": "печаль"}},
        "Ручное исправление",
    )
    with pytest.raises(AppError, match="Состояние NPC изменилось"):
        accept(service, chat, sid, changed, rebase=True)


def test_world_action_binding_known_route_and_presence_reconciliation(tmp_path):
    service, chat, sid, ids = setup_director(tmp_path)
    harbor, forest, hidden, person = [str(uuid4()) for _ in range(4)]
    world = service.worlds.save(
        "worlds",
        {
            "name": "Путь NPC",
            "entities": [
                {"id": harbor, "kind": "location", "name": "Гавань"},
                {"id": forest, "kind": "location", "name": "Лес"},
                {"id": hidden, "kind": "location", "name": "Тайник"},
                {"id": person, "kind": "person", "name": "Мира мира", "location_id": harbor},
            ],
            "links": [{"source_id": harbor, "target_id": forest, "kind": "путь"}],
            "lore": [
                {
                    "name": "Карта",
                    "text": "Дорога ведёт в лес",
                    "public": True,
                    "constant": True,
                    "entity_ids": [harbor, forest],
                }
            ],
        },
        0,
    )
    service.worlds.attach(
        sid, chat.branch(sid)["id"], chat.branch(sid)["revision"], world["id"], confirmed=True
    )
    service.worlds.apply(
        sid,
        chat.branch(sid)["id"],
        chat.branch(sid)["revision"],
        operation="move",
        location_id=harbor,
        reason="Установить сцену",
    )
    service.write_policy(
        sid,
        chat.branch(sid)["id"],
        chat.branch(sid)["revision"],
        {"enabled": True, "actor_entities": {ids[0]: person}, "actor_attribute_keys": {ids[0]: ["activity"]}},
        "Явная связь NPC",
    )
    invalid = proposal(
        service,
        chat,
        sid,
        ids[0],
        [
            {
                "kind": "world_action",
                "reason": "Неизвестное место",
                "source_card": True,
                "effect": {"op": "move_entity", "entity_id": person, "value": hidden},
            }
        ],
    )
    assert (
        invalid["payload"]["status"] == "invalid" and invalid["payload"]["error"]["code"] == "knowledge_scope"
    )
    allowed = proposal(
        service,
        chat,
        sid,
        ids[0],
        [
            {
                "kind": "world_action",
                "reason": "Идёт по известной дороге",
                "source_card": True,
                "effect": {"op": "move_entity", "entity_id": person, "value": forest},
            }
        ],
    )
    accept(service, chat, sid, allowed)
    assert chat.state(sid)["payload"]["world"]["entities"][person]["location_id"] == forest
    assert ids[0] not in chat.branch(sid)["payload"]["participants"]
    assert ids[1] in chat.branch(sid)["payload"]["participants"]
    denied = proposal(
        service,
        chat,
        sid,
        ids[0],
        [
            {
                "kind": "world_action",
                "reason": "Неразрешённый атрибут",
                "source_card": True,
                "effect": {"op": "set_attribute", "entity_id": person, "key": "hidden", "value": True},
            }
        ],
    )
    assert (
        denied["payload"]["status"] == "invalid"
        and denied["payload"]["error"]["code"] == "actor_binding_required"
    )
    assert chat.store.integrity()["ok"]


@pytest.mark.asyncio
async def test_priority_cancel_during_prepared_review_does_not_publish_late_state(tmp_path):
    import asyncio
    import threading

    from test_generation import terminal

    service, chat, sid, ids = setup_director(tmp_path)
    value = proposal(
        service,
        chat,
        sid,
        ids[0],
        [{"kind": "emotion", "source_card": True, "reason": "Фоновая оценка", "emotion": {"label": "гнев"}}],
    )
    queue = chat.generation.queue
    entered, release = threading.Event(), threading.Event()
    checks = []

    async def background(payload, cancel, emit):
        def guard():
            checks.append(True)
            if len(checks) == 2:
                entered.set()
                if not release.wait(5):
                    raise RuntimeError("Не получен сигнал теста")
            chat.cancellation_guard(cancel)

        return await asyncio.to_thread(
            service.review,
            sid,
            chat.branch(sid)["id"],
            value["id"],
            expected_revision=chat.branch(sid)["revision"],
            proposal_revision=value["revision"],
            accept=True,
            confirmed=True,
            before_commit=guard,
        )

    async def foreground(payload, cancel, emit):
        return {
            "emotion": service.read(sid, chat.branch(sid)["id"])["characters"][ids[0]]["emotion"]["label"]
        }

    queue.handlers.update(director_gate=background, foreground_probe=foreground)
    queue.preemptible_kinds.add("director_gate")
    job = await queue.enqueue("director_gate", {}, priority=10, idempotency_key="gate")
    try:
        assert await asyncio.to_thread(entered.wait, 5)
        main = asyncio.create_task(
            queue.enqueue("foreground_probe", {}, priority=0, idempotency_key="foreground")
        )
        for _ in range(100):
            if queue.cancellations[job["id"]].is_set():
                break
            await asyncio.sleep(0.01)
        assert queue.cancellations[job["id"]].is_set()
    finally:
        release.set()
    primary = await terminal(queue, (await main)["id"])
    stopped = await terminal(queue, job["id"])
    assert stopped["payload"]["state"] == "cancelled"
    assert primary["payload"]["result"] == {"emotion": "спокойствие"}
    assert (
        service.store.get(service.proposal_key(sid, chat.branch(sid)["id"], value["id"]))["payload"]["status"]
        == "pending"
    )
    assert chat.store.integrity()["ok"]
    await queue.close()


@pytest.mark.asyncio
async def test_server_tracks_context_facts_when_model_omits_free_text_citations(tmp_path):
    import json

    import httpx
    from taverna.director_ai import DirectorAI
    from taverna.knowledge import RecordInput
    from taverna.llm import LmStudioAdapter
    from test_generation import terminal

    service, chat, sid, ids = setup_director(tmp_path)
    service.write_policy(
        sid,
        chat.branch(sid)["id"],
        chat.branch(sid)["revision"],
        {"enabled": True, "auto_apply": True},
        "Проверка зависимостей",
    )
    fact = service.memory.save(
        sid,
        chat.branch(sid)["id"],
        chat.branch(sid)["revision"],
        RecordInput(text="Тайник — Яшмовый остров", nature="secret", knower_ids=[ids[0]]),
    )["record"]

    def transport(request):
        if request.method == "GET":
            return httpx.Response(
                200,
                json={
                    "models": [
                        {
                            "type": "llm",
                            "key": "llm",
                            "loaded_instances": [{"id": "loaded", "config": {"context_length": 32768}}],
                        }
                    ]
                },
            )
        body = json.loads(request.content)
        assert "Яшмовый остров" in str(body["messages"])
        output = json.dumps(
            {
                "actions": [
                    {
                        "kind": "initiative",
                        "reason": "Предложение по контексту",
                        "intent": "Отправиться на Яшмовый остров",
                        "source_card": True,
                    }
                ]
            }
        )
        chunk = json.dumps({"choices": [{"delta": {"content": output}}]})
        return httpx.Response(200, content=f"data: {chunk}\n\ndata: [DONE]\n\n".encode())

    chat.generation.adapter = LmStudioAdapter(transport=httpx.MockTransport(transport))
    DirectorAI(service)
    payload = {
        "story_id": sid,
        "branch_id": chat.branch(sid)["id"],
        "expected_revision": chat.branch(sid)["revision"],
        "character_id": ids[0],
        "model_id": "loaded",
        "endpoint": chat.generation.adapter.endpoint,
    }
    job = await chat.generation.queue.enqueue(
        "director", payload, priority=10, idempotency_key="implicit-deps"
    )
    result = await terminal(chat.generation.queue, job["id"])
    assert result["payload"]["state"] == "completed", result["payload"]
    snap = service.snapshot(sid, chat.branch(sid)["id"])
    intent = service.npc(snap, ids[0])["intentions"][0]
    assert intent["source_fact_versions"] == {fact["id"]: 1}
    service.memory.save(
        sid,
        chat.branch(sid)["id"],
        chat.branch(sid)["revision"],
        RecordInput(text=fact["text"], nature="secret", knower_ids=[ids[1]]),
        record_id=fact["id"],
        expected_version=1,
        reason="Пересмотр доступа",
    )
    with pytest.raises(AppError):
        service.intention(service.snapshot(sid, chat.branch(sid)["id"]), ids[0], intent["id"])
    await chat.generation.queue.close()


@pytest.mark.asyncio
async def test_one_external_trigger_can_generate_only_one_npc_turn_without_loop(tmp_path):
    import json

    import httpx
    from taverna.director_ai import DirectorAI
    from taverna.llm import LmStudioAdapter
    from test_generation import terminal

    service, chat, sid, ids = setup_director(tmp_path)
    service.write_policy(
        sid,
        chat.branch(sid)["id"],
        chat.branch(sid)["revision"],
        {
            "enabled": True,
            "before_reply": False,
            "after_every_messages": 1,
            "auto_apply": True,
            "auto_initiative": True,
            "model_id": "loaded",
        },
        "Явное разрешение инициативы",
    )

    def transport(request):
        if request.method == "GET":
            return httpx.Response(
                200,
                json={
                    "models": [
                        {
                            "type": "llm",
                            "key": "llm",
                            "loaded_instances": [{"id": "loaded", "config": {"context_length": 32768}}],
                        }
                    ]
                },
            )
        body = json.loads(request.content)
        text = (
            json.dumps(
                {
                    "actions": [
                        {
                            "kind": "initiative",
                            "source_card": True,
                            "reason": "Предложить помощь",
                            "intent": "Спросить, нужна ли помощь",
                        }
                    ]
                }
            )
            if '"actions"' in body["messages"][0]["content"]
            else "Можно предложить вам помощь?"
        )
        chunk = json.dumps({"choices": [{"delta": {"content": text}}]})
        return httpx.Response(200, content=f"data: {chunk}\n\ndata: [DONE]\n\n".encode())

    chat.generation.adapter = LmStudioAdapter(transport=httpx.MockTransport(transport))
    ai = DirectorAI(service)
    message = append(chat, sid, "Осматриваю таверну")
    scheduled = await ai.on_message(sid, chat.branch(sid)["id"], message["id"])
    batch = await terminal(chat.generation.queue, scheduled["generation_id"])
    assert batch["payload"]["state"] == "completed", batch["payload"]
    turn_id = batch["payload"]["result"]["initiative_generation_id"]
    assert turn_id
    turn = await terminal(chat.generation.queue, turn_id)
    assert turn["payload"]["state"] == "completed", turn["payload"]
    messages = chat.messages(sid)["items"]
    assert len(messages) == 2 and [m["role"] for m in messages] == ["user", "assistant"]
    assert messages[-1]["author_id"] in ids and messages[-1]["generated_by"] == "director_initiative"
    assert messages[-1]["event_ids"]
    jobs, _ = chat.store.scan("jobs/records/")
    assert len(jobs) == 2
    npc = service.read(sid, chat.branch(sid)["id"])["characters"][messages[-1]["author_id"]]
    assert npc["intentions"][0]["status"] == "turn_generated"
    await chat.generation.queue.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("retry", [False, True])
async def test_user_role_marker_cannot_be_silently_saved_even_in_warning_mode(tmp_path, retry):
    import json

    import httpx
    from taverna.llm import LmStudioAdapter
    from taverna.profiles import BindingWrite, ProfileWrite
    from test_generation import terminal

    _, chat, sid, ids = setup_director(tmp_path)
    append(chat, sid, "Пишу от собственного имени")
    profile = chat.generation.profiles.write(
        ProfileWrite(
            expected_revision=0,
            name="Проверка ролей",
            patch={
                "quality": {"mode": "retry" if retry else "warn", "retry_on": ["role"], "max_attempts": 2}
            },
        )
    )
    chat.generation.profiles.bind(
        BindingWrite(expected_revision=0, scope="story", scope_id=sid, profile_id=profile["id"])
    )
    calls = []

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
        calls.append(request)
        text = (
            "Пользователь: Я принимаю решение за игрока." if len(calls) == 1 else "Мира ждёт вашего решения."
        )
        chunk = json.dumps({"choices": [{"delta": {"content": text}}]})
        return httpx.Response(200, content=f"data: {chunk}\n\ndata: [DONE]\n\n".encode())

    chat.generation.adapter = LmStudioAdapter(transport=httpx.MockTransport(transport))
    payload = {
        "story_id": sid,
        "branch_id": chat.branch(sid)["id"],
        "expected_revision": chat.branch(sid)["revision"],
        "character_id": ids[0],
        "model_id": "loaded",
        "parameters": {},
        "endpoint": chat.generation.adapter.endpoint,
    }
    queued = await chat.generation.queue.enqueue("chat", payload, idempotency_key="roles-" + str(retry))
    result = await terminal(chat.generation.queue, queued["id"])
    if retry:
        assert result["payload"]["state"] == "completed", result["payload"]
        assert len(calls) == 2 and chat.messages(sid)["items"][-1]["text"] == "Мира ждёт вашего решения."
    else:
        assert (
            result["payload"]["state"] == "failed"
            and result["payload"]["error"]["code"] == "user_impersonation"
        )
        assert len(calls) == 1 and chat.messages(sid)["total"] == 1
    await chat.generation.queue.close()


@pytest.mark.asyncio
async def test_preparation_deadline_cannot_abandon_committed_apply_receipt(tmp_path, monkeypatch):
    import asyncio
    import json
    import time

    import httpx
    from taverna.director_ai import DirectorAI
    from taverna.llm import LmStudioAdapter

    service, chat, sid, ids = setup_director(tmp_path)
    service.write_policy(
        sid,
        chat.branch(sid)["id"],
        chat.branch(sid)["revision"],
        {"enabled": True, "auto_apply": True, "pre_timeout_seconds": 5},
        "Ограничена модельная фаза",
    )

    def transport(request):
        if request.method == "GET":
            return httpx.Response(
                200,
                json={
                    "models": [
                        {
                            "type": "llm",
                            "key": "llm",
                            "loaded_instances": [{"id": "loaded", "config": {"context_length": 32768}}],
                        }
                    ]
                },
            )
        output = json.dumps(
            {
                "actions": [
                    {
                        "kind": "emotion",
                        "reason": "Подготовленный результат",
                        "source_card": True,
                        "emotion": {"label": "радость"},
                    }
                ]
            }
        )
        chunk = json.dumps({"choices": [{"delta": {"content": output}}]})
        return httpx.Response(200, content=f"data: {chunk}\n\ndata: [DONE]\n\n".encode())

    chat.generation.adapter = LmStudioAdapter(transport=httpx.MockTransport(transport))
    ai = DirectorAI(service)
    base = chat.branch(sid)["revision"]
    original = service.review
    reached_commit = False

    def slow_commit(*args, **kwargs):
        source_guard = kwargs["before_commit"]
        first = True

        def guard():
            nonlocal first, reached_commit
            if first:
                first, reached_commit = False, True
                time.sleep(5.1)  # Filesystem-delay injection exceeds the model deadline in the worker.
            source_guard()

        return original(*args, **{**kwargs, "before_commit": guard})

    monkeypatch.setattr(service, "review", slow_commit)
    events = []

    async def emit(kind, data):
        events.append(kind)

    result = await asyncio.wait_for(
        ai.prepare(
            {
                "story_id": sid,
                "branch_id": chat.branch(sid)["id"],
                "expected_revision": base,
                "character_id": ids[0],
                "model_id": "loaded",
            },
            asyncio.Event(),
            emit,
        ),
        timeout=20,
    )
    assert reached_commit and "director_skipped" not in events
    assert result["status"] == "accepted" and result["branch_revision"] == base + 1
    assert chat.branch(sid)["revision"] == result["branch_revision"]
    assert service.read(sid, chat.branch(sid)["id"])["characters"][ids[0]]["emotion"]["label"] == "радость"


def test_director_editor_drafts_keep_invalid_values_without_publishing(tmp_path):
    from fastapi.testclient import TestClient
    from taverna.cards import new_card
    from taverna.core import Config
    from taverna.main import create_app

    app = create_app(Config(tmp_path))
    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
        csrf = client.post(
            "/api/v1/session/bootstrap", json={"token": app.state.sessions.bootstrap()}
        ).json()["csrf"]
        client.headers.update({"X-Taverna-CSRF": csrf})
        actor = client.post(
            "/api/v1/characters", json={"expected_revision": 0, "character": new_card("Мира")}
        ).json()["id"]
        sid = client.post("/api/v1/stories", json={"name": "Режиссёр", "character_ids": [actor]}).json()["id"]
        chat = app.state.chat
        branch = chat.branch(sid)
        bid, revision = branch["id"], branch["revision"]
        original_state = chat.state(sid)
        base = f"/api/v1/stories/{sid}/director"
        snap = client.get(base, params={"branch_id": bid}).json()
        npc = {
            k: v
            for k, v in snap["characters"][actor].items()
            if k in ("mode", "emotion", "emotions_enabled", "relationships_enabled", "traits", "adaptations")
        }
        npc["emotion"]["intensity"] = 3
        policy = {**snap["policy"], "after_every_messages": 0}
        bundle = {
            "actor_id": actor,
            "npc": npc,
            "policy": policy,
            "reason": "Ещё не закончено",
            "dirty_npc": True,
            "dirty_policy": True,
            "dirty_raw": True,
        }
        draft_id = str(uuid4())
        draft_base = base + f"/drafts/{bid}"
        body = {
            "expected_revision": 0,
            "template_id": actor,
            "source_revision": revision,
            "raw": "{незавершённый JSON",
            "payload": bundle,
        }
        saved = client.put(draft_base + "/" + draft_id, json=body)
        assert saved.status_code == 200, saved.text
        assert saved.json()["payload"]["payload"]["npc"]["emotion"]["intensity"] == 3
        assert client.put(draft_base + "/" + draft_id, json=body).status_code == 409
        assert client.post(base + "/validate", json={"policy": policy, "npc": npc}).status_code == 422
        npc["emotion"]["intensity"] = 0.5
        npc["traits"] = {"courage": 2}
        assert client.post(base + "/validate", json={"policy": snap["policy"], "npc": npc}).status_code == 422
        npc["traits"] = {"courage": 0.5}
        assert client.post(base + "/validate", json={"policy": snap["policy"], "npc": npc}).status_code == 200
        assert client.get(draft_base).json()["items"][0]["payload"]["raw"] == body["raw"]
        wrong = {**body, "template_id": str(uuid4())}
        assert client.put(draft_base + "/" + str(uuid4()), json=wrong).status_code == 422
        assert chat.branch(sid)["revision"] == revision and chat.state(sid) == original_state
        endpoint = f"/api/v1/stories/{sid}/branches/{bid}/revision"
        assert client.get(endpoint, params={"known_revision": revision}).json() == {
            "revision": revision,
            "active_branch": None,
        }
        assert client.get(endpoint).json()["active_branch"] == bid
        assert client.delete(draft_base + f"/{draft_id}?expected_revision=0").status_code == 409
        assert client.delete(draft_base + f"/{draft_id}?expected_revision=1").status_code == 200
        assert not client.get(draft_base).json()["items"]


def test_owner_can_refuse_accepted_intention_and_arc_without_new_user_action(tmp_path):
    service, chat, sid, ids = setup_director(tmp_path)
    actor = ids[0]
    value = proposal(
        service,
        chat,
        sid,
        actor,
        [
            {
                "kind": "initiative",
                "source_card": True,
                "intent": "Предложить путь через порт",
                "reason": "Возможность, не приказ",
            },
            {
                "kind": "arc",
                "source_card": True,
                "arc_id": str(uuid4()),
                "intent": "Поиск корабля",
                "reason": "Свободная линия",
            },
        ],
    )
    assert value["payload"]["status"] == "pending", value
    accept(service, chat, sid, value)
    bid = chat.branch(sid)["id"]
    snap = service.read(sid, bid)
    intent = snap["characters"][actor]["intentions"][0]
    arc = next(iter(snap["director"]["arcs"].values()))
    old_messages = chat.messages(sid)
    reason = "Игрок отказался от порта и выбрал другой путь"
    service.dismiss(sid, bid, chat.branch(sid)["revision"], actor, intent["id"], "intention", reason)
    service.dismiss(sid, bid, chat.branch(sid)["revision"], actor, arc["id"], "arc", reason)
    after = service.read(sid, bid)
    assert after["characters"][actor]["intentions"][0]["status"] == "dismissed"
    assert after["director"]["arcs"][arc["id"]]["resolution"] == reason
    assert chat.messages(sid) == old_messages
    with pytest.raises(AppError, match="Намерение"):
        service.intention(service.snapshot(sid, bid), actor, intent["id"])
    with pytest.raises(AppError):
        service.dismiss(sid, bid, chat.branch(sid)["revision"], actor, arc["id"], "arc", reason)
    from taverna.npc_context import npc_prompt

    snap = service.snapshot(sid, bid)
    text = npc_prompt(snap["story"], snap["state"], actor, service.memory.policy(snap))
    assert intent["text"] not in text and arc["description"] not in text
