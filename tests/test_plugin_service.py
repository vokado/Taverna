import asyncio
import json
from uuid import uuid4

import pytest
from taverna.core import AppError, Config
from taverna.main import create_app
from taverna.plugin_actions import PluginRun
from taverna.tool_models import ToolEntry
from test_plugin_compute import guest
from test_plugin_packages import package
from test_plugin_registry import configure, install


def setup(tmp_path, plan, *, runtime="declarative", fields=None, requests=None, code=None):
    app = create_app(Config(tmp_path))
    service = app.state.plugins
    requests = (
        requests
        if requests is not None
        else [
            {"capability": "own.write", "reason": "Настройки", "scopes": ["counter"]},
            {"capability": "notes.write", "reason": "Заметка", "scopes": ["new"]},
            {"capability": "books.write", "reason": "Рукопись", "scopes": ["new"]},
        ]
    )
    plan = {"format": 1, **plan}
    entry = code or (guest(plan) if runtime == "wasm" else json.dumps(plan, ensure_ascii=False).encode())
    raw = package(
        {
            "runtime": runtime,
            "entry": {"declarative": "main.json", "wasm": "main.wasm", "trusted_python": "main.py"}[runtime],
            "fields": fields or [],
            "requests": requests,
        },
        entry,
    )
    service.fixture_archive = raw
    row = install(service.registry, raw)
    row = configure(
        service.registry,
        row,
        grants=[{"capability": r["capability"], "scopes": r["scopes"]} for r in requests],
        trusted=runtime == "trusted_python",
    )
    return service, row


def request(row, catalog_revision=2, **extra):
    return PluginRun(
        catalog_revision=catalog_revision,
        plugin_revision=row["revision"],
        permission_epoch=row["payload"]["permission_epoch"],
        package_sha256=row["payload"]["package_sha256"],
        **extra,
    )


async def run(service, row, **extra):
    job = await service.enqueue(row["id"], request(row, **extra), str(uuid4()))
    async with asyncio.timeout(10):
        while (current := await service.queue.get(job["id"]))["payload"]["state"] in ("queued", "running"):
            await asyncio.sleep(0.01)
    return current


def proposal(service, job):
    assert job["payload"]["state"] == "completed", job["payload"]
    return service.store.get(service.proposal_key(job["payload"]["result"]["proposal_id"]))


@pytest.mark.asyncio
@pytest.mark.parametrize("runtime", ["declarative", "wasm"])
async def test_real_shared_queue_preview_atomic_own_note_book_and_repeated_accept(tmp_path, runtime):
    nid, bid = str(uuid4()), str(uuid4())
    plan = {
        "format": 1,
        "message": "Проверяемый план",
        "actions": [
            {"type": "own.set", "target": "counter", "expected_revision": 0, "value": {"count": 1}},
            {
                "type": "note.save",
                "target": nid,
                "expected_revision": 0,
                "payload": {"kind": "note", "title": "Дневник", "text": "Таверна"},
            },
            {
                "type": "book.save",
                "target": bid,
                "expected_revision": 0,
                "payload": {"title": "История", "chapters": [{"title": "Начало", "text": "Ночь"}]},
            },
        ],
    }
    service, row = setup(tmp_path, plan, runtime=runtime)
    try:
        before = service.store.integrity()
        assert before["ok"]
        p = proposal(service, await run(service, row))
        assert p["payload"]["status"] == "pending"
        assert service.store.get(service.tools.key(nid)) is None
        assert service.store.get(service.books.key(bid)) is None
        with pytest.raises(AppError):
            service.decide(p["id"], 1, False)
        result = service.decide(p["id"], 1, True)
        assert result["payload"]["status"] == "applied"
        assert service.tools.get(nid)["payload"]["text"] == "Таверна"
        assert service.books.get(bid)["revision"] == 1
        assert service.store.get(service.own_key(row["id"], "counter"))["payload"]["value"]["count"] == 1
        assert service.decide(p["id"], 1, True) == result
        assert len(service.books.history(bid)["items"]) == 1
        assert service.store.integrity()["ok"]
        # A fresh application reads the receipt instead of replaying effects.
        reopened = create_app(Config(tmp_path)).state.plugins
        assert reopened.decide(p["id"], 1, True)["payload"]["status"] == "applied"
    finally:
        await service.queue.close()


@pytest.mark.asyncio
async def test_current_rights_and_read_revision_rechecked_before_publication_and_accept(tmp_path):
    service, row = setup(
        tmp_path, {"actions": [{"type": "own.set", "target": "counter", "expected_revision": 0, "value": 3}]}
    )
    try:
        p = proposal(service, await run(service, row))
        configure(service.registry, row, grants=[])
        with pytest.raises(AppError) as e:
            service.decide(p["id"], 1, True)
        assert e.value.code in ("plugin_permission_revoked", "plugin_catalog_conflict")
        assert service.store.get(service.own_key(row["id"], "counter")) is None
        assert service.decide(p["id"], 1, True, False)["payload"]["status"] == "rejected"
        assert service.store.get(service.own_key(row["id"], "counter")) is None
    finally:
        await service.queue.close()


@pytest.mark.asyncio
async def test_inputs_explicit_scopes_no_templates_and_consistent_guards(tmp_path):
    nid = str(uuid4())
    requests = [{"capability": "notes.read", "reason": "Выбранная заметка", "scopes": [nid]}]
    service, row = setup(tmp_path, {"actions": [], "message": "Без изменений"}, requests=requests)
    note = service.tools.save(
        nid, 0, ToolEntry(kind="note", title="Источник", text="Первый").model_dump(mode="json")
    )
    try:
        body = request(row, reads=[{"kind": "note", "target": nid, "expected_revision": 1}])
        payload = {**body.model_dump(mode="json"), "plugin_id": row["id"]}
        _, data, guards = service.inputs(payload)
        assert data["data"][0]["value"]["text"] == "Первый"
        assert set(guards) == {
            service.registry.key(row["id"]),
            service.tools.key(nid),
            "config/plugin-catalog.json",
        }
        p = proposal(service, await run(service, row, reads=body.reads))
        note["payload"]["text"] = "Второй"
        service.tools.save(nid, 1, note["payload"])
        with pytest.raises(AppError):
            service.decide(p["id"], 1, True)
        job = await run(service, row, reads=body.reads)
        assert job["payload"]["error"]["code"] == "plugin_input_conflict"
        job = await run(
            service, row, reads=[{"kind": "note", "target": str(uuid4()), "expected_revision": 0}]
        )
        assert job["payload"]["error"]["code"] == "plugin_scope_denied"
        service.tools.save(
            nid,
            2,
            ToolEntry(
                kind="template", title="Секрет прав", template={"text": "x", "grants": ["notes.write"]}
            ).model_dump(mode="json"),
        )
        job = await run(service, row, reads=[{"kind": "note", "target": nid, "expected_revision": 3}])
        assert job["payload"]["error"]["code"] == "plugin_data_forbidden"
    finally:
        await service.queue.close()


@pytest.mark.asyncio
async def test_declarative_inputs_single_pass_types_and_idempotent_queue(tmp_path):
    service, row = setup(
        tmp_path,
        {"actions": [{"type": "own.set", "target": "counter", "expected_revision": 0, "value": "{{text}}"}]},
        fields=[{"key": "text", "label": "Текст", "required": True}],
    )
    try:
        for values in ({}, {"text": 3}, {"text": "ok", "extra": "x"}):
            with pytest.raises(AppError):
                await service.enqueue(row["id"], request(row, parameters=values), str(uuid4()))
        body = request(row, parameters={"text": "{{evil}}"})
        job = await service.enqueue(row["id"], body, "stable")
        duplicate = await service.enqueue(row["id"], body, "stable")
        assert duplicate["id"] == job["id"]
        async with asyncio.timeout(5):
            while (current := await service.queue.get(job["id"]))["payload"]["state"] in (
                "queued",
                "running",
            ):
                await asyncio.sleep(0.01)
        p = proposal(service, current)
        assert p["payload"]["plan"]["actions"][0]["value"] == "{{evil}}"
        with pytest.raises(AppError):
            await service.enqueue(row["id"], request(row, parameters={"text": "other"}), "stable")
    finally:
        await service.queue.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "actions,code",
    [
        ([{"type": "commands.exec", "target": "anything", "expected_revision": 0}], "plugin_plan_invalid"),
        (
            [{"type": "own.set", "target": "../other", "expected_revision": 0, "value": 1}],
            "plugin_plan_invalid",
        ),
        (
            [{"type": "own.set", "target": "private", "expected_revision": 0, "value": 1}],
            "plugin_scope_denied",
        ),
        (
            [
                {
                    "type": "note.save",
                    "target": str(uuid4()),
                    "expected_revision": 0,
                    "payload": {
                        "kind": "template",
                        "title": "Rights",
                        "template": {"grants": ["notes.write"]},
                    },
                }
            ],
            "plugin_plan_invalid",
        ),
        (
            [{"type": "own.set", "target": "counter", "expected_revision": 0, "value": 1}] * 2,
            "plugin_duplicate_effect",
        ),
    ],
)
async def test_invalid_or_ungranted_plans_never_publish_effects(tmp_path, actions, code):
    service, row = setup(tmp_path, {"actions": actions})
    try:
        job = await run(service, row)
        assert job["payload"]["state"] == "failed" and job["payload"]["error"]["code"] == code
        assert service.previews(row["id"])["items"] == []
        assert not service.store.scan("automations/plugin-data/")[0]
    finally:
        await service.queue.close()


@pytest.mark.asyncio
async def test_effect_cas_prevents_partial_publication(tmp_path):
    nid = str(uuid4())
    service, row = setup(
        tmp_path,
        {
            "actions": [
                {"type": "own.set", "target": "counter", "expected_revision": 0, "value": 1},
                {
                    "type": "note.save",
                    "target": nid,
                    "expected_revision": 0,
                    "payload": {"kind": "note", "title": "План"},
                },
            ]
        },
    )
    try:
        p = proposal(service, await run(service, row))
        service.tools.save(nid, 0, ToolEntry(kind="note", title="Чужое изменение").model_dump(mode="json"))
        with pytest.raises(AppError):
            service.decide(p["id"], 1, True)
        assert service.store.get(service.own_key(row["id"], "counter")) is None
        assert service.tools.get(nid)["payload"]["title"] == "Чужое изменение"
        assert service.store.get(service.proposal_key(p["id"]))["payload"]["status"] == "pending"
    finally:
        await service.queue.close()


@pytest.mark.asyncio
async def test_trusted_real_host_run_requires_per_run_ack_and_has_only_selected_input(tmp_path):
    source = b'def run(request):\n return {"actions":[],"message":str(sorted(request.keys()))}\n'
    service, row = setup(tmp_path, {}, runtime="trusted_python", code=source, requests=[])
    try:
        with pytest.raises(AppError):
            await service.enqueue(row["id"], request(row), str(uuid4()))
        p = proposal(service, await run(service, row, confirmed_full_access_run=True))
        assert p["payload"]["plan"]["message"] == "['data', 'format', 'parameters']"
        assert p["payload"]["metadata"]["isolation"] == "trusted-full-access-process"
    finally:
        await service.queue.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("phase,applied", [("prepared", False), ("committed", True)])
async def test_real_process_crash_effect_and_receipt_recover_together(tmp_path, phase, applied):
    import subprocess
    import sys

    nid = str(uuid4())
    service, row = setup(
        tmp_path,
        {
            "actions": [
                {"type": "own.set", "target": "counter", "expected_revision": 0, "value": 7},
                {
                    "type": "note.save",
                    "target": nid,
                    "expected_revision": 0,
                    "payload": {"kind": "note", "title": "Atomic"},
                },
            ]
        },
    )
    try:
        p = proposal(service, await run(service, row))
    finally:
        await service.queue.close()
    script = """
import os,sys
from pathlib import Path
from taverna.main import create_app
from taverna.core import Config
app=create_app(Config(Path(sys.argv[1])))
app.state.store.fault=lambda phase:os._exit(88) if phase==sys.argv[3] else None
app.state.plugins.decide(sys.argv[2],1,True)
"""
    result = await asyncio.to_thread(
        subprocess.run, [sys.executable, "-c", script, str(tmp_path), p["id"], phase], timeout=20
    )
    assert result.returncode == 88
    recovered = create_app(Config(tmp_path)).state.plugins
    receipt = recovered.store.get(recovered.proposal_key(p["id"]))
    assert (receipt["payload"]["status"] == "applied") == applied
    assert bool(recovered.store.get(recovered.tools.key(nid))) == applied
    assert bool(recovered.store.get(recovered.own_key(row["id"], "counter"))) == applied
    assert recovered.store.integrity()["ok"]
    recovered.decide(p["id"], 1, True)
    assert recovered.tools.get(nid)["revision"] == 1


@pytest.mark.asyncio
async def test_production_plugin_handler_revoked_by_real_http_api(tmp_path):
    import httpx

    marker = tmp_path / "entered-compute"
    source = (
        "from pathlib import Path\nimport time\ndef run(request):\n Path("
        + repr(str(marker))
        + ").write_text('entered')\n while True: time.sleep(.01)\n"
    ).encode()
    service, row = setup(tmp_path, {}, runtime="trusted_python", code=source, requests=[])
    app = create_app(Config(tmp_path))
    service = app.state.plugins
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:8765"
        ) as client:
            session = await client.post(
                "/api/v1/session/bootstrap", json={"token": app.state.sessions.bootstrap()}
            )
            headers = {"X-Taverna-CSRF": session.json()["csrf"], "Idempotency-Key": "production-revoke"}
            body = request(row, confirmed_full_access_run=True).model_dump(mode="json")
            assert (await client.post(f"/api/v1/plugins/{row['id']}/run", json=body)).status_code == 403
            response = await client.post(f"/api/v1/plugins/{row['id']}/run", json=body, headers=headers)
            assert response.status_code == 200
            job = response.json()
            async with asyncio.timeout(10):
                while not marker.exists():
                    await asyncio.sleep(0.01)
            response = await client.put(
                f"/api/v1/plugins/{row['id']}",
                headers=headers,
                json={
                    "expected_revision": row["revision"],
                    "catalog_revision": service.registry.list()["catalog_revision"],
                    "approved_sha256": row["payload"]["package_sha256"],
                    "enabled": False,
                    "grants": [],
                    "trusted_full_access_ack": False,
                    "confirmed": True,
                },
            )
            assert response.status_code == 200
            async with asyncio.timeout(10):
                while (current := await service.queue.get(job["id"]))["payload"]["state"] in (
                    "running",
                    "queued",
                ):
                    await asyncio.sleep(0.01)
            assert current["payload"]["state"] == "cancelled"
            assert service.previews(row["id"])["items"] == []
            assert marker.exists()  # Direct trusted Python effects are not reversible.
    finally:
        await service.queue.close()


@pytest.mark.asyncio
async def test_old_plan_cannot_survive_remove_reinstall_with_same_code_revision_and_epoch(tmp_path):
    service, row = setup(
        tmp_path, {"actions": [{"type": "own.set", "target": "counter", "expected_revision": 0, "value": 1}]}
    )
    raw = service.fixture_archive
    try:
        p = proposal(service, await run(service, row))
        service.registry.remove(row["id"], row["revision"], 2, row["payload"]["package_sha256"], True)
        replacement = install(service.registry, raw)
        replacement = configure(service.registry, replacement, grants=row["payload"]["grants"])
        assert replacement["revision"] == row["revision"]
        assert replacement["payload"]["permission_epoch"] == row["payload"]["permission_epoch"]
        assert replacement["payload"]["package_sha256"] == row["payload"]["package_sha256"]
        with pytest.raises(AppError) as error:
            service.decide(p["id"], 1, True)
        assert error.value.code == "plugin_catalog_conflict"
        assert service.store.get(service.own_key(row["id"], "counter")) is None
    finally:
        await service.queue.close()


@pytest.mark.asyncio
async def test_own_delete_keeps_monotonic_tombstone_and_old_plan_conflicts(tmp_path):
    service, row = setup(
        tmp_path,
        {
            "actions": [
                {"type": "own.set", "target": "counter", "expected_revision": 1, "value": "old intent"}
            ]
        },
    )

    def apply(action):
        mutations, guards = service.plan_effects(row, {"actions": [action]})
        service.store.transaction(mutations, guards=guards)

    apply({"type": "own.set", "target": "counter", "expected_revision": 0, "value": "initial"})
    try:
        p = proposal(service, await run(service, row))
        apply({"type": "own.delete", "target": "counter", "expected_revision": 1})
        tombstone = service.store.get(service.own_key(row["id"], "counter"))
        assert tombstone["revision"] == 2 and tombstone["payload"]["deleted"]
        apply({"type": "own.set", "target": "counter", "expected_revision": 2, "value": "new value"})
        with pytest.raises(AppError):
            service.decide(p["id"], 1, True)
        current = service.store.get(service.own_key(row["id"], "counter"))
        assert current["revision"] == 3 and current["payload"]["value"] == "new value"
    finally:
        await service.queue.close()


@pytest.mark.asyncio
async def test_declarative_expansion_budget_fails_before_unbounded_allocation(tmp_path):
    service, row = setup(
        tmp_path,
        {
            "actions": [
                {"type": "own.set", "target": "counter", "expected_revision": 0, "value": "{{text}}" * 40}
            ]
        },
        fields=[{"key": "text", "label": "Текст", "required": True}],
    )
    try:
        job = await run(service, row, parameters={"text": "я" * 16000})
        assert job["payload"]["state"] == "failed"
        assert job["payload"]["error"]["code"] == "plugin_output_limit"
        assert not service.previews(row["id"])["items"]
        p = proposal(service, await run(service, row, parameters={"text": "ok"}))
        assert p["payload"]["plan"]["actions"][0]["value"] == "ok" * 40
    finally:
        await service.queue.close()


def test_own_data_reader_checks_independent_scope_and_never_accepts_other_namespace(tmp_path):
    service, row = setup(
        tmp_path,
        {"actions": []},
        requests=[{"capability": "own.read", "reason": "Настройки", "scopes": ["counter"]}],
    )
    assert service.own_data(row["id"], "counter") == {
        "target": "counter",
        "revision": 0,
        "value": None,
        "deleted": False,
    }
    with pytest.raises(AppError):
        service.own_data(row["id"], "other")
    with pytest.raises(AppError):
        service.own_data(row["id"], "../counter")
    configure(service.registry, row, enabled=False)
    with pytest.raises(AppError):
        service.own_data(row["id"], "counter")
