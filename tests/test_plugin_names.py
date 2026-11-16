"""Actual generator, queue, preview and atomic JSON receipts."""

from uuid import uuid4

import pytest
from taverna.core import AppError, Config
from taverna.main import create_app
from taverna.tool_models import NameRun, ToolEntry
from test_plugin_registry import configure
from test_plugin_service import proposal, run, setup


def fixture(tmp_path, runtime="declarative", duplicate=False):
    tid, rid = str(uuid4()), str(uuid4())
    action = {
        "type": "tool.names",
        "target": tid,
        "expected_revision": 1,
        "seed": "Северный порт",
        "count": 10,
        "result_id": rid,
    }
    service, row = setup(
        tmp_path,
        {"actions": [action, action] if duplicate else [action]},
        runtime=runtime,
        requests=[{"capability": "tools.run", "reason": "Генератор", "scopes": [tid]}],
    )
    service.tools.save(
        tid,
        0,
        ToolEntry(
            kind="name_generator",
            title="Имена порта",
            generator={"prefixes": ["Аль", "Мир"], "middles": ["а", "о"], "suffixes": ["на", "дар"]},
        ).model_dump(mode="json"),
    )
    return service, row, tid, rid


@pytest.mark.asyncio
@pytest.mark.parametrize("runtime", ["declarative", "wasm"])
async def test_names_preview_atomic_receipt_matches_owner_generator_without_other_write_grants(
    tmp_path, runtime
):
    service, row, tid, rid = fixture(tmp_path, runtime)
    try:
        p = proposal(service, await run(service, row))
        shown = p["payload"]["metadata"]["tool_previews"][0]
        assert shown["action"] == "names" and shown["result_id"] == rid
        assert len(shown["text"].splitlines()) == 10
        assert not service.store.scan("manuscripts/tool-runs/")[0]
        assert service.tools.get(tid)["revision"] == 1
        service.decide(p["id"], 1, True)
        assert (
            create_app(Config(tmp_path)).state.plugins.decide(p["id"], 1, True)["payload"]["status"]
            == "applied"
        )
        rows, _ = service.store.scan("manuscripts/tool-runs/")
        assert len(rows) == 1 and rows[0]["value"]["id"] == rid
        result = rows[0]["value"]["payload"]
        assert result["names"] == shown["text"].splitlines()
        owner_result = service.tools.names(tid, NameRun(expected_revision=1, seed="Северный порт", count=10))
        assert result == owner_result["payload"]
        assert not service.store.scan("automations/plugin-data/")[0]
        assert len(service.tools.list()["items"]) == 1
        assert service.store.integrity()["ok"]
    finally:
        await service.queue.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["archive", "source", "revoke"])
async def test_names_source_or_grants_changed_before_accept_block_apply(tmp_path, change):
    service, row, tid, rid = fixture(tmp_path)
    try:
        p = proposal(service, await run(service, row))
        if change == "revoke":
            configure(service.registry, row, grants=[])
        else:
            tool = service.tools.get(tid)
            if change == "archive":
                tool["payload"]["archived"] = True
            else:
                tool["payload"]["generator"]["prefixes"] = ["Новая"]
            service.tools.save(tid, 1, tool["payload"])
        with pytest.raises(AppError):
            service.decide(p["id"], 1, True)
        assert service.store.get("manuscripts/tool-runs/" + rid + ".json") is None
        assert service.decide(p["id"], 1, True, False)["payload"]["status"] == "rejected"
    finally:
        await service.queue.close()


@pytest.mark.asyncio
async def test_duplicate_name_result_rejected_before_any_write(tmp_path):
    service, row, _, _ = fixture(tmp_path, duplicate=True)
    try:
        job = await run(service, row)
        assert job["payload"]["error"]["code"] == "plugin_duplicate_effect"
        assert not service.previews(row["id"])["items"]
        assert not service.store.scan("manuscripts/tool-runs/")[0]
    finally:
        await service.queue.close()
