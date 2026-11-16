"""Real JSON-backed automation rules, plugin queue and recovery semantics."""

import asyncio
import json
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest
from taverna.automations import RuleInput, RuleToggle
from taverna.core import AppError, Config
from taverna.main import create_app
from test_plugin_packages import package
from test_plugin_registry import configure, install


def setup(tmp_path, *, trigger="interval", max_runs=2):
    app = create_app(Config(tmp_path))
    p = {
        "format": 1,
        "message": "Готовая запись из макроса",
        "actions": [
            {"type": "own.set", "target": "memo", "expected_revision": 0, "value": {"text": "Привет"}},
        ],
    }
    archive = package(
        {
            "runtime": "declarative",
            "entry": "main.json",
            "requests": [
                {"capability": "own.write", "reason": "Точная запись", "scopes": ["memo"]}
            ],
        },
        json.dumps(p, ensure_ascii=False).encode("utf-8"),
    )
    row = install(app.state.plugin_registry, archive)
    row = configure(
        app.state.plugin_registry,
        row,
        grants=[{"capability": "own.write", "scopes": ["memo"]}],
    )
    body = RuleInput(
        name="План по расписанию",
        plugin_id=UUID(row["id"]),
        trigger=trigger,
        interval_minutes=15 if trigger == "interval" else None,
        max_runs=max_runs,
        confirmed=True,
    )
    rule = app.state.automations.create(body)
    return app, row, rule


async def finished(app, event):
    async with asyncio.timeout(12):
        while True:
            item = await app.state.generation.queue.get(event["payload"]["job_id"])
            if item["payload"]["state"] not in ("queued", "running"):
                return item
            await asyncio.sleep(0.02)


@pytest.mark.asyncio
async def test_interval_proposes_plan_no_implicit_writes_and_review_blocks_next(tmp_path):
    app, plugin, rule = setup(tmp_path)
    svc = app.state.automations
    due = datetime.fromisoformat(rule["payload"]["next_due_at"]) + timedelta(seconds=1)
    try:
        await svc.tick(due)
        items = svc.history(rule["id"])["items"]
        assert len(items) == 1
        event = items[0]
        job = await finished(app, event)
        assert job["payload"]["state"] == "completed"
        proposal = app.state.plugins.proposal_key(job["payload"]["result"]["proposal_id"])
        preview = app.state.store.get(proposal)
        assert preview["payload"]["status"] == "pending"
        assert app.state.store.get(app.state.plugins.own_key(plugin["id"], "memo")) is None

        await svc.tick(due + timedelta(days=1))
        assert len(svc.history(rule["id"])["items"]) == 1
        assert svc.get(rule["id"])["payload"]["runs_started"] == 1
        app.state.plugins.decide(preview["id"], preview["revision"], True)
        assert app.state.store.get(app.state.plugins.own_key(plugin["id"], "memo")) is not None

        # A second scheduled execution is possible only after owner review.
        await svc.tick(due + timedelta(days=1))
        assert len(svc.history(rule["id"])["items"]) == 2
        assert svc.get(rule["id"])["payload"]["runs_started"] == 2
        await svc.tick(due + timedelta(days=3))
        assert len(svc.history(rule["id"])["items"]) == 2
    finally:
        await app.state.generation.queue.close()


@pytest.mark.asyncio
async def test_manual_macro_has_durable_idempotency_and_review(tmp_path):
    app, plugin, rule = setup(tmp_path, trigger="manual")
    svc = app.state.automations
    try:
        with pytest.raises(AppError):
            await svc.fire(rule["id"], confirmed=False, token="request-1")
        first = await svc.fire(rule["id"], confirmed=True, token="request-1")
        again = await svc.fire(rule["id"], confirmed=True, token="request-1")
        assert first["id"] == again["id"]
        assert first["payload"]["job_id"] == again["payload"]["job_id"]
        assert svc.get(rule["id"])["payload"]["runs_started"] == 1
        job = await finished(app, first)
        assert job["payload"]["state"] == "completed"
        assert app.state.store.get(app.state.plugins.own_key(plugin["id"], "memo")) is None
        preview = app.state.store.get(
            app.state.plugins.proposal_key(job["payload"]["result"]["proposal_id"])
        )
        assert preview["payload"]["status"] == "pending"
        app.state.plugins.decide(preview["id"], 1, True)
        assert app.state.store.get(app.state.plugins.own_key(plugin["id"], "memo"))["revision"] == 1

        reopened = create_app(Config(tmp_path))
        recovered = await reopened.state.automations.fire(
            rule["id"], confirmed=True, token="request-1"
        )
        assert recovered["id"] == first["id"]
        assert reopened.state.automations.get(rule["id"])["payload"]["runs_started"] == 1
    finally:
        await app.state.generation.queue.close()


@pytest.mark.asyncio
async def test_revocation_or_disable_blocks_new_rule_attempts(tmp_path):
    app, plugin, rule = setup(tmp_path)
    svc = app.state.automations
    try:
        old = svc.toggle(rule["id"], RuleToggle(expected_revision=rule["revision"], enabled=False, confirmed=True))
        assert not old["payload"]["enabled"]
        with pytest.raises(AppError):
            await svc.fire(rule["id"], confirmed=True, token="disabled")
        assert not svc.history(rule["id"])["items"]

        old = svc.toggle(rule["id"], RuleToggle(expected_revision=old["revision"], enabled=True, confirmed=True))
        app.state.plugin_registry.configure(
            plugin["id"], plugin["revision"],
            app.state.plugin_registry.list()["catalog_revision"],
            False, [], False, plugin["payload"]["package_sha256"], True,
        )
        with pytest.raises(AppError):
            await svc.fire(rule["id"], confirmed=True, token="revoked")
        assert svc.get(rule["id"])["payload"]["runs_started"] == 0
    finally:
        await app.state.generation.queue.close()


@pytest.mark.asyncio
async def test_recover_reservation_without_duplicate_effects(tmp_path):
    app, plugin, rule = setup(tmp_path, trigger="manual")
    svc = app.state.automations
    when = datetime.now(timezone.utc)
    try:
        event = svc._reserve(rule, when, "manual", uuid4())
        assert event["payload"]["state"] == "reserved"
        await svc.recover()
        done = svc.history(rule["id"])["items"][0]
        assert done["id"] == event["id"]
        assert done["payload"]["job_id"]
        job = await finished(app, done)
        assert job["payload"]["state"] == "completed"
        await svc.recover()
        assert len(svc.history(rule["id"])["items"]) == 1
        assert app.state.store.get(app.state.plugins.own_key(plugin["id"], "memo")) is None
    finally:
        await app.state.generation.queue.close()


def test_trusted_python_never_registered_as_background_automation(tmp_path):
    app = create_app(Config(tmp_path))
    raw = package(
        {
            "runtime": "trusted_python", "entry": "main.py",
            "requests": [],
        },
        b'def run(req): return {"format":1,"actions":[]}',
    )
    row = install(app.state.plugin_registry, raw)
    row = configure(app.state.plugin_registry, row, trusted=True)
    with pytest.raises(AppError, match="Фоновый запуск Python"):
        app.state.automations.create(
            RuleInput(name="Небезопасный макрос", plugin_id=UUID(row["id"]), confirmed=True)
        )
    assert app.state.automations.list()["items"] == []
