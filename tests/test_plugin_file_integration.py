import base64
import copy
import json
from uuid import uuid4

import pytest
from taverna.core import AppError
from taverna.plugin_actions import PluginFilePreview
from taverna.storage import digest
from test_plugin_compute import guest
from test_plugin_service import proposal, request, run, setup


def fixture(tmp_path):
    scope = tmp_path / "Документы вне программы"
    scope.mkdir()
    raw = "Таверна: <script>не выполнять()</script>".encode()
    path = scope / "Материал с пробелами.txt"
    path.write_bytes(raw)
    # The real WASM scans its input buffer for the base64 content marker and
    # traps if the server failed to supply selected file data. No fake worker.
    result = {
        "format": 1,
        "actions": [
            {"type": "own.set", "target": "counter", "expected_revision": 0, "value": "Файл прочитан"}
        ],
        "message": "Снимок получен WASM",
    }
    length = len(json.dumps(result, ensure_ascii=False).encode())
    marker = int.from_bytes(base64.b64encode(raw)[:4], "little")
    body = f"""(block $missing (loop $scan local.get 1 i32.const 4 i32.lt_u br_if $missing local.get 0 i32.load i32.const {marker} i32.eq if i64.const {length} return end local.get 0 i32.const 1 i32.add local.set 0 local.get 1 i32.const 1 i32.sub local.set 1 br $scan)) unreachable"""
    requests = [
        {"capability": "filesystem.read", "reason": "Выбранный материал", "scopes": [str(scope)]},
        {"capability": "own.write", "reason": "Проверенный результат", "scopes": ["counter"]},
    ]
    service, row = setup(
        tmp_path / "app", result, runtime="wasm", requests=requests, code=guest(result, body=body)
    )
    return service, row, scope, path, raw


def preview(service, row, scope, path):
    return service.file_preview(
        row["id"],
        PluginFilePreview(
            **request(row).model_dump(exclude={"parameters", "reads", "files", "confirmed_full_access_run"}),
            scope=str(scope),
            name=path.name,
        ),
    )


@pytest.mark.asyncio
async def test_owner_file_preview_real_wasm_input_snapshot_provenance_and_atomic_own_effect(tmp_path):
    service, row, scope, path, raw = fixture(tmp_path)
    try:
        checked = preview(service, row, scope, path)
        assert checked["sha256"] == digest(raw) and checked["text_preview"] == raw.decode()
        assert not checked["truncated"]
        selection = {"scope": str(scope), "name": path.name, "sha256": checked["sha256"], "confirmed": True}
        _, inputs, _ = service.inputs(
            {**request(row, files=[selection]).model_dump(mode="json"), "plugin_id": row["id"]}
        )
        assert base64.b64decode(inputs["files"][0]["content_base64"]) == raw
        p = proposal(service, await run(service, row, files=[selection]))
        assert p["payload"]["metadata"]["external_sources"] == [
            {"scope": str(scope), "path": path.name, "sha256": digest(raw), "bytes": len(raw)}
        ]
        assert service.store.get(service.own_key(row["id"], "counter")) is None
        # Receipt refers to the approved input snapshot, not the current external
        # file, and does not write or silently reread the external filesystem.
        path.write_text("Позднее редактирование владельцем", encoding="utf-8")
        service.decide(p["id"], 1, True)
        assert service.store.get(service.own_key(row["id"], "counter"))["payload"]["value"] == "Файл прочитан"
        assert path.read_text(encoding="utf-8") == "Позднее редактирование владельцем"
    finally:
        await service.queue.close()


@pytest.mark.asyncio
async def test_changed_file_after_preview_and_missing_confirmation_block_before_effect(tmp_path):
    service, row, scope, path, raw = fixture(tmp_path)
    try:
        selected = {"scope": str(scope), "name": path.name, "sha256": digest(raw), "confirmed": False}
        with pytest.raises(AppError) as error:
            await service.enqueue(row["id"], request(row, files=[selected]), str(uuid4()))
        assert error.value.code == "confirmation_required"
        selected["confirmed"] = True
        path.write_text("Изменилось", encoding="utf-8")
        job = await run(service, row, files=[selected])
        assert job["payload"]["error"]["code"] == "plugin_file_conflict"
        assert not service.previews(row["id"])["items"]
        assert service.store.get(service.own_key(row["id"], "counter")) is None
    finally:
        await service.queue.close()


@pytest.mark.asyncio
async def test_aggregate_file_input_limit_and_truncated_binary_preview(tmp_path):
    service, row, scope, path, _ = fixture(tmp_path)
    try:
        path.write_text("я" * 9000, encoding="utf-8")
        checked = preview(service, row, scope, path)
        assert checked["truncated"] and len(checked["text_preview"]) == 8000
        path.write_bytes(b"\xff\xfe\x00")
        assert preview(service, row, scope, path)["text_preview"] is None
        files = []
        for index in range(2):
            target = scope / f"{index}.bin"
            target.write_bytes(bytes([255 - index]) * 128 * 1024)
            files.append(
                {
                    "scope": str(scope),
                    "name": target.name,
                    "sha256": digest(target.read_bytes()),
                    "confirmed": True,
                }
            )
        job = await run(service, row, files=files)
        assert job["payload"]["error"]["code"] == "plugin_input_limit"
        assert not service.previews(row["id"])["items"]
    finally:
        await service.queue.close()


@pytest.mark.asyncio
async def test_file_preview_http_requires_csrf_current_scope_and_never_executes_guest(tmp_path):
    import httpx
    from taverna.core import Config
    from taverna.main import create_app

    service, row, scope, path, raw = fixture(tmp_path)
    app = create_app(Config(service.store.root))
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:8765"
        ) as client:
            session = await client.post(
                "/api/v1/session/bootstrap", json={"token": app.state.sessions.bootstrap()}
            )
            body = {
                **request(row).model_dump(
                    exclude={"parameters", "reads", "files", "confirmed_full_access_run"}
                ),
                "scope": str(scope),
                "name": path.name,
            }
            url = f"/api/v1/plugins/{row['id']}/files/preview"
            assert (await client.post(url, json=body)).status_code == 403
            headers = {"X-Taverna-CSRF": session.json()["csrf"]}
            response = await client.post(url, json=body, headers=headers)
            assert response.status_code == 200 and response.json()["sha256"] == digest(raw)
            assert app.state.generation.queue.worker is None
            assert not app.state.store.scan("jobs/records/")[0]
            forbidden = copy.deepcopy(body)
            forbidden["scope"] = str(service.store.root)
            assert (await client.post(url, json=forbidden, headers=headers)).status_code == 403
    finally:
        await app.state.generation.queue.close()
