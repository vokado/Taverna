"""Actual shared queue, renderer and JSON transactions; no mocked tool runner."""

import copy
from uuid import uuid4

import pytest
from taverna.core import AppError, Config
from taverna.main import create_app
from taverna.tool_models import ToolEntry
from test_plugin_registry import configure
from test_plugin_service import proposal, setup
from test_plugin_service import run as queued_run

VALUE = "Альма {{hero}} <script>не выполнять</script>"


async def run(service, row):
    parameters = {"label": VALUE} if row["payload"]["manifest"]["runtime"] == "declarative" else {}
    return await queued_run(service, row, parameters=parameters)


def fixture(tmp_path, *, kind="note", runtime="declarative", missing=None, actions=None, tool_scopes=None):
    tid, bid, rid = (str(uuid4()) for _ in range(3))
    action = {
        "type": "tool.template",
        "target": tid,
        "expected_revision": 1,
        "result_id": rid,
        "inputs": {"hero": "{{label}}" if runtime == "declarative" else VALUE},
        **(
            {"manuscript_id": bid, "manuscript_revision": 1, "chapter_title": "Шаблонная глава"}
            if kind == "chapter"
            else {}
        ),
    }
    requests = [
        {"capability": "tools.run", "reason": "Точный шаблон", "scopes": tool_scopes or [tid]},
        {
            "capability": "notes.write" if kind == "note" else "books.write",
            "reason": "Результат",
            "scopes": ["new" if kind == "note" else bid],
        },
    ]
    if missing:
        requests = [r for r in requests if r["capability"] != missing]
    service, row = setup(
        tmp_path,
        {"actions": actions(action) if actions else [action]},
        runtime=runtime,
        requests=requests,
        fields=[{"key": "label", "label": "Текст", "type": "text", "required": True}]
        if runtime == "declarative"
        else [],
    )
    template = ToolEntry(
        kind="template",
        title="Избранный шаблон",
        template={
            "action": kind,
            "text": "Герой: {{hero}}",
            "variables": ["hero"],
            "grants": ["notes.write" if kind == "note" else "manuscripts.write"],
        },
    ).model_dump(mode="json")
    service.tools.save(tid, 0, template)
    if kind == "chapter":
        service.books.save(bid, 0, {"title": "Книга", "chapters": []}, "fixture")
    return service, row, tid, bid, rid


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["note", "chapter"])
@pytest.mark.parametrize("runtime", ["declarative", "wasm"])
async def test_template_render_preview_then_atomic_apply_and_restart_receipt(tmp_path, kind, runtime):
    service, row, tid, bid, rid = fixture(tmp_path, kind=kind, runtime=runtime)
    try:
        p = proposal(service, await run(service, row))
        rendered = p["payload"]["metadata"]["tool_previews"]
        assert len(rendered) == 1
        assert rendered[0]["text"] == "Герой: Альма {{hero}} <script>не выполнять</script>"
        assert rendered[0]["tool_id"] == tid and rendered[0]["result_id"] == rid
        assert not service.store.scan("manuscripts/tool-previews/")[0]
        assert service.store.get(service.tools.key(rid)) is None
        if kind == "chapter":
            assert service.books.get(bid)["payload"]["chapters"] == []
        service.decide(p["id"], 1, True)
        assert service.decide(p["id"], 1, True)["payload"]["status"] == "applied"
        reopened = create_app(Config(tmp_path)).state.plugins
        assert reopened.decide(p["id"], 1, True)["payload"]["status"] == "applied"
        if kind == "note":
            assert service.tools.get(rid)["revision"] == 1
            assert service.tools.get(rid)["payload"]["text"] == rendered[0]["text"]
        else:
            book = service.books.get(bid)
            assert book["revision"] == 2
            assert len(book["payload"]["chapters"]) == 1
            assert book["payload"]["chapters"][0]["id"] == rid
            assert len(service.books.history(bid)["items"]) == 2
        assert service.store.integrity()["ok"]
    finally:
        await service.queue.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kind,missing", [("note", "tools.run"), ("note", "notes.write"), ("chapter", "books.write")]
)
async def test_each_plugin_permission_required_without_publishing_tool_side_effects(tmp_path, kind, missing):
    service, row, _, bid, rid = fixture(tmp_path, kind=kind, missing=missing)
    try:
        job = await run(service, row)
        assert job["payload"]["state"] == "failed"
        assert job["payload"]["error"]["code"] == "plugin_scope_denied"
        assert not service.previews(row["id"])["items"]
        assert not service.store.scan("manuscripts/tool-previews/")[0]
        assert service.store.get(service.tools.key(rid)) is None
        if kind == "chapter":
            assert service.books.get(bid)["revision"] == 1
    finally:
        await service.queue.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["nested_revoke", "archive", "source", "destination", "plugin_revoke"])
async def test_revalidation_before_accept_never_applies_stale_template(tmp_path, change):
    service, row, tid, bid, rid = fixture(tmp_path, kind="chapter")
    try:
        p = proposal(service, await run(service, row))
        if change == "plugin_revoke":
            configure(service.registry, row, grants=[])
        elif change == "destination":
            book = service.books.get(bid)
            book["payload"]["title"] = "Изменено владельцем"
            service.books.save(bid, 1, book["payload"], "owner edit")
        else:
            tool = service.tools.get(tid)
            if change == "nested_revoke":
                tool["payload"]["template"]["grants"] = []
            elif change == "archive":
                tool["payload"]["archived"] = True
            else:
                tool["payload"]["template"]["text"] = "Новый текст"
            service.tools.save(tid, 1, tool["payload"])
        with pytest.raises(AppError):
            service.decide(p["id"], 1, True)
        assert service.books.get(bid)["payload"]["chapters"] == []
        assert service.store.get(service.proposal_key(p["id"]))["payload"]["status"] == "pending"
        assert service.decide(p["id"], 1, True, False)["payload"]["status"] == "rejected"
    finally:
        await service.queue.close()


@pytest.mark.asyncio
async def test_missing_template_nested_permission_blocks_preview(tmp_path):
    service, row, tid, _, _ = fixture(tmp_path)
    tool = service.tools.get(tid)
    tool["payload"]["template"]["grants"] = []
    service.tools.save(tid, 1, tool["payload"])
    # Ask for the actual current template revision to test grants, not stale CAS.
    try:
        from taverna.plugin_actions import PluginPlan

        plan = {
            "actions": [
                {
                    "type": "tool.template",
                    "target": tid,
                    "expected_revision": 2,
                    "result_id": str(uuid4()),
                    "inputs": {"hero": "Ночь"},
                }
            ]
        }
        with pytest.raises(AppError) as e:
            service.plan_effects(row, PluginPlan.model_validate(plan).model_dump(mode="json"))
        assert e.value.code == "permission_revoked"
        assert not service.store.scan("manuscripts/tool-previews/")[0]
    finally:
        await service.queue.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("order", ["tool_first", "note_first", "two_tools"])
async def test_duplicate_effect_rejected_across_template_and_direct_actions(tmp_path, order):
    def actions(action):
        direct = {
            "type": "note.save",
            "target": action["result_id"],
            "expected_revision": 0,
            "payload": {"kind": "note", "title": "Дубликат"},
        }
        if order == "two_tools":
            return [action, copy.deepcopy(action)]
        return [action, direct] if order == "tool_first" else [direct, action]

    service, row, _, _, rid = fixture(tmp_path, actions=actions)
    try:
        job = await run(service, row)
        assert job["payload"]["error"]["code"] == "plugin_duplicate_effect"
        assert service.store.get(service.tools.key(rid)) is None
    finally:
        await service.queue.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("scopes", [["*"], [str(uuid4())]])
async def test_tool_requires_exact_selected_scope_not_wildcard_or_another_tool(tmp_path, scopes):
    service, row, _, _, rid = fixture(tmp_path, tool_scopes=scopes)
    try:
        job = await run(service, row)
        assert job["payload"]["error"]["code"] == "plugin_scope_denied"
        assert not service.previews(row["id"])["items"]
        assert service.store.get(service.tools.key(rid)) is None
    finally:
        await service.queue.close()


@pytest.mark.asyncio
async def test_template_change_between_render_and_publication_is_guarded(tmp_path, monkeypatch):
    service, row, tid, _, rid = fixture(tmp_path)
    original = service.store.transaction
    raced = False

    def publish(changes, **kwargs):
        nonlocal raced
        if not raced and any(m.key.startswith("automations/plugin-previews/") for m in changes):
            raced = True
            tool = service.tools.get(tid)
            tool["payload"]["template"]["text"] = "Изменено одновременно"
            service.tools.save(tid, 1, tool["payload"])
        return original(changes, **kwargs)

    monkeypatch.setattr(service.store, "transaction", publish)
    try:
        job = await run(service, row)
        assert raced and job["payload"]["state"] == "failed"
        assert not service.previews(row["id"])["items"]
        assert service.store.get(service.tools.key(rid)) is None
    finally:
        await service.queue.close()
