import copy
from uuid import uuid4

import pytest
from taverna.core import AppError
from taverna.plugin_registry import PluginRegistry
from taverna.storage import JsonStore
from test_plugin_packages import package


def install(registry, raw):
    preview = registry.preview(raw)
    return registry.install(
        raw, preview["expected_revision"], preview["catalog_revision"], preview["sha256"], True
    )


def configure(registry, row, enabled=True, grants=None, trusted=False):
    return registry.configure(
        row["id"],
        row["revision"],
        registry.list()["catalog_revision"],
        enabled,
        grants or [],
        trusted,
        row["payload"]["package_sha256"],
        True,
    )


def test_install_disabled_grants_separate_epoch_cas_and_real_removal(tmp_path):
    registry = PluginRegistry(JsonStore(tmp_path))
    raw = package()
    preview = registry.preview(raw)
    assert registry.list()["items"] == []
    with pytest.raises(AppError):
        registry.install(raw, 0, 0, preview["sha256"], False)
    with pytest.raises(AppError):
        registry.install(raw, 0, 0, "0" * 64, True)
    row = install(registry, raw)
    assert not row["payload"]["enabled"] and not row["payload"]["grants"]
    assert install(registry, raw) == row
    grants = [{"capability": "notes.write", "scopes": ["new"]}]
    row = configure(registry, row, grants=grants)
    assert registry.allows(row, "notes.write", "new") and not registry.allows(row, "notes.read", "new")
    assert (
        registry.require_current(
            row["id"], row["revision"], row["payload"]["permission_epoch"], row["payload"]["package_sha256"]
        )
        == row
    )
    old = copy.deepcopy(row)
    row = configure(registry, row, enabled=False)
    with pytest.raises(AppError):
        registry.require_current(
            old["id"], old["revision"], old["payload"]["permission_epoch"], old["payload"]["package_sha256"]
        )
    with pytest.raises(AppError):
        configure(registry, old)
    root = tmp_path / "plugins" / row["id"]
    assert root.is_dir()
    result = registry.remove(
        row["id"],
        row["revision"],
        registry.list()["catalog_revision"],
        row["payload"]["package_sha256"],
        True,
    )
    assert result["deleted"] and result["files_removed"] and not root.exists()
    assert not registry.list()["items"] and registry.store.integrity()["ok"]


def test_dependencies_upgrade_reset_trust_disable_cascade_and_cycle(tmp_path):
    registry = PluginRegistry(JsonStore(tmp_path))
    aid, bid = str(uuid4()), str(uuid4())
    a = install(registry, package({"id": aid, "dependencies": [{"plugin_id": bid, "version": ">=1,<2"}]}))
    with pytest.raises(AppError):
        configure(registry, a)
    b = install(registry, package({"id": bid}))
    b = configure(registry, b)
    a = configure(registry, a, grants=[{"capability": "notes.write", "scopes": ["new"]}])
    assert a["payload"]["enabled"]
    b = install(registry, package({"id": bid, "version": "1.1.0"}))
    assert not b["payload"]["enabled"] and not b["payload"]["grants"]
    assert not registry.get(aid)["payload"]["enabled"]
    b = configure(registry, b)
    a = configure(registry, registry.get(aid))
    b = configure(registry, b, enabled=False)
    assert not registry.get(aid)["payload"]["enabled"]
    b = install(
        registry,
        package({"id": bid, "version": "1.2.0", "dependencies": [{"plugin_id": aid, "version": ">=1"}]}),
    )
    with pytest.raises(AppError) as error:
        configure(registry, b)
    assert error.value.code == "plugin_dependency_cycle"


def test_trusted_python_full_warning_hash_scope_and_tamper(tmp_path):
    registry = PluginRegistry(JsonStore(tmp_path))
    pid = str(uuid4())
    raw = package(
        {"id": pid, "runtime": "trusted_python", "entry": "main.py"}, b'def run(data): return {"actions": []}'
    )
    row = install(registry, raw)
    with pytest.raises(AppError) as error:
        configure(registry, row)
    assert error.value.status == 422
    row = configure(registry, row, trusted=True)
    assert row["payload"]["trusted_full_access_ack"]
    with pytest.raises(AppError):
        configure(registry, row, grants=[{"capability": "notes.read", "scopes": ["*"]}], trusted=True)
    with pytest.raises(AppError):
        configure(registry, row, grants=[{"capability": "notes.write", "scopes": ["*"]}], trusted=True)
    path = tmp_path / row["payload"]["directory"] / "main.py"
    path.write_bytes(b"changed")
    with pytest.raises(AppError) as error:
        registry.verify(row)
    assert error.value.code == "plugin_file_corrupt"
    # A new immutable version resets Python trust and all grants.
    row = install(
        registry,
        package(
            {"id": pid, "runtime": "trusted_python", "entry": "main.py", "version": "1.1.0"},
            b'def run(data): return {"actions": [], "version":2}',
        ),
    )
    assert not row["payload"]["trusted_full_access_ack"] and not row["payload"]["enabled"]


def test_foreign_catalog_change_invalidates_install_preview(tmp_path):
    registry = PluginRegistry(JsonStore(tmp_path))
    raw = package()
    preview = registry.preview(raw)
    install(registry, package())
    with pytest.raises(AppError):
        registry.install(raw, 0, preview["catalog_revision"], preview["sha256"], True)
    assert len(registry.list()["items"]) == 1


def test_filesystem_grants_cannot_expose_app_parent_or_redirect(tmp_path):
    root = tmp_path / "app"
    root.mkdir()
    external = tmp_path / "external"
    external.mkdir()
    registry = PluginRegistry(JsonStore(root))
    row = install(
        registry,
        package(
            {"requests": [{"capability": "filesystem.read", "reason": "Локальный документ", "scopes": []}]}
        ),
    )
    for scope in [str(root), str(root / "data"), str(tmp_path), "//remote/share"]:
        with pytest.raises(AppError):
            configure(registry, row, grants=[{"capability": "filesystem.read", "scopes": [scope]}])
    row = configure(registry, row, grants=[{"capability": "filesystem.read", "scopes": [str(external)]}])
    assert row["payload"]["enabled"]


def test_incompatible_package_may_be_installed_but_not_enabled(tmp_path):
    registry = PluginRegistry(JsonStore(tmp_path))
    row = install(registry, package({"application_versions": ">=9"}))
    with pytest.raises(AppError) as error:
        configure(registry, row)
    assert error.value.code == "plugin_incompatible"
    assert not registry.get(row["id"])["payload"]["enabled"]


def test_plugin_api_confirmation_csrf_and_permission_draft(tmp_path):
    from uuid import uuid4

    from test_bootstrap import client, login

    app, c = client(tmp_path, max_body=4 * 1024 * 1024)
    headers = login(app, c)
    raw = package()
    assert c.post("/api/v1/plugins/import-preview", content=raw).status_code == 403
    preview = c.post("/api/v1/plugins/import-preview", content=raw, headers=headers).json()
    params = {
        "expected_revision": 0,
        "catalog_revision": preview["catalog_revision"],
        "approved_sha256": preview["sha256"],
        "confirmed": "false",
    }
    assert c.post("/api/v1/plugins/import", content=raw, headers=headers, params=params).status_code == 422
    params["confirmed"] = "true"
    response = c.post("/api/v1/plugins/import", content=raw, headers=headers, params=params)
    assert response.status_code == 200
    row = response.json()
    catalog = c.get("/api/v1/plugins").json()["catalog_revision"]
    body = {
        "expected_revision": row["revision"],
        "catalog_revision": catalog,
        "approved_sha256": preview["sha256"],
        "enabled": True,
        "grants": [{"capability": "notes.write", "scopes": ["new"]}],
        "trusted_full_access_ack": False,
        "confirmed": True,
    }
    enabled = c.put("/api/v1/plugins/" + row["id"], json=body, headers=headers)
    assert enabled.status_code == 200 and enabled.json()["payload"]["enabled"]
    assert c.put("/api/v1/plugins/" + row["id"], json=body, headers=headers).status_code == 409
    draft = {
        "expected_revision": 0,
        "template_id": row["id"],
        "source_revision": 2,
        "raw": '{"grants": [',
        "payload": {
            "enabled": True,
            "grants": [
                {
                    "capability": "filesystem.read",
                    "scopes": ["unfinished directory"],
                    "full_access_warning_ack": False,
                }
            ],
            "trusted_full_access_ack": False,
        },
    }
    saved = c.put("/api/v1/owner-drafts/plugin/" + str(uuid4()), headers=headers, json=draft)
    assert saved.status_code == 200 and saved.json()["payload"]["raw"] == draft["raw"]
    assert app.state.plugin_registry.get(row["id"])["payload"]["grants"][0]["capability"] == "notes.write"


def test_redirected_filesystem_scope_is_explicitly_rejected(tmp_path):
    root = tmp_path / "app"
    root.mkdir()
    external = tmp_path / "external"
    external.mkdir()
    alias = tmp_path / "alias"
    try:
        alias.symlink_to(external, target_is_directory=True)
    except OSError as error:
        pytest.skip("OS did not permit creating the symlink fixture: " + type(error).__name__)
    registry = PluginRegistry(JsonStore(root))
    row = install(
        registry,
        package({"requests": [{"capability": "filesystem.read", "reason": "Документ", "scopes": []}]}),
    )
    with pytest.raises(AppError) as error:
        configure(registry, row, grants=[{"capability": "filesystem.read", "scopes": [str(alias)]}])
    assert error.value.code == "plugin_scope_invalid"


def test_windows_junction_scope_is_rejected(tmp_path):
    import os
    import subprocess

    if os.name != "nt":
        pytest.skip("Windows junction fixture requires Windows")
    root = tmp_path / "app"
    root.mkdir()
    external = tmp_path / "external"
    external.mkdir()
    alias = tmp_path / "junction"
    created = subprocess.run(["cmd", "/c", "mklink", "/J", str(alias), str(external)], capture_output=True)
    if created.returncode:
        pytest.skip("Windows did not permit creating the junction fixture")
    from taverna.storage import safe_path

    with pytest.raises(AppError):
        safe_path(tmp_path, "junction/protected.txt")
    registry = PluginRegistry(JsonStore(root))
    row = install(
        registry,
        package({"requests": [{"capability": "filesystem.read", "reason": "Документ", "scopes": []}]}),
    )
    with pytest.raises(AppError) as error:
        configure(registry, row, grants=[{"capability": "filesystem.read", "scopes": [str(alias)]}])
    assert error.value.code == "plugin_scope_invalid"


@pytest.mark.asyncio
async def test_revoke_api_cancels_an_actual_running_host_in_shared_queue(tmp_path):
    import asyncio

    import httpx
    from taverna.core import Config
    from taverna.main import create_app
    from taverna.plugin_compute import compute

    app = create_app(Config(tmp_path))
    registry = app.state.plugin_registry
    queue = app.state.generation.queue
    marker = tmp_path / "host-started"
    code = (
        "from pathlib import Path\nimport time\nPath("
        + repr(str(marker))
        + ').write_text("started")\ndef run(data): time.sleep(60); return {"actions": []}'
    ).encode()
    row = install(registry, package({"runtime": "trusted_python", "entry": "main.py"}, code))
    row = configure(registry, row, trusted=True)

    async def handler(payload, cancel, emit):
        current = registry.require_current(
            payload["plugin_id"],
            payload["plugin_revision"],
            payload["permission_epoch"],
            payload["package_sha256"],
        )
        return await compute(
            code,
            {},
            runtime="trusted_python",
            trusted_full_access=current["payload"]["trusted_full_access_ack"],
            cancel=cancel,
        )

    queue.handlers["plugin_compute"] = handler
    try:
        job = await queue.enqueue(
            "plugin_compute",
            {
                "plugin_id": row["id"],
                "plugin_revision": row["revision"],
                "permission_epoch": row["payload"]["permission_epoch"],
                "package_sha256": row["payload"]["package_sha256"],
            },
            priority=20,
            idempotency_key="real-host-revoke",
        )
        async with asyncio.timeout(5):
            while not marker.exists():
                await asyncio.sleep(0.01)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:8765"
        ) as client:
            auth = await client.post(
                "/api/v1/session/bootstrap", json={"token": app.state.sessions.bootstrap()}
            )
            body = {
                "expected_revision": row["revision"],
                "catalog_revision": registry.list()["catalog_revision"],
                "approved_sha256": row["payload"]["package_sha256"],
                "enabled": False,
                "grants": [],
                "trusted_full_access_ack": False,
                "confirmed": True,
            }
            response = await client.put(
                "/api/v1/plugins/" + row["id"], json=body, headers={"X-Taverna-CSRF": auth.json()["csrf"]}
            )
            assert response.status_code == 200
        async with asyncio.timeout(5):
            while (await queue.get(job["id"]))["payload"]["state"] not in (
                "cancelled",
                "failed",
                "completed",
                "interrupted",
            ):
                await asyncio.sleep(0.01)
        assert (await queue.get(job["id"]))["payload"]["state"] == "cancelled"
        assert not registry.get(row["id"])["payload"]["enabled"]
        assert marker.exists()  # Revocation cannot undo effects already performed by trusted code.
    finally:
        await queue.close()
