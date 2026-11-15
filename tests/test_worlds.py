import copy
from uuid import uuid4

import pytest
from taverna.core import AppError
from taverna.events import EventBus
from taverna.world_models import Calendar, Scenario, World, validate_condition
from taverna.worlds import WorldService
from test_chat import append, setup_chat


def setup_world(tmp_path):
    chat, sid, ids = setup_chat(tmp_path)
    service = WorldService(chat)
    port, forest, faction = map(str, (uuid4(), uuid4(), uuid4()))
    world = service.save(
        "worlds",
        {
            "name": "Архипелаг",
            "entities": [
                {"id": port, "name": "Гавань", "kind": "location"},
                {"id": forest, "name": "Лес", "kind": "location"},
                {"id": faction, "name": "Караван", "kind": "faction", "location_id": port},
            ],
            "resources": {"зерно": 10},
            "rules": [
                {
                    "name": "Караван достиг леса",
                    "condition": {"op": "gte", "path": "clock.minutes", "value": 60},
                    "effects": [
                        {"op": "move_entity", "entity_id": faction, "value": forest},
                        {"op": "change_resource", "key": "зерно", "value": -2},
                    ],
                }
            ],
        },
        0,
    )
    scenario = service.save(
        "scenarios",
        {
            "world_id": world["id"],
            "name": "Путешествие",
            "goals": ["Возможный путь, не обязательство"],
            "starts": [
                {"name": "Гавань", "location_id": port},
                {"name": "Лес", "location_id": forest, "minutes": 30},
            ],
        },
        0,
    )
    return service, chat, sid, ids, world, scenario, (port, forest, faction)


def attach(service, chat, sid, world, scenario=None, **kw):
    return service.attach(
        sid,
        chat.branch(sid)["id"],
        chat.branch(sid)["revision"],
        world["id"],
        scenario["id"] if scenario else None,
        confirmed=True,
        **kw,
    )


def apply(service, chat, sid, **kw):
    return service.apply(sid, chat.branch(sid)["id"], chat.branch(sid)["revision"], reason="Проверка", **kw)


def test_reusable_world_scenario_snapshot_isolation_random_seed_and_calendar(tmp_path):
    service, chat, sid, ids, world, scenario, places = setup_world(tmp_path)
    second = chat.create("Вторая история", ids)["id"]
    first_start = attach(service, chat, sid, world, scenario, seed=42)["start"]
    assert attach(service, chat, second, world, scenario, seed=42)["start"] == first_start
    data = copy.deepcopy(world["payload"])
    data["name"] = "Изменённый шаблон"
    service.save("worlds", data, world["revision"], world["id"])
    assert chat.state(sid)["payload"]["world"]["template"]["payload"]["name"] == "Архипелаг"
    apply(service, chat, sid, operation="time_advance", minutes=90)
    assert chat.state(second)["payload"]["world"]["clock"]["minutes"] == first_start["minutes"]
    assert service.read(sid, chat.branch(sid)["id"])["calendar"]["hour"] >= 1
    with pytest.raises(AppError, match="уже есть"):
        attach(service, chat, sid, world)
    calendar = Calendar(months=[{"name": "Снег", "days": 2}, {"name": "Дождь", "days": 3}], epoch_year=9)
    assert calendar.display(5 * 1440) == {
        "year": 10,
        "month": "Снег",
        "day": 1,
        "hour": 0,
        "minute": 0,
        "period": "ночь",
    }
    assert calendar.display(2 * 1440)["month"] == "Дождь"
    assert chat.store.integrity()["ok"]


def test_event_once_conflict_rollback_cooldown_and_offscene_action(tmp_path):
    service, chat, sid, _, world, _, (_, forest, faction) = setup_world(tmp_path)
    attach(service, chat, sid, world)
    bid, revision, eid = chat.branch(sid)["id"], chat.branch(sid)["revision"], str(uuid4())
    receipt = service.apply(
        sid, bid, revision, event_id=eid, operation="time_advance", minutes=60, reason="Путь каравана"
    )
    assert receipt["event"]["rules_fired"]
    state = chat.state(sid)["payload"]
    assert state["world"]["entities"][faction]["location_id"] == forest
    assert state["world"]["resources"]["зерно"] == 8
    again = service.apply(
        sid, bid, revision, event_id=eid, operation="time_advance", minutes=60, reason="Путь каравана"
    )
    assert again["already_applied"]
    assert len(chat.state(sid)["payload"]["events"]) == 1
    with pytest.raises(AppError, match="ID события"):
        service.apply(
            sid, bid, revision, event_id=eid, operation="time_advance", minutes=61, reason="Путь каравана"
        )
    apply(service, chat, sid, operation="time_advance", minutes=60)
    assert chat.state(sid)["payload"]["world"]["resources"]["зерно"] == 8
    before = chat.state(sid)
    with pytest.raises(AppError, match="Сущность"):
        apply(
            service,
            chat,
            sid,
            effects=[
                {"op": "change_resource", "key": "зерно", "value": 100},
                {"op": "set_attribute", "entity_id": str(uuid4()), "key": "опасность", "value": True},
            ],
        )
    assert chat.state(sid) == before
    with pytest.raises(AppError, match="Предыдущее"):
        apply(
            service,
            chat,
            sid,
            effects=[
                {"op": "change_resource", "key": "зерно", "value": -1, "check_expected": True, "expected": 99}
            ],
        )
    with pytest.raises(AppError, match="Причина"):
        apply(service, chat, sid, cause_ids=[str(uuid4())])
    assert chat.state(sid) == before


def test_world_fork_past_and_atomic_message_rules(tmp_path):
    service, chat, sid, _, world, _, _ = setup_world(tmp_path)
    payload = world["payload"]
    payload["rules"].append(
        {
            "name": "Первая реплика",
            "on": "message",
            "condition": {"op": "not", "arg": {"op": "exists", "path": "flags.first_message"}},
            "effects": [{"op": "set_flag", "key": "first_message", "value": True}],
        }
    )
    world = service.save("worlds", payload, 1, world["id"])
    attach(service, chat, sid, world)
    chat.state_reducer = service.message_state
    original = chat.branch(sid)["id"]
    first = append(chat, sid, "Начало")
    assert first["event_ids"] == [first["id"]]
    assert chat.state(sid)["payload"]["world"]["flags"]["first_message"] is True
    append(chat, sid, "Второй эпизод")
    apply(service, chat, sid, operation="time_advance", minutes=120)
    append(chat, sid, "Будущее")
    chat.fork(sid, expected_revision=chat.branch(sid)["revision"], through_id=first["id"])
    assert chat.state(sid)["payload"]["world"]["clock"]["minutes"] == 0
    assert chat.state(sid)["payload"]["world"]["flags"]["first_message"] is True
    assert len(chat.state(sid)["payload"]["events"]) == 1
    chat.switch(sid, original, chat.story(sid)["revision"])
    assert chat.state(sid)["payload"]["world"]["clock"]["minutes"] == 120


@pytest.mark.parametrize(
    "node",
    [
        {"op": "python", "code": "__import__('os')"},
        {"op": "eq", "path": "__class__.__mro__", "value": 1},
        {"op": "eq", "path": "flags.x", "value": {}},
        {"op": "all", "args": []},
        {"op": "exists", "path": "flags.x", "extra": 1},
    ],
)
def test_unsafe_rules_rejected(node):
    with pytest.raises(ValueError):
        validate_condition(node)


def test_world_invalid_references_cycles_and_deep_rules(tmp_path):
    service, _, _, _, world, _, _ = setup_world(tmp_path)
    data = world["payload"]
    data["entities"][0]["parent_id"] = data["entities"][1]["id"]
    data["entities"][1]["parent_id"] = data["entities"][0]["id"]
    with pytest.raises(ValueError):
        World.model_validate(data)
    node = {"op": "exists", "path": "flags.x"}
    for _ in range(20):
        node = {"op": "not", "arg": node}
    with pytest.raises(ValueError):
        validate_condition(node)
    with pytest.raises(AppError):
        service.save(
            "scenarios",
            {
                "world_id": world["id"],
                "name": "Ошибка",
                "starts": [{"name": "Неизвестное место", "location_id": str(uuid4())}],
            },
            0,
        )
    with pytest.raises(ValueError):
        Scenario(world_id=world["id"], name="Пустая цель", goals=[" "])


@pytest.mark.asyncio
async def test_one_event_bus_preserves_subscribers_and_reports_failure():
    bus = EventBus()
    calls = []

    async def memory(*args):
        calls.append("memory")
        return {"status": "queued"}

    async def broken(*args):
        raise RuntimeError("не печатать приватное содержимое")

    async def last(*args):
        calls.append("last")

    bus.subscribe("memory", memory)
    bus.subscribe("broken", broken)
    bus.subscribe("last", last)
    result = await bus("story", "branch", "message")
    assert calls == ["memory", "last"]
    assert result["memory"]["status"] == "queued"
    assert result["broken"]["code"] == "subscriber_failed"
    with pytest.raises(ValueError):
        bus.subscribe("memory", memory)


def test_lore_knowledge_before_triggers_and_sillytavern_preview(tmp_path):
    import json

    from taverna.world_lore import preview_lore, select_lore

    service, chat, sid, ids, world, _, _ = setup_world(tmp_path)
    payload = world["payload"]
    payload["lore"] = [
        {"name": "Секрет", "text": "Пароль — Комета", "keys": ["дверь"], "knower_ids": [ids[0]]},
        {"name": "Общее", "text": "Мост разрушен", "keys": ["мост"], "public": True},
        {"name": "Не рекурсивно", "text": "Скрытая цепочка", "keys": ["Комета"], "public": True},
    ]
    world = service.save("worlds", payload, world["revision"], world["id"])
    attach(service, chat, sid, world)
    runtime = chat.state(sid)["payload"]["world"]
    rows, excluded = select_lore(runtime, ids[1], "дверь и мост")
    assert [r[1] for r in rows] == ["Мост разрушен"]
    assert any(r["reason"] == "unknown_to_actor" for r in excluded)
    rows, _ = select_lore(runtime, ids[0], "дверь")
    assert [r[1] for r in rows] == ["Пароль — Комета"]
    source = {
        "entries": {
            "0": {"key": ["дверь"], "content": "Лор", "extensions": {"future": 42}},
            "1": {"key": [".*"], "content": "Регулярный", "useRegex": True},
        }
    }
    preview = preview_lore(json.dumps(source))
    assert not preview["entries"][0]["public"]
    assert preview["entries"][0]["extras"]["sillytavern_original"]["extensions"] == {"future": 42}
    assert not preview["entries"][1]["enabled"] and preview["warnings"]
    with pytest.raises(AppError):
        preview_lore('{"entries":[{"key":"не список","content":"x"}]}')


def test_world_package_roundtrip_idempotence_and_template_link_integrity(tmp_path):
    import json

    from taverna.world_packages import WorldPackages

    service, _, _, _, world, scenario, _ = setup_world(tmp_path)
    packages = WorldPackages(service)
    package = packages.export(world["id"])
    assert package["data"]["scenarios"][0]["id"] == scenario["id"]
    text = json.dumps(package)
    preview = packages.preview(text)
    assert preview["world"]["name"] == "Архипелаг"
    with pytest.raises(AppError, match="Подтвердите"):
        packages.apply(text, False)
    result = packages.apply(text, True)
    assert result["world_id"] != world["id"]
    assert packages.apply(text, True)["already_imported"]
    imported = service.get("scenarios", result["scenario_ids"][0])
    assert imported["payload"]["world_id"] == result["world_id"]
    assert len(service.list("worlds")["items"]) == 2
    data = copy.deepcopy(world["payload"])
    data["entities"] = []
    data["rules"] = []
    with pytest.raises(AppError, match="сценария"):
        service.save("worlds", data, 1, world["id"])
    package["data"]["world"]["payload"]["name"] = "Подмена"
    with pytest.raises(AppError):
        packages.preview(json.dumps(package))
    assert service.store.integrity()["ok"]


def test_world_api_confirmations_schemas_and_message_atomic_rollback(tmp_path):
    from fastapi.testclient import TestClient
    from taverna.core import Config
    from taverna.main import create_app

    service, chat, sid, _, world, _, _ = setup_world(tmp_path)
    app = create_app(Config(tmp_path))
    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
        token = app.state.sessions.bootstrap()
        csrf = client.post(
            "/api/v1/session/bootstrap", json={"token": token}, headers={"Origin": "http://127.0.0.1:8765"}
        ).json()["csrf"]
        client.headers.update({"X-Taverna-CSRF": csrf, "Origin": "http://127.0.0.1:8765"})
        assert client.get("/api/v1/templates/worlds").json()["items"][0]["id"] == world["id"]
        body = {
            "branch_id": chat.branch(sid)["id"],
            "expected_revision": chat.branch(sid)["revision"],
            "world_id": world["id"],
        }
        assert client.post(f"/api/v1/stories/{sid}/world/attach", json=body).status_code == 422
        assert (
            client.post(f"/api/v1/stories/{sid}/world/attach", json={**body, "confirmed": True}).status_code
            == 200
        )
        current = chat.branch(sid)
        event = {
            "branch_id": current["id"],
            "expected_revision": current["revision"],
            "event_id": str(uuid4()),
            "operation": "time_advance",
            "minutes": 60,
            "reason": "Проверка",
            "confirmed": True,
        }
        assert client.post(f"/api/v1/stories/{sid}/world/events", json=event).status_code == 200
        assert client.post(f"/api/v1/stories/{sid}/world/events", json=event).json()["already_applied"]
        assert (
            client.post(
                "/api/v1/templates/worlds/validate",
                json={"expected_revision": 0, "payload": {"name": "x", "resources": {"bad.key": 1}}},
            ).status_code
            == 422
        )
    # A failing deterministic consequence cannot publish either the message or part of its effects.
    data = chat.state(sid)["payload"]
    data["world"]["template"]["payload"]["rules"].append(
        {
            "id": str(uuid4()),
            "name": "Конфликт",
            "enabled": True,
            "on": "message",
            "once": True,
            "cooldown_minutes": 1,
            "condition": {"op": "exists", "path": "resources.зерно"},
            "effects": [
                {"op": "change_resource", "key": "зерно", "value": 1, "expected": 999, "check_expected": True}
            ],
        }
    )
    chat.update_state(sid, data, chat.branch(sid)["revision"])
    chat.state_reducer = service.message_state
    count, before = chat.messages(sid)["total"], chat.state(sid)
    with pytest.raises(AppError, match="Предыдущее"):
        append(chat, sid, "Не публиковать")
    assert chat.messages(sid)["total"] == count and chat.state(sid) == before


def test_catalog_cas_prevents_concurrent_new_scenario_breaking_world_references(tmp_path):
    service, _, _, _, world, _, _ = setup_world(tmp_path)
    # A scenario can be inserted after a writer inspected the collection. Catalog CAS covers that phantom.
    payload = copy.deepcopy(world["payload"])
    payload["name"] = "Новая редакция"
    _, changes, guards = service.plan_save("worlds", payload, world["revision"], world["id"])
    service.save("scenarios", {"world_id": world["id"], "name": "Конкурирующий сценарий"}, 0)
    with pytest.raises(AppError):
        service.store.transaction(changes, guards=guards)
    assert service.get("worlds", world["id"])["payload"]["name"] == "Архипелаг"


@pytest.mark.asyncio
async def test_world_assistant_actual_adapter_preview_confirm_and_stale_guard(tmp_path):
    import json

    import httpx
    from fastapi.testclient import TestClient
    from taverna.core import Config
    from taverna.llm import LmStudioAdapter
    from taverna.main import create_app
    from taverna.world_api import WorldAI
    from test_generation import terminal

    service, chat, _, _, world, _, _ = setup_world(tmp_path)
    mutate = False

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
        assert body["model"] == "loaded" and "схеме" in body["messages"][0]["content"]
        if mutate:
            owner = service.get("worlds", world["id"])
            owner["payload"]["description"] = "Ручное изменение владельца"
            service.save("worlds", owner["payload"], owner["revision"], owner["id"])
        text = json.dumps(
            {"name": "Предложенный мир", "description": "Проверяемый результат"}, ensure_ascii=False
        )
        chunk = json.dumps({"choices": [{"delta": {"content": text}}]}, ensure_ascii=False)
        return httpx.Response(200, content=f"data: {chunk}\n\ndata: [DONE]\n\n".encode())

    chat.generation.adapter = LmStudioAdapter(transport=httpx.MockTransport(transport))
    WorldAI(service)
    payload = {
        "kind": "worlds",
        "instruction": "Создай мир",
        "expected_revision": 0,
        "model_id": "loaded",
        "endpoint": chat.generation.adapter.endpoint,
    }
    job = await chat.generation.queue.enqueue(
        "world_assistant", payload, priority=10, idempotency_key="world-preview"
    )
    complete = await terminal(chat.generation.queue, job["id"])
    assert complete["payload"]["state"] == "completed", complete["payload"]
    proposal = complete["payload"]["result"]["proposal"]
    assert len(service.list("worlds")["items"]) == 1
    mutate = True
    stale = await chat.generation.queue.enqueue(
        "world_assistant",
        {**payload, "template_id": world["id"], "expected_revision": world["revision"]},
        priority=10,
        idempotency_key="world-stale",
    )
    failed = await terminal(chat.generation.queue, stale["id"])
    assert failed["payload"]["state"] == "failed"
    assert len(service.store.scan("worlds/proposals/")[0]) == 1
    await chat.generation.queue.close()
    app = create_app(Config(tmp_path))
    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
        token = app.state.sessions.bootstrap()
        csrf = client.post(
            "/api/v1/session/bootstrap", json={"token": token}, headers={"Origin": "http://127.0.0.1:8765"}
        ).json()["csrf"]
        client.headers.update({"X-Taverna-CSRF": csrf, "Origin": "http://127.0.0.1:8765"})
        path = f"/api/v1/world-proposals/{proposal['id']}/review"
        body = {
            "proposal_revision": proposal["revision"],
            "expected_revision": 0,
            "accept": True,
            "confirmed": True,
        }
        result = client.post(path, json=body)
        assert result.status_code == 200, result.text
        assert result.json()["proposal"]["payload"]["status"] == "accepted"
        assert client.post(path, json=body).json()["already_reviewed"]
        assert len(client.get("/api/v1/templates/worlds").json()["items"]) == 2


def test_repeat_rule_cooldown_absolute_clock_correction_and_branch_event_sources(tmp_path):
    service, chat, sid, _, world, _, _ = setup_world(tmp_path)
    data = world["payload"]
    data["rules"] = [
        {
            "name": "Поставка каждый час",
            "once": False,
            "cooldown_minutes": 60,
            "condition": {
                "op": "all",
                "args": [
                    {"op": "gte", "path": "clock.minutes", "value": 60},
                    {"op": "ne", "path": "flags.closed", "value": True},
                ],
            },
            "effects": [{"op": "change_resource", "key": "зерно", "value": 1}],
        }
    ]
    data["flags"] = {"closed": False}
    world = service.save("worlds", data, 1, world["id"])
    attach(service, chat, sid, world)
    apply(service, chat, sid, operation="time_advance", minutes=60)
    assert chat.state(sid)["payload"]["world"]["resources"]["зерно"] == 11
    apply(service, chat, sid, operation="time_advance", minutes=30)
    assert chat.state(sid)["payload"]["world"]["resources"]["зерно"] == 11
    apply(service, chat, sid, operation="time_advance", minutes=30)
    assert chat.state(sid)["payload"]["world"]["resources"]["зерно"] == 12
    apply(service, chat, sid, effects=[{"op": "set_clock", "value": 10}])
    assert chat.state(sid)["payload"]["world"]["clock"]["minutes"] == 10
    message = append(chat, sid, "Источники ветви")
    receipt = apply(
        service,
        chat,
        sid,
        effects=[{"op": "set_flag", "key": "ручной", "value": True}],
        source_ids=[message["id"]],
    )
    original_bid = chat.branch(sid)["id"]
    chat.fork(sid, expected_revision=chat.branch(sid)["revision"])
    event = chat.state(sid)["payload"]["world"]["applied_events"][receipt["event"]["id"]]
    assert event["branch_id"] == chat.branch(sid)["id"]
    assert event["origin"]["branch_id"] == original_bid
    assert event["source_ids"] == [chat.messages(sid)["items"][0]["id"]]


def test_template_draft_incomplete_json_and_shape_validation_cas(tmp_path):
    from fastapi.testclient import TestClient
    from taverna.core import Config
    from taverna.main import create_app

    app = create_app(Config(tmp_path))
    draft_id = str(uuid4())
    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
        token = app.state.sessions.bootstrap()
        csrf = client.post(
            "/api/v1/session/bootstrap", json={"token": token}, headers={"Origin": "http://127.0.0.1:8765"}
        ).json()["csrf"]
        client.headers.update({"X-Taverna-CSRF": csrf, "Origin": "http://127.0.0.1:8765"})
        path = f"/api/v1/template-drafts/worlds/{draft_id}"
        body = {
            "expected_revision": 0,
            "source_revision": 0,
            "payload": {"name": "", "entities": []},
            "raw": '{"неоконченный":',
        }
        saved = client.put(path, json=body)
        assert saved.status_code == 200, saved.text
        assert saved.json()["payload"]["raw"] == body["raw"]
        assert client.put(path, json=body).status_code == 409
        invalid = client.put(
            path, json={**body, "expected_revision": 1, "payload": {"name": "x", "entities": "bad"}}
        )
        assert invalid.status_code == 422
        assert client.get("/api/v1/templates/worlds").json()["items"] == []
        assert (
            client.get("/api/v1/template-drafts/worlds").json()["items"][0]["payload"]["raw"] == body["raw"]
        )
        assert client.delete(path + "?expected_revision=1").status_code == 200


def test_world_prompt_includes_public_opening_lore_but_not_objective_secrets_or_premise(tmp_path):
    from taverna.prompts import PromptBuilder

    service, chat, sid, ids, world, scenario, _ = setup_world(tmp_path)
    data = world["payload"]
    data["entities"][0]["description"] = "Тайный подземный ход под пристанью"
    data["lore"] = [
        {"name": "Известно всем", "text": "На пристани висит колокол", "constant": True, "public": True},
        {"name": "Секрет Миры", "text": "Пароль — Небесный камень", "constant": True, "knower_ids": [ids[0]]},
    ]
    world = service.save("worlds", data, world["revision"], world["id"])
    scenario["payload"]["premise"] = "Скрытый замысел: предательство смотрителя"
    scenario["payload"]["starts"][0]["text"] = "Открытая сцена у причала"
    scenario = service.save("scenarios", scenario["payload"], scenario["revision"], scenario["id"])
    attach(service, chat, sid, world, scenario, start_id=scenario["payload"]["starts"][0]["id"])
    append(chat, sid, "Осматриваю гавань")
    result = PromptBuilder().build(
        story=chat.story(sid),
        branch=chat.branch(sid),
        state=chat.state(sid),
        character_id=ids[1],
        history=chat._prefix(sid, chat.branch(sid)["id"]),
        resolved=chat.generation.profiles.resolve(story_id=sid, character_id=ids[1]),
    )
    text = str(result["messages"])
    assert "Открытая сцена у причала" in text and "На пристани висит колокол" in text
    assert "Небесный камень" not in text and "предательство" not in text and "подземный ход" not in text
    assert any(row["source"] == "lore" and row["reason"] == "unknown_to_actor" for row in result["excluded"])
