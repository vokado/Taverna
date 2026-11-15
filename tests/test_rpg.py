import copy
from uuid import uuid4

import pytest
from taverna.core import AppError
from taverna.rpg import RpgService, rpg_prompt
from taverna.rpg_models import MODULES, RpgAction, RpgSettings
from taverna.rpg_random import SeededRandom
from taverna.worlds import WorldService
from test_chat import append, setup_chat


def setup_rpg(tmp_path, enabled=MODULES):
    chat, sid, ids = setup_chat(tmp_path)
    service = RpgService(WorldService(chat))
    settings = RpgSettings(
        enabled={m: True for m in enabled},
        items={"sword": {"name": "Меч", "weight_grams": 1500}},
        currencies={"gold": {"name": "Золото"}},
        tables={
            "find": {
                "name": "Находка",
                "outcomes": [
                    {
                        "name": "Кошелёк",
                        "text": "Найден кошелёк с золотом",
                        "weight": 2,
                        "rewards": [{"kind": "currency", "key": "gold", "amount": 3}],
                    }
                ],
            }
        },
    ).model_dump(mode="json")
    service.configure(
        sid, chat.branch(sid)["id"], chat.branch(sid)["revision"], settings, "Настройка", "replay-test-seed"
    )
    return service, chat, sid, ids


def apply(service, chat, sid, actor, kind, **kw):
    return service.apply(
        sid,
        RpgAction(
            branch_id=chat.branch(sid)["id"],
            expected_revision=chat.branch(sid)["revision"],
            event_id=kw.pop("event_id", str(uuid4())),
            actor_id=actor,
            kind=kind,
            reason="Проверка",
            confirmed=True,
            **kw,
        ),
    )


def test_disabled_by_default_and_no_prompt_or_state_side_effect(tmp_path):
    chat, sid, ids = setup_chat(tmp_path)
    service = RpgService(WorldService(chat))
    before = chat.state(sid)
    result = service.read(sid, chat.branch(sid)["id"])
    assert not result["rpg"]["settings"]["enabled"] and chat.state(sid) == before
    assert rpg_prompt(before, ids[0]) == "" and before["payload"]["rpg"] == {}
    with pytest.raises(AppError, match="включите"):
        apply(service, chat, sid, ids[0], "roll")
    assert chat.state(sid) == before


def test_inventory_currency_trade_atomic_no_negative_capacity(tmp_path):
    service, chat, sid, ids = setup_rpg(tmp_path)
    a, b = ids[:2]
    apply(service, chat, sid, a, "adjust_currency", key="gold", amount=10)
    apply(service, chat, sid, b, "adjust_item", key="sword", amount=2)
    apply(service, chat, sid, a, "trade", target_id=b, key="sword", currency="gold", quantity=1, price=4)
    sheets = service.read(sid, chat.branch(sid)["id"])["rpg"]["actors"]
    assert sheets[a]["inventory"]["sword"] == sheets[b]["inventory"]["sword"] == 1
    assert sheets[a]["balances"]["gold"] == 6 and sheets[b]["balances"]["gold"] == 4
    before = chat.state(sid)
    for params in [dict(quantity=2, price=4), dict(quantity=1, price=7)]:
        with pytest.raises(AppError):
            apply(service, chat, sid, a, "trade", target_id=b, key="sword", currency="gold", **params)
        assert chat.state(sid) == before
    with pytest.raises(AppError, match="грузоподъёмность"):
        apply(service, chat, sid, a, "set_capacity", amount=100)
    assert chat.state(sid) == before
    apply(service, chat, sid, a, "transfer_item", target_id=b, key="sword", quantity=1)
    apply(service, chat, sid, a, "transfer_currency", target_id=b, key="gold", quantity=6)
    sheets = service.read(sid, chat.branch(sid)["id"])["rpg"]["actors"]
    assert sheets[b]["inventory"]["sword"] == 2 and sheets[b]["balances"]["gold"] == 10
    settings = service.read(sid, chat.branch(sid)["id"])["rpg"]["settings"]
    settings["items"] = {}
    with pytest.raises(AppError, match="Нельзя удалить"):
        service.configure(sid, chat.branch(sid)["id"], chat.branch(sid)["revision"], settings, "Удаление")


def test_rng_fixed_vector_and_replay_across_counters():
    first = {"seed": "known-seed", "counter": 0}
    rng = SeededRandom(first)
    values = [rng.below(20) for _ in range(20)]
    assert values[:5] == [5, 1, 3, 15, 16]  # frozen sha256-counter-v1 protocol vector
    replay = SeededRandom({"seed": "known-seed", "counter": 0})
    assert values == [replay.below(20) for _ in range(20)]
    tail = SeededRandom({"seed": "known-seed", "counter": 10})
    assert values[10:] == [tail.below(20) for _ in range(10)]
    assert first["counter"] == 20


def test_skill_checks_dice_repeat_restart_and_stale_event(tmp_path):
    service, chat, sid, ids = setup_rpg(tmp_path)
    actor = ids[0]
    apply(service, chat, sid, actor, "set_attribute", key="strength", amount=3)
    apply(service, chat, sid, actor, "set_skill", key="climbing", amount=2)
    bid = chat.branch(sid)["id"]
    revision = chat.branch(sid)["revision"]
    event = str(uuid4())
    result = apply(
        service,
        chat,
        sid,
        actor,
        "check",
        event_id=event,
        attribute="strength",
        skill="climbing",
        difficulty=14,
    )
    receipt = result["receipt"]
    assert receipt["result"]["modifier"] == 5
    assert receipt["result"]["success"] == (receipt["result"]["total"] >= 14)
    replay = SeededRandom({"seed": receipt["seed"], "counter": receipt["counter_before"]})
    expected = replay.dice({"count": 1, "sides": 20, "modifier": 5})
    assert expected["rolls"] == receipt["result"]["rolls"] and expected["total"] == receipt["result"]["total"]
    again = service.apply(sid, RpgAction.model_validate(receipt["input"]))
    assert again["already_applied"] and chat.branch(sid)["revision"] == revision + 1
    with pytest.raises(AppError, match="другими параметрами"):
        apply(service, chat, sid, actor, "check", event_id=event, difficulty=99)
    with pytest.raises(AppError, match="Ветвь изменилась"):
        service.apply(
            sid,
            RpgAction(
                branch_id=bid,
                expected_revision=revision,
                event_id=uuid4(),
                actor_id=actor,
                kind="roll",
                confirmed=True,
                reason="Устарело",
            ),
        )
    restarted = RpgService(WorldService(type(chat)(type(chat.store)(tmp_path), chat.generation)))
    assert restarted.read(sid, bid)["rpg"]["counter"] == receipt["counter_after"]
    rolled = apply(service, chat, sid, actor, "roll", dice={"count": 3, "sides": 6, "modifier": -2})[
        "receipt"
    ]["result"]
    assert len(rolled["rolls"]) == 3 and all(1 <= x <= 6 for x in rolled["rolls"])
    assert rolled["total"] == sum(rolled["rolls"]) - 2


def test_module_disable_preserves_data_and_only_own_prompt(tmp_path):
    service, chat, sid, ids = setup_rpg(tmp_path)
    a, b = ids[:2]
    apply(service, chat, sid, a, "set_attribute", key="СекретнаяСилаА", amount=7)
    apply(service, chat, sid, b, "set_attribute", key="СекретнаяСилаБ", amount=9)
    state = chat.state(sid)
    assert "СекретнаяСилаА" in rpg_prompt(state, a) and "СекретнаяСилаБ" not in rpg_prompt(state, a)
    saved = copy.deepcopy(state["payload"]["rpg"]["actors"])
    settings = state["payload"]["rpg"]["settings"]
    settings["enabled"] = {}
    service.configure(
        sid, chat.branch(sid)["id"], chat.branch(sid)["revision"], settings, "Литературный режим"
    )
    assert rpg_prompt(chat.state(sid), a) == ""
    assert chat.state(sid)["payload"]["rpg"]["actors"] == saved
    append(chat, sid, "Продолжаем литературную историю")
    assert chat.messages(sid)["items"][-1]["text"] == "Продолжаем литературную историю"


def test_world_policy_presence_scope_flags_and_atomic_random_reward(tmp_path):
    service, chat, sid, ids = setup_rpg(tmp_path)
    actor = ids[0]
    before = chat.state(sid)
    with pytest.raises(AppError, match="отсутствует"):
        apply(service, chat, sid, str(uuid4()), "roll")
    assert chat.state(sid) == before
    settings = service.read(sid, chat.branch(sid)["id"])["rpg"]["settings"]
    settings["world_policy"]["required_flags"] = {"доступ": True}
    service.configure(sid, chat.branch(sid)["id"], chat.branch(sid)["revision"], settings, "Ограничение мира")
    before = chat.state(sid)
    with pytest.raises(AppError, match="флагов"):
        apply(service, chat, sid, actor, "random_event", key="find")
    assert chat.state(sid) == before
    settings["world_policy"]["required_flags"] = {}
    service.configure(sid, chat.branch(sid)["id"], chat.branch(sid)["revision"], settings, "Снять условие")
    event = apply(service, chat, sid, actor, "random_event", key="find")["receipt"]
    assert event["result"]["outcome"]["name"] == "Кошелёк"
    assert service.read(sid, chat.branch(sid)["id"])["rpg"]["actors"][actor]["balances"]["gold"] == 3
    assert "Найден кошелёк" in rpg_prompt(chat.state(sid), actor)
    assert "Найден кошелёк" not in rpg_prompt(chat.state(sid), ids[1])
    settings["enabled"]["economy"] = False
    service.configure(
        sid, chat.branch(sid)["id"], chat.branch(sid)["revision"], settings, "Отключить награды"
    )
    before = chat.state(sid)
    with pytest.raises(AppError):
        apply(service, chat, sid, actor, "random_event", key="find")
    assert chat.state(sid) == before  # RNG also unchanged


def test_conflict_turn_damage_end_and_dead_actor_rejection(tmp_path):
    service, chat, sid, ids = setup_rpg(tmp_path)
    a, b = ids[:2]
    # Negative defense guarantees hit without an unseeded probabilistic assertion.
    for actor in (a, b):
        apply(service, chat, sid, actor, "set_attribute", key="defense", amount=-100)
    result = apply(service, chat, sid, a, "start_conflict", participants=[a, b])["receipt"]["result"]
    cid, order = result["conflict_id"], result["order"]
    first, second = order
    before = chat.state(sid)
    with pytest.raises(AppError, match="ход"):
        apply(service, chat, sid, second, "attack", target_id=first, conflict_id=cid)
    assert chat.state(sid) == before
    result = apply(
        service,
        chat,
        sid,
        first,
        "attack",
        target_id=second,
        conflict_id=cid,
        dice={"count": 1, "sides": 2, "modifier": 20},
    )["receipt"]["result"]
    assert (
        result["health_lost"] == 10
        and result["damage"]["total"] >= 21
        and result["conflict"]["status"] == "ended"
    )
    assert service.read(sid, chat.branch(sid)["id"])["rpg"]["actors"][second]["health"] == 0
    with pytest.raises(AppError, match="нулевым"):
        apply(service, chat, sid, second, "check")
    apply(service, chat, sid, second, "set_health", amount=10)
    next_fight = apply(service, chat, sid, a, "start_conflict", participants=[a, b])["receipt"]["result"][
        "conflict_id"
    ]
    apply(service, chat, sid, a, "end_conflict", conflict_id=next_fight)
    assert chat.state(sid)["payload"]["rpg"]["conflicts"][next_fight]["status"] == "ended"


def test_fork_restores_rpg_inventory_rng_and_independent_operations(tmp_path):
    service, chat, sid, ids = setup_rpg(tmp_path)
    actor = ids[0]
    apply(service, chat, sid, actor, "adjust_item", key="sword", amount=1)
    first = append(chat, sid, "Первый эпизод")
    append(chat, sid, "Второй эпизод")
    apply(service, chat, sid, actor, "roll")
    apply(service, chat, sid, actor, "adjust_item", key="sword", amount=1)
    parent = chat.branch(sid)["id"]
    parent_state = chat.state(sid)
    child = chat.fork(sid, expected_revision=chat.branch(sid)["revision"], through_id=first["id"])
    bid = chat.branch(sid)["id"]
    assert bid != parent
    restored = service.read(sid, bid)["rpg"]
    assert restored["actors"][actor]["inventory"]["sword"] == 1 and restored["counter"] == 0
    apply(service, chat, sid, actor, "adjust_item", key="sword", amount=3)
    assert service.read(sid, bid)["rpg"]["actors"][actor]["inventory"]["sword"] == 4
    assert chat.store.get(chat.state_key(sid, parent_state["id"])) == parent_state
    assert child


@pytest.mark.parametrize(
    "module,kind",
    [
        ("inventory", "adjust_item"),
        ("stats", "set_attribute"),
        ("skills", "check"),
        ("dice", "roll"),
        ("economy", "adjust_currency"),
        ("conflicts", "start_conflict"),
        ("random_events", "random_event"),
    ],
)
def test_each_module_without_optional_prompt_dependencies(tmp_path, module, kind):
    service, chat, sid, ids = setup_rpg(tmp_path, [module])
    args = (
        {"key": "sword", "amount": 1}
        if module == "inventory"
        else {"key": "force", "amount": 1}
        if module == "stats"
        else {"key": "gold", "amount": 1}
        if module == "economy"
        else {"participants": ids[:2]}
        if module == "conflicts"
        else {}
    )
    if module == "random_events":
        settings = service.read(sid, chat.branch(sid)["id"])["rpg"]["settings"]
        settings["tables"]["find"]["outcomes"][0]["rewards"] = []
        service.configure(
            sid, chat.branch(sid)["id"], chat.branch(sid)["revision"], settings, "Нелутовое событие"
        )
        args = {"key": "find"}
    assert apply(service, chat, sid, ids[0], kind, **args)["receipt"]["kind"] == kind


def test_world_snapshot_restriction_and_offscene_policy_cannot_be_bypassed(tmp_path):
    service, chat, sid, ids = setup_rpg(tmp_path)
    data = copy.deepcopy(chat.state(sid)["payload"])
    data["world"]["template"] = {"payload": {"extras": {"rpg_policy": {"allowed_modules": ["dice"]}}}}
    chat.update_state(sid, data, chat.branch(sid)["revision"])
    before = chat.state(sid)
    with pytest.raises(AppError, match="запрещает"):
        apply(service, chat, sid, ids[0], "adjust_item", key="sword", amount=1)
    assert chat.state(sid) == before
    apply(service, chat, sid, ids[0], "roll")
    data = copy.deepcopy(chat.state(sid)["payload"])
    data["scene"]["participants"] = [ids[1]]
    data["scene"]["speaker_order"] = [ids[1]]
    chat.update_state(sid, data, chat.branch(sid)["revision"])
    with pytest.raises(AppError, match="присутствия"):
        apply(service, chat, sid, ids[0], "roll")


def test_rpg_api_draft_invalid_domain_cas_and_no_primary_changes(tmp_path):
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
        sid = client.post("/api/v1/stories", json={"name": "RPG", "character_ids": [actor]}).json()["id"]
        chat = app.state.chat
        bid = chat.branch(sid)["id"]
        base = f"/api/v1/stories/{sid}/rpg"
        snap = client.get(base, params={"branch_id": bid}).json()
        assert snap["player_id"] and not snap["rpg"]["settings"]["enabled"]
        settings = snap["rpg"]["settings"]
        settings["items"] = {"sword": {"name": "Меч", "weight_grams": -5}}
        body = {
            "expected_revision": 0,
            "template_id": None,
            "source_revision": snap["branch_revision"],
            "raw": "{не закончен",
            "payload": {"settings": settings, "seed": "", "reason": "Черновик"},
        }
        draft_base = base + f"/drafts/{bid}"
        draft_id = str(uuid4())
        before = chat.state(sid)
        response = client.put(draft_base + "/" + draft_id, json=body)
        assert response.status_code == 200, response.text
        assert client.put(draft_base + "/" + draft_id, json=body).status_code == 409
        assert client.post(draft_base + "/validate", json=settings).status_code == 422
        assert (
            client.put(
                base + "/settings",
                json={
                    "branch_id": bid,
                    "expected_revision": snap["branch_revision"],
                    "settings": settings,
                    "reason": "Ошибка",
                },
            ).status_code
            == 422
        )
        assert chat.state(sid) == before
        assert client.get(draft_base).json()["items"][0]["payload"]["raw"] == body["raw"]
        settings["items"]["sword"]["weight_grams"] = 1500
        settings["enabled"] = {"dice": True}
        saved = client.put(
            base + "/settings",
            json={
                "branch_id": bid,
                "expected_revision": snap["branch_revision"],
                "settings": settings,
                "reason": "Кубики",
                "seed": "api-seed",
            },
        )
        assert saved.status_code == 200
        rolled = client.post(
            base + "/actions",
            json={
                "branch_id": bid,
                "expected_revision": saved.json()["branch_revision"],
                "event_id": str(uuid4()),
                "actor_id": snap["player_id"],
                "kind": "roll",
                "confirmed": True,
                "reason": "Бросок",
                "dice": {"count": 2, "sides": 6},
            },
        )
        assert rolled.status_code == 200 and len(rolled.json()["receipt"]["result"]["rolls"]) == 2
        assert client.delete(draft_base + f"/{draft_id}?expected_revision=1").status_code == 200


def test_rpg_story_package_roundtrip_and_invalid_receipts_or_scope(tmp_path):
    from taverna.rpg import rpg_value
    from taverna.story_packages import StoryPackages

    service, chat, sid, ids = setup_rpg(tmp_path / "source")
    apply(service, chat, sid, ids[0], "random_event", key="find")
    apply(service, chat, sid, ids[0], "roll")
    original = chat.state(sid)["payload"]["rpg"]
    raw = StoryPackages(chat).export(sid)
    target, _, _ = setup_chat(tmp_path / "target")
    packages = StoryPackages(target)
    preview = packages.preview(raw)
    packages.apply(raw, True, preview["sha256"])
    loaded = RpgService(WorldService(target)).read(sid, target.branch(sid)["id"])["rpg"]
    assert loaded == original and target.store.integrity()["ok"]
    malformed = copy.deepcopy(chat.state(sid)["payload"])
    malformed["rpg"]["audit"][-1]["result"] = None
    with pytest.raises(AppError):
        rpg_value(malformed)
    malformed = copy.deepcopy(original)
    malformed["actors"][str(uuid4())] = next(iter(malformed["actors"].values()))
    with pytest.raises(AppError, match="неизвестному"):
        service.validate_sheets(malformed, set(ids))


def test_disabling_stats_and_conflicts_removes_health_gate_from_independent_inventory(tmp_path):
    service, chat, sid, ids = setup_rpg(tmp_path)
    actor = ids[0]
    apply(service, chat, sid, actor, "set_health", amount=0)
    settings = service.read(sid, chat.branch(sid)["id"])["rpg"]["settings"]
    settings["enabled"] = {"inventory": True}
    service.configure(sid, chat.branch(sid)["id"], chat.branch(sid)["revision"], settings, "Только инвентарь")
    apply(service, chat, sid, actor, "adjust_item", key="sword", amount=1)
    apply(service, chat, sid, actor, "transfer_item", target_id=ids[1], key="sword", quantity=1)
    assert service.read(sid, chat.branch(sid)["id"])["rpg"]["actors"][ids[1]]["inventory"]["sword"] == 1


def test_unrelated_fields_of_previous_ui_action_do_not_add_npc_or_knowledge(tmp_path):
    service, chat, sid, ids = setup_rpg(tmp_path)
    result = apply(
        service,
        chat,
        sid,
        ids[0],
        "roll",
        target_id=str(uuid4()),
        participants=[ids[1]],
        key="unused",
        attribute="not_used",
    )
    receipt = result["receipt"]
    assert receipt["input"]["target_id"] is None and receipt["input"]["participants"] == []
    assert set(chat.state(sid)["payload"]["rpg"]["actors"]) == {ids[0]}
    assert chat.state(sid)["payload"]["events"][-1]["knower_ids"] == [ids[0]]
